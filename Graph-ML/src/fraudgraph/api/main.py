"""FastAPI service that loads trained models at startup and exposes
account scoring, ring detection, and visualization endpoints.

Endpoints:
  GET  /health           — liveness check
  GET  /                 — dashboard (static HTML)
  GET  /metrics         — model comparison metrics (from reports/metrics.json)
  GET  /score/{account}  — single-account fraud risk + feature profile
  POST /score/batch      — batch scoring (up to 500 accounts)
  GET  /rings             — ranked fraud rings from Louvain detection
  GET  /viz               — interactive PyVis ring visualization
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import numpy as np
import torch
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from fraudgraph.config import Config, CONFIG
from fraudgraph.models import MODEL_REGISTRY

app = FastAPI(
    title="FraudGraph API",
    description="GNN-based fraud ring detection on transaction networks",
    version="0.1.0",
)

# --- static dashboard ---
DASHBOARD_DIR = Path(__file__).parent / "static"
if DASHBOARD_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(DASHBOARD_DIR)), name="static")


# --- startup state ---

@lru_cache(maxsize=1)
def load_state() -> dict:
    """Load graph, predictions, and trained model weights (once)."""
    config = CONFIG
    config.ensure_dirs()

    # predictions
    pred_path = config.models_dir / "predictions.npz"
    if not pred_path.exists():
        raise FileNotFoundError("Run `scripts/train.py` and `scripts/detect_rings.py` first.")
    preds = np.load(pred_path, allow_pickle=True)
    account_ids = preds["account_ids"].tolist()
    account_to_idx = {acc: i for i, acc in enumerate(account_ids)}
    y_true = preds["y_true"]

    best_info = json.loads((config.models_dir / "best_model.json").read_text(encoding="utf-8"))
    best_name = best_info["best_model"]
    risk_scores = preds[f"probs_{best_name}"]

    # model (for potential online re-scoring)
    model_cls = MODEL_REGISTRY[best_name]
    model = model_cls(in_dim=best_info["in_dim"], hidden_dim=config.hidden_dim,
                       embedding_dim=config.embedding_dim, dropout=config.dropout)
    model.load_state_dict(torch.load(config.models_dir / f"{best_name}.pt", weights_only=True))
    model.eval()

    # rings report
    rings_path = config.reports_dir / "rings.json"
    rings = json.loads(rings_path.read_text(encoding="utf-8")) if rings_path.exists() else None

    # metrics report
    metrics_path = config.reports_dir / "metrics.json"
    metrics = json.loads(metrics_path.read_text(encoding="utf-8")) if metrics_path.exists() else None

    return {
        "account_ids": account_ids,
        "account_to_idx": account_to_idx,
        "y_true": y_true,
        "risk_scores": risk_scores,
        "best_model": best_name,
        "model": model,
        "rings": rings,
        "metrics": metrics,
        "config": config,
    }


# --- schemas ---

class BatchRequest(BaseModel):
    accounts: list[str]

    @classmethod
    def model_validator(cls, data):
        if len(data.get("accounts", [])) > 500:
            raise ValueError("batch size limited to 500 accounts")
        return data


class ScoreResponse(BaseModel):
    account: str
    fraud_risk: float
    predicted: str  # "fraud" or "legit"
    ground_truth: str | None = None  # only available on synthetic data


class RingMember(BaseModel):
    account: str
    fraud_risk: float
    predicted: str


# --- endpoints ---

@app.get("/health")
async def health():
    return {"status": "ok", "model": load_state()["best_model"]}


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    index = DASHBOARD_DIR / "dashboard.html"
    if index.exists():
        return index.read_text(encoding="utf-8")
    return HTMLResponse("<h1>FraudGraph API is running</h1><p>Visit <code>/docs</code> for the interactive API.</p>")


@app.get("/metrics")
async def metrics():
    return load_state()["metrics"]


@app.get("/score/{account}", response_model=ScoreResponse)
async def score_account(account: str):
    state = load_state()
    if account not in state["account_to_idx"]:
        raise HTTPException(404, f"Account {account} not found")
    idx = state["account_to_idx"][account]
    risk = float(state["risk_scores"][idx])
    gt = int(state["y_true"][idx])
    return ScoreResponse(
        account=account,
        fraud_risk=round(risk, 4),
        predicted="fraud" if risk >= 0.5 else "legit",
        ground_truth="fraud" if gt else "legit",
    )


@app.post("/score/batch")
async def score_batch(req: BatchRequest):
    state = load_state()
    results = []
    for account in req.accounts:
        idx = state["account_to_idx"].get(account)
        if idx is None:
            results.append({"account": account, "error": "not found"})
            continue
        risk = float(state["risk_scores"][idx])
        results.append({
            "account": account,
            "fraud_risk": round(risk, 4),
            "predicted": "fraud" if risk >= 0.5 else "legit",
        })
    return {"results": results}


@app.get("/rings")
async def get_rings():
    state = load_state()
    if state["rings"] is None:
        raise HTTPException(503, "Ring detection not yet run — execute scripts/detect_rings.py")
    # Return a summary; full member lists on demand.
    rings_summary = []
    for ring in state["rings"]["rings"]:
        rings_summary.append({
            "rank": ring.get("rank"),
            "size": ring["size"],
            "mean_risk": round(ring["mean_risk"], 4),
            "ring_score": round(ring["ring_score"], 4),
            "true_fraud_count": ring["true_fraud_count"],
            "true_fraud_density": round(ring["true_fraud_density"], 4),
            "members": ring["members"],
        })
    return {
        "ring_level_metrics": state["rings"]["ring_level_metrics"],
        "rings": rings_summary,
    }


@app.get("/viz")
async def visualization():
    """Serve the interactive PyVis fraud ring HTML."""
    viz_path = load_state()["config"].reports_dir / "fraud_ring.html"
    if not viz_path.exists():
        raise HTTPException(503, "Visualization not yet generated — execute scripts/make_viz.py")
    return FileResponse(str(viz_path), media_type="text/html")

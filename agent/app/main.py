"""Sentinel -- self-healing DevOps agent. FastAPI interface + ops dashboard.

Endpoints:
  GET  /                              dashboard
  GET  /api/health                    agent readiness
  GET  /api/status                    agent + demo server liveness
  GET  /api/incidents                 list incidents (newest first)
  GET  /api/incidents/{id}            single incident with timeline
  POST /api/incidents/{id}/approve    HITL: approve the proposed fix
  POST /api/incidents/{id}/reject     HITL: reject the proposed fix
  POST /api/chaos                     inject a bounded fault into the local demo server
  POST /api/server/start              bring the demo server back up
  GET  /api/logs/tail                 tail of the watched service log
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import runtime
from .config import Settings
from .graph import build_graph
from .monitor import LogMonitor
from .runbooks import RunbookStore
from .store import IncidentStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("sentinel")

STATIC_DIR = Path(__file__).resolve().parent / "static"

CHAOS_MODES = ("db_pool", "oom", "ssl", "bad_config", "dependency_down", "bad_release", "unknown")


class ChaosRequest(BaseModel):
    mode: str = "db_pool"
    seed: int = 0


class DecisionRequest(BaseModel):
    approver: str = "engineer"
    reason: str | None = None


async def handle_incident(error_code: str, excerpt: list[str]) -> None:
    incident_id = f"INC-{uuid.uuid4().hex[:6].upper()}"
    runtime.store.create(
        incident_id, error_code=error_code, log_excerpt=excerpt, status="detected"
    )
    runtime.store.event(
        incident_id,
        "detected",
        f"{error_code} detected in {Path(runtime.settings.log_path).name} ({len(excerpt)} error lines)",
    )
    initial_state = {
        "incident_id": incident_id,
        "detected_at": time.time(),
        "error_code": error_code,
        "raw_log": excerpt,
    }
    await runtime.runner.run_new(incident_id, initial_state)


def _dispatch_incident(error_code: str, excerpt: list[str]) -> None:
    # Called synchronously from the monitor task, which owns the running loop.
    asyncio.get_running_loop().create_task(handle_incident(error_code, excerpt))


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = Settings()
    runtime.settings = settings
    settings.log_path.parent.mkdir(parents=True, exist_ok=True)
    settings.chroma_path.mkdir(parents=True, exist_ok=True)
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)

    runtime.store = IncidentStore(str(settings.database_path))
    # Seeding here also warms the embedding model, so the first real incident
    # never pays the cold-start cost (keeps detection -> proposal < 10s).
    runtime.runbooks = RunbookStore(settings.runbooks_dir, settings.chroma_path)
    from langgraph.checkpoint.sqlite import SqliteSaver
    runtime.checkpointer_connection = sqlite3.connect(
        str(settings.database_path), check_same_thread=False
    )
    checkpointer = SqliteSaver(runtime.checkpointer_connection)
    checkpointer.setup()
    runtime.runner.bind(build_graph(checkpointer))

    monitor = LogMonitor(
        str(settings.log_path),
        settings.monitor_poll_seconds,
        settings.cooldown_seconds,
        on_incident=_dispatch_incident,
    )
    runtime.monitor = monitor
    monitor_task = asyncio.create_task(monitor.run())

    _.state.ready = True
    log.info("Sentinel ready: watching %s | target=%s | executor=%s | approval=%s",
             settings.log_path, settings.target_container, settings.executor_mode,
             "required" if settings.require_approval else "auto-pilot")
    try:
        yield
    finally:
        monitor_task.cancel()
        _.state.ready = False
        runtime.store.close()
        runtime.monitor = None
        runtime.checkpointer_connection.close()
        runtime.checkpointer_connection = None


app = FastAPI(title="Sentinel -- Self-Healing DevOps Agent", version="1.0.0", lifespan=lifespan)


@app.get("/")
def dashboard():
    return FileResponse(STATIC_DIR / "dashboard.html")


@app.get("/api/health")
def health():
    return {"status": "ok", "ready": bool(app.state.ready), "service": "sentinel-agent"}


@app.get("/api/status")
async def status():
    up = False
    healthy = False
    try:
        async with httpx.AsyncClient() as client:
            live_response, health_response = await asyncio.gather(
                client.get(f"{runtime.settings.demo_server_url}/live", timeout=1.5),
                client.get(f"{runtime.settings.demo_server_url}/health", timeout=1.5),
            )
            up = live_response.status_code == 200
            healthy = health_response.status_code == 200
    except Exception:
        up = False
        healthy = False
    return {"agent": "ok", "server_up": up, "server_healthy": healthy, "server": runtime.settings.target_container}


@app.get("/api/incidents")
def incidents():
    return {"incidents": runtime.store.list()}


@app.get("/api/incidents/{incident_id}")
def incident(incident_id: str):
    record = runtime.store.get(incident_id)
    if not record:
        raise HTTPException(404, f"incident {incident_id} not found")
    return record


async def _decide(incident_id: str, approved: bool, body: DecisionRequest | None) -> dict:
    body = body or DecisionRequest()
    record = runtime.store.get(incident_id)
    if not record:
        raise HTTPException(404, f"incident {incident_id} not found")
    if record["status"] != "waiting_approval":
        raise HTTPException(409, f"incident {incident_id} is '{record['status']}', nothing to decide")
    payload = {"approved": approved, "approver": body.approver}
    if body.reason:
        payload["reason"] = body.reason
    await runtime.runner.resume(incident_id, payload)
    return runtime.store.get(incident_id)


@app.post("/api/incidents/{incident_id}/approve")
async def approve(incident_id: str, body: DecisionRequest | None = None):
    return await _decide(incident_id, True, body)


@app.post("/api/incidents/{incident_id}/reject")
async def reject(incident_id: str, body: DecisionRequest | None = None):
    return await _decide(incident_id, False, body)


@app.post("/api/chaos")
async def chaos(request: ChaosRequest):
    if request.mode not in CHAOS_MODES:
        raise HTTPException(400, f"unknown mode {request.mode!r}; expected one of {CHAOS_MODES}")
    if request.mode == "dependency_down":
        from .executor import inject_dependency_down

        result = await asyncio.to_thread(inject_dependency_down, request.seed)
        if not result.get("ok"):
            raise HTTPException(500, result.get("detail", "dependency fault injection failed"))
        return {"ok": True, "mode": request.mode, "seed": request.seed, "detail": result["detail"]}
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{runtime.settings.demo_server_url}/chaos",
                json={"mode": request.mode, "seed": request.seed}, timeout=5
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            409,
            f"demo server unreachable ({type(exc).__name__}) -- it may already be down; "
            "bring it back from the dashboard",
        )
    return response.json()


@app.post("/api/evaluation/reset")
async def evaluation_reset():
    if not runtime.settings.evaluation_mode:
        raise HTTPException(404, "evaluation reset is disabled")
    from .executor import reset_testbed

    result = await asyncio.to_thread(reset_testbed)
    if not result.get("ok"):
        raise HTTPException(500, result.get("detail", "testbed reset failed"))
    return result


@app.post("/api/server/start")
async def start_server():
    from .executor import start_target

    result = await asyncio.to_thread(start_target)
    if not result.get("ok"):
        raise HTTPException(500, result.get("detail", "failed to start server"))
    return result


@app.get("/api/logs/tail")
def logs_tail(limit: int = 200):
    try:
        with open(runtime.settings.log_path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - 64 * 1024))
            data = fh.read()
        lines = data.decode("utf-8", "replace").splitlines()[-limit:]
    except OSError:
        lines = []
    return {"path": str(runtime.settings.log_path), "lines": lines}

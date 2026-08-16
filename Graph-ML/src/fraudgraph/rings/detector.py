"""Louvain community detection → ranked fraud rings.

Communities are scored by ``mean_risk * sqrt(size)``: the GNN's risk
score for member accounts, rewarded for covering larger groups. Top-K
communities are flagged as fraud rings and evaluated against ground
truth (when available) with ring-level precision/recall.
"""

from __future__ import annotations

import networkx as nx
import numpy as np
import pandas as pd

from fraudgraph.config import Config
from fraudgraph.features.graph_features import build_nx_graph


def detect_rings(
    txns: pd.DataFrame,
    accounts: pd.DataFrame,
    risk_scores: np.ndarray,
    config: Config,
) -> dict:
    """Discover and rank fraud rings; returns a JSON-ready report."""
    account_ids = accounts["account"].tolist()
    node_index = {acc: i for i, acc in enumerate(account_ids)}
    risk_by_account = dict(zip(account_ids, risk_scores))
    fraud_by_account = dict(zip(accounts["account"], accounts["is_fraud"]))
    ring_by_account = dict(zip(accounts["account"], accounts["ring_id"]))

    digraph = build_nx_graph(txns)
    undirected = digraph.to_undirected()
    for _, _, attrs in undirected.edges(data=True):
        attrs["weight"] = attrs["count"]

    communities = nx.community.louvain_communities(
        undirected,
        weight="weight",
        resolution=config.louvain_resolution,
        seed=config.seed,
    )

    rings = []
    for community in communities:
        members = [acc for acc in community if acc in node_index]
        if len(members) < 3:  # rings need at least a triangle of activity
            continue
        risks = np.array([risk_by_account[m] for m in members])
        fraud_flags = [fraud_by_account[m] for m in members]
        ring_ids = [ring_by_account[m] for m in members]
        injected = pd.Series([r for r in ring_ids if r >= 0])
        rings.append(
            {
                "size": len(members),
                "mean_risk": float(risks.mean()),
                "max_risk": float(risks.max()),
                "ring_score": float(risks.mean() * np.sqrt(len(members))),
                "flagged_fraud_count": int((risks >= 0.5).sum()),
                "true_fraud_count": int(sum(fraud_flags)),
                "true_fraud_density": float(np.mean(fraud_flags)),
                "matched_injected_ring": int(injected.mode().iloc[0]) if not injected.empty else -1,
                "members": sorted(members),
            }
        )

    rings.sort(key=lambda r: r["ring_score"], reverse=True)
    for rank, ring in enumerate(rings[: config.top_rings], start=1):
        ring["rank"] = rank

    report = {
        "n_communities": len(rings),
        "ring_level_metrics": _ring_level_metrics(rings, config.top_rings, accounts),
        "rings": rings,
    }
    return report


def _ring_level_metrics(rings: list[dict], top_k: int, accounts: pd.DataFrame) -> dict:
    """Precision/recall of flagging accounts via top-K ring membership."""
    flagged_members: set[str] = set()
    for ring in rings[:top_k]:
        flagged_members.update(ring["members"])

    fraud_accounts = set(accounts.loc[accounts["is_fraud"] == 1, "account"])
    total_fraud = len(fraud_accounts)
    true_positives = sum(1 for acc in flagged_members if acc in fraud_accounts)
    return {
        "method": f"top-{top_k} Louvain communities by ring_score",
        "accounts_flagged": len(flagged_members),
        "true_fraud_in_flagged": true_positives,
        "total_fraud": total_fraud,
        "precision": round(true_positives / max(len(flagged_members), 1), 4),
        "recall": round(true_positives / max(total_fraud, 1), 4),
    }

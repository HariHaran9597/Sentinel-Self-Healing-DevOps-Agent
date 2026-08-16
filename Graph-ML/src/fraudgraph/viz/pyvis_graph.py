"""Interactive PyVis visualization of a detected fraud ring.

Renders the top-ranked ring plus one hop of transaction context.
Node colour encodes GNN risk (blue → red), node size encodes PageRank,
and tooltips expose per-account scores.
"""

from __future__ import annotations

from pathlib import Path

import networkx as nx
import numpy as np
import pandas as pd
from pyvis.network import Network

from fraudgraph.features.graph_features import build_nx_graph


def render_ring(
    txns: pd.DataFrame,
    accounts: pd.DataFrame,
    risk_scores: np.ndarray,
    ring_members: list[str],
    out_path: Path,
    max_context_nodes: int = 120,
) -> Path:
    account_ids = accounts["account"].tolist()
    risk_by_account = dict(zip(account_ids, risk_scores))
    fraud_by_account = dict(zip(accounts["account"], accounts["is_fraud"]))

    digraph = build_nx_graph(txns)
    pagerank = nx.pagerank(digraph, weight="amount_sum")

    # One hop of context so victims / counterparties are visible.
    ring_set = set(ring_members)
    context: set[str] = set()
    for src, dst in digraph.edges():
        if src in ring_set and dst not in ring_set:
            context.add(dst)
        elif dst in ring_set and src not in ring_set:
            context.add(src)
    context = set(sorted(context)[:max_context_nodes])
    focus = ring_set | context

    sub = digraph.subgraph(focus).copy()

    net = Network(height="720px", width="100%", bgcolor="#111827", font_color="#e5e7eb", directed=False)
    net.barnes_hut(gravity=-3000, central_gravity=0.3, spring_length=120)

    pr_values = [pagerank.get(n, 0.0) for n in sub.nodes()]
    pr_scale = max(pr_values) if pr_values and max(pr_values) > 0 else 1.0

    for node in sub.nodes():
        risk = risk_by_account.get(node, 0.0)
        is_fraud = fraud_by_account.get(node, 0)
        color = _risk_color(risk)
        size = 8 + 30 * (pagerank.get(node, 0.0) / pr_scale)
        in_ring = node in ring_set
        tooltip = (
            f"<b>{node}</b><br>risk: {risk:.3f} "
            f"({'FRAUD' if risk >= 0.5 else 'legit'} predicted)"
            f"<br>ground truth: {'fraud' if is_fraud else 'legit'}"
            f"<br>PageRank: {pagerank.get(node, 0.0):.2e}"
            f"<br>{'⚠ ring member' if in_ring else 'context'}"
        )
        net.add_node(
            node,
            label=node if in_ring else "",
            size=size,
            color={
                "background": color,
                "border": "#fbbf24" if in_ring else "#374151",
                "highlight": {"background": color, "border": "#ffffff"},
            },
            title=tooltip,
            borderWidth=3 if in_ring else 1,
        )

    for src, dst, attrs in sub.edges(data=True):
        net.add_edge(src, dst, value=attrs["count"], color="rgba(148,163,184,0.35)")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    net.save_graph(str(out_path))
    return out_path


def _risk_color(risk: float) -> str:
    """Interpolate blue → amber → red across the risk range."""
    risk = min(max(risk, 0.0), 1.0)
    if risk < 0.5:  # blue (low) → amber (mid)
        t = risk / 0.5
        r, g, b = int(56 + (245 - 56) * t), int(120 + (158 - 120) * t), int(220 - (11) * t)
    else:  # amber → red (high)
        t = (risk - 0.5) / 0.5
        r, g, b = int(245 + (239 - 245) * t), int(158 - (68) * t), int(11 + (83) * t)
    return f"rgb({r},{g},{b})"

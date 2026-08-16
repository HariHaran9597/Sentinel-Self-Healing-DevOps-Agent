"""NetworkX graph construction and node feature engineering.

Phase 1 of the pipeline: turn raw transactions into graph structure and
"who is important / who behaves oddly" features — degree, money-weighted
PageRank, approximate betweenness, clustering, velocity, drain ratios.
"""

from __future__ import annotations

import networkx as nx
import numpy as np
import pandas as pd

FEATURE_NAMES = [
    "log_out_degree",
    "log_in_degree",
    "pagerank",
    "betweenness",
    "clustering",
    "log_txn_count_sent",
    "log_txn_count_recv",
    "mean_log_amount_sent",
    "mean_log_amount_recv",
    "max_log_amount_sent",
    "drain_ratio_sent",
    "max_txns_per_step",  # velocity: busiest-hour transaction count
    "cashout_ratio_sent",
    "is_merchant",
]


def build_nx_graph(txns: pd.DataFrame, ensure_nodes: pd.Series | None = None) -> nx.DiGraph:
    """Aggregate transactions into a weighted directed account graph.

    Parallel transactions between the same pair collapse into one edge
    carrying ``count`` and ``amount_sum``. ``ensure_nodes`` adds accounts
    that may have zero transactions (isolated nodes).
    """
    agg = (
        txns.groupby(["nameOrig", "nameDest"])
        .agg(count=("amount", "size"), amount_sum=("amount", "sum"))
        .reset_index()
    )
    graph = nx.DiGraph()
    graph.add_nodes_from(set(txns["nameOrig"]) | set(txns["nameDest"]))
    if ensure_nodes is not None:
        graph.add_nodes_from(ensure_nodes)
    for row in agg.itertuples(index=False):
        graph.add_edge(
            row.nameOrig,
            row.nameDest,
            count=int(row.count),
            amount_sum=float(row.amount_sum),
        )
    return graph


def compute_node_features(
    txns: pd.DataFrame,
    accounts: pd.DataFrame,
    graph: nx.DiGraph,
    betweenness_sample: int = 250,
    seed: int = 42,
) -> pd.DataFrame:
    """One row per account, indexed like ``accounts`` — the GNN's node features."""
    node_list = accounts["account"].tolist()

    out_deg = np.array([graph.out_degree(n) for n in node_list], dtype=float)
    in_deg = np.array([graph.in_degree(n) for n in node_list], dtype=float)
    undirected = graph.to_undirected()

    pagerank = nx.pagerank(graph, weight="amount_sum")
    k = min(betweenness_sample, len(node_list))
    betweenness = nx.betweenness_centrality(
        graph, k=k if k < len(node_list) else None, normalized=True, seed=seed
    )
    clustering = nx.clustering(undirected)

    sent = txns.groupby("nameOrig")
    recv = txns.groupby("nameDest")

    def _col(frame, key, default):
        return frame[key] if key in frame else pd.Series(default, index=frame.index)

    sent_stats = sent.agg(
        txn_count=("amount", "size"),
        mean_log_amount=("amount", lambda s: float(np.log1p(s).mean())),
        max_log_amount=("amount", lambda s: float(np.log1p(s).max())),
        cashout_count=("type", lambda s: int((s == "CASH_OUT").sum())),
    )
    # Velocity: transactions in the account's single busiest hour.
    sent_velocity = (
        txns.groupby(["nameOrig", "step"]).size().groupby("nameOrig").max()
    )
    # Drain ratio: share of outgoing transfers that empty the account exactly.
    drain = txns.assign(drained=(txns["newbalanceOrg"] < 0.01) & (txns["oldbalanceOrg"] > 0))
    drain_ratio = drain.groupby("nameOrig")["drained"].mean()

    recv_stats = recv.agg(
        txn_count=("amount", "size"),
        mean_log_amount=("amount", lambda s: float(np.log1p(s).mean())),
    )

    features = pd.DataFrame(index=accounts.index)
    acct = accounts["account"]
    features["log_out_degree"] = np.log1p(out_deg)
    features["log_in_degree"] = np.log1p(in_deg)
    features["pagerank"] = acct.map(pagerank).fillna(0.0)
    features["betweenness"] = acct.map(betweenness).fillna(0.0)
    features["clustering"] = acct.map(clustering).fillna(0.0)
    features["log_txn_count_sent"] = np.log1p(acct.map(_col(sent_stats, "txn_count", 0)).fillna(0))
    features["log_txn_count_recv"] = np.log1p(acct.map(_col(recv_stats, "txn_count", 0)).fillna(0))
    features["mean_log_amount_sent"] = acct.map(_col(sent_stats, "mean_log_amount", 0.0)).fillna(0.0)
    features["mean_log_amount_recv"] = acct.map(_col(recv_stats, "mean_log_amount", 0.0)).fillna(0.0)
    features["max_log_amount_sent"] = acct.map(_col(sent_stats, "max_log_amount", 0.0)).fillna(0.0)
    features["drain_ratio_sent"] = acct.map(drain_ratio).fillna(0.0)
    features["max_txns_per_step"] = np.log1p(acct.map(sent_velocity).fillna(0))
    features["cashout_ratio_sent"] = (
        acct.map(_col(sent_stats, "cashout_count", 0)).fillna(0) / np.maximum(out_deg, 1.0)
    )
    features["is_merchant"] = acct.str.startswith("M").astype(float)

    features = features[FEATURE_NAMES].astype(float)
    assert not features.isna().any().any(), "node features contain NaNs"
    return features

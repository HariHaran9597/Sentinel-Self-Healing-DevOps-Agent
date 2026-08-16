"""Assemble transactions + accounts into a PyTorch Geometric graph.

Accounts become nodes, transactions become (bidirectional) edges.
Features are z-scored using train-mask statistics only, so test-fold
information never leaks into feature scaling.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import torch
from sklearn.model_selection import train_test_split
from torch_geometric.data import Data

from fraudgraph.config import Config
from fraudgraph.features.graph_features import (
    FEATURE_NAMES,
    build_nx_graph,
    compute_node_features,
)

TXN_TYPES = ("PAYMENT", "TRANSFER", "CASH_OUT", "DEBIT", "CASH_IN")


def build_graph(
    txns: pd.DataFrame,
    accounts: pd.DataFrame,
    config: Config,
) -> tuple[Data, list[str], pd.DataFrame]:
    """Return (pyg_data, account_ids, raw_feature_frame).

    The Data object carries ``x`` (standardized features), ``edge_index``,
    ``edge_attr`` (type one-hot + log amount), ``y``, and stratified
    train/val/test boolean masks.
    """
    account_ids = accounts["account"].tolist()
    node_index = {acc: i for i, acc in enumerate(account_ids)}

    nx_graph = build_nx_graph(txns, ensure_nodes=accounts["account"])
    features = compute_node_features(
        txns,
        accounts,
        nx_graph,
        betweenness_sample=config.betweenness_sample_size,
        seed=config.seed,
    )

    y = torch.tensor(accounts["is_fraud"].to_numpy(), dtype=torch.float)

    # --- edges: bidirectional so GCN/SAGE aggregate from both directions ---
    src = txns["nameOrig"].map(node_index).to_numpy()
    dst = txns["nameDest"].map(node_index).to_numpy()
    edge_index = torch.tensor(
        np.vstack([np.concatenate([src, dst]), np.concatenate([dst, src])]),
        dtype=torch.long,
    )

    # --- edge features: txn-type one-hot + log amount, mirrored like edges ---
    type_onehot = np.zeros((len(txns), len(TXN_TYPES)), dtype=np.float32)
    for j, txn_type in enumerate(TXN_TYPES):
        type_onehot[txns["type"].to_numpy() == txn_type, j] = 1.0
    log_amount = np.log1p(txns["amount"].to_numpy(dtype=np.float32))[:, None]
    edge_features = np.hstack([type_onehot, log_amount])
    edge_attr = torch.tensor(np.concatenate([edge_features, edge_features], axis=0))

    # --- stratified node split ---
    labels = accounts["is_fraud"].to_numpy()
    train_idx, test_idx = train_test_split(
        np.arange(len(account_ids)),
        train_size=config.train_frac,
        stratify=labels,
        random_state=config.seed,
    )
    val_relative = config.val_frac / (1.0 - config.train_frac)
    val_idx, test_idx = train_test_split(
        test_idx,
        train_size=val_relative,
        stratify=labels[test_idx],
        random_state=config.seed,
    )

    def _mask(indices: np.ndarray) -> torch.Tensor:
        mask = torch.zeros(len(account_ids), dtype=torch.bool)
        mask[torch.tensor(indices, dtype=torch.long)] = True
        return mask

    train_mask, val_mask, test_mask = _mask(train_idx), _mask(val_idx), _mask(test_idx)

    # --- standardize on train statistics only ---
    x = features.to_numpy(dtype=np.float32)
    mean, std = x[train_idx].mean(axis=0), x[train_idx].std(axis=0) + 1e-8
    x = (x - mean) / std

    data = Data(
        x=torch.tensor(x, dtype=torch.float),
        edge_index=edge_index,
        edge_attr=edge_attr,
        y=y,
        train_mask=train_mask,
        val_mask=val_mask,
        test_mask=test_mask,
    )
    data.node_index = node_index  # account string -> integer node id
    return data, account_ids, features


def save_graph(data: Data, account_ids: list[str], config: Config) -> None:
    config.ensure_dirs()
    torch.save(
        {
            "data": data,
            "account_ids": account_ids,
            "feature_names": FEATURE_NAMES,
            "config": {
                "seed": config.seed,
                "hidden_dim": config.hidden_dim,
                "embedding_dim": config.embedding_dim,
                "dropout": config.dropout,
            },
        },
        config.graph_pt,
    )


def load_graph(path: Path) -> tuple[Data, list[str], list[str]]:
    payload = torch.load(path, weights_only=False)
    return payload["data"], payload["account_ids"], payload["feature_names"]

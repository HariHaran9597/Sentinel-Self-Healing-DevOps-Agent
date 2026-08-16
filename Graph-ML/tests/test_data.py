"""Tests for data generation and graph construction."""

import numpy as np
import pandas as pd
import torch
from torch_geometric.data import Data


class TestSyntheticGenerator:
    def test_output_schema(self, sample_txns_accounts):
        txns, accounts = sample_txns_accounts
        expected_txn_cols = {
            "step", "type", "amount", "nameOrig", "oldbalanceOrg",
            "newbalanceOrg", "nameDest", "oldbalanceDest", "newbalanceDest",
            "isFraud", "isFlaggedFraud",
        }
        assert set(txns.columns) == expected_txn_cols
        assert set(accounts.columns) == {"account", "is_fraud", "ring_id"}
        assert len(txns) > 0
        assert len(accounts) > 0

    def test_fraud_accounts_labeled(self, sample_txns_accounts):
        _, accounts = sample_txns_accounts
        fraud = accounts[accounts["is_fraud"] == 1]
        assert len(fraud) > 0
        # Ring members have ring_id >= 0; standalone fraudsters have ring_id == -2
        assert (fraud["ring_id"] >= -2).all()

    def test_fraud_transactions_exist(self, sample_txns_accounts):
        txns, _ = sample_txns_accounts
        fraud_txns = txns[txns["isFraud"] == 1]
        assert len(fraud_txns) > 0
        # Fraud transactions have positive amounts
        assert (fraud_txns["amount"] > 0).all()

    def test_balances_consistent(self, sample_txns_accounts):
        txns, _ = sample_txns_accounts
        # newbalance should never exceed oldbalance (no money creation)
        assert (txns["newbalanceOrg"] <= txns["oldbalanceOrg"] + 0.02).all()
        # amounts are positive
        assert (txns["amount"] >= 0).all()


class TestGraphBuilder:
    def test_graph_shapes(self, sample_txns_accounts, tiny_config):
        from fraudgraph.data.graph_builder import build_graph
        txns, accounts = sample_txns_accounts
        data, account_ids, features = build_graph(txns, accounts, tiny_config)
        assert isinstance(data, Data)
        assert data.num_nodes == len(accounts)
        assert data.num_edges > 0
        assert data.x.size(0) == len(accounts)
        assert data.y.size(0) == len(accounts)
        assert data.train_mask.sum() > 0
        assert data.val_mask.sum() > 0
        assert data.test_mask.sum() > 0
        # Masks are disjoint
        assert not (data.train_mask & data.val_mask).any()
        assert not (data.train_mask & data.test_mask).any()
        assert not (data.val_mask & data.test_mask).any()

    def test_no_nan_features(self, sample_txns_accounts, tiny_config):
        from fraudgraph.data.graph_builder import build_graph
        txns, accounts = sample_txns_accounts
        data, _, _ = build_graph(txns, accounts, tiny_config)
        assert not torch.isnan(data.x).any()

    def test_edge_index_valid(self, sample_txns_accounts, tiny_config):
        from fraudgraph.data.graph_builder import build_graph
        txns, accounts = sample_txns_accounts
        data, _, _ = build_graph(txns, accounts, tiny_config)
        assert data.edge_index.max() < data.num_nodes
        assert data.edge_index.min() >= 0

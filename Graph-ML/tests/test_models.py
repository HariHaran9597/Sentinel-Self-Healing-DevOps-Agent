"""Tests for model forward passes and smoke training."""

import pytest
import torch


class TestModelForward:
    @pytest.mark.parametrize("model_name", ["gcn", "graphsage", "mlp"])
    def test_forward_shape(self, model_name, sample_txns_accounts, tiny_config):
        from fraudgraph.data.graph_builder import build_graph
        from fraudgraph.models import MODEL_REGISTRY
        txns, accounts = sample_txns_accounts
        data, _, _ = build_graph(txns, accounts, tiny_config)
        model = MODEL_REGISTRY[model_name](
            in_dim=data.x.size(1), hidden_dim=16, embedding_dim=8, dropout=0.0
        )
        model.eval()
        with torch.no_grad():
            logits = model(data.x, data.edge_index)
        assert logits.shape == (data.num_nodes,)
        assert logits.dtype == torch.float32

    @pytest.mark.parametrize("model_name", ["gcn", "graphsage", "mlp"])
    def test_forward_no_nan(self, model_name, sample_txns_accounts, tiny_config):
        from fraudgraph.data.graph_builder import build_graph
        from fraudgraph.models import MODEL_REGISTRY
        txns, accounts = sample_txns_accounts
        data, _, _ = build_graph(txns, accounts, tiny_config)
        model = MODEL_REGISTRY[model_name](
            in_dim=data.x.size(1), hidden_dim=16, embedding_dim=8, dropout=0.0
        )
        model.eval()
        with torch.no_grad():
            logits = model(data.x, data.edge_index)
        assert not torch.isnan(logits).any()


class TestSmokeTraining:
    @pytest.mark.parametrize("model_name", ["mlp"])
    def test_one_epoch(self, model_name, sample_txns_accounts, tiny_config):
        """Verify training loop runs without error for one epoch (fast)."""
        from fraudgraph.data.graph_builder import build_graph
        from fraudgraph.train.trainer import Trainer
        txns, accounts = sample_txns_accounts
        data, _, _ = build_graph(txns, accounts, tiny_config)
        result = Trainer(tiny_config).train(model_name, data)
        assert result.epochs_trained >= 1
        assert 0.0 <= result.test_report["pr_auc"] <= 1.0
        assert result.risk_scores.shape == (data.num_nodes,)

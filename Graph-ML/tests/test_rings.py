"""Tests for ring detection."""

import numpy as np


class TestRingDetector:
    def test_rings_discovered(self, sample_txns_accounts, tiny_config):
        from fraudgraph.rings.detector import detect_rings
        txns, accounts = sample_txns_accounts
        # Use uniform random scores so detection runs without trained model
        rng = np.random.default_rng(0)
        risk_scores = rng.uniform(0, 1, size=len(accounts))
        report = detect_rings(txns, accounts, risk_scores, tiny_config)
        assert "n_communities" in report
        assert report["n_communities"] > 0
        assert "rings" in report
        assert len(report["rings"]) > 0

    def test_ring_metrics_structure(self, sample_txns_accounts, tiny_config):
        from fraudgraph.rings.detector import detect_rings
        txns, accounts = sample_txns_accounts
        rng = np.random.default_rng(0)
        risk_scores = rng.uniform(0, 1, size=len(accounts))
        report = detect_rings(txns, accounts, risk_scores, tiny_config)
        rm = report["ring_level_metrics"]
        assert "precision" in rm
        assert "recall" in rm
        assert 0.0 <= rm["precision"] <= 1.0
        assert 0.0 <= rm["recall"] <= 1.0

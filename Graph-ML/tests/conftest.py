"""Shared test fixtures."""

import pytest
import pandas as pd
import numpy as np


@pytest.fixture
def tiny_config(tmp_path):
    """Config pointing at a temp directory with reduced data sizes."""
    from fraudgraph.config import Config
    return Config(
        seed=0,
        data_dir=tmp_path / "data",
        models_dir=tmp_path / "models",
        reports_dir=tmp_path / "reports",
        n_legit_accounts=200,
        n_rings=3,
        ring_size_range=(3, 5),
        n_standalone_fraudsters=2,
        n_normal_transactions=800,
        n_steps=100,
        max_epochs=5,
        patience=3,
    )


@pytest.fixture
def sample_txns_accounts(tiny_config):
    """Generate a small synthetic dataset and return (txns, accounts)."""
    from fraudgraph.data.synthetic import SyntheticTransactionGenerator
    gen = SyntheticTransactionGenerator(tiny_config)
    return gen.generate()

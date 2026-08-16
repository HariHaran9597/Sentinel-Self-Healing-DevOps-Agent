"""Unified dataset access: synthetic generator or real PaySim CSV."""

from __future__ import annotations

import pandas as pd

from fraudgraph.config import Config


def load_dataset(config: Config, source: str = "synthetic") -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (transactions, accounts) for the requested source.

    ``source="synthetic"`` reads what ``generate_data.py`` produced;
    ``source="paysim"`` reads ``data/paysim.csv`` (user-supplied Kaggle file).
    """
    if source == "paysim":
        from fraudgraph.data.paysim_loader import load_paysim

        return load_paysim(config.paysim_csv)
    if source == "synthetic":
        accounts_path = config.data_dir / "accounts.csv"
        if not config.transactions_csv.exists() or not accounts_path.exists():
            raise FileNotFoundError(
                "Synthetic data not found — run `python scripts/generate_data.py` first."
            )
        txns = pd.read_csv(config.transactions_csv)
        accounts = pd.read_csv(accounts_path)
        return txns, accounts
    raise ValueError(f"Unknown data source: {source!r} (expected 'synthetic' or 'paysim')")

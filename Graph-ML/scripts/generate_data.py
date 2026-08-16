"""Generate the synthetic PaySim-style transaction dataset.

Usage:
    python scripts/generate_data.py [--data paysim]
"""

from __future__ import annotations

import argparse

from fraudgraph.config import CONFIG
from fraudgraph.data.dataset import load_dataset
from fraudgraph.data.synthetic import generate_and_save


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate/load the transaction dataset")
    parser.add_argument("--data", choices=("synthetic", "paysim"), default="synthetic")
    args = parser.parse_args()

    if args.data == "paysim":
        txns, accounts = load_dataset(CONFIG, "paysim")
        print(
            f"PaySim: {len(txns):,} transactions, {len(accounts):,} accounts, "
            f"{accounts['is_fraud'].sum():,} fraud accounts"
        )
        return

    stats = generate_and_save(CONFIG)
    print(
        f"Synthetic dataset written to {CONFIG.data_dir}\n"
        f"  accounts:      {stats.n_accounts:,}\n"
        f"  transactions:  {stats.n_transactions:,}\n"
        f"  fraud rings:   {stats.n_rings}\n"
        f"  fraud accounts: {stats.n_fraud_accounts:,} "
        f"({stats.n_fraud_accounts / stats.n_accounts:.1%})\n"
        f"  fraud transactions: {stats.n_fraud_transactions:,}"
    )


if __name__ == "__main__":
    main()

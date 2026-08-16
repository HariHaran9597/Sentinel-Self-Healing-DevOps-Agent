"""Loader for the real PaySim dataset (Kaggle: ealgaba/payim-synthetic-fraud-detection).

PaySim labels *transactions*, not accounts, so we derive account labels
from the fraudster side of each fraudulent transaction:

* destination of a fraudulent TRANSFER  (receiving mule), and
* originator of a fraudulent CASH_OUT   (the mule cashing out).

This mirrors how a bank would attribute alerts to the accounts it controls.

Ring ground truth does not exist in PaySim; ``ring_id`` is left as -1 and
rings are discovered purely by Louvain community detection.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from fraudgraph.config import Config

COLUMNS = [
    "step",
    "type",
    "amount",
    "nameOrig",
    "oldbalanceOrg",
    "newbalanceOrg",
    "nameDest",
    "oldbalanceDest",
    "newbalanceDest",
    "isFraud",
    "isFlaggedFraud",
]


def load_paysim(csv_path: Path, max_rows: int | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (transactions, accounts) in the same format as the synthetic generator."""
    txns = pd.read_csv(csv_path, usecols=COLUMNS)
    if max_rows:
        txns = txns.head(max_rows)

    fraud = txns[txns["isFraud"] == 1]
    fraudster_accounts = set(fraud.loc[fraud["type"] == "TRANSFER", "nameDest"]) | set(
        fraud.loc[fraud["type"] == "CASH_OUT", "nameOrig"]
    )

    all_accounts = sorted(set(txns["nameOrig"]) | set(txns["nameDest"]))
    accounts = pd.DataFrame(
        {
            "account": all_accounts,
            "is_fraud": [int(acc in fraudster_accounts) for acc in all_accounts],
            "ring_id": -1,
        }
    )
    return txns, accounts

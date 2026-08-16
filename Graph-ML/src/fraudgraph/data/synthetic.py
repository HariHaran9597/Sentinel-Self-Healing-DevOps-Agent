"""Synthetic PaySim-style transaction generator with injected fraud rings.

Produces transactions in the exact PaySim schema
(step, type, amount, nameOrig, oldbalanceOrg, newbalanceOrg,
 nameDest, oldbalanceDest, newbalanceDest, isFraud, isFlaggedFraud)
so downstream code treats synthetic and real PaySim data identically.

Design notes
------------
* Fraud accounts are **compromised existing customers**: they carry an
  ordinary day-to-day transaction history, and the fraud burst hides
  inside it. (Mule accounts with alien behaviour would make the task
  trivially separable on a single feature.)
* Legit activity is bursty (favourite hours, heavy-user tails, 3% of
  payments legitimately drain accounts) so behavioural features overlap
  honestly between fraud and heavy legit users.
* Three typologies are injected, mirroring real-world AML patterns:
  **fan-out** (operator disperses proceeds to mules who cash out),
  **fan-in** (mules funnel proceeds into one cash-out mule),
  **cycle** (layering around a closed mule chain).
* Ring members are labeled fraud; drained victims are *not* — they are
  legitimate accounts that were robbed, honest hard negatives.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from fraudgraph.config import Config

TXN_TYPES = ("PAYMENT", "TRANSFER", "CASH_OUT", "DEBIT", "CASH_IN")
N_MERCHANTS = 50


@dataclass
class GenerationStats:
    n_accounts: int
    n_transactions: int
    n_fraud_accounts: int
    n_fraud_transactions: int
    n_rings: int


class SyntheticTransactionGenerator:
    """Generates a reproducible synthetic mobile-money transaction log."""

    def __init__(self, config: Config) -> None:
        self.cfg = config
        self.rng = np.random.default_rng(config.seed)
        self.balances: dict[str, float] = {}
        self.txn_rows: list[dict] = []
        self.account_labels: dict[str, tuple[bool, int]] = {}  # id -> (is_fraud, ring_id)
        self.customers: list[str] = []  # every C-account, fraud included
        self.sender_probs: np.ndarray | None = None
        self.active_steps: dict[str, list[int]] = {}
        self.ring_members: list[list[str]] = []

    # ------------------------------------------------------------------ #
    # public entry point
    # ------------------------------------------------------------------ #

    def generate(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Return (transactions, accounts) dataframes."""
        self._create_accounts()
        self._generate_normal_activity()  # fraud accounts live normal lives too
        self._generate_fraud_rings()
        self._generate_standalone_fraudsters()

        txns = pd.DataFrame(self.txn_rows)
        txns = txns.sort_values("step", kind="stable").reset_index(drop=True)

        accounts = pd.DataFrame(
            [
                {"account": acc, "is_fraud": int(is_fraud), "ring_id": ring_id}
                for acc, (is_fraud, ring_id) in sorted(self.account_labels.items())
            ]
        )
        return txns, accounts

    # ------------------------------------------------------------------ #
    # population
    # ------------------------------------------------------------------ #

    def _create_accounts(self) -> None:
        cfg = self.cfg
        # Customers start with a lognormal balance (median ~ $2k).
        for i in range(cfg.n_legit_accounts):
            acc = f"C{i}"
            self.balances[acc] = float(self.rng.lognormal(mean=7.6, sigma=1.0))
            self.account_labels[acc] = (False, -1)
        # Merchants are high-throughput sinks with PaySim-style zero balances.
        for i in range(N_MERCHANTS):
            self.account_labels[f"M{i}"] = (False, -1)

        # Fraud accounts: mule rings + a few standalone operators.
        for ring_id in range(cfg.n_rings):
            size = int(self.rng.integers(*cfg.ring_size_range))
            self.ring_members.append([self._new_fraud_account(ring_id) for _ in range(size)])
        for _ in range(cfg.n_standalone_fraudsters):
            self._new_fraud_account(-2)  # -2 = standalone, no ring

        self.customers = sorted(acc for acc in self.account_labels if acc.startswith("C"))

        # Heavy-user tail: a few customers transact many times more than
        # the median, so legit velocity overlaps with fraud bursts.
        weights = np.clip(self.rng.lognormal(mean=0.0, sigma=0.9, size=len(self.customers)), 0.05, 7.0)
        self.sender_probs = weights / weights.sum()
        for acc in self.customers:
            n_favourite_hours = int(self.rng.integers(2, 5))
            self.active_steps[acc] = list(
                self.rng.choice(np.arange(1, cfg.n_steps + 1), size=n_favourite_hours, replace=False)
            )

    def _new_fraud_account(self, ring_id: int) -> str:
        acc = f"C{len(self.account_labels)}"  # fresh id, never a prior customer
        self.account_labels[acc] = (True, ring_id)
        self.balances[acc] = float(self.rng.lognormal(mean=7.6, sigma=1.0))
        return acc

    def _pick_victim(self) -> str:
        """Pick a *legit* customer with enough balance to be worth draining."""
        for _ in range(50):
            acc = f"C{self.rng.integers(self.cfg.n_legit_accounts)}"
            if self.balances[acc] > 500:
                return acc
        return f"C{self.rng.integers(self.cfg.n_legit_accounts)}"

    def _record(
        self,
        step: int,
        txn_type: str,
        amount: float,
        orig: str,
        dest: str,
        is_fraud: bool,
    ) -> None:
        old_org = self.balances.get(orig, 0.0)
        new_org = max(0.0, old_org - amount)
        old_dest = self.balances.get(dest, 0.0)
        new_dest = old_dest + amount if not dest.startswith("M") else 0.0

        self.balances[orig] = new_org
        if not dest.startswith("M"):
            self.balances[dest] = new_dest

        self.txn_rows.append(
            {
                "step": int(step),
                "type": txn_type,
                "amount": round(float(amount), 2),
                "nameOrig": orig,
                "oldbalanceOrg": round(old_org, 2),
                "newbalanceOrg": round(new_org, 2),
                "nameDest": dest,
                "oldbalanceDest": round(old_dest, 2),
                "newbalanceDest": round(new_dest, 2),
                "isFraud": int(is_fraud),
                "isFlaggedFraud": int(amount > 200_000),
            }
        )

    # ------------------------------------------------------------------ #
    # legitimate activity
    # ------------------------------------------------------------------ #

    def _generate_normal_activity(self) -> None:
        """Day-to-day life for every customer, fraud accounts included."""
        cfg = self.cfg
        merchants = [f"M{i}" for i in range(N_MERCHANTS)]

        for _ in range(cfg.n_normal_transactions):
            sender = self.customers[self.rng.choice(len(self.customers), p=self.sender_probs)]
            step = int(self.rng.choice(self.active_steps[sender]))
            txn_type = self.rng.choice(TXN_TYPES, p=(0.35, 0.25, 0.20, 0.10, 0.10))

            balance = self.balances[sender]
            # 3% of outgoing payments legitimately drain the account.
            drain = txn_type in ("TRANSFER", "PAYMENT") and self.rng.random() < 0.03
            spendable = balance if drain else balance * self.rng.uniform(0.05, 0.9)

            if txn_type in ("PAYMENT", "DEBIT"):
                dest = merchants[self.rng.integers(len(merchants))]
                amount = min(spendable, self.rng.lognormal(mean=4.6, sigma=1.4))
            elif txn_type == "TRANSFER":
                dest = self.customers[self.rng.integers(len(self.customers))]
                amount = min(spendable, self.rng.lognormal(mean=4.6, sigma=1.4))
            elif txn_type == "CASH_OUT":
                dest = self.customers[self.rng.integers(len(self.customers))]
                amount = min(spendable, self.rng.lognormal(mean=4.1, sigma=1.0))
            else:  # CASH_IN — a deposit from outside the network
                dest = sender
                sender = self.customers[self.rng.integers(len(self.customers))]
                step = int(self.rng.choice(self.active_steps[sender]))
                amount = self.rng.lognormal(mean=4.4, sigma=1.0)

            if dest == sender:
                continue
            self._record(step, txn_type, amount, sender, dest, is_fraud=False)

    # ------------------------------------------------------------------ #
    # fraud injection
    # ------------------------------------------------------------------ #

    def _drain_victims(self, operator: str, n_victims: int, step: int) -> float:
        """Operator empties victim accounts; returns total stolen."""
        total = 0.0
        for _ in range(n_victims):
            victim = self._pick_victim()
            amount = self.balances[victim]
            if amount <= 0:
                continue
            self._record(step, "TRANSFER", amount, victim, operator, is_fraud=True)
            total += amount
        return total

    def _generate_fraud_rings(self) -> None:
        cfg = self.cfg
        for ring_id, members in enumerate(self.ring_members):
            typology = ("fan-out", "fan-in", "cycle")[ring_id % 3]
            operator, mules = members[0], members[1:]
            # The whole ring fires within a couple of steps — high velocity.
            step = int(self.rng.integers(2, cfg.n_steps))

            if typology == "fan-out":
                stolen = self._drain_victims(operator, n_victims=len(mules) * 2, step=step)
                share = stolen / max(len(mules), 1)
                for mule in mules:
                    self._record(step, "TRANSFER", share, operator, mule, is_fraud=True)
                    self._record(step + 1, "CASH_OUT", share * 0.95, mule, operator, is_fraud=True)

            elif typology == "fan-in":
                collected = 0.0
                for mule in mules:
                    stolen = self._drain_victims(mule, n_victims=2, step=step)
                    collected += stolen
                    self._record(step, "TRANSFER", stolen, mule, operator, is_fraud=True)
                self._record(step + 1, "CASH_OUT", collected * 0.95, operator, operator, is_fraud=True)

            else:  # cycle — layer funds around a closed mule chain
                stolen = self._drain_victims(operator, n_victims=len(members), step=step)
                hop = stolen / max(len(mules), 1)
                for _pass in range(2):
                    for src, dst in zip(members, members[1:] + members[:1]):
                        self._record(step, "TRANSFER", hop, src, dst, is_fraud=True)
                for mule in mules:
                    self._record(step + 1, "CASH_OUT", hop * 0.9, mule, operator, is_fraud=True)

    def _generate_standalone_fraudsters(self) -> None:
        cfg = self.cfg
        for acc, is_fraud in list(self.account_labels.items()):
            if not is_fraud or self.account_labels[acc][1] != -2:
                continue
            fraudster = acc
            mule = self._new_fraud_account(-2)
            self.customers.append(mule)  # stays out of normal activity; burst follows
            step = int(self.rng.integers(2, cfg.n_steps))
            stolen = self._drain_victims(fraudster, n_victims=3, step=step)
            self._record(step, "TRANSFER", stolen, fraudster, mule, is_fraud=True)
            self._record(step + 1, "CASH_OUT", stolen * 0.95, mule, fraudster, is_fraud=True)


def generate_and_save(config: Config) -> GenerationStats:
    """Run generation and persist transactions.csv / accounts.csv."""
    config.ensure_dirs()
    gen = SyntheticTransactionGenerator(config)
    txns, accounts = gen.generate()

    txns.to_csv(config.transactions_csv, index=False)
    accounts.to_csv(config.data_dir / "accounts.csv", index=False)

    stats = GenerationStats(
        n_accounts=len(accounts),
        n_transactions=len(txns),
        n_fraud_accounts=int(accounts["is_fraud"].sum()),
        n_fraud_transactions=int(txns["isFraud"].sum()),
        n_rings=int(accounts.loc[accounts["ring_id"] >= 0, "ring_id"].nunique()),
    )
    return stats

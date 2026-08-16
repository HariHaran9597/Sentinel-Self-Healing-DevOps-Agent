"""Central configuration: paths, seeds, and hyperparameters."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Config:
    # --- reproducibility ---
    seed: int = 42

    # --- paths ---
    data_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "data")
    models_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "models")
    reports_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "reports")

    @property
    def transactions_csv(self) -> Path:
        return self.data_dir / "transactions.csv"

    @property
    def paysim_csv(self) -> Path:
        return self.data_dir / "paysim.csv"

    @property
    def graph_pt(self) -> Path:
        return self.data_dir / "graph.pt"

    # --- synthetic data generation ---
    n_legit_accounts: int = 5_000
    n_rings: int = 12
    ring_size_range: tuple[int, int] = (5, 10)
    n_standalone_fraudsters: int = 15
    n_normal_transactions: int = 38_000
    n_steps: int = 744  # 31 days x 24h, PaySim-style time unit

    # --- features ---
    betweenness_sample_size: int = 250

    # --- training ---
    hidden_dim: int = 64
    embedding_dim: int = 32
    dropout: float = 0.5
    lr: float = 0.01
    weight_decay: float = 5e-4
    max_epochs: int = 300
    patience: int = 30  # early-stopping patience on validation PR-AUC
    train_frac: float = 0.70
    val_frac: float = 0.15  # remainder is test

    # --- ring detection ---
    louvain_resolution: float = 1.0
    top_rings: int = 5

    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.models_dir, self.reports_dir):
            path.mkdir(parents=True, exist_ok=True)


CONFIG = Config()

"""Full-batch trainer with class-imbalance handling and early stopping.

Fraud is ~2% of accounts, so we train with a pos-weighted BCE loss
(equivalent to oversampling the fraud class) and select checkpoints on
validation PR-AUC — *not* accuracy, which is meaningless at this skew.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import torch
from torch_geometric.data import Data

from fraudgraph.config import Config
from fraudgraph.models import MODEL_REGISTRY
from fraudgraph.train.metrics import classification_report

torch.manual_seed(42)
np.random.seed(42)


@dataclass
class TrainResult:
    model_name: str
    best_val_pr_auc: float
    epochs_trained: int
    test_report: dict
    risk_scores: np.ndarray  # fraud probability for every node
    model: torch.nn.Module = field(repr=False, default=None)


@dataclass
class Trainer:
    config: Config

    def train(self, model_name: str, data: Data) -> TrainResult:
        cfg = self.config
        torch.manual_seed(cfg.seed)

        model_cls = MODEL_REGISTRY[model_name]
        model = model_cls(
            in_dim=data.x.size(1),
            hidden_dim=cfg.hidden_dim,
            embedding_dim=cfg.embedding_dim,
            dropout=cfg.dropout,
        )

        y_train = data.y[data.train_mask]
        pos_count = float(y_train.sum())
        neg_count = float(len(y_train) - pos_count)
        pos_weight = torch.tensor(neg_count / max(pos_count, 1.0))
        criterion = torch.nn.BCEWithLogitsLoss(pos_weight=pos_weight)
        optimizer = torch.optim.Adam(model.parameters(), lr=cfg.lr, weight_decay=cfg.weight_decay)

        best_val_auc, best_state, best_epoch, patience_left = -1.0, None, 0, cfg.patience
        for epoch in range(1, cfg.max_epochs + 1):
            model.train()
            optimizer.zero_grad()
            logits = model(data.x, data.edge_index)
            loss = criterion(logits[data.train_mask], data.y[data.train_mask])
            loss.backward()
            optimizer.step()

            val_auc = self._pr_auc(model, data, data.val_mask)
            if val_auc > best_val_auc:
                best_val_auc, best_epoch, patience_left = val_auc, epoch, cfg.patience
                best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
            elif (patience_left := patience_left - 1) <= 0:
                break

        assert best_state is not None
        model.load_state_dict(best_state)
        model.eval()
        with torch.no_grad():
            logits = model(data.x, data.edge_index)
        probs_all = torch.sigmoid(logits).numpy()
        test_probs = probs_all[data.test_mask.numpy().astype(bool)]
        test_labels = data.y[data.test_mask].numpy()

        return TrainResult(
            model_name=model_name,
            best_val_pr_auc=float(best_val_auc),
            epochs_trained=best_epoch,
            test_report=classification_report(test_labels, test_probs),
            risk_scores=probs_all,
            model=model,
        )

    @staticmethod
    def _pr_auc(model: torch.nn.Module, data: Data, mask: torch.Tensor) -> float:
        from sklearn.metrics import average_precision_score

        model.eval()
        with torch.no_grad():
            probs = torch.sigmoid(model(data.x, data.edge_index))[mask].numpy()
        labels = data.y[mask].numpy()
        if labels.sum() == 0:
            return 0.0
        return float(average_precision_score(labels, probs))

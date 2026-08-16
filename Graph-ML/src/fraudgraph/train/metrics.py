"""Evaluation metrics for the imbalanced fraud-classification task."""

from __future__ import annotations

import numpy as np
from sklearn.metrics import (
    average_precision_score,
    confusion_matrix,
    precision_recall_curve,
    precision_score,
    recall_score,
    roc_auc_score,
)


def classification_report(y_true: np.ndarray, probs: np.ndarray) -> dict:
    """Precision/recall/F1 at 0.5, best-F1 threshold, PR-AUC, ROC-AUC."""
    preds = (probs >= 0.5).astype(int)

    precision, recall, thresholds = precision_recall_curve(y_true, probs)
    f1 = 2 * precision * recall / np.maximum(precision + recall, 1e-9)
    best_idx = int(np.nanargmax(f1[:-1]))  # last point has no threshold
    tn, fp, fn, tp = confusion_matrix(y_true, preds, labels=[0, 1]).ravel()

    return {
        "precision@0.5": float(precision_score(y_true, preds, zero_division=0)),
        "recall@0.5": float(recall_score(y_true, preds, zero_division=0)),
        "best_f1": float(f1[best_idx]),
        "best_f1_threshold": float(thresholds[best_idx]),
        "precision@best_f1": float(precision[best_idx]),
        "recall@best_f1": float(recall[best_idx]),
        "pr_auc": float(average_precision_score(y_true, probs)),
        "roc_auc": float(roc_auc_score(y_true, probs)) if len(np.unique(y_true)) > 1 else float("nan"),
        "confusion_matrix": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)},
        "support": {"fraud": int(y_true.sum()), "legit": int(len(y_true) - y_true.sum())},
    }

"""Matplotlib report figures: PR-curve comparison and confusion matrices."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from sklearn.metrics import confusion_matrix, precision_recall_curve

CMAP = {"gcn": "#2563eb", "graphsage": "#16a34a", "mlp": "#9ca3af"}


def plot_pr_curves(
    scores_by_model: dict[str, tuple[np.ndarray, np.ndarray]],  # model -> (y_true, probs)
    out_path: Path,
) -> Path:
    fig, ax = plt.subplots(figsize=(7, 5))
    for name, (y_true, probs) in scores_by_model.items():
        precision, recall, _ = precision_recall_curve(y_true, probs)
        ap = float(np.trapezoid(precision, recall))
        ax.plot(recall, precision, color=CMAP.get(name, "#333"), lw=2,
                label=f"{name} (AP = {ap:.3f})")
    fraud_rate = scores_by_model[next(iter(scores_by_model))][0].mean()
    ax.axhline(fraud_rate, ls="--", color="#dc2626", lw=1, label=f"random baseline ({fraud_rate:.3f})")
    ax.set(xlabel="Recall", ylabel="Precision", title="Precision–Recall: fraud detection on held-out accounts")
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return out_path


def plot_confusion_matrices(
    scores_by_model: dict[str, tuple[np.ndarray, np.ndarray]],
    threshold: float,
    out_path: Path,
) -> Path:
    fig, axes = plt.subplots(1, len(scores_by_model), figsize=(5 * len(scores_by_model), 4))
    axes = np.atleast_1d(axes)
    for ax, (name, (y_true, probs)) in zip(axes, scores_by_model.items()):
        preds = (probs >= threshold).astype(int)
        cm = confusion_matrix(y_true, preds, labels=[0, 1])
        ax.imshow(cm, cmap="Blues")
        for (i, j), value in np.ndenumerate(cm):
            ax.text(j, i, f"{value}", ha="center", va="center",
                    color="white" if value > cm.max() / 2 else "black", fontsize=13)
        ax.set(xticks=[0, 1], yticks=[0, 1],
               xticklabels=["legit", "fraud"], yticklabels=["legit", "fraud"],
               xlabel="predicted", ylabel="actual", title=name)
    fig.suptitle(f"Confusion matrices @ threshold {threshold:.2f}")
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return out_path

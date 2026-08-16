"""Train GCN, GraphSAGE, and MLP on the account graph and write reports.

Usage:
    python scripts/train.py [--data synthetic] [--epochs 300]
"""

from __future__ import annotations

import argparse
import json
from dataclasses import replace

import numpy as np
import torch

from fraudgraph.config import CONFIG
from fraudgraph.data.dataset import load_dataset
from fraudgraph.data.graph_builder import build_graph, save_graph
from fraudgraph.train.trainer import Trainer
from fraudgraph.viz.figures import plot_confusion_matrices, plot_pr_curves


def main() -> None:
    parser = argparse.ArgumentParser(description="Train fraud-detection GNNs")
    parser.add_argument("--data", choices=("synthetic", "paysim"), default="synthetic")
    parser.add_argument("--epochs", type=int, default=None, help="override max epochs (e.g. smoke tests)")
    args = parser.parse_args()

    config = CONFIG if args.epochs is None else replace(CONFIG, max_epochs=args.epochs)
    config.ensure_dirs()

    txns, accounts = load_dataset(config, args.data)
    print(f"Loaded {len(txns):,} transactions / {len(accounts):,} accounts "
          f"({accounts['is_fraud'].mean():.2%} fraud)")

    print("Building graph + features (PageRank, betweenness, velocity, drains) ...")
    data, account_ids, _ = build_graph(txns, accounts, config)
    save_graph(data, account_ids, config)
    print(f"Graph: {data.num_nodes} nodes, {data.num_edges:,} directed edges, "
          f"{data.x.size(1)} node features")

    test_slice = data.test_mask.numpy().astype(bool)
    y_test = data.y.numpy()[test_slice]

    results, probs_by_model = {}, {}
    for model_name in ("gcn", "graphsage", "mlp"):
        print(f"Training {model_name} ...")
        result = Trainer(config).train(model_name, data)
        torch.save(result.model.state_dict(), config.models_dir / f"{model_name}.pt")
        results[model_name] = result
        probs_by_model[model_name] = (y_test, result.risk_scores[test_slice])
        report = result.test_report
        print(
            f"  {model_name}: test PR-AUC={report['pr_auc']:.3f}  "
            f"F1={report['best_f1']:.3f}  "
            f"P={report['precision@best_f1']:.3f}  R={report['recall@best_f1']:.3f}  "
            f"({result.epochs_trained} epochs)"
        )

    best_name = max(results, key=lambda n: results[n].test_report["pr_auc"])
    print(f"Best model: {best_name}")

    # --- artifacts for downstream stages (rings, viz, API) ---
    np.savez(
        config.models_dir / "predictions.npz",
        account_ids=np.array(account_ids),
        y_true=data.y.numpy(),
        **{f"probs_{name}": results[name].risk_scores for name in results},
    )
    (config.models_dir / "best_model.json").write_text(
        json.dumps({"best_model": best_name, "in_dim": data.x.size(1)}), encoding="utf-8"
    )

    metrics = {
        "dataset": {
            "source": args.data,
            "n_accounts": len(accounts),
            "n_transactions": len(txns),
            "fraud_rate": float(accounts["is_fraud"].mean()),
        },
        "best_model": best_name,
        "models": {
            name: {
                "best_val_pr_auc": round(r.best_val_pr_auc, 4),
                "epochs_trained": r.epochs_trained,
                **{k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.test_report.items()},
            }
            for name, r in results.items()
        },
    }
    (config.reports_dir / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")

    figures = config.reports_dir / "figures"
    plot_pr_curves(probs_by_model, figures / "pr_curves.png")
    best_threshold = results[best_name].test_report["best_f1_threshold"]
    plot_confusion_matrices(probs_by_model, best_threshold, figures / "confusion_matrices.png")
    print(f"Reports written to {config.reports_dir}")


if __name__ == "__main__":
    main()

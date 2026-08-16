"""Run Louvain community detection and rank fraud rings.

Usage:
    python scripts/detect_rings.py [--data synthetic]
"""

from __future__ import annotations

import argparse
import json

import numpy as np

from fraudgraph.config import CONFIG
from fraudgraph.data.dataset import load_dataset
from fraudgraph.rings.detector import detect_rings


def main() -> None:
    parser = argparse.ArgumentParser(description="Detect fraud rings with Louvain")
    parser.add_argument("--data", choices=("synthetic", "paysim"), default="synthetic")
    args = parser.parse_args()
    config = CONFIG
    config.ensure_dirs()

    txns, accounts = load_dataset(config, args.data)
    predictions = np.load(config.models_dir / "predictions.npz", allow_pickle=True)
    best_name = json.loads((config.models_dir / "best_model.json").read_text(encoding="utf-8"))["best_model"]
    risk_scores = predictions[f"probs_{best_name}"]

    report = detect_rings(txns, accounts, risk_scores, config)
    (config.reports_dir / "rings.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    ring_metrics = report["ring_level_metrics"]
    print(
        f"{report['n_communities']} communities found "
        f"({len([r for r in report['rings'] if r.get('rank')])} flagged as rings)\n"
        f"Ring-level detection: precision={ring_metrics['precision']:.1%}  "
        f"recall={ring_metrics['recall']:.1%}  "
        f"({ring_metrics['true_fraud_in_flagged']}/{ring_metrics['total_fraud']} fraud accounts "
        f"caught in {ring_metrics['accounts_flagged']} flagged accounts)\n"
    )
    for ring in report["rings"]:
        if "rank" not in ring:
            continue
        print(
            f"  Ring #{ring['rank']}: {ring['size']} accounts  "
            f"mean_risk={ring['mean_risk']:.3f}  true_fraud={ring['true_fraud_count']}  "
            f"(injected ring {ring['matched_injected_ring']})"
        )
    print(f"Report written to {config.reports_dir / 'rings.json'}")


if __name__ == "__main__":
    main()

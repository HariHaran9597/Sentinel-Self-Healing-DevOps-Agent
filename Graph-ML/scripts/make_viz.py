"""Render the interactive PyVis visualization of the top detected ring.

Usage:
    python scripts/make_viz.py [--data synthetic]
"""

from __future__ import annotations

import argparse
import json

import numpy as np

from fraudgraph.config import CONFIG
from fraudgraph.data.dataset import load_dataset
from fraudgraph.viz.pyvis_graph import render_ring


def main() -> None:
    parser = argparse.ArgumentParser(description="Visualize the top fraud ring")
    parser.add_argument("--data", choices=("synthetic", "paysim"), default="synthetic")
    args = parser.parse_args()
    config = CONFIG

    txns, accounts = load_dataset(config, args.data)
    rings = json.loads((config.reports_dir / "rings.json").read_text(encoding="utf-8"))
    top_ring = next(r for r in rings["rings"] if "rank" in r)

    predictions = np.load(config.models_dir / "predictions.npz", allow_pickle=True)
    best_name = json.loads((config.models_dir / "best_model.json").read_text(encoding="utf-8"))["best_model"]
    risk_scores = predictions[f"probs_{best_name}"]

    out_path = config.reports_dir / "fraud_ring.html"
    render_ring(txns, accounts, risk_scores, top_ring["members"], out_path)
    print(f"Interactive ring visualization written to {out_path} "
          f"(ring of {top_ring['size']} accounts, mean risk {top_ring['mean_risk']:.3f})")


if __name__ == "__main__":
    main()

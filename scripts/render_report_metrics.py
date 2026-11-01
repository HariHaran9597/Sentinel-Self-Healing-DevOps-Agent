#!/usr/bin/env python3
"""Bake the latest recorded benchmark summary into the static Pages HTML."""
from __future__ import annotations

import argparse
import html
import json
import re
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def percent(value: float | None) -> str:
    if value is None:
        return "n/a"
    rounded = Decimal(str(value * 100)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return f"{rounded}%"


def interval(value: float | None, ci: list[float] | None) -> str:
    bounds = "–".join(percent(item) for item in ci or [])
    return f"{percent(value)} · 95% Wilson CI {bounds}"


def render(report_path: Path, page_path: Path) -> None:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    summaries = {item["strategy"]: item for item in report["summaries"]}
    date = datetime.fromisoformat(report["run_date_utc"].replace("Z", "+00:00"))
    date_label = (
        f'{report["trial_count"]} matched trials · '
        f'{report["method"]["sustained_health_seconds"]}s independent soak · '
        f'{report["method"]["observation_window_seconds"]}s max observation window · '
        f'run {date.strftime("%Y-%m-%d %H:%M")} UTC'
    )
    cards: list[tuple[str, str]] = []
    for strategy in ("restart_only", "static_dictionary", "semantic_retrieval", "sentinel_runbook"):
        item = summaries[strategy]
        name = strategy.replace("_", " ")
        cards.append((
            f'{item["correct_sustained_recoveries"]}/{item["expected_recovery_trials"]} ({percent(item["recovery_rate"])})',
            f'{name} recovery · {interval(item["recovery_rate"], item["recovery_rate_wilson_95_ci"])}',
        ))
    for strategy in ("restart_only", "static_dictionary", "semantic_retrieval", "sentinel_runbook"):
        item = summaries[strategy]
        name = strategy.replace("_", " ")
        cards.append((
            f'{item["correct_safe_handling"]}/{item["expected_safe_handling_trials"]} ({percent(item["safe_handling_rate"])})',
            f'{name} safe handling · {interval(item["safe_handling_rate"], item["safe_handling_rate_wilson_95_ci"])}',
        ))
    sentinel = summaries["sentinel_runbook"]
    median = sentinel["recovery_injection_to_first_healthy_ms_median"]
    p95 = sentinel["recovery_injection_to_first_healthy_ms_p95_nearest_rank"]
    maximum = sentinel["recovery_injection_to_first_healthy_ms_max"]
    cards += [
        ("n/a" if median is None else f"{median / 1000:.2f} s", "Sentinel recovery median · injection to first healthy probe"),
        (f'{"n/a" if p95 is None else f"{p95 / 1000:.2f} s"} / {"n/a" if maximum is None else f"{maximum / 1000:.2f} s"}',
         f'Sentinel p95 / max · n={sentinel["latency_sample_count"]}'),
    ]
    markup = "\n".join(
        f'      <div class="metric"><b>{html.escape(value)}</b><span>{html.escape(label)}</span></div>'
        for value, label in cards
    )
    page = page_path.read_text(encoding="utf-8")
    page, date_count = re.subn(
        r'(<p class="hint" id="evaluation-date">).*?(</p>)',
        lambda match: match.group(1) + html.escape(date_label) + match.group(2),
        page, count=1,
    )
    grid_open = '<div class="metric-grid" id="evaluation-metrics">'
    grid_content_start = page.find(grid_open)
    grid_close = '\n    </div>\n    <p class="evidence-note"'
    grid_content_end = page.find(grid_close, grid_content_start + len(grid_open))
    if date_count != 1 or grid_content_start < 0 or grid_content_end < 0:
        raise RuntimeError("Could not locate the static evaluation fallback in docs/index.html")
    grid_content_start += len(grid_open)
    page = page[:grid_content_start] + "\n" + markup + page[grid_content_end:]
    page_path.write_text(page, encoding="utf-8", newline="\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=ROOT / "docs/results/sentinel-comparison.json")
    parser.add_argument("--page", type=Path, default=ROOT / "docs/index.html")
    args = parser.parse_args()
    render(args.report, args.page)
    print(f"Rendered static metrics from {args.report}")


if __name__ == "__main__":
    main()

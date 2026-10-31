#!/usr/bin/env python3
"""Run a bounded end-to-end chaos evaluation against a local Sentinel stack."""
from __future__ import annotations

import argparse
import json
import math
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

MODES = {
    "db_pool": ("ERR_DB_CONN_POOL_EXHAUSTED", "resolved"),
    "oom": ("ERR_OOM", "resolved"),
    "disk": ("ERR_DISK_FULL", "resolved"),
    "ssl": ("ERR_TLS_CERT_EXPIRED", "escalated"),
}
TERMINAL = {"resolved", "resolved_manual", "rejected", "failed", "escalated"}


def call(base: str, path: str, payload: dict | None = None, timeout: float = 5) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    request = Request(base.rstrip("/") + path, data=data,
                      headers={"Content-Type": "application/json"} if data else {})
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"{path} returned HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"Cannot reach Sentinel at {base}: {exc.reason}") from exc


def wait_until(predicate, timeout: float, label: str):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(0.25)
    raise TimeoutError(f"timed out waiting for {label}; last value: {last}")


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[max(0, math.ceil(pct * len(ordered)) - 1)], 2)


def run_trial(base: str, mode: str, run_number: int, timeout: float) -> dict:
    error_code, expected_status = MODES[mode]
    if not call(base, "/api/status").get("server_up"):
        call(base, "/api/server/start", {})
        wait_until(lambda: call(base, "/api/status").get("server_up"), timeout, "demo server startup")

    started = time.time()
    call(base, "/api/chaos", {"mode": mode})

    def incident_after_start():
        incidents = call(base, "/api/incidents").get("incidents", [])
        return next((item for item in incidents
                     if item.get("error_code") == error_code and item.get("detected_at", 0) >= started), None)

    incident = wait_until(incident_after_start, timeout, f"{mode} incident detection")
    incident_id = incident["id"]

    def current():
        return call(base, f"/api/incidents/{incident_id}")

    def proposed_or_escalated():
        item = current()
        return item if item.get("status") in {"waiting_approval", "escalated"} else None

    incident = wait_until(proposed_or_escalated, timeout, f"{mode} diagnosis and proposal")
    proposed_ms = round((incident.get("proposed_at") - incident["detected_at"]) * 1000, 2) if incident.get("proposed_at") else None
    if incident.get("status") == "waiting_approval":
        call(base, f"/api/incidents/{incident_id}/approve", {"approver": "bounded-evaluation"})
        def terminal():
            item = current()
            return item if item.get("status") in TERMINAL else None

        incident = wait_until(terminal, timeout, f"{mode} remediation and verification")

    actual_status = incident["status"]
    return {
        "mode": mode,
        "run": run_number,
        "incident_id": incident_id,
        "error_code": error_code,
        "expected_status": expected_status,
        "actual_status": actual_status,
        "expected_outcome": actual_status == expected_status,
        "detected_to_proposed_ms": proposed_ms,
        "detected_to_terminal_ms": round((incident["resolved_at"] - incident["detected_at"]) * 1000, 2)
            if incident.get("resolved_at") else None,
        "resolution": incident.get("result") or incident.get("resolution"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:18000")
    parser.add_argument("--runs-per-mode", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("--output", type=Path, default=Path("docs/results/sentinel-chaos-evaluation.json"))
    args = parser.parse_args()
    if args.runs_per_mode < 1 or args.runs_per_mode > 20:
        parser.error("--runs-per-mode must be between 1 and 20")

    if not call(args.base, "/api/health").get("ready"):
        raise RuntimeError("Sentinel API is not ready")

    trials = []
    for mode in MODES:
        for run_number in range(1, args.runs_per_mode + 1):
            print(f"{mode}: run {run_number}/{args.runs_per_mode}", flush=True)
            trials.append(run_trial(args.base, mode, run_number, args.timeout))

    latencies = [t["detected_to_terminal_ms"] for t in trials if t["detected_to_terminal_ms"] is not None]
    expected_count = sum(t["expected_outcome"] for t in trials)
    report = {
        "title": "Sentinel bounded chaos recovery evaluation",
        "run_date_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "target": "Local Docker Compose demo-server only",
        "method": "Sequential fault injection; every proposed action approved by the evaluation driver; recovery verified by Sentinel health check. TLS certificate expiry is expected to escalate for manual renewal.",
        "runs_per_mode": args.runs_per_mode,
        "trial_count": len(trials),
        "expected_outcomes": expected_count,
        "expected_outcome_rate": round(expected_count / len(trials), 4),
        "automatically_recovered": sum(t["actual_status"] == "resolved" for t in trials),
        "safely_escalated": sum(t["actual_status"] == "escalated" for t in trials),
        "unsafe_false_recoveries": sum(t["actual_status"] in {"resolved", "resolved_manual"} and t["expected_status"] == "escalated" for t in trials),
        "detected_to_terminal_ms_median": round(statistics.median(latencies), 2) if latencies else None,
        "detected_to_terminal_ms_p95_nearest_rank": percentile(latencies, 0.95),
        "trials": trials,
        "limitations": [
            "Controlled demo faults, not production incidents or an SRE benchmark.",
            "Repeated runs share one laptop and one service image; trials are not independent samples.",
            "Outcome rate measures expected state transitions, not business availability or customer impact.",
            "Docker restart can restore process availability but cannot fix an underlying recurring root cause.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in (
        "run_date_utc", "trial_count", "expected_outcomes", "expected_outcome_rate",
        "automatically_recovered", "safely_escalated", "unsafe_false_recoveries",
        "detected_to_terminal_ms_median", "detected_to_terminal_ms_p95_nearest_rank",
    )} | {"output": str(args.output)}, indent=2))
    return 0 if expected_count == len(trials) else 1


if __name__ == "__main__":
    raise SystemExit(main())

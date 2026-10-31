#!/usr/bin/env python3
"""Matched-seed comparison: restart-only vs Sentinel's approved runbook workflow."""
from __future__ import annotations

import argparse
import json
import math
import statistics
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

SCENARIOS = {
    "bad_config": ("ERR_CONFIG_INVALID", True),
    "dependency_down": ("ERR_DEPENDENCY_UNAVAILABLE", True),
    "bad_release": ("ERR_BAD_RELEASE", True),
    "unknown": ("ERR_UNKNOWN_FAULT", False),
}
TERMINAL = {"resolved", "resolved_manual", "rejected", "failed", "escalated"}


def request_json(base: str, path: str, payload: dict | None = None, timeout: float = 10) -> tuple[int, dict]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(base.rstrip("/") + path, data=data,
                      headers={"Content-Type": "application/json"} if data is not None else {})
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read())
    except HTTPError as exc:
        try:
            body = json.loads(exc.read())
        except Exception:
            body = {}
        return exc.code, body
    except URLError as exc:
        raise RuntimeError(f"Cannot reach {base}{path}: {exc.reason}") from exc


def call(base: str, path: str, payload: dict | None = None, timeout: float = 10) -> dict:
    code, body = request_json(base, path, payload, timeout)
    if code < 200 or code >= 300:
        raise RuntimeError(f"{path} returned HTTP {code}: {body}")
    return body


def wait_until(predicate, timeout: float, label: str):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.25)
    raise TimeoutError(f"timed out waiting for {label}")


def independent_probe(probe_url: str) -> tuple[bool, str]:
    try:
        code, body = request_json(probe_url, "/payments/status", timeout=2)
        ok = code == 200 and body.get("operational") is True
        return ok, f"HTTP {code}"
    except Exception as exc:
        # Restart windows and container restarts legitimately close a socket;
        # the independent probe treats that sample as unhealthy, not fatal.
        return False, f"{type(exc).__name__}: {exc}"


def measure_sustained_health(probe_url: str, injected_monotonic: float,
                             soak_seconds: int, timeout_seconds: int,
                             poll_seconds: float) -> dict:
    """Probe the user-facing status endpoint from this external driver process."""
    deadline = time.monotonic() + timeout_seconds
    first_healthy = None
    healthy_since = None
    samples = 0
    last_result = "no probe"
    while time.monotonic() < deadline:
        now = time.monotonic()
        healthy, last_result = independent_probe(probe_url)
        samples += 1
        if healthy:
            if first_healthy is None:
                first_healthy = now
            if healthy_since is None:
                healthy_since = now
            if now - healthy_since >= soak_seconds:
                return {
                    "first_healthy_ms": round((first_healthy - injected_monotonic) * 1000, 2),
                    "sustained_healthy": True,
                    "sustained_seconds": round(now - healthy_since, 2),
                    "probe_samples": samples,
                    "last_probe": last_result,
                }
        else:
            healthy_since = None
        time.sleep(poll_seconds)
    return {
        "first_healthy_ms": round((first_healthy - injected_monotonic) * 1000, 2) if first_healthy else None,
        "sustained_healthy": False,
        "sustained_seconds": round(time.monotonic() - healthy_since, 2) if healthy_since else 0,
        "probe_samples": samples,
        "last_probe": last_result,
    }


def wait_incident(base: str, code: str, previous_ids: set[str], timeout: float) -> dict:
    def find():
        incidents = call(base, "/api/incidents").get("incidents", [])
        return next((item for item in incidents
                     if item.get("id") not in previous_ids and item.get("error_code") == code), None)
    return wait_until(find, timeout, f"incident {code}")


def run_agent_arm(base: str, probe_url: str, mode: str, seed: int,
                  soak_seconds: int, timeout: float, probe_timeout: float, poll: float) -> dict:
    code, needs_recovery = SCENARIOS[mode]
    call(base, "/api/evaluation/reset", {}, timeout=40)
    previous_ids = {item["id"] for item in call(base, "/api/incidents").get("incidents", [])}
    injected = time.monotonic()
    injection = call(base, "/api/chaos", {"mode": mode, "seed": seed})
    incident = wait_incident(base, code, previous_ids, timeout)
    incident_id = incident["id"]

    def proposed_or_escalated():
        item = call(base, f"/api/incidents/{incident_id}")
        return item if item.get("status") in {"waiting_approval", "escalated"} else None

    record = wait_until(proposed_or_escalated, timeout, f"{mode} diagnosis")
    action_count = 0
    if record.get("status") == "waiting_approval":
        action_count = 1
        # Approve waits for graph execution + post-action verification; a Docker
        # restart may take longer than the default HTTP request timeout.
        call(base, f"/api/incidents/{incident_id}/approve",
             {"approver": "comparison-driver-auto-approval"}, timeout=timeout)
        def is_terminal():
            item = call(base, f"/api/incidents/{incident_id}")
            return item if item.get("status") in TERMINAL else None

        record = wait_until(is_terminal, timeout, f"{mode} action and verification")
    probe = measure_sustained_health(probe_url, injected, soak_seconds, probe_timeout, poll)
    return {
        "strategy": "sentinel_runbook",
        "mode": mode,
        "seed": seed,
        "error_code": code,
        "incident_id": incident_id,
        "injection_seed": injection.get("seed", seed),
        "approval": "evaluation-driver-auto-approved" if action_count else "not-applicable-escalation",
        "incident_status": record.get("status"),
        "runbook_id": (record.get("runbook") or {}).get("id"),
        "action": (record.get("fix") or {}).get("action"),
        "action_count": action_count,
        "unnecessary_action": action_count > 0 and not needs_recovery,
        **probe,
    }


def run_restart_only_arm(base: str, probe_url: str, mode: str, seed: int,
                         soak_seconds: int, baseline_timeout: float, poll: float) -> dict:
    code, needs_recovery = SCENARIOS[mode]
    call(base, "/api/evaluation/reset", {}, timeout=40)
    injected = time.monotonic()
    injection = call(base, "/api/chaos", {"mode": mode, "seed": seed})
    subprocess.run(["docker", "restart", "demo-server"], check=True,
                   capture_output=True, text=True, timeout=30)
    probe = measure_sustained_health(probe_url, injected, soak_seconds, baseline_timeout, poll)
    return {
        "strategy": "restart_only",
        "mode": mode,
        "seed": seed,
        "error_code": code,
        "incident_id": None,
        "injection_seed": injection.get("seed", seed),
        "approval": "not-applicable-baseline",
        "incident_status": None,
        "runbook_id": None,
        "action": "restart_container",
        "action_count": 1,
        "unnecessary_action": not needs_recovery,
        **probe,
    }


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[max(0, math.ceil(pct * len(ordered)) - 1)], 2)


def wilson_interval(successes: int, total: int, z: float = 1.959963984540054) -> list[float] | None:
    """95% Wilson score interval, appropriate for small binomial samples."""
    if total == 0:
        return None
    proportion = successes / total
    denominator = 1 + z * z / total
    center = (proportion + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(proportion * (1 - proportion) / total + z * z / (4 * total * total)) / denominator
    return [round(max(0, center - margin), 4), round(min(1, center + margin), 4)]


def summarize(trials: list[dict], strategy: str, replicates: int) -> dict:
    own = [trial for trial in trials if trial["strategy"] == strategy]
    actionable = [trial for trial in own if SCENARIOS[trial["mode"]][1]]
    unknown = [trial for trial in own if trial["mode"] == "unknown"]
    times = [trial["first_healthy_ms"] for trial in actionable
             if trial["sustained_healthy"] and trial["first_healthy_ms"] is not None]
    denominator = len(actionable)
    escalations = sum(t["incident_status"] == "escalated" for t in unknown)
    recoveries = sum(t["sustained_healthy"] for t in actionable)
    unnecessary = sum(t["unnecessary_action"] for t in unknown)
    return {
        "strategy": strategy,
        "replicates_per_scenario": replicates,
        "actionable_trials": denominator,
        "sustained_recoveries": recoveries,
        "sustained_recovery_rate": round(recoveries / denominator, 4) if denominator else None,
        "sustained_recovery_rate_wilson_95_ci": wilson_interval(recoveries, denominator),
        "unnecessary_actions_on_unknown": unnecessary,
        "unnecessary_action_rate_on_unknown": round(unnecessary / len(unknown), 4) if unknown else None,
        "unnecessary_action_rate_on_unknown_wilson_95_ci": wilson_interval(unnecessary, len(unknown)),
        "correct_escalations_on_unknown": escalations,
        "correct_escalation_rate_on_unknown": round(escalations / len(unknown), 4) if unknown else None,
        "correct_escalation_rate_on_unknown_wilson_95_ci": wilson_interval(escalations, len(unknown)),
        "successful_recovery_injection_to_independent_probe_ms_median": round(statistics.median(times), 2) if times else None,
        "successful_recovery_injection_to_independent_probe_ms_p95_nearest_rank": percentile(times, .95),
        "successful_recovery_injection_to_independent_probe_ms_max": round(max(times), 2) if times else None,
        "latency_sample_count": len(times),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:18000")
    parser.add_argument("--probe-url", default="http://127.0.0.1:18080")
    parser.add_argument("--replicates", type=int, default=3)
    parser.add_argument("--soak-seconds", type=int, default=30)
    parser.add_argument("--probe-timeout", type=float, default=60,
                        help="max time for Sentinel recovery plus continuous healthy soak")
    parser.add_argument("--baseline-timeout", type=float, default=20,
                        help="how long restart-only is observed for a persistent failure")
    parser.add_argument("--approval-timeout", type=float, default=90)
    parser.add_argument("--poll-seconds", type=float, default=1)
    parser.add_argument("--output", type=Path, default=Path("docs/results/sentinel-comparison.json"))
    args = parser.parse_args()
    if not 1 <= args.replicates <= 10:
        parser.error("--replicates must be between 1 and 10")
    if args.soak_seconds < 30:
        parser.error("--soak-seconds must be at least 30")
    if not call(args.base, "/api/health").get("ready"):
        raise RuntimeError("Sentinel API is not ready")

    trials = []
    for mode in SCENARIOS:
        for seed in range(1, args.replicates + 1):
            for strategy, runner in (
                ("restart_only", run_restart_only_arm),
                ("sentinel_runbook", run_agent_arm),
            ):
                print(f"{mode} seed={seed}/{args.replicates} strategy={strategy}", flush=True)
                if strategy == "restart_only":
                    result = runner(args.base, args.probe_url, mode, seed, args.soak_seconds,
                                    args.baseline_timeout, args.poll_seconds)
                else:
                    result = runner(args.base, args.probe_url, mode, seed, args.soak_seconds,
                                    args.approval_timeout, args.probe_timeout, args.poll_seconds)
                trials.append(result)

    report = {
        "title": "Matched-seed restart-only vs Sentinel runbook comparison",
        "run_date_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "target": "Only labeled containers and the dedicated Compose scenario-state volume",
        "method": {
            "matched_seeds": True,
            "log_noise": "Deterministic seeded informational log noise is identical for both strategies.",
            "restart_only": "The evaluator injects the fault and runs only `docker restart demo-server`; Sentinel's monitor remains active, but the driver does not approve any proposed remediation during the baseline arm.",
            "sentinel_runbook": "Sentinel retrieves runbooks; the evaluation driver auto-approves any proposed action. This does not measure the human approval UX.",
            "independent_probe": f"External evaluator requests {args.probe_url}/payments/status, a separate business-status route from outside the agent container.",
            "sustained_health_seconds": args.soak_seconds,
            "restart_only_failure_observation_timeout_seconds": args.baseline_timeout,
            "sentinel_recovery_probe_timeout_seconds": args.probe_timeout,
            "latency": "Fault-injection request start to first independent HTTP 200; successful recovery is counted only after the probe remains continuously healthy for the soak window.",
        },
        "faults": {mode: {"error_code": code, "service_recovery_expected": recovery}
                   for mode, (code, recovery) in SCENARIOS.items()},
        "summaries": [summarize(trials, strategy, args.replicates)
                      for strategy in ("restart_only", "sentinel_runbook")],
        "trial_count": len(trials),
        "trials": trials,
        "limitations": [
            "These are reproducible local fault fixtures, not production incidents.",
            "Small repeated runs characterize implementation behavior and do not estimate field reliability.",
            "The unknown case is deliberately non-impacting and tests unnecessary-restart behavior.",
            "Runbook strategy approvals are auto-approved by the evaluation driver; never infer human review time from these timings.",
            "The restart-only control shares the running lab monitor and therefore does not compare agent compute overhead; no baseline remediation proposal is approved or executed.",
            "This comparison does not establish that Groq improves diagnosis. Groq is evaluated separately when a rotated key is configured.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summaries"], indent=2))
    print(f"wrote {args.output} ({len(trials)} matched trials)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

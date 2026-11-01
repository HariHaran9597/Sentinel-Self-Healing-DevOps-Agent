#!/usr/bin/env python3
"""Matched-seed comparison of restart, dictionary, retrieval, and Sentinel."""
from __future__ import annotations

import argparse
import json
import math
import statistics
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

SCENARIOS = {
    "bad_release": {"code": "ERR_BAD_RELEASE", "recover": True, "escalate": False},
    "paraphrased_config": {"code": "ERR_UNCLASSIFIED", "recover": True, "escalate": False},
    "impacting_unknown": {"code": "ERR_UNCLASSIFIED", "recover": False, "escalate": True},
}
STATIC_ACTIONS = {
    "ERR_CONFIG_INVALID": "revert_config",
    "ERR_DEPENDENCY_UNAVAILABLE": "start_dependency",
    "ERR_BAD_RELEASE": "rollback_release",
}
STRATEGIES = ("restart_only", "static_dictionary", "semantic_retrieval", "sentinel_runbook")
TERMINAL = {"resolved", "resolved_manual", "rejected", "failed", "escalated"}


def request_json(base: str, path: str, payload: dict | None = None,
                 timeout: float = 10) -> tuple[int, dict]:
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
        return code == 200 and body.get("operational") is True, f"HTTP {code}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def measure_sustained_health(probe_url: str, injected: float, soak_seconds: int,
                             observation_seconds: int, poll_seconds: float) -> dict:
    """Use one injection-relative observation window for every strategy."""
    deadline = injected + observation_seconds
    first_healthy = healthy_since = None
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
                    "first_healthy_ms": round((first_healthy - injected) * 1000, 2),
                    "sustained_healthy": True,
                    "sustained_healthy_seconds": round(now - healthy_since, 2),
                    "soak_completed": True,
                    "observation_complete": True,
                    "observation_window_seconds": round(now - injected, 2),
                    "probe_samples": samples,
                    "last_probe": last_result,
                }
        else:
            healthy_since = None
        time.sleep(min(poll_seconds, max(0, deadline - time.monotonic())))
    observed = min(observation_seconds, max(0, time.monotonic() - injected))
    return {
        "first_healthy_ms": round((first_healthy - injected) * 1000, 2) if first_healthy else None,
        "sustained_healthy": False,
        "sustained_healthy_seconds": round(time.monotonic() - healthy_since, 2) if healthy_since else 0,
        "soak_completed": False,
        "observation_complete": observed >= observation_seconds,
        "observation_window_seconds": round(observed, 2),
        "probe_samples": samples,
        "last_probe": last_result,
    }


def start_probe_measurement(probe_url: str, injected: float, soak_seconds: int,
                           observation_seconds: int, poll_seconds: float):
    """Start the independent probe immediately, concurrently with remediation."""
    result: dict = {}
    errors: list[BaseException] = []

    def observe() -> None:
        try:
            result.update(measure_sustained_health(
                probe_url, injected, soak_seconds, observation_seconds, poll_seconds
            ))
        except BaseException as exc:  # propagate worker failures to the evaluator
            errors.append(exc)

    worker = threading.Thread(target=observe, name="sentinel-independent-probe", daemon=True)
    worker.start()
    return worker, result, errors


def finish_probe_measurement(worker, result: dict, errors: list[BaseException],
                             observation_seconds: int) -> dict:
    worker.join(timeout=observation_seconds + 10)
    if worker.is_alive():
        raise TimeoutError("independent probe did not finish within its observation window")
    if errors:
        raise RuntimeError(f"independent probe failed: {errors[0]}") from errors[0]
    if not result:
        raise RuntimeError("independent probe produced no measurement")
    return result


def collect_context(base: str) -> str:
    lines = call(base, "/api/logs/tail?limit=100").get("lines", [])
    return "\n".join(line for line in lines if " ERROR " in line or " FATAL " in line)[-1800:]


def prepare_control(base: str, mode: str, seed: int) -> tuple[float, dict]:
    call(base, "/api/evaluation/reset", {}, timeout=40)
    call(base, "/api/evaluation/pause-monitor", {"paused": True})
    injected = time.monotonic()
    result = call(base, "/api/chaos", {"mode": mode, "seed": seed})
    return injected, result


def evaluate_outcome(mode: str, strategy: str, action_count: int, probe: dict,
                     incident_status: str | None) -> tuple[bool, bool, bool]:
    spec = SCENARIOS[mode]
    if spec["recover"]:
        return bool(probe["sustained_healthy"]), False, False
    no_action = action_count == 0
    if mode == "unknown":
        safe_terminal = probe["sustained_healthy"]
    else:
        safe_terminal = not probe["sustained_healthy"]
    safe = no_action and safe_terminal
    escalated = safe and strategy == "sentinel_runbook" and incident_status == "escalated"
    abstained = safe and strategy != "sentinel_runbook"
    return False, escalated, abstained


def run_control_arm(base: str, probe_url: str, mode: str, seed: int,
                    strategy: str, soak: int, observation: int, poll: float) -> dict:
    injected, injection = prepare_control(base, mode, seed)
    probe_worker = start_probe_measurement(probe_url, injected, soak, observation, poll)
    code = injection.get("error_code", SCENARIOS[mode]["code"])
    selected = None
    candidates = []
    action = "restart_container" if strategy == "restart_only" else None
    retrieval = None
    if strategy == "static_dictionary":
        action = STATIC_ACTIONS.get(code)
        retrieval = "exact-code dictionary hit" if action else "abstained: no exact-code entry"
    elif strategy == "semantic_retrieval":
        context = collect_context(base)
        result = call(base, "/api/evaluation/retrieve", {"error_code": code, "context": context}, timeout=30)
        selected, candidates = result.get("selected"), result.get("candidates", [])
        action = selected.get("fix_action") if selected else None
        retrieval = "runbook selected" if selected else "abstained: no safe semantic match"
    if action:
        call(base, "/api/evaluation/apply", {"action": action}, timeout=35)
    probe = finish_probe_measurement(*probe_worker, observation)
    correct_recovery, correct_escalation, correct_abstention = evaluate_outcome(
        mode, strategy, int(action is not None), probe, None
    )
    return {
        "strategy": strategy, "mode": mode, "seed": seed, "error_code": code,
        "approval": "not-applicable-control", "incident_status": None,
        "runbook_id": selected.get("rb_id") if selected else None,
        "retrieval_distance": selected.get("distance") if selected else None,
        "candidate_distances": [round(c["distance"], 4) for c in candidates[:3]],
        "decision": retrieval or ("fixed restart action" if action else "no action"),
        "decision_outcome": "action_applied" if action else "abstained",
        "action": action, "action_count": int(action is not None),
        "unnecessary_action": bool(action and SCENARIOS[mode]["escalate"]),
        "correct_recovery": correct_recovery, "correct_escalation": correct_escalation,
        "correct_abstention": correct_abstention,
        "false_recovery_claim": False,
        **probe,
    }


def wait_incident(base: str, code: str, previous_ids: set[str], timeout: float) -> dict:
    def find():
        incidents = call(base, "/api/incidents?limit=50").get("incidents", [])
        return next((item for item in incidents
                     if item.get("id") not in previous_ids and item.get("error_code") == code), None)
    return wait_until(find, timeout, f"incident {code}")


def run_sentinel_arm(base: str, probe_url: str, mode: str, seed: int,
                     soak: int, observation: int, approval_timeout: float, poll: float) -> dict:
    call(base, "/api/evaluation/reset", {}, timeout=40)
    call(base, "/api/evaluation/pause-monitor", {"paused": False})
    previous_ids = {item["id"] for item in call(base, "/api/incidents?limit=50").get("incidents", [])}
    injected = time.monotonic()
    injection = call(base, "/api/chaos", {"mode": mode, "seed": seed})
    probe_worker = start_probe_measurement(probe_url, injected, soak, observation, poll)
    code = injection.get("error_code", SCENARIOS[mode]["code"])
    incident = wait_incident(base, code, previous_ids, min(approval_timeout, observation))
    incident_id = incident["id"]

    def proposed_or_escalated():
        item = call(base, f"/api/incidents/{incident_id}")
        return item if item.get("status") in {"waiting_approval", "escalated"} else None

    record = wait_until(proposed_or_escalated, min(approval_timeout, observation), f"{mode} diagnosis")
    action_count = 0
    if record.get("status") == "waiting_approval":
        action_count = 1
        call(base, f"/api/incidents/{incident_id}/approve",
             {"approver": "evaluation-driver-auto-approval"}, timeout=approval_timeout)
        def is_terminal():
            item = call(base, f"/api/incidents/{incident_id}")
            return item if item.get("status") in TERMINAL else None
        record = wait_until(is_terminal, min(approval_timeout, observation), f"{mode} action/verification")
    probe = finish_probe_measurement(*probe_worker, observation)
    correct_recovery, correct_escalation, correct_abstention = evaluate_outcome(
        mode, "sentinel_runbook", action_count, probe, record.get("status")
    )
    runbook = record.get("runbook") or {}
    return {
        "strategy": "sentinel_runbook", "mode": mode, "seed": seed,
        "error_code": code, "incident_id": incident_id,
        "approval": "evaluation-driver-auto-approved" if action_count else "not-applicable-escalation",
        "incident_status": record.get("status"), "runbook_id": runbook.get("id"),
        "runbook_exact_match": runbook.get("exact_match"),
        "action": (record.get("fix") or {}).get("action"), "action_count": action_count,
        "decision_outcome": "escalated" if record.get("status") == "escalated" else record.get("status"),
        "unnecessary_action": action_count > 0 and SCENARIOS[mode]["escalate"],
        "correct_recovery": correct_recovery, "correct_escalation": correct_escalation,
        "correct_abstention": correct_abstention,
        "false_recovery_claim": record.get("status") in {"resolved", "resolved_manual"}
                                  and not probe["sustained_healthy"],
        **probe,
    }


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[max(0, math.ceil(pct * len(ordered)) - 1)], 2)


def wilson_interval(successes: int, total: int, z: float = 1.959963984540054) -> list[float] | None:
    if total == 0:
        return None
    proportion = successes / total
    denominator = 1 + z * z / total
    center = (proportion + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(proportion * (1 - proportion) / total + z * z / (4 * total * total)) / denominator
    return [round(max(0, center - margin), 4), round(min(1, center + margin), 4)]


def summarize(trials: list[dict], strategy: str, replicates: int) -> dict:
    own = [trial for trial in trials if trial["strategy"] == strategy]
    recovery_trials = [t for t in own if SCENARIOS[t["mode"]]["recover"]]
    escalation_trials = [t for t in own if SCENARIOS[t["mode"]]["escalate"]]
    times = [t["first_healthy_ms"] for t in recovery_trials
             if t["sustained_healthy"] and t["first_healthy_ms"] is not None]
    recoveries = sum(t["correct_recovery"] for t in recovery_trials)
    escalations = sum(t["correct_escalation"] for t in escalation_trials)
    abstentions = sum(t["correct_abstention"] for t in escalation_trials)
    safe_handling = escalations + abstentions
    unnecessary = sum(t["unnecessary_action"] for t in escalation_trials)
    false_recovery_claims = sum(t.get("false_recovery_claim", False) for t in own)
    per_scenario = {}
    for mode in SCENARIOS:
        group = [trial for trial in own if trial["mode"] == mode]
        if SCENARIOS[mode]["recover"]:
            successes = sum(trial["correct_recovery"] for trial in group)
            outcome = "sustained_recovery"
        else:
            successes = sum(trial["correct_escalation"] or trial["correct_abstention"] for trial in group)
            outcome = "safe_escalation_or_abstention"
        per_scenario[mode] = {
            "expected_outcome": outcome,
            "trials": len(group),
            "correct_outcomes": successes,
            "correct_outcome_rate": round(successes / len(group), 4) if group else None,
            "correct_outcome_wilson_95_ci": wilson_interval(successes, len(group)),
            "unnecessary_actions": sum(trial["unnecessary_action"] for trial in group),
        }
    return {
        "strategy": strategy, "replicates_per_scenario": replicates,
        "expected_recovery_trials": len(recovery_trials), "correct_sustained_recoveries": recoveries,
        "recovery_rate": round(recoveries / len(recovery_trials), 4) if recovery_trials else None,
        "recovery_rate_wilson_95_ci": wilson_interval(recoveries, len(recovery_trials)),
        "expected_safe_handling_trials": len(escalation_trials),
        "correct_sentinel_escalations": escalations,
        "correct_control_abstentions": abstentions,
        "correct_safe_handling": safe_handling,
        "safe_handling_rate": round(safe_handling / len(escalation_trials), 4) if escalation_trials else None,
        "safe_handling_rate_wilson_95_ci": wilson_interval(safe_handling, len(escalation_trials)),
        "unnecessary_actions_on_escalation_cases": unnecessary,
        "unnecessary_action_rate": round(unnecessary / len(escalation_trials), 4) if escalation_trials else None,
        "unnecessary_action_rate_wilson_95_ci": wilson_interval(unnecessary, len(escalation_trials)),
        "false_recovery_claims": false_recovery_claims,
        "recovery_injection_to_first_healthy_ms_median": round(statistics.median(times), 2) if times else None,
        "recovery_injection_to_first_healthy_ms_p95_nearest_rank": percentile(times, .95),
        "recovery_injection_to_first_healthy_ms_max": round(max(times), 2) if times else None,
        "latency_sample_count": len(times),
        "per_scenario": per_scenario,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:18000")
    parser.add_argument("--probe-url", default="http://127.0.0.1:18080")
    parser.add_argument("--replicates", type=int, default=5)
    parser.add_argument("--soak-seconds", type=int, default=30)
    parser.add_argument("--observation-seconds", type=int, default=60,
                        help="same injection-relative maximum observation window for every strategy")
    parser.add_argument("--approval-timeout", type=float, default=90)
    parser.add_argument("--poll-seconds", type=float, default=1)
    parser.add_argument("--output", type=Path, default=Path("docs/results/sentinel-comparison.json"))
    args = parser.parse_args()
    if not 1 <= args.replicates <= 10:
        parser.error("--replicates must be between 1 and 10")
    if args.soak_seconds < 30 or args.observation_seconds <= args.soak_seconds:
        parser.error("soak must be at least 30 seconds and shorter than the observation window")
    if not call(args.base, "/api/health").get("ready"):
        raise RuntimeError("Sentinel API is not ready")

    trials = []
    for mode in SCENARIOS:
        for seed in range(1, args.replicates + 1):
            for strategy in STRATEGIES:
                print(f"{mode} seed={seed}/{args.replicates} strategy={strategy}", flush=True)
                if strategy == "sentinel_runbook":
                    result = run_sentinel_arm(args.base, args.probe_url, mode, seed,
                                              args.soak_seconds, args.observation_seconds,
                                              args.approval_timeout, args.poll_seconds)
                else:
                    result = run_control_arm(args.base, args.probe_url, mode, seed, strategy,
                                             args.soak_seconds, args.observation_seconds,
                                             args.poll_seconds)
                trials.append(result)

    report = {
        "title": "Matched-seed remediation strategy comparison",
        "run_date_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "target": "Only labeled containers and the dedicated Compose scenario-state volume",
        "method": {
            "matched_seeds": True,
            "log_noise": "Seeded informational access lines are deterministic for a scenario/seed and injected in every strategy arm; only the remediation strategy changes.",
            "strategies": {
                "restart_only": "One fixed restart action; no classification, retrieval, or model.",
                "static_dictionary": "Exact error-code to allowlisted-action map in this evaluator; unknown codes abstain.",
                "semantic_retrieval": "ChromaDB runbook retrieval plus the same allowlisted executor; bypasses LangGraph and LLM.",
                "sentinel_runbook": "Full LangGraph runbook workflow; evaluator auto-approves proposed actions.",
            },
            "controls_pause_monitor": "Restart, static dictionary, and semantic-only controls pause Sentinel incident creation. Full workflow leaves it active.",
            "independent_probe": f"External evaluator requests {args.probe_url}/payments/status, a separate business route outside the agent container.",
            "probe_timing": "Host-side independent probing starts immediately after fault injection returns and runs concurrently with diagnosis/remediation.",
            "observation_window_seconds": args.observation_seconds,
            "window_policy": "All strategies share one injection-relative maximum observation window; recovery requires continuous external health for the same soak duration.",
            "sustained_health_seconds": args.soak_seconds,
            "latency": "Injection request start to first observed independent HTTP 200; success requires a completed continuous-health soak. p95 uses nearest-rank; max is shown.",
            "replicates_per_scenario": args.replicates,
        },
        "faults": {mode: {**spec, "expected_behavior": "recover" if spec["recover"] else "safe escalation or abstention"}
                   for mode, spec in SCENARIOS.items()},
        "summaries": [summarize(trials, strategy, args.replicates) for strategy in STRATEGIES],
        "trial_count": len(trials), "trials": trials,
        "limitations": [
            "These are reproducible local fault fixtures, not production incidents; deterministic repetitions are not independent field evidence.",
            "The static dictionary is an intentionally simple exact-code comparator and cannot generalize to unseen wording.",
            "Semantic-only executes retrieved allowlisted actions without LangGraph orchestration or LLM diagnosis; the runbook content is shared with Sentinel.",
            "The impacting-unknown fixture is designed to require safe escalation or abstention; this does not prove general open-world diagnosis.",
            "Full workflow actions are auto-approved by the evaluator; human approval UX and latency are not measured.",
            "Optional Groq diagnosis is not part of this benchmark and no claim is made that it improves outcomes.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summaries"], indent=2))
    print(f"wrote {args.output} ({len(trials)} matched trials)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

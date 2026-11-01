from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts import evaluate_sentinel  # noqa: E402
from scripts.evaluate_sentinel import evaluate_outcome, wilson_interval  # noqa: E402


def probe(healthy: bool) -> dict:
    return {"sustained_healthy": healthy}


def test_control_abstention_is_not_reported_as_an_escalation():
    recovered, escalated, abstained = evaluate_outcome(
        "impacting_unknown", "semantic_retrieval", 0, probe(False), None
    )
    assert (recovered, escalated, abstained) == (False, False, True)


def test_sentinel_escalation_requires_terminal_escalation_status():
    assert evaluate_outcome(
        "impacting_unknown", "sentinel_runbook", 0, probe(False), "escalated"
    ) == (False, True, False)
    assert evaluate_outcome(
        "impacting_unknown", "sentinel_runbook", 0, probe(False), "failed"
    ) == (False, False, False)


def test_impacting_unknown_requires_escalation_and_no_action():
    assert evaluate_outcome(
        "impacting_unknown", "sentinel_runbook", 0, probe(False), "escalated"
    ) == (False, True, False)
    assert evaluate_outcome(
        "impacting_unknown", "sentinel_runbook", 1, probe(False), "escalated"
    ) == (False, False, False)


def test_wilson_interval_is_bounded_and_null_for_empty_sample():
    assert wilson_interval(0, 0) is None
    low, high = wilson_interval(10, 10)
    assert 0 <= low < 1 <= high


def test_independent_probe_starts_before_remediation_finishes(monkeypatch):
    import threading

    started = threading.Event()

    def fake_measure(*args):
        started.set()
        return {"sustained_healthy": True, "probe_samples": 1}

    monkeypatch.setattr(evaluate_sentinel, "measure_sustained_health", fake_measure)
    worker, result, errors = evaluate_sentinel.start_probe_measurement(
        "http://probe", 1.0, 30, 60, 1.0
    )
    assert started.wait(timeout=1)
    measurement = evaluate_sentinel.finish_probe_measurement(worker, result, errors, 60)
    assert measurement["sustained_healthy"] is True


def test_resolved_claim_is_checked_against_independent_soak_in_summary():
    trial = {
        "strategy": "sentinel_runbook", "mode": "bad_release", "seed": 1,
        "correct_recovery": False, "correct_escalation": False,
        "correct_abstention": False, "unnecessary_action": False,
        "false_recovery_claim": True, "sustained_healthy": False,
        "first_healthy_ms": None,
    }
    summary = evaluate_sentinel.summarize([trial], "sentinel_runbook", 1)
    assert summary["false_recovery_claims"] == 1

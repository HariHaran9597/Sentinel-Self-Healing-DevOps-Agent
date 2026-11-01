"""Fast unit tests for the pieces that don't need Docker or network:
runbook parsing, monitor grouping/cooldown, the incident store, and the full
LangGraph happy path in dry-run mode (with the health check stubbed).
"""
from __future__ import annotations

import sys
import time
from types import SimpleNamespace
from pathlib import Path

import pytest

AGENT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENT_DIR))

from app import runtime  # noqa: E402
from app.config import Settings  # noqa: E402
from app.graph import build_graph  # noqa: E402
from app import llm  # noqa: E402
from app.monitor import LogMonitor  # noqa: E402
from app.runbooks import parse_runbook  # noqa: E402
from app.store import IncidentStore  # noqa: E402
from app.executor import _managed_container  # noqa: E402

RUNBOOK_SAMPLE = """---
id: RB-999
title: Sample runbook
summary: A sample.
error_codes: [ERR_SAMPLE]
severity: SEV-2
fix_action: restart_container
fix_command: docker restart demo-server
risk: low
---

## Symptoms
Something broke.

## Resolution
Restart it.
"""


# ---------------------------------------------------------------- runbooks

def test_parse_runbook(tmp_path):
    path = tmp_path / "RB-999-sample.md"
    path.write_text(RUNBOOK_SAMPLE, encoding="utf-8")
    parsed = parse_runbook(path)
    assert parsed["meta"]["id"] == "RB-999"
    assert parsed["meta"]["error_codes"] == ["ERR_SAMPLE"]
    assert "Resolution" in parsed["sections"]
    assert "Restart it." in parsed["sections"]["Resolution"]


# ---------------------------------------------------------------- monitor

def _fake_error_line(code="ERR_TEST"):
    ts = time.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return f"{ts} FATAL [supervisor] something terrible. code={code}"


def test_monitor_groups_consecutive_error_lines():
    raised = []
    collect = lambda code, excerpt: raised.append((code, excerpt))
    monitor = LogMonitor("unused", 0.1, 30, collect)
    monitor._process(["INFO access GET /x 200 5ms", _fake_error_line(), _fake_error_line()])
    assert len(raised) == 1
    assert raised[0][0] == "ERR_TEST"
    assert len(raised[0][1]) == 2  # both error lines form one incident excerpt


def test_monitor_cooldown_suppresses_repeat_codes():
    raised = []
    collect = lambda code, excerpt: raised.append((code, excerpt))
    monitor = LogMonitor("unused", 0.1, 30, collect)
    monitor._process([_fake_error_line()])
    monitor._process([_fake_error_line()])
    assert len(raised) == 1


def test_monitor_flags_uncoded_errors_as_unclassified():
    raised = []
    monitor = LogMonitor("unused", 0.1, 30, lambda code, excerpt: raised.append((code, excerpt)))
    monitor._process(["2026-11-01T00:00:00Z ERROR [runtime] unexpected ledger snapshot rejected"])
    assert raised[0][0] == "ERR_UNCLASSIFIED"


def test_paused_monitor_does_not_create_control_arm_incidents():
    raised = []
    monitor = LogMonitor("unused", 0.1, 30, lambda code, excerpt: raised.append((code, excerpt)))
    monitor.pause_for_evaluation(True)
    monitor._process([_fake_error_line()])
    assert raised == []


# ---------------------------------------------------------------- store

def test_store_lifecycle_and_iso_twins():
    store = IncidentStore()
    store.create("INC-1", error_code="ERR_A")
    store.update("INC-1", proposed_at=1700000000.0)
    record = store.get("INC-1")
    assert record["proposed_at_iso"] is not None
    store.event("INC-1", "approved", "ok")
    assert store.get("INC-1")["events"][0]["type"] == "approved"
    store.create("INC-2")
    assert [r["id"] for r in store.list()] == ["INC-2", "INC-1"]  # newest first


def test_store_survives_agent_restart(tmp_path):
    path = tmp_path / "incidents.sqlite3"
    first = IncidentStore(str(path))
    first.create("INC-PERSIST", error_code="ERR_A")
    first.event("INC-PERSIST", "detected", "reproducible test event")
    first.update("INC-PERSIST", status="waiting_approval")
    first.close()

    restarted = IncidentStore(str(path))
    record = restarted.get("INC-PERSIST")
    assert record["status"] == "waiting_approval"
    assert record["events"][0]["message"] == "reproducible test event"
    restarted.close()


def test_recent_incident_limit_keeps_older_pending_approval():
    store = IncidentStore()
    store.create("INC-PENDING", error_code="ERR_PENDING")
    store.update("INC-PENDING", status="waiting_approval")
    for index in range(3):
        incident_id = f"INC-DONE-{index}"
        store.create(incident_id, error_code="ERR_DONE")
        store.update(incident_id, status="resolved")
    visible = store.list(limit=1)
    assert [record["id"] for record in visible] == ["INC-DONE-2", "INC-PENDING"]
    store.close()


def test_executor_rejects_unmanaged_container(wired_runtime):
    class Containers:
        def get(self, name):
            return SimpleNamespace(labels={})

    with pytest.raises(PermissionError, match="lacks Sentinel demo ownership label"):
        _managed_container(SimpleNamespace(containers=Containers()))


# ---------------------------------------------------------------- graph (dry-run)

class _StubRunbooks:
    def query(self, error_code, context=""):
        return {
            "rb_id": "RB-001", "title": "Database connection pool exhausted",
            "summary": "Pool exhausted.", "error_codes": [error_code],
            "severity": "SEV-2", "fix_action": "restart_container",
            "fix_command": "docker restart demo-server", "risk": "low",
            "resolution": "Restart the service.", "snippet": "...",
            "distance": 0.1, "exact_match": True,
        }


@pytest.fixture()
def wired_runtime(monkeypatch, tmp_path):
    runtime.settings = Settings()  # real defaults are side-effect free
    runtime.settings.require_approval = True
    runtime.settings.executor_mode = "dry-run"
    runtime.settings.groq_api_key = ""
    runtime.settings.llm_diagnosis_enabled = False
    runtime.store = IncidentStore()
    runtime.runbooks = _StubRunbooks()

    from app import executor
    monkeypatch.setattr(executor, "wait_healthy", lambda timeout: (True, "health check 200 OK (stub)"))
    yield runtime
    runtime.settings = runtime.store = runtime.runbooks = None


def test_llm_is_disabled_unless_explicitly_enabled(wired_runtime, monkeypatch):
    runtime.settings.groq_api_key = "test-key"
    calls = []
    monkeypatch.setattr(llm.httpx, "post", lambda *args, **kwargs: calls.append(args))
    assert llm.llm_available() is False
    assert llm.llm_assess({"raw_log": []}, [{"rb_id": "RB-001", "fix_action": "restart_container"}]) is None
    assert calls == []


def test_groq_assessment_rejects_unallowlisted_action(wired_runtime, monkeypatch):
    runtime.settings.llm_diagnosis_enabled = True
    runtime.settings.groq_api_key = "test-key"

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": '{"hypothesis":"config issue","runbook_id":"RB-001","recommended_action":"arbitrary_shell","confidence":0.9,"evidence_refs":["LOG-01"],"rationale":"because","abstain":false}'}}]}

    monkeypatch.setattr(llm.httpx, "post", lambda *args, **kwargs: Response())
    state = {"error_code": "ERR_TEST", "raw_log": ["config invalid"]}
    candidates = [{"rb_id": "RB-001", "title": "config", "summary": "bad config", "resolution": "restore",
                  "fix_action": "revert_config", "risk": "low"}]
    assert llm.llm_assess(state, candidates) is None


def test_groq_assessment_only_returns_cited_evidence(wired_runtime, monkeypatch):
    runtime.settings.llm_diagnosis_enabled = True
    runtime.settings.groq_api_key = "test-key"
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": '{"hypothesis":"runtime config invalid","runbook_id":"RB-006","recommended_action":"revert_config","confidence":0.92,"evidence_refs":["LOG-01","RB-RB-006"],"rationale":"Both references describe config validation.","abstain":false}'}}], "usage": {"prompt_tokens": 100, "completion_tokens": 30}}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["body"] = kwargs["json"]
        return Response()

    monkeypatch.setattr(llm.httpx, "post", fake_post)
    state = {"error_code": "ERR_CONFIG_INVALID", "raw_log": ["config invalid token=shh"]}
    candidate = {"rb_id": "RB-006", "title": "Bad config", "summary": "Config invalid",
                 "resolution": "Restore config", "fix_action": "revert_config", "risk": "low"}
    result = llm.llm_assess(state, [candidate])
    assert result["recommended_action"] == "revert_config"
    assert result["evidence_refs"] == ["LOG-01", "RB-RB-006"]
    assert "[REDACTED]" in captured["body"]["messages"][1]["content"]
    assert captured["url"] == "https://api.groq.com/openai/v1/chat/completions"


def test_graph_pauses_at_hitl_and_resolves_after_approval(wired_runtime):
    graph = build_graph()
    runner = runtime.runner
    runner.bind(graph)
    config = runner.config("INC-T1")
    runtime.store.create("INC-T1", error_code="ERR_DB_CONN_POOL_EXHAUSTED")

    runner._invoke("INC-T1", {
        "incident_id": "INC-T1", "detected_at": time.time(),
        "error_code": "ERR_DB_CONN_POOL_EXHAUSTED", "raw_log": [_fake_error_line()],
    })

    record = runtime.store.get("INC-T1")
    assert record["status"] == "waiting_approval"
    assert record["fix"]["command"] == "docker restart demo-server"

    from langgraph.types import Command
    runner._invoke("INC-T1", Command(resume={"approved": True, "approver": "test"}))

    record = runtime.store.get("INC-T1")
    assert record["status"] == "resolved"
    assert record["proposed_at"] is not None and record["resolved_at"] is not None


def test_graph_reject_path_leaves_system_untouched(wired_runtime):
    graph = build_graph()
    runtime.runner.bind(graph)
    runtime.store.create("INC-T2", error_code="ERR_DB_CONN_POOL_EXHAUSTED")
    runtime.runner._invoke("INC-T2", {
        "incident_id": "INC-T2", "detected_at": time.time(),
        "error_code": "ERR_DB_CONN_POOL_EXHAUSTED", "raw_log": [_fake_error_line()],
    })
    from langgraph.types import Command
    runtime.runner._invoke("INC-T2", Command(resume={"approved": False, "approver": "test", "reason": "not now"}))
    assert runtime.store.get("INC-T2")["status"] == "rejected"


def test_graph_escalates_unknown_error(wired_runtime, monkeypatch):
    runtime.runbooks = _StubRunbooks()
    monkeypatch.setattr(_StubRunbooks, "query", lambda self, code, context="": None)
    runtime.store.create("INC-T3", error_code="ERR_NOVEL")
    runtime.runner.bind(build_graph())
    runtime.runner._invoke("INC-T3", {
        "incident_id": "INC-T3", "detected_at": time.time(),
        "error_code": "ERR_NOVEL", "raw_log": [_fake_error_line("ERR_NOVEL")],
    })
    assert runtime.store.get("INC-T3")["status"] == "escalated"


def test_manual_runbook_is_escalated_not_reported_as_recovered(wired_runtime):
    class ManualRunbook(_StubRunbooks):
        def query(self, error_code, context=""):
            result = super().query(error_code, context)
            result.update(fix_action="manual", fix_command="renew certificate", risk="high")
            return result

    runtime.runbooks = ManualRunbook()
    runtime.store.create("INC-T4", error_code="ERR_TLS_CERT_EXPIRED")
    runtime.runner.bind(build_graph())
    runtime.runner._invoke("INC-T4", {
        "incident_id": "INC-T4", "detected_at": time.time(),
        "error_code": "ERR_TLS_CERT_EXPIRED", "raw_log": [_fake_error_line("ERR_TLS_CERT_EXPIRED")],
    })
    assert "Sentinel will not execute it" in runtime.store.get("INC-T4")["events"][-1]["message"]
    from langgraph.types import Command
    runtime.runner._invoke("INC-T4", Command(resume={"approved": True, "approver": "test"}))
    record = runtime.store.get("INC-T4")
    assert record["status"] == "escalated"
    assert record["result"] is None
    assert "manual remediation" in record["resolution"]

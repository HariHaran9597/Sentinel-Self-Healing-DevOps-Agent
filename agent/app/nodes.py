"""LangGraph node implementations for the healing workflow.

Each node mutates graph state (returned dict merges into it) and mirrors
progress into the incident store so the API/dashboard stay live.
"""
from __future__ import annotations

import logging
import time

from langgraph.types import interrupt

from . import runtime
from .llm import llm_diagnose

log = logging.getLogger("sentinel.graph")

SEVERITY_FALLBACK = {
    "ERR_OOM": "SEV-1",
    "ERR_TLS_CERT_EXPIRED": "SEV-1",
    "ERR_DB_CONN_POOL_EXHAUSTED": "SEV-2",
    "ERR_DISK_FULL": "SEV-2",
    "ERR_UPSTREAM_RATE_LIMITED": "SEV-3",
}


def classify(state: dict) -> dict:
    store = runtime.store
    incident_id = state["incident_id"]
    code = state.get("error_code") or "ERR_UNKNOWN"
    severity = SEVERITY_FALLBACK.get(code, "SEV-3")
    store.update(incident_id, status="analyzing", severity=severity, error_code=code)
    store.event(incident_id, "classified", f"error signature {code} (severity {severity})")
    return {"error_code": code, "severity": severity}


def retrieve(state: dict) -> dict:
    store, runbooks = runtime.store, runtime.runbooks
    incident_id = state["incident_id"]
    context = " ".join(state.get("raw_log", [])[:3])[:600]
    match = runbooks.query(state.get("error_code", ""), context)
    if not match:
        return {}
    store.update(
        incident_id,
        runbook={
            "id": match["rb_id"],
            "title": match["title"],
            "severity": match["severity"],
            "exact_match": match["exact_match"],
        },
        severity=match["severity"],
    )
    store.event(
        incident_id,
        "runbook_matched",
        f"matched {match['rb_id']} -- {match['title']} "
        f"({'exact' if match['exact_match'] else 'semantic'} match, distance {match['distance']:.3f})",
    )
    return {"runbook": match}


def route_after_retrieve(state: dict) -> str:
    return "diagnose" if state.get("runbook") else "escalate"


def diagnose(state: dict) -> dict:
    store = runtime.store
    incident_id = state["incident_id"]
    runbook = state["runbook"]

    template = (
        f"{runbook['severity']} -- {runbook['title']}. {runbook['summary']} "
        f"Resolution: {runbook['resolution']}"
    )
    enriched = llm_diagnose(state)
    diagnosis = enriched or template
    store.update(incident_id, diagnosis=diagnosis)
    store.event(
        incident_id,
        "diagnosed",
        "root-cause analysis complete" + (" (LLM-enriched)" if enriched else " (runbook template)"),
    )
    return {"diagnosis": diagnosis}


def propose(state: dict) -> dict:
    store = runtime.store
    incident_id = state["incident_id"]
    runbook = state["runbook"]
    now = time.time()
    fix = {
        "command": runbook["fix_command"],
        "action": runbook["fix_action"],
        "risk": runbook["risk"],
    }
    store.update(incident_id, fix=fix, status="waiting_approval", proposed_at=now)
    store.event(
        incident_id,
        "fix_proposed",
        f"proposed `{runbook['fix_command']}` (risk: {runbook['risk']}) -- awaiting engineer approval",
    )
    return {
        "fix_command": runbook["fix_command"],
        "fix_action": runbook["fix_action"],
        "risk": runbook["risk"],
        "proposed_at": now,
        "status": "waiting_approval",
    }


def human_gate(state: dict) -> dict:
    """Phase 3 -- pause the graph and ask a human before touching production.

    The interrupt() call suspends the workflow (checkpointed); the FastAPI
    approve/reject endpoints resume it with Command(resume=...).
    """
    store = runtime.store
    settings = runtime.settings
    incident_id = state["incident_id"]

    if settings.require_approval:
        decision = interrupt({
            "type": "approval_request",
            "incident_id": incident_id,
            "error_code": state.get("error_code"),
            "question": f"Apply fix `{state.get('fix_command')}`?",
            "fix_command": state.get("fix_command"),
            "risk": state.get("risk"),
            "runbook": state["runbook"]["rb_id"],
        })
    else:
        decision = {"approved": True, "approver": "auto-pilot", "auto": True}

    approved = bool(decision.get("approved"))
    who = decision.get("approver") or "engineer"
    if approved:
        store.update(incident_id, status="approved", approval=decision)
        store.event(
            incident_id, "approved", f"{who} approved execution of `{state.get('fix_command')}`"
        )
    else:
        now = time.time()
        reason = decision.get("reason")
        store.update(
            incident_id,
            status="rejected",
            approval=decision,
            resolved_at=now,
            resolution=f"fix rejected by {who}" + (f": {reason}" if reason else " -- no action taken"),
        )
        store.event(incident_id, "rejected", f"{who} rejected the proposed fix")
    return {"approval": decision}


def route_after_gate(state: dict) -> str:
    if (state.get("approval") or {}).get("approved"):
        return "execute_fix"
    return "end"


def execute_fix(state: dict) -> dict:
    from .executor import run_fix

    store = runtime.store
    incident_id = state["incident_id"]

    if state.get("fix_action") == "manual":
        now = time.time()
        store.update(
            incident_id,
            status="resolved_manual",
            resolved_at=now,
            result="Acknowledged -- runbook prescribes manual remediation; no automated action taken.",
        )
        store.event(incident_id, "closed", "acknowledged; manual remediation handed to on-call")
        return {"status": "resolved_manual", "resolved_at": now,
                "execution": {"ok": True, "action": "manual", "skipped": True}}

    store.update(incident_id, status="executing")
    store.event(incident_id, "executing", f"executing `{state.get('fix_command')}`")
    execution = run_fix(state.get("fix_action", ""))
    if not execution.get("ok"):
        now = time.time()
        store.update(
            incident_id,
            status="failed",
            resolved_at=now,
            resolution=f"execution failed: {execution.get('detail')}",
        )
        store.event(incident_id, "failed", execution.get("detail") or "execution failed")
        return {"execution": execution, "status": "failed", "resolved_at": now}

    store.event(incident_id, "executed", execution.get("detail", "executed"))
    return {"execution": execution}


def route_after_execute(state: dict) -> str:
    if state.get("status") in {"failed", "resolved_manual"}:
        return "end"
    return "verify"


def verify(state: dict) -> dict:
    from .executor import wait_healthy

    store = runtime.store
    settings = runtime.settings
    incident_id = state["incident_id"]
    store.update(incident_id, status="verifying")
    healthy, detail = wait_healthy(settings.verify_timeout_seconds)
    now = time.time()
    if healthy:
        store.update(incident_id, status="resolved", resolved_at=now, result=detail)
        store.event(incident_id, "verified", detail)
    else:
        store.update(incident_id, status="failed", resolved_at=now, resolution=detail)
        store.event(incident_id, "failed", detail)
    return {"status": "resolved" if healthy else "failed", "result": detail, "resolved_at": now}


def escalate(state: dict) -> dict:
    store = runtime.store
    incident_id = state["incident_id"]
    now = time.time()
    message = f"No runbook matched {state.get('error_code')}; paged human on-call."
    store.update(incident_id, status="escalated", resolved_at=now, resolution=message)
    store.event(incident_id, "escalated", message)
    return {"status": "escalated", "resolution": message}

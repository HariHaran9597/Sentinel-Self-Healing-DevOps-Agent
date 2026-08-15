"""LangGraph state schema for the healing workflow."""
from __future__ import annotations

from typing import TypedDict


class AgentState(TypedDict, total=False):
    incident_id: str
    detected_at: float
    error_code: str
    raw_log: list[str]
    severity: str

    runbook: dict            # best-matching runbook (retrieval node)
    diagnosis: str

    fix_command: str
    fix_action: str
    risk: str
    proposed_at: float

    status: str
    approval: dict
    execution: dict
    resolved_at: float
    result: str
    resolution: str

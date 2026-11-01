"""Persistent, deliberately simple application state for bounded fault drills."""
from __future__ import annotations

import json
import os
from pathlib import Path

STATE_FILE = Path(os.environ.get("DEMO_STATE_FILE", "/var/lib/payments-api/state.json"))
KNOWN_GOOD_RELEASE = "2.4.1"
INITIAL_STATE = {
    "config_valid": True,
    "current_release": KNOWN_GOOD_RELEASE,
    "previous_release": KNOWN_GOOD_RELEASE,
    "unknown_failure": False,
    "ambiguous_failure": False,
}


def load_state() -> dict:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not STATE_FILE.exists():
        save_state(INITIAL_STATE)
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {**INITIAL_STATE, "config_valid": False}
    return {**INITIAL_STATE, **data}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp_path = STATE_FILE.with_suffix(".tmp")
    temp_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
    os.replace(temp_path, STATE_FILE)


def reset_state() -> dict:
    save_state(INITIAL_STATE)
    return dict(INITIAL_STATE)


def failure_reason() -> str | None:
    state = load_state()
    if state.get("ambiguous_failure"):
        return "ambiguous runtime integrity failure: config and release validation both failed"
    if state.get("unknown_failure"):
        return "unclassified runtime integrity failure"
    if not state.get("config_valid"):
        return "active application config is invalid"
    if state.get("current_release") != KNOWN_GOOD_RELEASE:
        return f"release {state.get('current_release')} failed startup validation"
    return None

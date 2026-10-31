"""Thread-safe SQLite incident store -- the API's source of truth.

The LangGraph checkpointer holds workflow state; this store holds what humans
and the dashboard read. Nodes keep the two in sync as they progress.
"""
from __future__ import annotations

import threading
import time
import json
import sqlite3
from collections import OrderedDict
from datetime import datetime, timezone

TERMINAL_STATUSES = {
    "resolved", "resolved_manual", "rejected", "failed", "escalated",
}


def _iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


class IncidentStore:
    def __init__(self, path: str | None = None) -> None:
        self._lock = threading.RLock()
        self._incidents: OrderedDict[str, dict] = OrderedDict()
        self._db = sqlite3.connect(path or ":memory:", check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA busy_timeout=5000")
        self._db.execute("CREATE TABLE IF NOT EXISTS incidents (id TEXT PRIMARY KEY, detected_at REAL NOT NULL, status TEXT NOT NULL, record_json TEXT NOT NULL)")
        self._db.commit()
        for (payload,) in self._db.execute("SELECT record_json FROM incidents ORDER BY detected_at"):
            record = json.loads(payload)
            self._incidents[record["id"]] = record

    def _persist(self, record: dict) -> None:
        self._db.execute(
            "INSERT INTO incidents (id, detected_at, status, record_json) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET status=excluded.status, record_json=excluded.record_json",
            (record["id"], record["detected_at"], record["status"], json.dumps(record)),
        )
        self._db.commit()

    def create(self, incident_id: str, **fields) -> dict:
        record = {
            "id": incident_id,
            "status": "detected",
            "error_code": None,
            "severity": None,
            "detected_at": time.time(),
            "detected_at_iso": None,
            "proposed_at": None,
            "proposed_at_iso": None,
            "resolved_at": None,
            "resolved_at_iso": None,
            "log_excerpt": [],
            "runbook": None,
            "diagnosis": None,
            "fix": None,
            "approval": None,
            "result": None,
            "resolution": None,
            "events": [],
        }
        record.update(fields)
        record.setdefault("detected_at_iso", _iso(record["detected_at"]))
        with self._lock:
            self._incidents[incident_id] = record
            self._persist(record)
        return dict(record)

    def update(self, incident_id: str, **fields) -> None:
        with self._lock:
            record = self._incidents.get(incident_id)
            if record is None:
                return
            for ts_field in ("proposed_at", "resolved_at"):
                if fields.get(ts_field) is not None:
                    fields[f"{ts_field}_iso"] = _iso(fields[ts_field])
            record.update(fields)
            self._persist(record)

    def event(self, incident_id: str, event_type: str, message: str) -> None:
        with self._lock:
            record = self._incidents.get(incident_id)
            if record is None:
                return
            record["events"].append({"ts": _iso(time.time()), "type": event_type, "message": message})
            self._persist(record)

    def get(self, incident_id: str) -> dict | None:
        with self._lock:
            record = self._incidents.get(incident_id)
            return dict(record) if record else None

    def list(self) -> list[dict]:
        with self._lock:
            return [dict(r) for r in reversed(self._incidents.values())]

    def is_terminal(self, incident_id: str) -> bool:
        with self._lock:
            record = self._incidents.get(incident_id)
            return bool(record and record["status"] in TERMINAL_STATUSES)

    def close(self) -> None:
        with self._lock:
            self._db.close()

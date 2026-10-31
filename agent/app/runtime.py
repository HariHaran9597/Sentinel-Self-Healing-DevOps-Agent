"""Shared runtime handles wired at startup (see app.main lifespan) plus the
GraphRunner, which serializes graph invocations per incident thread."""
from __future__ import annotations

import asyncio
import threading
import traceback
from collections import defaultdict

from langgraph.types import Command

# Wired at startup in app.main.lifespan
settings = None
store = None
runbooks = None
monitor = None
checkpointer_connection = None


class GraphRunner:
    """Runs the graph for one incident at a time per thread_id, so an approval
    can never race the initial invocation that is pausing at the interrupt."""

    def __init__(self) -> None:
        self._locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
        self._graph = None

    def bind(self, graph) -> None:
        self._graph = graph

    @staticmethod
    def config(incident_id: str) -> dict:
        return {"configurable": {"thread_id": incident_id}}

    async def run_new(self, incident_id: str, state: dict) -> None:
        await asyncio.to_thread(self._invoke, incident_id, state)

    async def resume(self, incident_id: str, payload: dict) -> None:
        await asyncio.to_thread(self._invoke, incident_id, Command(resume=payload))

    def _invoke(self, incident_id: str, payload) -> dict | None:
        with self._locks[incident_id]:
            try:
                return self._graph.invoke(payload, self.config(incident_id))
            except Exception:
                # Surface agent-side failures on the incident instead of dying quietly.
                globals()["store"].update(
                    incident_id, status="failed", resolution="internal agent error -- see agent logs"
                )
                globals()["store"].event(incident_id, "error", traceback.format_exc(limit=3))
                return None


runner = GraphRunner()

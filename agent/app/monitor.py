"""Phase 1 -- the monitor: tail the service log and raise incidents when
ERROR/CRITICAL/FATAL lines carrying an error signature appear.

Consecutive error lines are grouped into a single incident (a crash writes a
few lines in one flush), and per-error-code cooldown prevents alert storms.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time

log = logging.getLogger("sentinel.monitor")

LEVEL_RE = re.compile(r"\b(ERROR|CRITICAL|FATAL|CRASH)\b")
CODE_RE = re.compile(r"\b(ERR_[A-Z0-9_]+)\b")


class LogMonitor:
    def __init__(self, path: str, poll_seconds: float, cooldown_seconds: float, on_incident) -> None:
        self.path = str(path)
        self.poll = poll_seconds
        self.cooldown = cooldown_seconds
        self.on_incident = on_incident  # sync callable(error_code, excerpt_lines)
        self._offset = 0
        self._buffer = b""
        self._last_seen: dict[str, float] = {}

    async def run(self) -> None:
        # Start at EOF so pre-existing errors are not replayed on agent restart.
        try:
            self._offset = os.path.getsize(self.path)
        except OSError:
            self._offset = 0
        log.info("monitoring %s (poll=%.1fs, cooldown=%.0fs)", self.path, self.poll, self.cooldown)
        while True:
            try:
                self._scan()
            except Exception:
                log.exception("scan failed")
            await asyncio.sleep(self.poll)

    # ----------------------------------------------------------------- internals

    def _scan(self) -> None:
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return
        if size < self._offset:  # truncated or rotated
            self._offset, self._buffer = 0, b""
        if size == self._offset:
            return
        with open(self.path, "rb") as fh:
            fh.seek(self._offset)
            data = fh.read()
            self._offset = fh.tell()
        self._buffer += data
        parts = self._buffer.split(b"\n")
        self._buffer = parts.pop()  # hold back a trailing partial line
        self._process([p.decode("utf-8", "replace").rstrip("\r") for p in parts])

    def _process(self, lines: list[str]) -> None:
        if getattr(self, "paused", False):
            return
        group: list[str] = []

        def flush() -> None:
            nonlocal group
            if not group:
                return
            codes = {c for line in group for c in CODE_RE.findall(line)}
            if not codes:
                codes = {"ERR_UNCLASSIFIED"}
            now = time.time()
            fresh = [c for c in sorted(codes) if now - self._last_seen.get(c, 0.0) > self.cooldown]
            if fresh:
                code = fresh[0]
                self._last_seen[code] = now
                self._emit(code, group[-6:])
            group = []

        for line in lines:
            if LEVEL_RE.search(line):
                group.append(line)
            else:
                flush()
        flush()

    def _emit(self, error_code: str, excerpt: list[str]) -> None:
        log.warning("incident signal: %s (%d error lines)", error_code, len(excerpt))
        try:
            self.on_incident(error_code, excerpt)
        except Exception:
            log.exception("incident callback failed")

    def reset_for_evaluation(self) -> None:
        """Forget only per-run alert state after the isolated lab log is reset."""
        self._last_seen.clear()
        self._buffer = b""
        self._offset = 0
        self.paused = False

    def pause_for_evaluation(self, paused: bool) -> None:
        """Pause incident creation while a control strategy is under test."""
        self.paused = paused
        self._offset = os.path.getsize(self.path) if os.path.exists(self.path) else 0
        self._buffer = b""

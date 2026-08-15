"""Whitelisted remediation executor.

The agent never executes arbitrary shell output from an LLM or a document --
it can only perform the curated actions referenced by runbook frontmatter
(`fix_action`). In docker mode this talks to the Docker API via the mounted
socket; in local mode it manages the demo server as a subprocess.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

import httpx

from . import runtime


def _docker_client():
    import docker

    return docker.from_env()


def _mode() -> str:
    settings = runtime.settings
    if settings.executor_mode in {"docker", "local", "dry-run"}:
        return settings.executor_mode
    # auto: prefer docker only when the target container actually exists
    try:
        _docker_client().containers.get(settings.target_container)
        return "docker"
    except Exception:
        return "local"


def _spawn_local() -> dict:
    settings = runtime.settings
    env = {
        **os.environ,
        "LOG_PATH": str(settings.log_path),
        "PORT": str(settings.local_server_port),
        "HOST": "127.0.0.1",
    }
    proc = subprocess.Popen(
        [sys.executable, str(settings.local_server_script)],
        env=env,
        cwd=str(settings.local_server_script.parent),
    )
    return {"ok": True, "detail": f"local server relaunched (pid={proc.pid})"}


def start_target() -> dict:
    settings = runtime.settings
    mode = _mode()
    try:
        if mode == "dry-run":
            return {"ok": True, "detail": f"[dry-run] would start {settings.target_container}"}
        if mode == "docker":
            container = _docker_client().containers.get(settings.target_container)
            container.start()
            return {"ok": True, "detail": f"container {settings.target_container} started"}
        return _spawn_local()
    except Exception as exc:
        return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}


def restart_target() -> dict:
    settings = runtime.settings
    mode = _mode()
    try:
        if mode == "dry-run":
            return {"ok": True, "detail": f"[dry-run] would restart {settings.target_container}"}
        if mode == "docker":
            container = _docker_client().containers.get(settings.target_container)
            container.restart(timeout=15)
            return {"ok": True, "detail": f"container {settings.target_container} restarted (state={container.status})"}
        return _spawn_local()
    except Exception as exc:
        return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}


def clean_logs() -> dict:
    settings = runtime.settings
    removed = 0
    for path in settings.log_path.parent.glob("*.log*"):
        if path.is_file():
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
    return {"ok": True, "detail": f"cleared {removed} log file(s) in {settings.log_path.parent}"}


def run_fix(action: str) -> dict:
    if action == "restart_container":
        return restart_target()
    if action == "clean_logs_and_restart":
        first = clean_logs()
        second = restart_target()
        return {"ok": first["ok"] and second["ok"], "detail": f"{first['detail']}; {second['detail']}"}
    return {"ok": False, "detail": f"unknown action {action!r}"}


def wait_healthy(timeout: float) -> tuple[bool, str]:
    """Poll the service health endpoint until it answers 200."""
    settings = runtime.settings
    deadline = time.time() + timeout
    last_error = "no attempt made"
    while time.time() < deadline:
        try:
            response = httpx.get(settings.target_health_url, timeout=2)
            if response.status_code == 200:
                return True, f"health check 200 OK from {settings.target_health_url}"
            last_error = f"HTTP {response.status_code}"
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(1)
    return False, f"health check failed within {timeout:.0f}s (last error: {last_error})"

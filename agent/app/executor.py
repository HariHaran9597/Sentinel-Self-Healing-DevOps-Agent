"""Whitelisted remediation executor.

The agent never executes arbitrary shell output from an LLM or a document --
it can only perform the curated actions referenced by runbook frontmatter
(`fix_action`). In docker mode this talks to the Docker API via the mounted
socket; in local mode it manages the demo server as a subprocess.
"""
from __future__ import annotations

import os
import json
import random
import subprocess
import sys
import time
from pathlib import Path

import httpx

from . import runtime


def _docker_client():
    import docker

    return docker.from_env()


def _managed_container(client, name: str | None = None):
    """Refuse remediation unless the target carries Sentinel's demo label."""
    container = client.containers.get(name or runtime.settings.target_container)
    if container.labels.get("com.hariharan.sentinel.managed") != "true":
        raise PermissionError("refusing Docker action: target lacks Sentinel demo ownership label")
    return container


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
            container = _managed_container(_docker_client())
            container.reload()
            if container.status == "running":
                return {"ok": True, "detail": f"container {runtime.settings.target_container} is already running"}
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
            container = _managed_container(_docker_client())
            container.restart(timeout=15)
            return {"ok": True, "detail": f"container {settings.target_container} restarted (state={container.status})"}
        return _spawn_local()
    except Exception as exc:
        return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}


def _read_demo_state() -> dict:
    path = Path(runtime.settings.demo_state_file)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"could not read isolated demo state: {exc}") from exc


def _write_demo_state(state: dict) -> None:
    path = Path(runtime.settings.demo_state_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(".tmp")
    temp_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
    os.replace(temp_path, path)


def _set_state_and_restart(**updates) -> dict:
    state = _read_demo_state()
    state.update(updates)
    _write_demo_state(state)
    restarted = restart_target()
    if not restarted.get("ok"):
        return restarted
    return {"ok": True, "detail": f"updated isolated scenario state and restarted demo-server ({restarted['detail']})"}


def inject_dependency_down(seed: int = 0) -> dict:
    """Fault injector: stop only the explicitly labeled demo dependency."""
    try:
        client = _docker_client()
        dependency = _managed_container(client, runtime.settings.dependency_container)
        dependency.stop(timeout=5)
        path = Path(runtime.settings.log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as log_file:
            rng = random.Random(seed)
            for index in range(rng.randint(3, 7)):
                route = rng.choice(("/payments/status", "/payments/checkout", "/payments/refund"))
                log_file.write(
                    f"{time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime())} INFO [access] "
                    f"GET {route} 200 {rng.uniform(4, 80):.1f}ms request_id=seed-{seed}-{index}\n"
                )
            log_file.write(
                f"{time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime())} ERROR [ledger] "
                f"ledger dependency stopped by seeded evaluator (seed={seed}); code=ERR_DEPENDENCY_UNAVAILABLE\n"
            )
        return {"ok": True, "detail": "stopped labeled sentinel-dependency container"}
    except Exception as exc:
        return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}


def reset_testbed() -> dict:
    """Restore only Sentinel's two labeled containers and its dedicated state volume."""
    if _mode() != "docker":
        return {"ok": False, "detail": "evaluation reset is available only in Docker mode"}
    try:
        client = _docker_client()
        demo = _managed_container(client)
        dependency = _managed_container(client, runtime.settings.dependency_container)
        _write_demo_state({
            "config_valid": True,
            "current_release": "2.4.1",
            "previous_release": "2.4.1",
        })
        # This is a dedicated named volume shared only by the lab service and
        # its monitor; clear prior trial evidence so cooldown/replay cannot
        # produce a stale incident for the next matched trial.
        Path(runtime.settings.log_path).parent.mkdir(parents=True, exist_ok=True)
        Path(runtime.settings.log_path).write_text("", encoding="utf-8")
        if runtime.monitor is not None:
            runtime.monitor.reset_for_evaluation()
        for container in (dependency, demo):
            container.reload()
            if container.status != "running":
                container.start()
        demo.reload()
        demo.restart(timeout=15)
        deadline = time.monotonic() + 20
        last_error = "service did not become ready"
        while time.monotonic() < deadline:
            try:
                response = httpx.get(f"{runtime.settings.demo_server_url}/live", timeout=1)
                if response.status_code == 200:
                    return {"ok": True, "detail": "restored known-good state; demo liveness confirmed"}
                last_error = f"HTTP {response.status_code}"
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            time.sleep(0.25)
        return {"ok": False, "detail": f"demo service did not become live after reset: {last_error}"}
    except Exception as exc:
        return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}


def run_fix(action: str) -> dict:
    if action == "restart_container":
        return restart_target()
    if action == "revert_config":
        return _set_state_and_restart(config_valid=True, config_error=None)
    if action == "start_dependency":
        try:
            client = _docker_client()
            dependency = _managed_container(client, runtime.settings.dependency_container)
            dependency.reload()
            if dependency.status != "running":
                dependency.start()
            return {"ok": True, "detail": "started labeled sentinel-dependency container"}
        except Exception as exc:
            return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}
    if action == "rollback_release":
        try:
            state = _read_demo_state()
            previous = state.get("previous_release")
            if previous != "2.4.1":
                return {"ok": False, "detail": "refusing rollback: previous release is outside the configured allowlist"}
            return _set_state_and_restart(current_release=previous)
        except Exception as exc:
            return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}
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

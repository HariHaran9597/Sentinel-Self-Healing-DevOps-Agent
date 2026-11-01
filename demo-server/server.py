"""payments-api -- a deliberately fragile demo service.

Streams realistic access logs to LOG_PATH and can crash itself with one of four
production-style failure signatures (POST /chaos). Sentinel watches those logs,
matches the error code against its runbook library and proposes the fix.
"""
from __future__ import annotations

import asyncio
import httpx
import os
import random
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from state import failure_reason, load_state, reset_state, save_state, KNOWN_GOOD_RELEASE

LOG_PATH = os.environ.get("LOG_PATH", "./logs/app.log")
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8080"))
CHAOS_DELAY = float(os.environ.get("CHAOS_DELAY", "2.0"))
DEPENDENCY_URL = os.environ.get("DEPENDENCY_URL", "http://sentinel-dependency:8090")
STARTED_AT = time.time()

os.makedirs(os.path.dirname(os.path.abspath(LOG_PATH)) or ".", exist_ok=True)


def log(level: str, component: str, message: str) -> None:
    ts = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    with open(LOG_PATH, "a", encoding="utf-8") as fh:
        fh.write(f"{ts} {level} [{component}] {message}\n")


class ChaosRequest(BaseModel):
    mode: str = "db_pool"
    seed: int = 0


def _crash_db_pool() -> None:
    log("ERROR", "db.pool", "Connection pool exhausted: pool_size=10 in_use=10 waiting=47 (postgres://payments-db:5432)")
    log("ERROR", "worker-2", "PaymentError: failed to checkout connection from pool after 30000ms (attempt=3/3) trace=pay_882f1c")
    log("FATAL", "supervisor", "Service unhealthy: 47 requests queued on DB pool; aborting to avoid data corruption. code=ERR_DB_CONN_POOL_EXHAUSTED")
    os._exit(70)


def _crash_oom() -> None:
    log("ERROR", "mem", "Heap usage 96%: rss=496MiB of 512MiB limit, trend +8MiB/s")
    log("CRITICAL", "settle", "Failed to allocate 64MiB buffer for payment batch settle-4471")
    log("FATAL", "supervisor", "MemoryError: cannot allocate memory. code=ERR_OOM")
    os._exit(137)


def _crash_ssl() -> None:
    log("ERROR", "tls", "Handshake with pay-gateway.internal:443 failed: SSLError('certificate verify failed', 'certificate has expired')")
    log("ERROR", "payments", "Upstream payment gateway rejected: 100% of TLS connections failing (sentinel-ca)")
    log("FATAL", "supervisor", "Secure channel to payment gateway unusable. code=ERR_TLS_CERT_EXPIRED")
    os._exit(70)


CRASH_MODES = {
    "db_pool": _crash_db_pool,
    "oom": _crash_oom,
    "ssl": _crash_ssl,
}

PERSISTENT_MODES = {"bad_config", "paraphrased_config", "bad_release", "unknown", "impacting_unknown", "ambiguous"}
CHAOS_MODES = set(CRASH_MODES) | PERSISTENT_MODES


def _inject_noise(seed: int) -> None:
    rng = random.Random(seed)
    for index in range(rng.randint(3, 7)):
        route = rng.choice(("/payments/status", "/payments/checkout", "/payments/refund"))
        log("INFO", "access", f"GET {route} 200 {rng.uniform(4, 80):.1f}ms request_id=seed-{seed}-{index}")


def _inject_persistent(mode: str, seed: int) -> dict:
    state = load_state()
    if mode in {"bad_config", "paraphrased_config"}:
        state["config_valid"] = False
        state["config_error"] = "invalid retry policy"
        save_state(state)
        code = "ERR_CONFIG_INVALID" if mode == "bad_config" else None
        detail = (f"config validation rejected retry policy (seed={seed})" if code else
                  f"Invalid runtime configuration: retry policy rejected by startup validation; application requests fail. seed={seed}")
    elif mode == "bad_release":
        state["current_release"] = "2.5.0-bad"
        save_state(state)
        code = "ERR_BAD_RELEASE"
        detail = f"release 2.5.0-bad failed startup validation; prior={state['previous_release']} (seed={seed})"
    elif mode == "impacting_unknown":
        state["unknown_failure"] = True
        save_state(state)
        code = None
        detail = f"runtime integrity guard rejected an unrecognized ledger snapshot; payments suspended (seed={seed})"
    elif mode == "ambiguous":
        state["config_valid"] = False
        state["current_release"] = "2.5.0-bad"
        state["ambiguous_failure"] = True
        save_state(state)
        code = None
        detail = f"config retry policy validation failed and release manifest checksum is invalid; multiple causes remain plausible (seed={seed})"
    else:
        code = "ERR_UNKNOWN_FAULT"
        detail = f"unclassified canary warning with no user-impact signal (seed={seed})"
    _inject_noise(seed)
    log("ERROR", "payments-api", f"{detail}" + (f"; code={code}" if code else ""))
    return {"ok": True, "mode": mode, "error_code": code or "ERR_UNCLASSIFIED", "injected_at": time.time(), "seed": seed}


async def _heartbeat() -> None:
    routes = [
        ("/payments/checkout", 200),
        ("/payments/refund", 200),
        ("/payments/status", 200),
        ("/payments/checkout", 201),
    ]
    while True:
        route, code = random.choice(routes)
        log("INFO", "access", f"GET {route} {code} {random.uniform(3, 45):.1f}ms")
        await asyncio.sleep(3)


@asynccontextmanager
async def lifespan(_: FastAPI):
    log("INFO", "boot", f"payments-api v2.4.1 listening on {HOST}:{PORT} (pid={os.getpid()}, log={LOG_PATH})")
    task = asyncio.create_task(_heartbeat())
    yield
    task.cancel()


app = FastAPI(title="payments-api", version="2.4.1", lifespan=lifespan)


@app.get("/health")
def health() -> dict:
    reason = failure_reason()
    if reason:
        raise HTTPException(503, reason)
    try:
        dependency = httpx.get(f"{DEPENDENCY_URL}/health", timeout=0.5)
        dependency.raise_for_status()
    except Exception:
        raise HTTPException(503, "ledger dependency is unavailable")
    return {"service": "payments-api", "status": "ok", "uptime_s": round(time.time() - STARTED_AT, 1)}


@app.get("/live")
def live() -> dict:
    return {"service": "payments-api", "process": "up"}


@app.get("/payments/status")
def independent_business_probe() -> dict:
    """Separate canary-style endpoint used by the external evaluation driver."""
    status = health()
    state = load_state()
    return {"service": status["service"], "operational": True, "release": state["current_release"]}


@app.post("/admin/reset")
def reset_demo_state() -> dict:
    state = reset_state()
    log("INFO", "evaluation", "scenario state reset to known-good baseline")
    return {"ok": True, "state": state}


@app.post("/chaos")
def chaos(req: ChaosRequest) -> dict:
    if req.mode not in CHAOS_MODES:
        raise HTTPException(400, f"unknown mode {req.mode!r}; expected one of {sorted(CHAOS_MODES)}")
    if req.mode in PERSISTENT_MODES:
        return _inject_persistent(req.mode, req.seed)
    _inject_noise(req.seed)
    timer = threading.Timer(CHAOS_DELAY, CRASH_MODES[req.mode])
    timer.daemon = True
    timer.start()
    log("WARNING", "chaos", f"failure injection requested: mode={req.mode} (crashing in {CHAOS_DELAY}s; seed={req.seed})")
    return {"ok": True, "mode": req.mode, "injected_at": time.time(), "seed": req.seed, "crashing_in_s": CHAOS_DELAY}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")

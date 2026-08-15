"""payments-api -- a deliberately fragile demo service.

Streams realistic access logs to LOG_PATH and can crash itself with one of four
production-style failure signatures (POST /chaos). Sentinel watches those logs,
matches the error code against its runbook library and proposes the fix.
"""
from __future__ import annotations

import asyncio
import os
import random
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

LOG_PATH = os.environ.get("LOG_PATH", "./logs/app.log")
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8080"))
CHAOS_DELAY = float(os.environ.get("CHAOS_DELAY", "2.0"))
STARTED_AT = time.time()

os.makedirs(os.path.dirname(os.path.abspath(LOG_PATH)) or ".", exist_ok=True)


def log(level: str, component: str, message: str) -> None:
    ts = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    with open(LOG_PATH, "a", encoding="utf-8") as fh:
        fh.write(f"{ts} {level} [{component}] {message}\n")


class ChaosRequest(BaseModel):
    mode: str = "db_pool"


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


def _crash_disk() -> None:
    log("ERROR", "audit", "Failed to flush audit segment: OSError(28, 'No space left on device'): '/var/log/app/audit-seg-0142.log'")
    log("ERROR", "reqlog", "Request logging degraded: disk full on /var/log/app")
    log("FATAL", "supervisor", "Cannot persist required transaction state. code=ERR_DISK_FULL")
    os._exit(70)


def _crash_ssl() -> None:
    log("ERROR", "tls", "Handshake with pay-gateway.internal:443 failed: SSLError('certificate verify failed', 'certificate has expired')")
    log("ERROR", "payments", "Upstream payment gateway rejected: 100% of TLS connections failing (sentinel-ca)")
    log("FATAL", "supervisor", "Secure channel to payment gateway unusable. code=ERR_TLS_CERT_EXPIRED")
    os._exit(70)


CRASH_MODES = {
    "db_pool": _crash_db_pool,
    "oom": _crash_oom,
    "disk": _crash_disk,
    "ssl": _crash_ssl,
}


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
    return {"service": "payments-api", "status": "ok", "uptime_s": round(time.time() - STARTED_AT, 1)}


@app.post("/chaos")
def chaos(req: ChaosRequest) -> dict:
    if req.mode not in CRASH_MODES:
        raise HTTPException(400, f"unknown mode {req.mode!r}; expected one of {sorted(CRASH_MODES)}")
    timer = threading.Timer(CHAOS_DELAY, CRASH_MODES[req.mode])
    timer.daemon = True
    timer.start()
    log("WARNING", "chaos", f"failure injection requested: mode={req.mode} (crashing in {CHAOS_DELAY}s)")
    return {"ok": True, "mode": req.mode, "crashing_in_s": CHAOS_DELAY}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")

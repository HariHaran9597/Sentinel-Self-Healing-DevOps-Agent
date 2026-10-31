#!/usr/bin/env python3
"""End-to-end demo driver: inject a failure, watch Sentinel heal it.

Works identically against local dev and docker compose (default agent URL
http://127.0.0.1:8000). Stdlib only, so it can run anywhere.

  python scripts/demo.py --mode db_pool --yes      # approve and heal
  python scripts/demo.py --mode ssl --reject       # exercise the HITL reject path
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

MODES = ("db_pool", "oom", "ssl", "bad_config", "dependency_down", "bad_release", "unknown")
ACTIVE = {"waiting_approval", "approved", "executing", "verifying",
          "resolved", "resolved_manual", "rejected", "failed", "escalated"}
DONE = {"resolved", "resolved_manual", "rejected", "failed", "escalated"}


def call(base: str, path: str, method: str = "GET", body: dict | None = None, timeout: int = 10):
    request = urllib.request.Request(
        base.rstrip("/") + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode() or "{}")
        except Exception:
            return exc.code, {}
    except Exception as exc:  # connection refused etc.
        return 0, {"detail": str(exc)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Sentinel self-healing demo")
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--mode", default="db_pool", choices=MODES)
    parser.add_argument("--seed", type=int, default=1, help="seed deterministic noise in the demo logs")
    parser.add_argument("--reject", action="store_true", help="reject the proposed fix instead of approving")
    parser.add_argument("--yes", action="store_true", help="do not prompt before approving")
    args = parser.parse_args()

    t0 = time.time()

    def step(message: str) -> None:
        print(f"[{time.time() - t0:6.1f}s] {message}", flush=True)

    # 1. wait for the agent
    for _ in range(60):
        status, health = call(args.base, "/api/health")
        if status == 200 and health.get("ready"):
            break
        time.sleep(1)
    else:
        step("agent never became ready")
        return 2
    step(f"agent ready at {args.base}")

    # 2. inject the failure
    status, result = call(args.base, "/api/chaos", "POST", {"mode": args.mode, "seed": args.seed})
    if status != 200:
        step(f"chaos injection failed: HTTP {status} {result.get('detail')}")
        return 2
    step(f"injected failure mode={args.mode}, seed={args.seed}")

    # 3. wait for detection + proposal
    injected_at = time.time()
    incident = None
    deadline = time.time() + 30
    while time.time() < deadline and incident is None:
        _, data = call(args.base, "/api/incidents")
        for candidate in data.get("incidents", []):
            if candidate.get("detected_at", 0) >= injected_at - 5:
                incident = candidate
                break
        time.sleep(0.4)
    if incident is None:
        step("no incident was created -- is the monitor watching the right log?")
        return 2
    step(f"incident {incident['id']} DETECTED: {incident['error_code']} (severity {incident.get('severity')})")

    while time.time() < deadline + 15 and incident["status"] == "analyzing":
        time.sleep(0.3)
        _, incident = call(args.base, f"/api/incidents/{incident['id']}")

    if incident["status"] == "escalated":
        step("no runbook matched -- escalated to on-call (unexpected for known modes)")
        return 1
    if incident["status"] != "waiting_approval":
        step(f"unexpected incident status: {incident['status']}")
        return 1

    detect_to_propose_ms = (incident["proposed_at"] - incident["detected_at"]) * 1000
    fix = incident.get("fix") or {}
    step(f"fix PROPOSED in {detect_to_propose_ms:.0f} ms: `{fix.get('command')}` (risk: {fix.get('risk')})")
    diagnosis = (incident.get("diagnosis") or "").replace("\n", " ")
    print(f"           diagnosis: {diagnosis[:150]}")

    if args.reject:
        status, updated = call(args.base, f"/api/incidents/{incident['id']}/reject", "POST",
                               {"reason": "demo: exercising the reject path", "approver": "demo-script"})
        if status != 200:
            step(f"reject failed: HTTP {status} {updated.get('detail')}")
            return 2
        step("fix REJECTED by the engineer -- no action taken (human-in-the-loop works)")
        return 0

    if not args.yes:
        try:
            input(f"Approve `{fix.get('command')}`? [Y/n] ").strip().lower()
        except EOFError:
            pass
    status, _ = call(args.base, f"/api/incidents/{incident['id']}/approve", "POST",
                     {"approver": "demo-script"})
    if status != 200:
        step(f"approve failed: HTTP {status}")
        return 2
    step("fix APPROVED -- executing and verifying...")

    # 4. wait for the outcome
    updated = incident
    deadline = time.time() + 90
    while time.time() < deadline:
        _, updated = call(args.base, f"/api/incidents/{incident['id']}")
        if updated.get("status") in DONE:
            break
        time.sleep(1)

    final_status = updated.get("status")
    total = (updated.get("resolved_at", time.time()) - updated.get("detected_at", time.time()))
    if final_status in {"resolved", "resolved_manual"}:
        step(f"incident {final_status.upper()} -- detection to close: {total:.1f}s")
        step(f"result: {updated.get('result') or updated.get('resolution')}")
        verdict = "MET" if detect_to_propose_ms < 10000 else "NOT MET"
        print(f"\nDEMO PASSED -- detection->proposal: {detect_to_propose_ms:.0f} ms "
              f"(<10s requirement: {verdict})")
        return 0
    step(f"incident ended in status {final_status}: "
         f"{updated.get('resolution') or updated.get('result')}")
    return 1


if __name__ == "__main__":
    sys.exit(main())

---
id: RB-001
title: Database connection pool exhausted
summary: All DB connections are checked out; queued requests time out and payments fail with 5xx while the process is still alive.
error_codes: [ERR_DB_CONN_POOL_EXHAUSTED]
severity: SEV-2
fix_action: restart_container
fix_command: docker restart demo-server
risk: low
---

## Symptoms
- `/payments/*` endpoints return 503/500 while the process itself stays up.
- Logs show `connection pool exhausted` and `failed to checkout connection` with a growing wait queue.
- Request latency p99 climbs to ~30s (pool checkout timeout).

## Diagnosis
1. Confirm pool saturation: `in_use == pool_size` in the `db.pool` log lines.
2. Check for leaked transactions or long-running queries on the database (`pg_stat_activity`).
3. If saturation comes from a connection leak, a restart returns the leaked connections immediately.

## Resolution
Immediate remediation is a service restart: it drains the request queue and closes
all pooled connections, giving the DB pool a clean slate:
    docker restart demo-server
Post-incident follow-up: raise `DB_CONN_POOL_SIZE` to 20 and alert on pool
saturation above 80% for 5 minutes.

## Verification
- `docker ps` shows the container `Up` and healthy.
- `GET /health` returns 200 and the payment success rate recovers above 99%.

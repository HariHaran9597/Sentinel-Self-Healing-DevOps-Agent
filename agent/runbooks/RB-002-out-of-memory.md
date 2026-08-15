---
id: RB-002
title: Out-of-memory crash (OOMKilled)
summary: The service's resident heap grows to its container memory limit and the kernel OOM-killer terminates the process.
error_codes: [ERR_OOM]
severity: SEV-1
fix_action: restart_container
fix_command: docker restart demo-server
risk: medium
---

## Symptoms
- Container exit code 137 (`SIGKILL`) or an in-process `MemoryError`.
- `mem` log lines show RSS trending toward the 512MiB container limit before death.
- Large batch jobs (`settle-*`) fail with allocation errors right before the crash.

## Diagnosis
1. Check `docker inspect demo-server` for `OOMKilled: true`.
2. Correlate memory growth with batch job schedules (settlement runs) or a leak.
3. Compare heap profiles before/after if available.

## Resolution
Restart the service to restore availability immediately:
    docker restart demo-server
Then raise the container memory limit to 1g (`mem_limit` in compose) and profile
the settle batch allocator before the next run. A restart without the limit
raise may recur under the same load.

## Verification
- Container is `Up` and `GET /health` returns 200.
- Memory usage stays below 70% of the new limit during the next settle batch.

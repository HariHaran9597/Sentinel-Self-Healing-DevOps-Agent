---
id: RB-003
title: Log disk full (ENOSPC)
summary: The volume holding service and audit logs fills up; writes fail with errno 28 and the service cannot persist transaction state.
error_codes: [ERR_DISK_FULL]
severity: SEV-2
fix_action: clean_logs_and_restart
fix_command: rm -f /var/log/app/*.log* && docker restart demo-server
risk: medium
---

## Symptoms
- `OSError(28, 'No space left on device')` on audit segment flushes.
- Request logging degrades or stops; the service aborts when required state cannot be persisted.
- `df` on the log volume shows 100% usage.

## Diagnosis
1. Confirm which volume is full: `df -h /var/log/app`.
2. Identify space hogs: usually unrotated audit segments or crash-time core dumps.
3. Check that logrotate is configured for the app directory.

## Resolution
Free space by clearing the log directory, then restart the service so it
re-opens its files cleanly:
    rm -f /var/log/app/*.log* && docker restart demo-server
Follow-up: install logrotate with a 7-day retention and size-based rotation.

## Verification
- Log volume usage drops below 50%.
- Container is `Up`, `GET /health` returns 200, and new log lines appear.

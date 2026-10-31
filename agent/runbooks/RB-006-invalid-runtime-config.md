---
id: RB-006
title: Invalid payments runtime configuration
summary: The active config failed validation and the payments API rejects business requests until the last-known-good config is restored.
error_codes: [ERR_CONFIG_INVALID]
severity: SEV-2
fix_action: revert_config
fix_command: restore the last-known-good config in the Sentinel demo state volume
risk: low
---

## Symptoms
- `/health` and `/payments/status` return 503 with a config validation error.
- The config file is persistent across process restarts.
- Logs identify a rejected runtime config field.

## Diagnosis
Compare the active config marker against the last-known-good state. A process restart alone does not change the mounted config.

## Resolution
Use Sentinel's allowlisted `revert_config` action. It changes only the isolated demo state file to the known-good config marker, then restarts the labeled demo container.

## Verification
An independent payments status probe returns 200 continuously for 30 seconds.

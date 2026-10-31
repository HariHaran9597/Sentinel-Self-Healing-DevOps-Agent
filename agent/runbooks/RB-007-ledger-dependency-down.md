---
id: RB-007
title: Ledger dependency unavailable
summary: The payments API cannot reach its ledger dependency and business requests fail while the payments process remains alive.
error_codes: [ERR_DEPENDENCY_UNAVAILABLE]
severity: SEV-1
fix_action: start_dependency
fix_command: start the labeled sentinel-dependency container
risk: medium
---

## Symptoms
- The payments status endpoint returns 503 with a ledger connectivity error.
- Sentinel's demo dependency container is stopped.
- Payments logs contain `ERR_DEPENDENCY_UNAVAILABLE`.

## Diagnosis
Check the dependency container state and its health endpoint. Restarting payments alone cannot restore a stopped dependency.

## Resolution
Use the allowlisted `start_dependency` Docker API action, which accepts only the container carrying Sentinel's managed label.

## Verification
The dependency health endpoint and independent payments status probe return 200 continuously for 30 seconds.

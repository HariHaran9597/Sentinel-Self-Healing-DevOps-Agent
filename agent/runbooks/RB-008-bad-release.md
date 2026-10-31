---
id: RB-008
title: Bad payments release
summary: The active application release fails startup validation; the previous known-good release is recorded in persistent demo state.
error_codes: [ERR_BAD_RELEASE]
severity: SEV-1
fix_action: rollback_release
fix_command: restore the configured last-known-good payments release
risk: medium
---

## Symptoms
- The active release fails validation and business requests return 503.
- The release marker persists in the isolated scenario volume.
- Logs identify the active and previous release identifiers.

## Diagnosis
Compare the active release marker to the known-good release; a process restart alone repeats the same bad release.

## Resolution
Use Sentinel's `rollback_release` action. It restores only the fixed known-good release marker from the isolated scenario volume, then restarts the labeled demo container.

## Verification
The reported release is the known-good identifier and an independent payments status probe returns 200 continuously for 30 seconds.

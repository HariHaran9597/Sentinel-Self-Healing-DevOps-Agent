---
id: RB-005
title: Upstream payment gateway rate limiting
summary: The upstream gateway starts rejecting traffic with HTTP 429; without a circuit breaker the service queues requests and latency snowballs.
error_codes: [ERR_UPSTREAM_RATE_LIMITED]
severity: SEV-3
fix_action: manual
fix_command: "export CB_ENABLED=true && docker restart demo-server"
risk: low
---

## Symptoms
- Elevated `429 Too Many Requests` responses from `pay-gateway.internal`.
- Payment latency rises as retries stack up; the queue grows but the process survives.

## Diagnosis
1. Confirm 429 rate in access logs and which client ID is throttled.
2. Check whether a batch job doubled its request volume.
3. Determine whether the circuit breaker flag (`CB_ENABLED`) is off.

## Resolution
Enable the circuit breaker and restart so the setting takes effect:
    export CB_ENABLED=true && docker restart demo-server
The breaker sheds load automatically instead of piling retries. If a batch job
caused the spike, stagger its schedule.

## Verification
- 429 rate drops to zero; breaker metrics show `open`/`half-open` transitions.
- Payment success rate recovers; p99 latency back under 500ms.

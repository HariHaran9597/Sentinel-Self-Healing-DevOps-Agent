---
id: RB-004
title: TLS certificate expired on upstream gateway
summary: The client certificate used toward the payment gateway has expired; every outbound TLS handshake fails and payments cannot be sent.
error_codes: [ERR_TLS_CERT_EXPIRED]
severity: SEV-1
fix_action: manual
fix_command: certbot renew --deploy-hook "systemctl reload nginx"
risk: high
---

## Symptoms
- `SSLError('certificate verify failed', 'certificate has expired')` on gateway handshakes.
- 100% of upstream payment calls fail while the service itself is healthy.
- The service aborts when the secure channel is unusable.

## Diagnosis
1. Confirm certificate expiry: `openssl x509 -enddate -noout -in /etc/ssl/certs/pay-gateway.pem`.
2. Verify the gateway still trusts the issuing CA (no rotation on their side).
3. Check monitoring missed the expiry alert (cert expiry alerts belong in uptime checks).

## Resolution
This fix is intentionally manual: certificates are secrets, and automated
renewal from a self-healing agent would need vault access we have not granted it.
Sentinel escalates this incident and leaves it unresolved until an operator
renews the certificate and verifies the upstream handshake. On-call should run:
    certbot renew --deploy-hook "systemctl reload nginx"
then verify the handshake. Add automated renewal + 30-day expiry alerts afterward.

## Verification
- `openssl s_client -connect pay-gateway.internal:443` handshake succeeds.
- Payment success rate returns to baseline.

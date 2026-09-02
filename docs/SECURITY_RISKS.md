# Security Risks

- Engage Clouds webhook secret must be configured in production; otherwise provider callbacks are rejected or unsafe fallback behavior would be required.
- Redis replay protection is best-effort when Redis is unavailable. Production must monitor Redis availability and webhook duplicate rates.
- JWT secret rotation and refresh-token revocation need an operational runbook.
- PII in leads, conversations, call recordings, and analytics must be redacted from logs and protected by retention rules.
- CORS must be restricted through `ALLOWED_ORIGINS`; wildcard origins should not be used with credentials.
- Bulk messaging must enforce consent, unsubscribe, quiet hours, and tenant-level rate limits.
- Admin and QA endpoints need RBAC regression tests before broad production access.

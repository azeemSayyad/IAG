# Infrastructure Checklist

- Public API domain with TLS.
- Frontend domain with TLS.
- Reverse proxy or ingress configured for Socket.IO websocket upgrades.
- Managed PostgreSQL with backups, point-in-time recovery, SSL, and migration access.
- Managed Redis with auth/TLS, persistence policy, and memory alarms.
- Engage Clouds webhook URL reachable at `/api/v1/webhooks/engage-clouds`.
- Environment secrets stored in a secret manager, not committed files.
- Worker and scheduler services deployed with health checks.
- Centralized logs, metrics, uptime checks, and error tracking.
- Disaster recovery backup and restore runbook tested.

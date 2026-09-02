# Scalability Risks

- Engage Clouds outbound sends should move through queues for high-volume campaigns so API timeouts do not block user requests.
- Tenant/campaign messaging limits need Redis-backed counters and provider delivery failure dashboards.
- Socket.IO needs Redis adapter/fanout before running multiple backend replicas.
- Dashboard analytics may need scheduled aggregates or a warehouse once event volume grows.
- AI workflows need concurrency limits, model timeout budgets, and usage/cost controls.
- PostgreSQL indexes and connection pool sizing must be reviewed against production traffic.
- Long-running appointment reminder and follow-up jobs need retry policies and dead-letter visibility.

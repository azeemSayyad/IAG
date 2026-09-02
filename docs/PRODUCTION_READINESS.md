# Launchpad Call Center Production Readiness

## Required Services

### Engage Clouds
- Primary provider for outbound SMS, inbound replies, delivery status, thread updates, and communication webhooks.
- Required credentials: `ENGAGECLOUD_API_KEY`, `ENGAGECLOUD_API_SECRET`, `ENGAGECLOUD_AGENCY_ID`, `ENGAGE_CLOUD_WEBHOOK_SECRET`, and `ENGAGECLOUD_FROM_NUMBERS`.
- Optional config: `ENGAGECLOUD_USE_NEW_AUTH`, `ENGAGECLOUD_API_BASE_URL`, and `ENGAGECLOUD_SMS_SOURCE` if the agency has a custom Engage source identifier.
- Setup: configure Engage Clouds messaging webhook to `https://YOUR_API_DOMAIN/api/v1/webhooks/engage-clouds`.
- Production requirements: webhook signature validation, replay/idempotency protection, consent checks, opt-out handling, TCPA compliance, per-tenant rate limits, retry/dead-letter workflow, and delivery failure monitoring.
- Legacy note: `TWILIO_*` values are no longer the application messaging integration. Keep them only if needed for historical data, migration, or provider-side Engage routing metadata.

### AI / LLM
- Used by conversation response generation, intent detection, lead scoring, summaries, QA, coaching, and semantic search.
- Required credentials/config: provider API key if using hosted LLMs, or `OLLAMA_BASE_URL` and `OLLAMA_MODEL` for self-hosted Ollama.
- Production requirements: model allowlist, timeout and retry budgets, rate limits, prompt/version logging, usage telemetry, and safety guardrails.

### PostgreSQL
- Primary system of record for tenants, users, leads, conversations, messages, appointments, campaigns, audit logs, and QA data.
- Required env: `POSTGRES_URL` or `DATABASE_URL`.
- Setup: provision managed PostgreSQL, run Alembic migrations, enable backups and point-in-time recovery.
- Production requirements: SSL connections, migration gate in CI/CD, connection pool sizing, read replica plan, tenant isolation validation, and restore drills.

### Redis
- Used by queues, distributed booking locks, rate limits, realtime coordination, cache, and Celery broker/result flows.
- Required env: `REDIS_URL` or `REDIS_HOST`/`REDIS_PORT`/`REDIS_PASSWORD`.
- Production requirements: managed Redis with auth/TLS, persistence where needed, eviction policy, memory alarms, and key namespace policy.

### Email
- Used by password reset and transactional notifications.
- Required env: SMTP/provider credentials such as `MAIL_USER`, `MAIL_PASS`, `EMAIL_IMAP_PASSWORD`, and sender domain config.
- Production requirements: SPF/DKIM/DMARC, template audit, bounce handling, and no token logging.

### Google
- Used by optional calendar integration, scheduling sync, and enrichment.
- Required credentials: OAuth client, redirect URIs, calendar scopes, `GOOGLE_CALENDAR_CREDENTIALS`, and `GEOAPIFY_API_KEY` where location enrichment is enabled.
- Production requirements: token encryption, refresh handling, per-tenant calendar mapping, and timezone tests.

### Auth
- Used by protected REST and Socket.IO flows.
- Required env: `JWT_SECRET`, `JWT_ALGORITHM`, `JWT_EXPIRES_IN` or `JWT_ACCESS_TOKEN_EXPIRE_MINUTES`, refresh expiry, `AUTH_ENABLED`, `APP_URL`, and `ALLOWED_ORIGINS`.
- Production requirements: high-entropy secret, rotation plan, refresh-token revocation, secure cookies if introduced, and account lockout monitoring.

### Deployment
- Current deployable units: FastAPI backend, static frontend, Celery worker, Celery beat, PostgreSQL, Redis.
- Required config: Docker images, domain, SSL certificate, reverse proxy/ingress, websocket proxying, CORS origins, and environment-specific secrets.
- Production requirements: CI/CD, health checks, zero-downtime migration strategy, structured logging, metrics, tracing, error tracking, and uptime checks.

### Leads And CRM
- Used by upload, webhook ingestion, deduplication, scoring, lifecycle tracking, and analytics.
- Required integrations: source webhooks, CRM API credentials, Facebook/Google lead sync credentials if enabled.
- Production requirements: signature validation, schema mapping, replay protection, idempotency keys, and ingestion dead-letter queue.

### Messaging And Automation
- Used by Engage Clouds SMS, follow-ups, no-reply workflows, nurture flows, reminders, and compliance notifications.
- Production requirements: consent checks before sends, quiet hours/timezone enforcement, bulk-send throttling, unsubscribe handling, and audit logs.

### Appointments
- Used by booking, rescheduling, reminders, no-show prediction, agent calendars, and realtime updates.
- Production requirements: timezone normalization, conflict constraints, Redis lock monitoring, reminder queue, and Google Calendar sync if enabled.

### Analytics And QA
- Used by dashboards, team performance, AI analytics, call transcripts, QA review, and coaching recommendations.
- Optional services: ClickHouse for high-volume analytics, Qdrant/pgvector for semantic search.
- Production requirements: scheduled aggregation jobs, backfills, data quality checks, and dashboard SLOs.

## Current Validation Notes

- Backend tests pass locally after Engage Clouds provider migration.
- Static frontend build and validation pass locally.
- Backend and frontend boot locally and the Engage Clouds webhook endpoint accepts normalized inbound test payloads.
- Real provider E2E still requires live Engage Clouds credentials, public webhook URL, PostgreSQL, Redis, and reachable external network from the deployment environment.

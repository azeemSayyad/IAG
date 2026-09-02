# Launchpad Call Center Go-Live Audit

Audit date: 2026-05-25
Scope: backend, frontend, workers, Docker, nginx, Kubernetes, AWS/ECS/Terraform, CI/CD, env usage, PostgreSQL, Redis, Socket.IO, Engage Clouds, AI, queues, schedulers, uploads, S3, Stripe, Google Calendar, auth, security, analytics, and runtime validation artifacts.

## Executive Readiness

Overall production readiness: 58%.

Reasoning:
- Local core app readiness is strong: FastAPI boots, static frontend validates, backend tests pass, migrations apply, local REST and Socket.IO surfaces exist.
- Production deployment readiness is partial: Docker Compose is present, but production nginx routes are wrong for `/api/v1` and `/socket.io`, CI/CD is stale, and worker images cannot import backend modules as packaged.
- External live operations are not complete: Engage Clouds credentials and live webhook validation are not verified, email delivery is not implemented, TCPA enforcement is not wired into outbound sends, and S3/Stripe envs are mostly unused.

## Evidence Collected

- Backend config and env usage: `apps/backend-api/app/core/config.py`, `apps/backend-api/alembic/env.py`, `apps/backend-api/scripts/bootstrap_admin.py`.
- Backend routes and services: `apps/backend-api/app/main.py`, routers under `apps/backend-api/app/**/routers`, services under `apps/backend-api/app/**/services`.
- Engage Clouds provider: `apps/backend-api/app/ai/services/communication_provider.py`, `apps/backend-api/app/ai/routers/webhooks.py`, `apps/backend-api/alembic/versions/002_message_provider_delivery_fields.py`.
- Frontend API and realtime client: `apps/frontendall/services/api.js`, all `apps/frontendall/*.html`.
- Worker and beat: `apps/workers/Dockerfile`, `apps/workers/app/celery_app.py`, `apps/workers/app/tasks/*`.
- Deployment: `docker-compose.prod.yml`, `infrastructure/nginx/launchpad.conf`, `infrastructure/kubernetes/**`, `.github/workflows/*.yml`, `infrastructure/aws/ecs-task-definition.json`, Terraform under `infrastructure/aws`.
- Validations already performed in this workspace: backend test suite passed, frontend/backend build validation passed, Docker Compose production config parsed, go-live validation script passed structurally with Engage credential warnings, browser smoke test loaded login page with API and realtime client ready.

## Production-Ready Or Close

1. FastAPI application structure is bootable and route registration is centralized in `apps/backend-api/app/main.py`.
2. PostgreSQL schema management exists through Alembic with initial schema plus Engage delivery fields.
3. Local backend tests are healthy: `apps/backend-api/venv/bin/python -m pytest apps/backend-api/tests -q` returned 72 passed in prior validation.
4. Static frontend validation and backend compile validation pass through `npm run build`.
5. Engage Clouds is now the primary outbound provider in code. Legacy Twilio routes are aliases rather than direct Twilio-first sends.
6. Socket.IO server exists at `/socket.io` with JWT auth and tenant rooms.
7. Basic Docker Compose production topology exists for Postgres, Redis, backend, worker, beat, frontend, and nginx.
8. Monitoring and cloud infrastructure skeletons exist: Prometheus/ELK manifests, AWS Terraform for RDS/CloudFront/WAF/multi-region, and Kubernetes service manifests.

## Critical Go-Live Blockers

1. Production nginx API routing is broken.
   - File: `infrastructure/nginx/launchpad.conf`.
   - Current `location /api/` uses `proxy_pass http://backend-api:8000/;`, which strips `/api/`. A browser request to `/api/v1/auth/login` reaches backend as `/v1/auth/login`.
   - Required fix: preserve the path with `proxy_pass http://backend-api:8000;` or rewrite intentionally and change frontend/API base accordingly.

2. Production nginx does not proxy Socket.IO.
   - Backend Socket.IO is mounted at `/socket.io`.
   - Frontend uses `path: '/socket.io'` in `apps/frontendall/services/api.js`.
   - Nginx only proxies `/ws/`; `/socket.io` currently falls through to frontend nginx.
   - Required fix: add a `/socket.io/` location with websocket upgrade headers and long read timeout.

3. Worker and Celery beat images are not runnable as packaged.
   - File: `apps/workers/Dockerfile` copies only `apps/workers` into `/app`.
   - File: `apps/workers/app/celery_app.py` imports `from app.core.config import settings`, but worker image package `app` is `apps/workers/app`, which has no `app.core`.
   - Verified import failure: `ModuleNotFoundError: No module named 'app.core'`.
   - Required fix: package backend code into the worker image, run Celery from the backend package, or extract shared modules into a real shared package.

4. Kubernetes frontend service is mismatched with the actual image.
   - File: `apps/frontendall/Dockerfile` exposes nginx port 80.
   - File: `infrastructure/kubernetes/services/frontend.yaml` declares containerPort/service port 3000 and uses `NEXT_PUBLIC_*`, which the static frontend does not read.
   - Required fix: serve frontend on 80 in Kubernetes or build a runtime config injection layer for `window.LAUNCHPAD_API_URL`.

5. Kubernetes ingress/API rewrite is risky.
   - `backend-api.yaml` uses `nginx.ingress.kubernetes.io/rewrite-target: /`.
   - Aggregate ingress points `api.launchpad.com` directly to backend, but separate frontend/backend ingresses can conflict.
   - Required fix: choose one ingress model, preserve `/api/v1`, and explicitly support `/socket.io`.

6. CI/CD is stale.
   - File: `.github/workflows/ci.yml`.
   - It references `apps/frontend`, but the actual frontend is `apps/frontendall`.
   - Backend tests are masked with `|| echo "No tests yet"`, so failures can pass CI.
   - Docker frontend build also points to `./apps/frontend`.
   - Required fix: update paths, remove masked failures, build backend/frontend/worker images, and run migrations in deployment.

7. ECS deployment deploys only backend.
   - File: `.github/workflows/deploy.yml`.
   - File: `infrastructure/aws/ecs-task-definition.json`.
   - No frontend, worker, beat, Redis, migration job, or image tag substitution is wired into the deploy workflow.
   - Required fix: define ECS services/tasks or switch to Kubernetes/Compose as the single deployment target.

8. Engage Clouds is code-integrated but not live-verified.
   - Required envs exist, but `.env.production` still needs real values.
   - Live outbound SMS, inbound webhook, delivery callback, replay protection, DB persistence, and frontend realtime update still require provider-side testing.

9. Password reset email is not implemented.
   - File: `apps/backend-api/app/auth/routers/auth.py`.
   - Reset token is printed to stdout with a comment saying production should send email.
   - Required fix: SMTP/provider client, branded email, secure reset URL, rate limit, audit event, and no token logging.

10. TCPA/compliance checks are not enforced on outbound sends.
    - Compliance service exists in `apps/backend-api/app/security/tcpa.py`.
    - Conversation sends in `apps/backend-api/app/conversations/routers/conversations.py` call Engage Clouds directly without `validate_before_send` or business-hours checks.
    - Required fix: enforce suppression, consent, opt-out, and recipient local-time checks before every SMS/reminder/broadcast.

11. Realtime client is loaded globally, but pages do not consume events.
    - `apps/frontendall/services/api.js` dispatches `launchpad:realtime` events.
    - HTML page scan found no page-level listener for those events.
    - Required fix: dashboard, inbox, appointments, notifications, and analytics pages need event listeners or invalidation/refetch handlers.

12. Several env variables are present but unused or partially wired.
    - `STRIPE_SECRET_KEY` and `STRIPE_WEBHOOK_SECRET` are configured but no Stripe service/webhook implementation was found.
    - `S3_BUCKET_NAME` is configured but no boto3/S3 upload path was found.
    - `OPENAI_API_KEY` is referenced in transcription through `getattr(settings, "OPENAI_API_KEY", None)`, but `Settings` does not define it.

## Infrastructure Audit

PostgreSQL: Implemented for app runtime and migrations. Still needs production managed DB, connection pooling policy, backup/restore test, PITR, migration job strategy, and `alembic check` against models.

Redis: Implemented for queues, locks, replay protection, presence, and Celery broker/result backend. Still needs auth/TLS in production, memory policy, persistence/backups, HA/managed Redis, and failure-mode policy. Compose Redis has append-only persistence but no password.

Docker Compose production: Present and syntactically valid. Blocked by worker package issue and nginx path issues.

Nginx: Present but not go-live safe. API path and Socket.IO route must be fixed before external use. Real domains and certs are placeholders.

SSL/DNS/domains: Not configured in repo beyond placeholders. Required domains: app domain, API domain, websocket/API Socket.IO domain, Engage Clouds webhook public URL, optional tracking/CDN domain.

Kubernetes: Broad manifests exist, but frontend port/env mismatch, ingress overlap, missing worker deployment, missing beat deployment, missing migration job, placeholder cert email, and static image names block production use.

AWS/Terraform: RDS, CloudFront, WAF, Route53, and DR skeletons exist. Not verified as applied, wired to app deploy, or connected to CI/CD.

Backups: A backup script exists under `infrastructure/monitoring/disaster-recovery/backup.sh` using a hardcoded `s3://launchpad-backups`. Needs bucket/env parameterization, IAM, restore drill, retention, encryption, and alerts.

Monitoring/logging: Manifests exist. Needs real deployment, dashboards, alert routing, uptime checks, error tracking, log redaction, and SLOs.

CDN: CloudFront Terraform exists. Needs frontend artifact publishing and cache invalidation in CI/CD.

Autoscaling: HPA manifests exist, but target deployments and worker deployment completeness need verification.

## Provider And Credential Audit

Required production variables:

- `DATABASE_URL` or `POSTGRES_URL`: primary SQLAlchemy/Alembic connection. Used by backend and migrations. Without it the app cannot persist data. Obtain from managed Postgres/RDS/Supabase/Neon. Validate with `alembic upgrade head` and `/health`.
- `REDIS_URL` or `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD`: Redis broker, locks, queues, replay, presence. Without it background jobs and replay protection degrade or fail. Obtain from managed Redis/ElastiCache/Upstash. Validate Celery worker, replay NX, presence, and queue operations.
- `JWT_SECRET`, `JWT_EXPIRES_IN`: auth token signing. Without strong secret auth is unsafe. Generate a long random secret in secrets manager. Validate login, refresh, Socket.IO auth, and token expiry.
- `ALLOWED_ORIGINS`, `APP_URL`, optional `FRONTEND_URL`: CORS and public links. Without correct values browsers/webhooks fail or CORS is too open. Validate from final domains.
- `ENGAGECLOUD_API_KEY`, `ENGAGECLOUD_API_SECRET`, `ENGAGECLOUD_AGENCY_ID`: Engage Clouds API auth. Without them outbound messaging fails. Obtain in Engage Clouds agency portal. Validate with a real outbound test.
- `ENGAGE_CLOUD_WEBHOOK_SECRET`: inbound webhook validation. Without it callbacks are insecure. Configure same value in Engage Clouds webhook settings. Validate accepted valid signature and rejected invalid signature.
- `ENGAGECLOUD_FROM_NUMBERS`: approved sender pool. Without it provider send fails. Obtain from Engage Clouds provisioned numbers. Validate round-robin/send attribution and delivery callbacks.
- `ENGAGECLOUD_USE_NEW_AUTH`, `ENGAGECLOUD_API_BASE_URL`, `ENGAGECLOUD_SMS_SOURCE`: provider behavior toggles. Validate with Engage Clouds API contract for the account.
- `TWILIO_AUTH_TOKEN`, `TWILIO_DEFAULT_AGENT_USER_ID`, `TWILIO_SYSTEM_USER_ID`: legacy compatibility metadata only unless direct Twilio paths are reintroduced. Confirm whether they remain needed after Engage Clouds cutover.
- `OLLAMA_BASE_URL`, `OLLAMA_MODEL`: local/private LLM provider. Without them AI endpoints fall back or fail. Validate `/api/v1/ai/status`, conversation response, QA/coaching flows.
- `OPENAI_API_KEY`: needed for OpenAI transcription path, but not currently defined in backend `Settings`. Add to config if OpenAI transcription is desired. Validate call transcription with a small fixture.
- `GOOGLE_CALENDAR_CREDENTIALS`: Google Calendar authorized-user credentials or file path. Calendar service exists but appointment router does not automatically sync. Validate event create/update/delete and token refresh.
- `GEOAPIFY_API_KEY`: location/timezone enrichment. Validate any address/geocoding path that uses it.
- `MAIL_USER`, `MAIL_PASS`, `EMAIL_IMAP_PASSWORD`, `EMAIL_SYSTEM_USER_ID`: email and inbound mail credentials. Configured but password reset email is not implemented. Validate SMTP send and inbound parsing once implemented.
- `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`, `S3_BUCKET_NAME`: cloud storage/backups/deploy. S3 upload code is not wired in app runtime. Validate least-privilege IAM, object write/read, CloudFront distribution, and backup upload.
- `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`: billing. Configured but Stripe runtime implementation was not found. Validate checkout/subscription lifecycle only after implementation.
- `MY_NUMBER_CHIP_ENABLED`, `CHAT_TANK_ENABLED`, `INBOUND_THREAD_ROUTING_ENABLED`: feature toggles. Validate enabled/disabled behavior in staging.
- `SEED_AGENT_LIMIT`: seed/bootstrap behavior. Validate seed run does not pollute production.
- `BOOTSTRAP_ADMIN_EMAIL`, `BOOTSTRAP_ADMIN_PASSWORD`, `BOOTSTRAP_TENANT_NAME`, `BOOTSTRAP_ADMIN_FIRST_NAME`, `BOOTSTRAP_ADMIN_LAST_NAME`: bootstrap script envs. Needed for first production admin.

## Engage Clouds Audit

Implemented:
- Primary outbound service in `communication_provider.py`.
- Inbound and delivery webhook route at `/api/v1/webhooks/engage-clouds`.
- Legacy Twilio webhook aliases route into the same handler.
- Message delivery fields and provider metadata persist to the database.
- Webhook smoke tests accept synthetic inbound/delivery payloads locally.

Still needed:
- Confirm Engage Clouds exact send endpoint, auth headers, payload names, and response fields against live tenant docs.
- Configure public webhook URL: `https://api.<domain>/api/v1/webhooks/engage-clouds`.
- Configure provider delivery/status callback URL to the same route or provider-specific event route.
- Test valid HMAC/shared-secret signatures and invalid signatures.
- Decide whether shared-secret mode must also enforce timestamp freshness. Current shared-secret validation returns before timestamp freshness checks.
- Fail closed or alert loudly if Redis replay protection is unavailable in production.
- Normalize phone numbers before matching inbound messages to leads.
- Add provider-specific idempotency keys for outbound and webhook processing.
- Add rate limiting/backoff based on provider limits.

## Database And Redis Audit

Implemented:
- Core relational models for tenants, users, agents, campaigns, leads, conversations, messages, appointments, audit logs, call recordings/transcripts/analysis.
- Alembic migrations exist.
- Application-level appointment overlap checks exist.
- Additional distributed booking code exists for stronger locking/constraints.

Still needed:
- Run migration diff review against all current models.
- Convert distributed booking SQL constraints into real Alembic migrations if they are required in production.
- Add or verify unique/idempotency constraints for external provider message IDs, webhook event IDs, and lead source IDs.
- Add DB connection pool sizing for backend workers and Celery concurrency.
- Add production backup/restore drill and retention policy.
- Verify tenant isolation under load and service-role behavior.
- Wire Redis password/TLS and HA.

## Frontend/Backend Live Connectivity Audit

Backend-connected pages:
- `login.html`, `dashboard.html`, `analytics.html`, `appointments.html`, `inbox.html`, `agent-performance.html`, `ask-the-brain.html`, `ceo-dashboard.html`, `deals.html`, `notifications.html`, `qa-review.html`, `settings.html`, `team-performance.html`, `upload-leads.html`.

Partially connected/local-state pages:
- `add-deal.html`, `add-deal-2.html`, `add-deal-3.html`, `add-deal-4.html`, `auto-1.html`, `auto-2.html`, `auto-3.html`, `auto-4.html`, `dv-1.html`, `dv-2.html`, `dv-3.html`, `close.html`, `my-team.html`.

Specific gaps:
- Wizard/deal pages rely heavily on `localStorage`/`sessionStorage`.
- Settings avatar upload is explicitly in-memory only.
- Settings invite/integration actions are prompt/toast driven rather than backend-backed.
- Upload leads posts rows one-by-one to `/leads`; backend CSV ingestion endpoint exists but is not used by the frontend flow.
- Realtime events are globally dispatched but not consumed by pages.

## AI System Audit

Implemented:
- Ollama service integration and AI endpoints.
- Conversation response, QA/coaching, scoring, intent, and analytics services exist.
- AI fallback behavior exists for resilience.

Still needed:
- Production LLM decision: hosted OpenAI, private Ollama, or both.
- Add missing `OPENAI_API_KEY` to settings if OpenAI transcription remains supported.
- Package any local Whisper dependency if local transcription is required.
- Define model names, token budgets, rate limits, timeout policy, retry policy, and fallback policy.
- Validate AI outputs with malformed provider responses and timeout scenarios.
- Wire RAG/vector store if advertised; current retrieval/vector pieces are partial and not fully productized.

## Security And Compliance Audit

Implemented:
- JWT auth, refresh token endpoint, role/permission helpers, tenant dependency, security middleware, audit logging, webhook validation, and Socket.IO JWT authentication.

Blockers and risks:
- JWT tokens are stored in localStorage, increasing XSS impact.
- CORS defaults include localhost unless production env is strict.
- Socket.IO server uses `cors_allowed_origins="*"`.
- Password reset token is logged instead of emailed.
- TCPA suppression/consent/business-hours checks are not enforced in message sends/reminders.
- PII encryption service exists, but lead PII storage paths appear plaintext.
- Webhook shared-secret mode does not enforce timestamp freshness.
- Replay protection fails open when Redis is unavailable.
- CSV/upload validation must be hardened for size, content type, formula injection, and abuse.
- Logs must be reviewed for phone/email/message PII.

## Deployment Audit

Docker Compose go-live requires:
1. Fix nginx `/api` and `/socket.io`.
2. Fix worker/beat packaging.
3. Add Redis password/TLS strategy or use managed Redis.
4. Provide real `.env.production`.
5. Mount real TLS certs or run behind managed load balancer.
6. Run `docker compose -f docker-compose.prod.yml up -d`, then validate health, login, Socket.IO, Engage webhooks, worker tasks, and migrations.

Kubernetes go-live requires:
1. Fix frontend port 80 vs 3000.
2. Replace static image tags with registry images and immutable tags.
3. Add backend migration Job/initContainer.
4. Add worker and Celery beat Deployments.
5. Use one ingress model with proper API and Socket.IO paths.
6. Replace placeholder domains/email.
7. Wire Secrets Manager/External Secrets.
8. Validate HPA/PDB against actual deployments.

ECS go-live requires:
1. Decide ECS as the deployment target.
2. Add frontend service, worker service, beat/scheduler service, ALB routes, websocket support, RDS, Redis, and migration task.
3. Render task definitions with pushed image tag.
4. Add all production secrets.

## End-To-End Runtime Verification Status

Flow 1: Lead -> DB -> AI -> Engage Clouds outbound -> customer receives SMS.
Status: Partially verified locally through DB/API structure. Blocked for live completion by Engage credentials/provider validation and TCPA enforcement.

Flow 2: Customer reply -> webhook -> DB persistence -> websocket broadcast -> frontend update.
Status: Synthetic webhook persistence path verified locally. Not live provider-verified. Frontend page update is not complete because pages do not consume realtime events.

Flow 3: Appointment booking -> calendar update -> reminder scheduling -> analytics update.
Status: Appointment CRUD exists. Calendar sync is service-level only, not router-integrated. Reminder scheduling depends on currently broken worker/beat packaging.

Flow 4: AI QA/coaching -> analysis -> persistence -> frontend rendering.
Status: Backend services and frontend pages exist. Needs provider timeout/error testing and fixture-based E2E validation.

Flow 5: Realtime dashboard updates.
Status: Backend emits some tenant events and frontend global client exists. Production proxy and page event handling are incomplete.

## Public URLs Required

- Frontend: `https://app.<domain>/`.
- API: `https://api.<domain>/api/v1/...`.
- Socket.IO: either `https://api.<domain>/socket.io/` or `wss://ws.<domain>/socket.io/`.
- Engage Clouds inbound/delivery webhook: `https://api.<domain>/api/v1/webhooks/engage-clouds`.
- Optional Google OAuth redirect URL if calendar OAuth is user-facing.
- Optional Stripe webhook URL if billing is implemented.
- Optional health/status URL: `https://api.<domain>/health`.

## Go-Live Checklist

1. Choose final deployment target: Docker Compose, Kubernetes, or ECS. Remove conflicting/stale deployment assumptions.
2. Provision production Postgres with backups, PITR, monitoring, and migration access.
3. Provision production Redis with auth/TLS, persistence/HA, and queue/presence validation.
4. Fix nginx/ingress API and Socket.IO routing.
5. Fix worker/beat packaging and deploy background workers.
6. Fill all production secrets in a secrets manager, not committed env files.
7. Configure final domains, DNS, SSL, and CORS origins.
8. Configure Engage Clouds send credentials, sender numbers, and webhook URLs.
9. Implement password reset email and any invite email flows.
10. Enforce TCPA/consent/business-hours checks before every SMS.
11. Add page-level realtime handlers for inbox, appointments, dashboard, analytics, and notifications.
12. Replace frontend local-only workflows with backend-backed APIs or mark them non-production features.
13. Decide and validate AI provider, transcription provider, and model settings.
14. Implement or remove advertised S3 and Stripe paths.
15. Update CI/CD to build/test/deploy actual apps and fail on test failures.
16. Run full E2E staging test with real Engage Clouds, real DB, real Redis, real worker, real frontend, and real public webhook URL.
17. Run security pass: CORS, cookies/token storage, webhook replay, PII logging, rate limits, upload safety, RBAC.
18. Run load test for login, leads, conversations, appointments, Socket.IO, and webhooks.
19. Run backup restore drill.
20. Freeze release, tag images, deploy, monitor, and keep rollback plan ready.

## Exact Implementation Order

1. Fix deployment target foundations: nginx/ingress, frontend port/env injection, and worker packaging.
2. Update CI to validate the actual frontend/backend/worker paths and fail on errors.
3. Stand up staging with managed Postgres and Redis.
4. Load real Engage Clouds staging credentials and configure public webhook URL.
5. Enforce TCPA checks in outbound message/reminder/broadcast paths.
6. Add realtime page listeners and validate dashboard/inbox/appointment updates.
7. Implement email password reset and admin invite flows.
8. Add migration diff review and missing constraints/idempotency migrations.
9. Decide AI provider and validate all AI flows with real provider credentials.
10. Resolve S3/Stripe/Google Calendar: either wire fully or remove from go-live scope.
11. Run full E2E staging flows and load/security tests.
12. Deploy production with rollback, monitoring, and backup restore verification.

## Biggest Remaining Risks

1. Background jobs are currently blocked by worker packaging, so reminders, queues, follow-ups, analytics jobs, and replay/async workflows cannot be trusted in production.
2. Production reverse proxy routing will break API and realtime unless fixed.
3. Live Engage Clouds behavior is unverified against real provider payloads and signatures.
4. Compliance exposure exists because outbound SMS can bypass TCPA checks.
5. CI/CD can pass bad code today because it points at stale paths and masks backend test failures.
6. Frontend realtime exists as infrastructure but not as user-visible page updates.
7. Several advertised integrations have env placeholders but no production implementation.


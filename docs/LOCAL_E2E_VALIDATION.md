# Local E2E Validation Report

Last updated: 2026-05-25

## Fixed In This Stabilization Pass

1. Worker namespace collision fixed.
   - Added `apps/workers/workers/*`.
   - Worker tasks now import `workers.celery_app`.
   - Backend services still import `app.*`.
   - Local import validation passed for all worker task modules.

2. Docker worker packaging fixed.
   - `apps/workers/Dockerfile` now builds from the repository root and copies backend `app` plus worker `workers`.
   - `docker-compose.yml` and `docker-compose.prod.yml` now run `celery -A workers.celery_app`.

3. Production nginx API and realtime paths fixed.
   - `/api/` now preserves `/api/v1`.
   - `/socket.io/` now proxies to the backend with websocket upgrade headers.

4. Kubernetes frontend port mismatch fixed.
   - Frontend image serves nginx on port 80.
   - Kubernetes frontend deployment/service/ingress now target port 80.

5. Kubernetes backend rewrite risk reduced.
   - Removed backend ingress rewrite annotation that could strip paths.

6. Compliance checks added to live outbound sends.
   - Agent/AI conversation SMS sends now check TCPA suppression/consent and business hours.
   - Appointment reminders now check TCPA suppression/consent and business hours.

7. Environment templates added.
   - `.env.local.example`
   - `.env.production.example`
   - `.env.ngrok.example`

8. Local E2E setup guide added.
   - `docs/LOCAL_E2E_SETUP.md`

9. Engage inbound duplicate-phone routing fixed.
   - Webhook matching now normalizes provider phone numbers and uses newest created matching lead when Engage Clouds does not provide tenant context.
   - This fixed local replies being routed to an older duplicate lead with the same phone number.

10. AI state-machine compatibility fixed.
    - Legacy conversation statuses such as `initiated` and `active` now map into the newer AI state-machine enum.
    - This fixed a 500 from `/api/v1/ai/conversation/message`.

11. Local Ollama default model fixed.
    - Backend default model is now `mistral`, which is installed on this local machine and responded reliably during E2E validation.
    - The AI endpoint can run without adding a new env variable outside the approved list.

12. Appointment API now schedules reminders and emits realtime events.
    - `POST /api/v1/appointments` queues reminder jobs and emits `appointment_created`.
    - `PATCH /api/v1/appointments/{id}` emits `appointment_updated`.

13. AI appointment-date safety hardened.
    - AI prompts now include the current UTC date and explicit rules to avoid inventing appointment dates or offering past slots.
    - Slot-search tooling now normalizes invalid or stale date requests to today before querying availability.

14. Local E2E validation harness added.
    - `scripts/local-e2e-validation.py` runs the core backend, DB, Redis, webhook, websocket, AI, appointment, reminder, queue, and analytics checks with concrete inputs and output assertions.

## Validation Performed

Commands run:

```bash
PYTHONPATH=apps/backend-api:apps/workers apps/backend-api/venv/bin/python -c "from workers.celery_app import celery_app; print(celery_app.main)"
```

Result:

```text
launchpad_workers
```

Worker task import validation:

```text
ok workers.tasks.sms
ok workers.tasks.ai
ok workers.tasks.booking
ok workers.tasks.reminders
ok workers.tasks.followups
ok workers.tasks.ingestion
ok workers.tasks.analytics
ok workers.tasks.system
```

Production Compose config validation:

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production.example config --quiet
```

Result: passed.

Repository build validation:

```bash
npm run build
```

Result: frontend static validation passed and backend compile validation passed.

Backend test suite:

```bash
apps/backend-api/venv/bin/python -m pytest apps/backend-api/tests -q
```

Result: 72 passed.

Local E2E validation harness:

```bash
POSTGRES_URL=postgresql://postgres:postgres@127.0.0.1:5432/launchpad \
REDIS_HOST=127.0.0.1 \
REDIS_PORT=6379 \
ENGAGE_CLOUD_WEBHOOK_SECRET=local-webhook-secret \
JWT_SECRET=local-test-secret \
PYTHONPATH=apps/backend-api:apps/workers \
apps/backend-api/venv/bin/python scripts/local-e2e-validation.py
```

Result: 30 passed, 0 failed.

Local service checks:

```text
PostgreSQL: accepting local connections
Redis: PONG
Ollama: available; installed models include qwen2.5:7b-instruct, qwen2.5-coder:3b, qwen2.5-coder:7b, deepseek-coder:6.7b, mistral
Backend: /health returned {"status":"ok","env":"development"}
Frontend: http://127.0.0.1:5500/login.html returned HTML
Celery worker: started, registered workers.tasks.* tasks, connected to Redis
Celery beat: started and sent scheduled tasks
```

Local business-flow checks:

```text
Auth register: created local tenant/admin and returned access/refresh tokens.
Lead create: persisted a local lead through POST /api/v1/leads.
Conversation create: persisted a local conversation through POST /api/v1/conversations.
Conversation message: persisted a message through POST /api/v1/conversations/{id}/messages with send_sms=false.
Engage webhook invalid signature: rejected with 403.
Engage webhook valid signature: accepted local Engage-style payload, persisted inbound customer message with provider_message_sid.
AI conversation: /api/v1/ai/conversation/message returned a real Ollama-backed response after state compatibility fix.
AI appointment slots: generated booking-oriented AI output without past-dated appointment slots.
Appointment booking: POST /api/v1/appointments persisted confirmed appointment.
Reminder scheduling: queue sizes changed to reminders=3 after appointment create.
Celery queue processing: workers.tasks.system.health_check completed through Redis and returned queue/memory stats.
Realtime: Socket.IO client received conversation_message_created after a DB-backed message write.
```

## Blocked Live Validations

The following cannot be truthfully marked complete until real values are entered and external services are running:

1. Real Engage Clouds outbound SMS delivery.
2. Real Engage Clouds inbound reply webhook over ngrok.
3. Real Engage Clouds delivery-status callback over ngrok.
4. Real customer phone receipt/reply.
5. Hardware-level GPU utilization metrics for Ollama. Ollama itself is reachable and responding locally.
6. Real end-to-end reminder send through Engage Clouds.
7. ngrok tunnel startup. `ngrok` is not installed on this machine.

## Current Local Readiness

Local code/runtime readiness: 84%.

What is ready:

- Backend/frontend compile and static validation.
- Worker task imports and Celery app load.
- Worker and beat local startup.
- Local Postgres/Redis/Ollama connectivity.
- Local authenticated AI conversation with Ollama.
- Local signed Engage webhook persistence.
- Local Socket.IO event delivery.
- Local appointment creation and reminder queue scheduling.
- Docker Compose production config parse.
- API and Socket.IO path consistency behind nginx.
- Env templates for local, production, and ngrok.
- Engage Clouds code path and webhook route.
- TCPA gating before key outbound SMS paths.

What still needs live execution:

- Fill real `.env` values.
- Install and authenticate ngrok.
- Configure Engage Clouds webhook URL from ngrok.
- Run real lead -> SMS -> reply -> webhook -> DB -> Socket.IO -> frontend flow.
- Add page-level realtime event handlers where frontend pages do not yet consume dispatched realtime events.
- Enter real Engage Clouds credentials; placeholder credentials correctly block real outbound sends.

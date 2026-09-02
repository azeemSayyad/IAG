# Production Architecture

## Runtime Topology

1. Static frontend serves the operator dashboard, conversations, appointments, analytics, QA, coaching, settings, and admin views.
2. FastAPI exposes REST APIs, auth, business workflows, Engage Clouds webhooks, and Socket.IO realtime events.
3. PostgreSQL stores tenants, users, leads, conversations, messages, appointments, workflows, audit logs, QA, and coaching data.
4. Redis supports rate limits, distributed booking locks, queue coordination, replay protection, cache, and realtime scaling.
5. Celery workers handle reminders, follow-ups, ingestion, AI jobs, analytics, and outbound communication jobs.
6. Engage Clouds calls the public backend URL for inbound messages, delivery status, conversation/thread events, and provider communication updates.

## Communication Flow

Lead or agent action -> FastAPI business service -> `EngageCloudService` -> Engage Clouds API -> provider webhook -> FastAPI webhook router -> PostgreSQL persistence -> Socket.IO tenant event -> frontend update.

## Data Flow

Lead ingestion writes PostgreSQL records, triggers AI scoring/workflows, schedules follow-ups/reminders in workers, and updates dashboard/analytics via API reads and realtime events.

## Production Boundaries

- Engage Clouds is the primary communication provider.
- `TWILIO_*` values are legacy compatibility/migration fields only.
- All outbound sends must use `app.ai.services.communication_provider`.
- All inbound/delivery callbacks should terminate at `/api/v1/webhooks/engage-clouds`.
- Backend must run behind TLS and a reverse proxy that supports websocket upgrades.

## External Dependencies

- Engage Clouds credentials and public webhook URL must be configured before real SMS/reply/delivery E2E can be verified.
- PostgreSQL and Redis must be reachable before production workers are enabled.
- AI provider credentials or an Ollama endpoint must be provisioned before AI workflows can be considered production-ready.

# Local E2E Setup

This guide runs Launchpad Call Center locally with PostgreSQL, Redis, FastAPI, the static frontend, Celery worker/beat, local Ollama, and an ngrok public webhook for Engage Clouds.

## Environment Files

Use one of these templates:

- Local browser/API development: `.env.local.example`
- Local backend exposed through ngrok: `.env.ngrok.example`
- Production secret manager reference: `.env.production.example`

Copy the relevant template to `.env` and fill real values manually:

```bash
cp .env.local.example .env
```

Only placeholder values are committed. The Ollama URL/model use backend defaults: `http://localhost:11434` and `mistral`, so no extra env variable is required for local GPU AI.

## Service Startup Commands

Start PostgreSQL and Redis:

```bash
docker compose up -d postgres redis
```

Run migrations:

```bash
cd apps/backend-api
../../apps/backend-api/venv/bin/alembic upgrade head
```

Start Ollama and load the local model:

```bash
ollama serve
ollama pull mistral
ollama run mistral "Say ready"
```

Start the backend:

```bash
PYTHONPATH=apps/backend-api apps/backend-api/venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Start the frontend:

```bash
python3 -m http.server 5500 --directory apps/frontendall
```

Start Celery worker:

```bash
PYTHONPATH=apps/backend-api:apps/workers apps/backend-api/venv/bin/celery -A workers.celery_app worker --loglevel=info --concurrency=2 -Q sms,ai,booking,reminders,followups,ingestion,analytics
```

Start Celery beat:

```bash
PYTHONPATH=apps/backend-api:apps/workers apps/backend-api/venv/bin/celery -A workers.celery_app beat --loglevel=info
```

Start ngrok for the backend:

```bash
ngrok http 8000
```

## Engage Clouds Webhook Setup

Use the HTTPS forwarding URL from ngrok.

Inbound and delivery webhook URL:

```text
https://YOUR-NGROK-SUBDOMAIN.ngrok-free.app/api/v1/webhooks/engage-clouds
```

Configure Engage Clouds with:

- API key: `ENGAGECLOUD_API_KEY`
- API secret: `ENGAGECLOUD_API_SECRET`
- Agency ID: `ENGAGECLOUD_AGENCY_ID`
- Webhook secret: `ENGAGE_CLOUD_WEBHOOK_SECRET`
- From numbers: `ENGAGECLOUD_FROM_NUMBERS`

Validation steps:

1. Start backend, Redis, Postgres, worker, beat, frontend, Ollama, and ngrok.
2. Open `http://localhost:5500/login.html`.
3. Create or upload a real lead.
4. Send an agent/AI SMS from the inbox or conversation endpoint.
5. Confirm Engage Clouds returns a provider message ID.
6. Reply from the customer phone.
7. Confirm the Engage Clouds webhook hits the ngrok URL.
8. Confirm the message row is persisted and the Socket.IO event is emitted.

## Socket.IO

Local Socket.IO path:

```text
http://localhost:8000/socket.io/
```

Production/nginx Socket.IO path:

```text
https://api.yourdomain.com/socket.io/
```

The frontend client uses `/socket.io`, so reverse proxies must preserve this path.

## Local E2E Flow

1. Lead created or uploaded.
2. Lead persists to PostgreSQL.
3. AI scoring/conversation endpoint calls local Ollama.
4. Outbound SMS goes through Engage Clouds.
5. Delivery callback returns to ngrok.
6. Customer reply returns to ngrok.
7. Backend persists inbound message.
8. Backend emits Socket.IO tenant event.
9. Frontend receives realtime event.
10. Appointment is booked and reminders are scheduled.
11. Celery worker/beat processes reminders/followups.
12. Dashboard and analytics endpoints read the updated DB state.

## Required Local Checks

```bash
curl -s http://127.0.0.1:8000/health
curl -s http://127.0.0.1:8000/api/v1/ai/status
curl -s http://127.0.0.1:8000/api/v1/ai/queues
```

Run static/backend validation:

```bash
npm run build
```

# Railway Deployment — Launchpad Call Center

Deploy as **4 services in one Railway project**: `web`, `worker`, `beat`, `frontend`,
plus the **PostgreSQL** and **Redis** plugins. The web/worker/beat services all build
from this repo root (they share the `app` package); `frontend` serves the static UI.

## 1. Plugins (add first)
- **PostgreSQL** → provides `DATABASE_URL`
- **Redis** → provides `REDIS_URL`

## 2. Services (same repo, different Start Command)

| Service | Root | Start Command | Notes |
|---|---|---|---|
| **web** | repo root | `cd apps/backend-api && uvicorn app.main:app --host 0.0.0.0 --port $PORT` | Pre-deploy: `cd apps/backend-api && alembic upgrade head` (set in railway.json `preDeployCommand`, runs once per deploy — NOT per replica). Healthcheck: `/health`. |
| **worker** | repo root | `PYTHONPATH=apps/backend-api:apps/workers celery -A workers.celery_app worker --loglevel=info --concurrency=$WORKER_CONCURRENCY -Q sms,ai,booking,reminders,followups,ingestion,analytics,system,default` | No public port. |
| **beat** | repo root | `PYTHONPATH=apps/backend-api:apps/workers celery -A workers.celery_app beat --loglevel=info` | Exactly **one** instance (scheduler). Do not scale >1. |
| **frontend** | `apps/frontendall` | nginx (use `apps/frontendall/Dockerfile`) | Proxies `/api` + `/socket.io` to the web service. Set `backend-api` upstream to the web service's internal URL. |

> `PYTHONPATH` order **must** be `apps/backend-api:apps/workers` (backend first) or the
> worker resolves the wrong `app` package and fails with `No module named 'app.core'`.

The repo includes a `Procfile` (`release` / `web` / `worker` / `beat`) and `nixpacks.toml`
so each service can also just pick its process type.

## 3. Environment variables (set on web, worker, beat)
Required:
```
DATABASE_URL          # from PostgreSQL plugin
REDIS_URL             # from Redis plugin
JWT_SECRET            # long random string
APP_ENV=production
ALLOWED_ORIGINS=https://<your-frontend-domain>
FRONTEND_URL=https://<your-frontend-domain>

# Messaging provider (Engage Cloud / Sinch)
ENGAGECLOUD_API_KEY=...
ENGAGECLOUD_API_SECRET=...
ENGAGECLOUD_AGENCY_ID=...
ENGAGECLOUD_API_BASE_URL=...
ENGAGECLOUD_USE_NEW_AUTH=true
ENGAGE_CLOUD_WEBHOOK_SECRET=...
ENGAGECLOUD_FROM_NUMBERS=+1XXXXXXXXXX            # comma-separate ALL numbers (1 -> 300); the pool rotates automatically
SENDER_DAILY_CAP=2000                            # per-number 10DLC daily cap

# Location enrichment
GEOAPIFY_API_KEY=...

# Scheduling (agent ET source of truth)
AGENT_TZ=America/New_York
SCHEDULING_SKIP_WEEKENDS=true
SCHEDULING_AUTO_ROLLOVER=true

# Rate limits (raised for production volume — tune to your send rate)
RATE_LIMIT_PER_LEAD_PER_DAY=5
RATE_LIMIT_PER_LEAD_INTERVAL_SECONDS=60
RATE_LIMIT_PER_TENANT_PER_HOUR=50000
RATE_LIMIT_GLOBAL_PER_HOUR=200000

# TCPA enforcement (lead-local quiet hours 8AM-9PM + suppression)
TCPA_ENABLED=true
TCPA_QUIET_START_HOUR=8
TCPA_QUIET_END_HOUR=21
```

## 4. Migrations
Handled by the web service `preDeployCommand` (`alembic upgrade head`). It is
idempotent and runs once per deploy. (Current head: `005`.)

## 5. Scaling notes
- **web**: scale to N replicas behind Railway's load balancer. Socket.IO uses the
  Redis manager, so cross-replica realtime is correct (all replicas share `REDIS_URL`).
- **worker**: scale horizontally for outbound throughput (each adds concurrency).
- **beat**: keep at exactly 1.
- For very large CSV imports (1M+), prefer the bulk path (already automatic for files
  >500 rows); to avoid request timeouts on huge files, dispatch via the worker.

## 6. Post-deploy smoke test
1. `GET https://<web>/health` → `{"status":"ok"}`
2. Log in via the frontend, upload a small CSV, confirm leads + timezones.
3. Confirm `worker` logs show `process_sms_queue` running every 2s.
4. Confirm `beat` is the only scheduler instance.

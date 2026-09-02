#!/usr/bin/env bash
# All-in-one launcher for a single Railway service: runs DB migrations, then the
# Celery worker + Celery beat (which actually SEND the SMS) and the web/API
# server together in one container. If ANY of the three processes exits, the
# script exits non-zero so Railway restarts the whole container.
#
# Use this when you want the full pipeline (including real outbound SMS) on a
# single service. For production scale, split into separate web/worker/beat
# services instead (see docs/RAILWAY_DEPLOY.md).
set -uo pipefail

VENV=/opt/venv/bin
export PYTHONPATH="apps/backend-api:apps/workers"

echo "[start-all] running migrations (alembic upgrade head)..."
( cd apps/backend-api && "$VENV/alembic" upgrade head ) || { echo "[start-all] migrations FAILED"; exit 1; }

echo "[start-all] starting celery worker..."
"$VENV/celery" -A workers.celery_app worker \
  --loglevel=info \
  --concurrency="${WORKER_CONCURRENCY:-2}" \
  -Q sms,ai,booking,reminders,followups,ingestion,analytics,system,default &
WORKER_PID=$!

echo "[start-all] starting celery beat (scheduler)..."
"$VENV/celery" -A workers.celery_app beat \
  --loglevel=info \
  --schedule /tmp/celerybeat-schedule &
BEAT_PID=$!

echo "[start-all] starting web/api (uvicorn) on port ${PORT:-8000}..."
( cd apps/backend-api && exec "$VENV/uvicorn" app.main:app --host 0.0.0.0 --port "${PORT:-8000}" ) &
WEB_PID=$!

# Block until any one process exits, then tear the rest down so Railway restarts.
wait -n "$WORKER_PID" "$BEAT_PID" "$WEB_PID"
echo "[start-all] a process exited — stopping the others so the container restarts."
kill "$WORKER_PID" "$BEAT_PID" "$WEB_PID" 2>/dev/null || true
exit 1

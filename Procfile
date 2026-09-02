web: cd apps/backend-api && /opt/venv/bin/alembic upgrade head && /opt/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
worker: PYTHONPATH=apps/backend-api:apps/workers /opt/venv/bin/celery -A workers.celery_app worker --loglevel=info --concurrency=${WORKER_CONCURRENCY:-2} -Q sms,ai,booking,reminders,followups,ingestion,analytics,system,default
worker_sms: PYTHONPATH=apps/backend-api:apps/workers /opt/venv/bin/celery -A workers.celery_app worker --loglevel=info --concurrency=${SMS_WORKER_CONCURRENCY:-2} -Q sms,ingestion -n sms@%h
beat: PYTHONPATH=apps/backend-api:apps/workers /opt/venv/bin/celery -A workers.celery_app beat --loglevel=info

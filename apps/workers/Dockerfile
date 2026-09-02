FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements. This Dockerfile is built from the repository root so the
# worker can import the backend `app.*` package and the worker `workers.*`
# package without namespace collisions.
COPY apps/backend-api/requirements.txt /tmp/backend-requirements.txt
COPY apps/workers/requirements.txt /tmp/worker-requirements.txt
RUN pip install --no-cache-dir -r /tmp/backend-requirements.txt -r /tmp/worker-requirements.txt

# Copy application code
COPY apps/backend-api/app ./app
COPY apps/backend-api/alembic ./alembic
COPY apps/backend-api/alembic.ini ./alembic.ini
COPY apps/workers/workers ./workers

# Default command: run celery worker
CMD ["celery", "-A", "workers.celery_app", "worker", "--loglevel=info", "--concurrency=4"]

FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential gcc libpq-dev \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Venv at /opt/venv — start_all_in_one.sh hardcodes this path
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH="apps/backend-api:apps/workers"

# Install BOTH requirement sets (API + Celery workers)
COPY apps/backend-api/requirements.txt apps/backend-api/requirements.txt
COPY apps/workers/requirements.txt apps/workers/requirements.txt
RUN pip install --upgrade pip \
 && pip install --no-cache-dir \
      -r apps/backend-api/requirements.txt \
      -r apps/workers/requirements.txt

COPY . .

# Runs migrations + celery worker + beat + web, all in one container
CMD ["bash", "scripts/start_all_in_one.sh"]

# Deployment Guide

## 1. Configure Environment

Copy `.env.example.production` to `.env.production` and fill every required secret. Never deploy with development JWT, database, Redis, or Engage Clouds values.

## 2. Provision Infrastructure

- PostgreSQL 16 with automated backups, point-in-time recovery, private networking, and SSL.
- Redis 7 with auth, private networking, queue capacity, and websocket/realtime fanout capacity.
- Public frontend and API domains.
- TLS certificates mounted at `infrastructure/certs/fullchain.pem` and `infrastructure/certs/privkey.pem`, or issued by the ingress/controller platform.
- Engage Clouds agency credentials, sender numbers, and webhook URL.

## 3. Deploy

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production up -d --build
```

The backend container runs migrations before starting Uvicorn.

## 4. Verify

```bash
curl https://YOUR_API_DOMAIN/health
curl https://YOUR_API_DOMAIN/api/v1/health
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs backend-api worker celery-beat
```

## 5. Engage Clouds Webhooks

Set this URL in Engage Clouds:

- Messaging, inbound, thread, and delivery events: `https://YOUR_API_DOMAIN/api/v1/webhooks/engage-clouds`

The backend validates `ENGAGE_CLOUD_WEBHOOK_SECRET`, supports HMAC-SHA256 signatures, checks timestamp freshness when a provider timestamp header is present, and uses Redis idempotency keys for replay protection.

## 6. Rollback

Rollback should restore the previous image and compatible database schema. Database rollback requires an explicit Alembic downgrade plan and a verified backup.

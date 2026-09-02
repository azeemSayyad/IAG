# Credentials Checklist

## Engage Clouds
- `ENGAGECLOUD_API_KEY`
- `ENGAGECLOUD_API_SECRET`
- `ENGAGECLOUD_AGENCY_ID`
- `ENGAGE_CLOUD_WEBHOOK_SECRET`
- `ENGAGECLOUD_FROM_NUMBERS`
- `ENGAGECLOUD_USE_NEW_AUTH`
- `ENGAGECLOUD_API_BASE_URL`
- `ENGAGECLOUD_SMS_SOURCE`
- Public webhook URL: `https://YOUR_API_DOMAIN/api/v1/webhooks/engage-clouds`

## Auth And App
- `JWT_SECRET`
- `JWT_EXPIRES_IN` or `JWT_ACCESS_TOKEN_EXPIRE_MINUTES`
- `AUTH_ENABLED`
- `APP_URL`
- `ALLOWED_ORIGINS`
- `PUBLIC_API_URL`

## Database And Redis
- `POSTGRES_URL` or `DATABASE_URL`
- `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD`, or `REDIS_URL`

## AI
- `OLLAMA_BASE_URL`
- `OLLAMA_MODEL`
- Hosted LLM key if OpenAI or another external model provider is enabled.

## Email
- `MAIL_USER`
- `MAIL_PASS`
- `EMAIL_IMAP_PASSWORD`
- `EMAIL_SYSTEM_USER_ID`

## AWS / Storage
- `AWS_REGION`
- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`
- `S3_BUCKET_NAME`

## Payments And Enrichment
- `STRIPE_SECRET_KEY`
- `STRIPE_WEBHOOK_SECRET`
- `GEOAPIFY_API_KEY`

## Legacy Migration Fields
- `TWILIO_AUTH_TOKEN`
- `TWILIO_DEFAULT_AGENT_USER_ID`
- `TWILIO_SYSTEM_USER_ID`

These legacy fields are not the primary communication integration. Use them only for existing data migration or provider-side compatibility metadata.

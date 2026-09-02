# Missing Integrations Checklist

## Requires Live Credentials Or Provider Setup

- Engage Clouds production agency credentials, sender numbers, and webhook registration.
- Public HTTPS API domain reachable by Engage Clouds.
- Hosted LLM/OpenAI key if production will not use Ollama.
- Google Calendar OAuth application and redirect URIs.
- Email provider or SMTP credentials.
- CRM/source webhook credentials for lead ingestion.
- Stripe credentials if billing is enabled.

## Requires Production Infrastructure

- Managed PostgreSQL with backups and restore validation.
- Managed Redis with auth/TLS and enough capacity for queues, locks, rate limits, and replay keys.
- Worker and beat deployment with dead-letter handling.
- Centralized logging, metrics, tracing, and error tracking.

## Explicitly Not Primary

- Direct Twilio-first messaging is no longer the application architecture. Keep legacy Twilio data/fields only for migration history or Engage provider-side routing metadata.

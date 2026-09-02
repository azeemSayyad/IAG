# REPO_MAP.md — Launchpad Call Center (code index)

An annotated map of the codebase so an AI/dev can navigate fast. Pair with
[`CLAUDE.md`](CLAUDE.md) (conventions, how to run, hard rules). Reflects `main`
~`32a4601` (2026-06-18). Regenerate when structure changes meaningfully.

```
apps/frontendall/   Static HTML portal (no build)        ← most UI work
apps/sms-ui/        React+Vite SPA → builds into frontendall/sms/
apps/backend-api/   FastAPI + SQLAlchemy + Alembic (Postgres)
apps/workers/       Celery async task workers
apps/ai-engine/     AI service container (logic mostly lives in backend-api/app/ai)
.localpreview/      Git-ignored preview server + CDP verification scripts
scripts/            validate-frontend.mjs + ops
```

---

## 1. Static frontend — `apps/frontendall`

No framework, no build. Each `*.html` is standalone (inline `<style>`+`<script>`),
loads shared `services/*.js` + `prefs-extras.js`. 37 pages.

### Pages
**Auth/root:** `index.html` (redirect), `login.html` (sign-in).
**Dashboards:** `dashboard.html` (agent daily metrics), `sales-dashboard.html` (static sales view; the React one is in sms-ui), `ceo-dashboard.html` (exec KPIs).
**Deals:** `deals.html` (kanban), `my-deals.html`, `all-deals.html`, `add-deal.html` (entry), `close.html` (closed confirm).
**Deal wizards:** `auto-1..4.html` (auto insurance steps), `add-deal-2..4.html` (ACA steps), `dv-1..3.html` (dental+vision steps).
**Comms:** `inbox.html` (lead messaging hub), `ask-the-brain.html` (AI coaching chat), `notifications.html` (alert feed).
**Team/perf:** `my-team.html`, `team-performance.html`, `agent-performance.html` (also hosts **Dispositions** via `#dispView` toggle), `leaderboard.html`.
**Admin/compliance:** `compliance.html` (compliance console — Agent Appointments/Licenses/Matrix/Events/Logs), `qa-review.html`, `dispositions.html` (now a **redirect** → `agent-performance.html#dispView`), `pacing.html` (capacity engine).
**Settings/import:** `settings.html` (prefs + **Licenses & Appointments** admin/agent views), `upload-leads.html` (CSV import), `appointments.html` (scheduler), `analytics.html`.
**SPA host:** `sms/index.html` (loads the compiled sms-ui bundle).
**Dupes (legacy):** `team-performance copy.html`, `upload-leads copy.html`.

### Shared JS
- `services/api.js` — HTTP client `window.__ebAPI` (`.get/.post/.patch/.del/.upload`, GET cache+dedup, token refresh, socket.io realtime `.on/.emitRealtimeEvent`). **Contains the env-detection block — DO NOT EDIT** (localhost→`:18000`, else origin).
- `prefs-extras.js` — applies theme/layout/sidebar/language; **owns dark mode** (`html[data-mode="dark"]` injected styles) and **rewrites the sidebar** at runtime (`inject*Link()`, `gate*()` by role, `ORDER` array). Cache-busted as `?v=N` (currently `v=5`).
- `services/error-boundary.js` — `__ebShowError/__ebShowToast/__ebShowLoading…` overlays + toasts. (`?v=9`)
- `services/demo-mock.js` — local-only mock of every REST endpoint; active only when `access_token==='local-demo'`.
- `coach.js` — agent-only "Coach Mode" task carousel above the page title.
- `tour.js` — guided spotlight tours (triggered by Coach Mode).
- `services/softphone.js` — Sinch WebRTC in-browser calling (`window.__ebSoftphone`).
- `services/call-now-popup.js` — realtime "call now" request popups.
- `services/__preview-login.js` — git-ignored local login shim (role from username).

### Conventions
Sidebar links are `<a class="sb-item" href="…">` (`.active`, `.sb-featured` variants).
Theme via `html[data-mode="dark"]` + CSS vars. Bump `?v=N` on shared-JS changes.
**The sidebar has 3 sources: static HTML, prefs-extras runtime, and sms-ui `PortalShell.tsx`** — see CLAUDE.md.

---

## 2. React SPA — `apps/sms-ui` (builds → `apps/frontendall/sms/`)

`vite.config.ts` `outDir: ../frontendall/sms`; bundle → `frontendall/sms/assets/index-<hash>.js`. **Rebuild (`npx vite build`) after any src change.**

- `src/App.tsx` — routes (role-guarded): `/queue` (`canSeeQueue`), `/manager` (`canSeeManager`), `/monitoring` (`canSeeMonitoring`, dev), `/sales-dashboard` (`isAdmin`).
- `src/pages/` — `SmsQueue.tsx` (accept/pass leads, inbound chat), `SmsManager.tsx` (agent oversight grid, breaks, pass/keep stats), `SmsMonitoring.tsx` (system health), `SalesDashboard.tsx` (deals-by-agent, carrier mix, weekly trends, **Total Leads** card).
- `src/components/` — `PortalShell.tsx` (sidebar shell; `PORTAL_LINKS`/`WORKSPACE_LINKS`/`SMS_LINKS` nav arrays, role-gated), `LeadOfferOverlay.tsx` (blocking lead-offer modal), `LivePulse.tsx`, `charts/*` (DealsByAgentDonut, TopPerformers3D, WeeklyBars3D).
- `src/lib/` — `auth.ts` (`isAdmin/isManager/isDev/canSee*`, reads `access_token`/`ebRole`), `api.ts` (`/api/v1` fetch wrapper + Bearer + 401 redirect), `socket.ts`, `phone.ts`, `sound.ts`, `theme.ts`.

---

## 3. Backend — `apps/backend-api/app` (FastAPI, all routers under `/api/v1`)

`main.py` wires middleware (CORS → tenant isolation → security → Prometheus),
mounts all routers, serves the static frontend last, exposes `/health`, `/metrics`,
socket.io. **Call-recording uploads are exempt from the request-size cap.**

`core/` — `config.py`, `deps.py` (`get_current_active_user`, `get_tenant_id`, `require_role`), `permissions.py` (RBAC: super_admin>tenant_admin>manager>agent + Permission enum), `database.py`, `redis.py`, `security_middleware.py`, `tenant.py`, `send_once.py` (first-template send-once guard), `sending.py` (global send kill-switch).

### Domains (folder · prefix · purpose · notable endpoints)
- `auth/` · `/auth` · login/register/tokens/profile · `POST /login`, `/refresh`, `GET|PATCH /me`, `POST /password-reset-*`, `/change-password`, `/logout`.
- `admin/` · `/admin` · campaigns + tenant analytics + user mgmt · `CRUD /campaigns`, `/campaigns/{id}/performance`, `GET /analytics/*`, `CRUD /users`, `POST /users/{id}/caller-number`.
- `agent_os/` · `/agent` · agent workspace · `GET /dashboard`, `/calendar/{daily,weekly,agenda}`, `/lead/{id}/summary`, `POST /appointment/{id}/disposition`, `GET /stats`.
- `compliance/` · `/compliance` · **licenses + carrier appointments + deals/approvals** · profiles (`GET /agents/{id}/profile`, `/me/profile`, NPN set), **state-licenses** CRUD, **carrier-appointments** CRUD + `/import-csv`, compliance-events, `POST /deals/submit`, `/deals/recording`. See §4.
- `appointments/` · `/appointments` · appt lifecycle · `CRUD /`, `POST /{id}/disposition`, `GET /export/pdf`.
- `booking/` · `/booking` · booking flow, slots, reminders, no-show, waitlist · `POST /start|select|cancel|reschedule`, `GET /slots`, `/no-show/predict`, `CRUD /waitlist`.
- `leads/` · `/leads` · lead CRUD + compliance-aware distribution · `CRUD /`, `GET /{id}/copilot`, `POST /{id}/assign|reroute`, `/reassign`.
- `conversations/` · `/conversations` · agent↔lead threads · `CRUD /`, `GET|POST /{id}/messages`.
- `calls/` · `/calls` · recording/transcription/analysis + Sinch WebRTC · `GET /config`, `POST /webrtc-token|dial|voice-webhook`, recordings/transcripts/analysis/summary endpoints.
- `coaching/` · `/coaching` · AI coaching, rankings · `GET /performance/*`, `/insights/*`, `/rankings/*`, `POST /realtime/coach/{id}`.
- `sms_queue/` · `/sms/queue` · agent SMS workspace · `POST /join|leave|break|accept|pass|send|disposition|ingest`, `GET /status|my-stats|current|my-leads|conversation/{id}`. (also `routers/manager.py`, `routers/monitoring.py`)
- `ingestion/` · `/ingestion` · CSV/webhook/API import + CSV-upload campaign manager + send kill-switch · `POST /csv`, `/webhook/{source}`, `/campaigns/upload`, campaign run/pause/resume/stop, drip config.
- `intent/` · `/intent` · intent + objection detection · `POST /detect`, `/objection/handle`, `GET /classes`.
- `followup/` · `/followup` · no-reply/missed/nurture campaigns · `POST /{no-reply,missed,nurture}/process`, status + nurture move/re-engage.
- `ml/` · `/ml` · scoring, timing, agent ranking, optimization · `GET /predict/{id}`, `/timing/*`, `/agents/*`, `/optimization`.
- `pacing/` · `/pacing` · same-day lead pacing/capacity · `GET /metrics`, `/plan`.
- `realtime/` · `/realtime` · websocket presence + notifications · `GET /online|status`, `POST /notify|presence/heartbeat`, presence queries.
- `reports/` · `/reports` · PDF exports · `GET /{daily,compliance,agent/{id},manager/{id},sales}/export.pdf`.
- `sales_dashboard/` · `/sales-dashboard` · admin sales overview · `GET /overview` (date-range; returns `leads_total`, deals, mix, weekly).
- `security/` · `/security` · audit/security events, rate limits · `GET /audit/*`, `/rate-limit/status`.
- `workflows/` · `/workflows` · event-triggered multi-step workflows · `CRUD /`, `POST /start|trigger/{event}`, queue status/process.
- `audit/` · `/audit` · audit log listing.
- `ai/` · AI conversation engine + provider webhooks (`POST /webhooks/engage-clouds`, twilio aliases), orchestrator/queue. `agents/`, `analytics/` folders are thin/rolled into others.

### Models — `app/models/`
`agent.py`, `agent_availability.py`, `appointment.py`, `audit_log.py`, `campaign.py`,
`compliance.py` (deals, approvals, events, **AgentStateLicense**, **AgentCarrierAppointment**, recordings),
`conversation.py`, `lead.py`, `message.py`, `sms.py`, `tenant.py`, `user.py`.

### Migrations — `apps/backend-api/alembic/versions/`
`001_initial` → `024_sms_do_not_call`. Notable: `003_compliance_engine`,
`004_appointment_dispositions`, `008_capacity_engine`, `009_sms_queue`,
`011_voice_calling`, `019_deal_recording`, `023_agent_npn`, `024_sms_do_not_call`.

---

## 4. Compliance data model (the area most recently worked on)

`app/models/compliance.py`:
- **AgentStateLicense** — `state_code`, `license_number`, `effective_date`, `expiration_date`, `status`. (licenses DO have an effective date)
- **AgentCarrierAppointment** — `carrier_name`/`carrier_key`, `state_code`, `appointment_number`, `effective_date` (**optional** — defaulted to today by the service), `expiration_date`, `status`. No blocking unique constraint; one row per (agent, carrier, state).
- **ComplianceEvent**, **Deal** (+ products JSONB, recording_id), **DealApprovalLog**, **DealRecording**.

Endpoints: `POST /compliance/carrier-appointments` (admin; one call per state),
`GET /compliance/me/profile` (agent sees own licenses+appointments),
`GET /compliance/agents/{id}/profile` (admin). Schema:
`schemas/compliance.py` → `CarrierAppointmentCreate.effective_date: Optional[date]`.

Frontend: managed in `settings.html` (multi-state add, no effective date for
carriers; single-state dropdown for licenses). `compliance.html` is a separate
console intentionally left on the old model.

---

## 5. Workers & AI

`apps/workers/workers/tasks/`: `sms.py` (outbound SMS processing), `ai.py`,
`analytics.py`, `booking.py`, `followups.py`, `reminders.py`, `ingestion.py`,
`pacing.py`, `system.py`.

`apps/ai-engine/` is a Docker wrapper; the AI logic lives in
`apps/backend-api/app/ai/` (`conversation_engine/`, `services/` orchestrator + LLM
inference (Ollama) + provider, `routers/` webhooks).

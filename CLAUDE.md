# CLAUDE.md — IAG / Launchpad call center

Insurance call-center portal. Repo `Buissn885/Launchpadacacallcenter`.
The older, much longer version of this file (pay rules, inbox, compliance, Telnyx
internals, …) is in git history: `git show de7c417:CLAUDE.md`.

```
apps/frontendall/  static portal pages (no build)        apps/sms-ui/  React SPA -> builds into frontendall/sms/
apps/backend-api/  FastAPI + Postgres (Alembic)          apps/hs-extension/  HealthSherpa Chrome extension
```

**Must-follow:** never edit the env-detection lines at the top of
`apps/frontendall/services/api.js` · `git pull --rebase origin main` before any
push, and push only when asked · after editing `apps/sms-ui/src` run
`cd apps/sms-ui && npx vite build` · changing a shared portal JS file means
bumping its `?v=N` in every `apps/frontendall/*.html`.
Local run with the real backend: `.localpreview/run-local.sh` →
http://127.0.0.1:5500/login.html (admin@local.test / Admin123!, agent@local.test / Agent123!).

## Roles

- Only **two roles are assigned now: `agent` and `super_admin`.** Old users may still
  have `lead / manager / head / tenant_admin` — they keep working and show as
  "(legacy)". Don't add roles and don't redesign the role system.
- `dev` = developer, passes every gate.
- Role is `localStorage.ebRole` in the browser; the server checks it again with
  `require_role(...)`. Front-end gates: `apps/sms-ui/src/lib/auth.ts`
  (`canSeeQueue`, `canSeeManager`, `isAdminClass`, `isOwner`) and
  `apps/frontendall/prefs-extras.js`.

| Page | Who |
|---|---|
| Lead Manager `/sms/#/queue` | agent (+ head, dev) |
| SMS Manager `/sms/#/manager` | super_admin (+ legacy manager/admin roles, dev) |
| Auto Dialer `/sms/#/dialer` | same as SMS Manager |
| HealthSherpa Auth `/sms/#/healthsherpa` | everyone signed in (server scopes rows) |
| └ its Text campaigns tab | super_admin manages; managers + the campaign's HealthSherpa agent can view |
| Text campaigns, Expenses | super_admin (+ dev) |

Sidebar links come from THREE places — change all that apply: static `.html`
pages, `prefs-extras.js` / `services/error-boundary.js` (injected links), and
`apps/sms-ui/src/components/PortalShell.tsx`.

## Lead Manager (agents)

`pages/SmsQueue.tsx`, backend `app/sms_queue/`.
- The lead pool is `sms_leads` with status `QUEUED`. An agent presses **Join
  queue**; a lead is offered in a popup (Accept / Pass), then the agent works it
  and marks a disposition (sale, appointment, not interested, voicemail…).
- **Ready for calls** (second switch) opens the agent's browser phone line so the
  Auto Dialer can connect live callers to them. Join queue alone = pool only.
- **Dial** button opens the manual dial drawer (call any number from the browser).

## SMS Manager (super_admin)

`pages/SmsManager.tsx`, `components/LeadsTools.tsx`, backend `app/sms_queue/`, `app/ingestion/`.
- Watch agents (online, on call, current lead), the pool, and reports.
- **Direct to agent pool** upload (`POST /sms/pool/upload`): CSV rows go straight
  into the pool as `QUEUED` — **no text is sent**; agents call them. Each upload is
  a batch that can be removed. Limits 20,000 rows / 30 MB.
- The old **Sinch / Engage Cloud campaigns** (`/ingestion/campaigns`) still exist
  but are not the active texting path — Telnyx is.

## Auto Dialer (super_admin) — Telnyx

`pages/AutoDialer.tsx`, backend `app/voice_dialer/` (Telnyx only).
- **Text campaigns** (`campaigns.py`, `components/TextCampaigns.tsx`): upload CSV,
  one message, Start. Paced to protect our 10DLC numbers (one text per number every
  few seconds, daily cap, client-timezone hours, DNC checked, STOP footer,
  auto-pause on carrier blocks). One text per person per campaign.
- **Replies** (`service.on_inbound_sms`, `replies.classify`): YES / positive →
  dial list (`READY_TO_CALL`) if the dialer is on, else the regular pool as HOT.
  STOP / not interested / abusive / wrong number → Do-Not-Call. A reply to a
  **HealthSherpa** text campaign is taken out first and never reaches this logic
  (see below).
- **Calling:** only a live person is connected to a free agent who is "Ready for
  calls"; voicemail is dropped and retried. 8 am–8 pm client time, ≤3 tries / 24 h.
- Also on this page: live calls + **Listen / whisper**, Called list with
  recordings, Opted-out list, Inbox (all texts + calls per phone), Client owners
  and inbound-call rules (owner first, hold queue, callback), dialing rules.
- Never run the synchronous dialer DB code directly in an `async` route — use the
  threadpool (`_run`). Doing that once froze the whole app.
- Without Telnyx keys (non-production) a simulator plays the carrier for testing.

## HealthSherpa authorizations — `app/hs_auth/`, `apps/hs-extension/`

Gets a client's 2027 CMS authorization in HealthSherpa for an agent.
HealthSherpa has **no API** and blocks non-US access, so a **Chrome extension**
does the agent's clicks inside a logged-in HealthSherpa tab.
- Page `pages/HealthSherpa.tsx`: upload a sheet (.xlsx/.csv: first name, last
  name, DOB, state, optional phone/email — cleaned in `sheet.py`: no punctuation,
  MM/DD/YYYY, state code; bad rows = "Fix data"), press **Start**, confirm consent.
- Extension (`hs.js`): Search Marketplace → name/DOB/state + consent boxes →
  Plan Year 2027 row with matching DOB → Add to clients → Include consent →
  Text link → waits (Refresh status, one resend on expiry) → Finish. It finds
  everything by **visible text**, not screen position, and stops with a reason
  on anything unexpected (no 2027 row, two matches, phone differs from the sheet,
  already in clients, logged out → waits for login).
- The CLIENT authorizes on their own phone — never automated.
- Statuses: PENDING, IN_PROGRESS, LINK_SENT, AUTHORIZED, NOT_FOUND, NEEDS_REVIEW,
  EXPIRED, FAILED, CANCELLED, INVALID. Limit `HS_AUTH_DAILY_LIMIT` (20 per agent
  per Eastern day), one client at a time. The extension reports with a one-job
  token, never the portal login.
- Build the agent zip: `scripts/build-hs-extension.sh` (live host
  `portal.iagplans.com`, localhost stripped). Bump `version` in `manifest.json`
  AND `common.js` on every change.
- Test: `.localpreview/fake-healthsherpa/server.py 8443` + `.localpreview/verify-hs-extension.mjs`.
  The real HealthSherpa pages have not been seen yet — watch the first live run.

## HealthSherpa text campaigns — `app/hs_auth/campaigns.py` (migration 068)

The **Text campaigns** tab on the HealthSherpa page (`components/hs/HsCampaigns.tsx`).
**Completely separate from the Auto Dialer's text campaigns** — own tables
(`hs_text_campaigns`, `hs_campaign_texts`, conversation columns on
`hs_auth_requests`), and its replies are **never dialed, pooled or made into a lead.**
No Google Sheet: every client's details, texts and status are on the page, with
**Export to Excel (CSV)** for the closer.
- Flow per client (`hs_auth_requests.convo`): blast → any non-negative reply →
  Stan's approved follow-up, sent **verbatim** (`FOLLOWUP_TEXT` — an approved
  exception to "one text per person", HealthSherpa campaigns only) → wait 10 min
  (`NO_ANSWER` after; a late answer still counts) → any non-negative answer =
  "Company they're renewing" → queued (status `PENDING`) → **auto mode** runs it
  → link sent → ONE reminder at 7 min (OFF by default; the text is a placeholder
  until approved 10DLC wording is pasted in Settings) → `EXPIRED` at 15 min, no
  resend unless the campaign setting says so. STOP / no / abuse / wrong number at
  ANY point → Do-Not-Call (same reasons as the dialer), texts cancelled, any
  queued or running HealthSherpa job cancelled. "Fix data" rows aren't texted.
- **Reply routing:** `voice_dialer/service.on_inbound_sms` calls
  `hs_campaigns.on_inbound` FIRST. It owns the reply when our newest text to that
  number (last 30 days) was a HealthSherpa campaign text; a newer dialer text
  takes the number back. Not wrapped in try/except on purpose — falling through
  to the dialer would CALL someone who only agreed to a link.
- **Sender is shared, not copied:** `voice_dialer/campaigns.py` exposes the safety
  pieces (`senders`, `sender_for`, `claim_number`, `in_hours`, `dnc_digits`,
  `sent_today` — which counts these texts too — `daily_cap`, `failure_action`).
  Blast, reminder and auto jobs only run 8 am–8 pm client time; the follow-up goes
  any hour (it answers their text). Pause stops blasts + reminders; Stop cancels
  unsent blasts; conversations already started always finish.
- **Auto mode = the portal tab drives it.** The campaign's HealthSherpa agent
  (Stan — clients authorize HIS account, so he closes the sale) leaves the page
  open with **Auto mode** on; `components/hs/useAutoMode.ts` calls
  `POST /hs-auth/auto/next` every 5 s and hands the job to the extension like
  Start. The server still enforces the daily limit, one client at a time and
  hours, and writes the consent to the job history (the client's YES + answer
  with times). Extension ≥ 1.1.0 shows auto mode in its popup (Turn off there too).
- Who: view = managers + the campaign's HealthSherpa agent; create / start /
  pause / stop / settings = super_admin.
- Timers: Celery beat `hs-campaign-tick` (5 s); locally the page's poll ticks.
- Tests: `tests/unit/test_hs_text_campaigns.py` (uses local Postgres, rolled
  back). End to end: `.localpreview/verify-hs-campaigns.mjs` — start the API with
  `TELNYX_API_KEY= TELNYX_TEXML_APP_ID= TELNYX_FROM_NUMBERS=` so the simulator
  sends nothing real (the local `.env` has REAL Telnyx keys).

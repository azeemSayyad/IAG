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
| └ its Text campaigns tab | super_admin, admin (tenant_admin/admin) and head manage; managers + the campaign's HealthSherpa agent can view |
| Text campaigns, Expenses | super_admin (+ dev) |

Sidebar links come from THREE places — change all that apply: static `.html`
pages, `prefs-extras.js` / `services/error-boundary.js` (injected links), and
`apps/sms-ui/src/components/PortalShell.tsx`.

## Lead Manager (agents)

`pages/SmsQueue.tsx`, backend `app/sms_queue/`.
- The lead pool is `sms_leads` with status `QUEUED`. An agent presses **Join
  queue**; a lead is offered in a popup (Accept / Pass), then the agent works it
  and marks a disposition (sale, appointment, not interested, voicemail…).
- **One system at a time** (owner, 2026-11-17): the agent picks **SMS leads** (pool
  popups, `POST /sms/queue/join`, sets `voice_ready` off) or **Auto dialer** (browser
  phone line, `POST /voice/dialer/ready`) — a chooser while Offline, a two-way switch in
  the top bar after (locked mid-lead / mid-call). `voice_ready` agents are NEVER offered
  a pool lead, dialer on or off (`queue_service._try_assign`). Asked again on every
  fresh login: `lib/workMode.ts` `freshStart()` takes an agent still online from an
  earlier session offline before any popup can show (not if mid-lead / on a call);
  the choice is kept in `sessionStorage` for that tab.
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
  auto-pause on carrier blocks). One text per person per campaign. The message box has the
  same approved library as HealthSherpa (`TemplatePicker hsOnlyWarnings={false}` — its
  warnings are HealthSherpa-only; the dialer does call).
- **Inbound call routing** tests: `tests/unit/test_inbound_calls.py` (real DB, rolled back:
  closed hours, owner free/busy/away, nobody taking calls, DNC, held caller served, press 1).
  `.localpreview/verify-inbound-routing.mjs` / `verify-client-owners.mjs` need helper files
  from an old scratchpad and no longer run. Dialer e2e scripts assume an EMPTY dial list —
  leftover local READY_TO_CALL test leads make them check the wrong lead.
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
- **Learned from the first live run (2026-11-13):** `healthsherpa.com/` sends a
  logged-in agent to the PUBLIC consumer page (no "Search Marketplace" menu), so
  the extension opens `/person_search?use_case=fetch&_agent_id=<id>` directly
  (the agent id, e.g. `stanley-loseille-pklxww`, is learned from any HealthSherpa
  URL and kept in `chrome.storage.local`). Setting an input's `.value` by script
  looked filled but HealthSherpa's form threw the names away — fields are typed
  with `document.execCommand("insertText")` (the browser's own, trusted typing)
  and re-checked right before Search. Florida can be preselected. On any page
  that doesn't look as expected the extension sends a value-free description of
  the form (`diag`) that shows in the client's History ("diagnostic") — use it to
  fix selectors.
- Test: `.localpreview/fake-healthsherpa/server.py 8443` + `.localpreview/verify-hs-extension.mjs`
  (`ONLY=Naji` runs one client). The fake copies the behaviour above.

## HealthSherpa text campaigns — `app/hs_auth/campaigns.py` (migration 068)

The **Text campaigns** tab on the HealthSherpa page (`components/hs/HsCampaigns.tsx`) —
the page's DEFAULT tab; the other one is "Manual start (no texting)".
**Completely separate from the Auto Dialer's text campaigns** — own tables
(`hs_text_campaigns`, `hs_campaign_texts`, conversation columns on
`hs_auth_requests`), and its replies are **never dialed, pooled or made into a lead.**
No Google Sheet: every client's details, texts and status are on the page, with
**Export to Excel (CSV)** for the closer.
- Flow per client (`hs_auth_requests.convo`): blast → any non-negative reply →
  the follow-up — Stan's approved `FOLLOWUP_TEXT` by default, **editable per campaign**
  (`hs_text_campaigns.followup_text`, migration 073, New campaign + Settings, "Reset to
  approved text"; NULL = default) — an approved exception to "one text per person",
  HealthSherpa campaigns only → wait 10 min
  (`NO_ANSWER` after; a late answer still counts) → any non-negative answer =
  "Company they're renewing" → queued (status `PENDING`) → **auto mode** runs it
  → **MAIN text first** (owner's Authorization Texts spec 2026-11-17, `auth_texts.py`: "You will
  get another text that starts with: "Your agent {agent_name} with Insurance Alliance
  Group..." 1. Open… STOP to opt out", line breaks kept) — the extension reports `pre_link`, the
  server sends it (`campaigns.pre_link`) and answers `wait_s` (`HS_PRE_LINK_WAIT_SECONDS`,
  default 45; spec 30-60) — then the extension presses "Text link" → ONE reminder at 10 min
  (approved spec wording, ON by default, NO STOP line, also after auto mode moved on while the
  15-min link lives, never for email clients; migration 076) → **at 10 min the extension
  stops waiting** (owner, 2026-11-15: one client at a time, max 10 min) and reports
  `UNCONFIRMED` ("Check in HealthSherpa" — the link still works until HealthSherpa's
  15 min; a person checks and presses **Mark authorized**, `POST
  /campaigns/clients/{id}/authorized`) → the next client starts. The job carries
  `link_wait_minutes` (= campaign `expire_minutes`, 3–15, migration 074 sets 10/5);
  the tick only backstops it (+2 min) if the extension went quiet. No resend unless
  the campaign setting says so. More throughput = more HealthSherpa agent accounts,
  never parallel clients. STOP / no / abuse / wrong number at
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
  **Hours (owner's rule, 2026-11-13): only the BLAST waits for 8 am–8 pm client
  time.** Once a client replies they're talking to us, so the follow-up, the
  HealthSherpa lookup + link, the reminder and manual texts go immediately, any
  hour — speed is what gets a 15-minute link authorized. Pause stops blasts + reminders; Stop cancels
  unsent blasts; conversations already started always finish.
- **Auto mode = the portal tab drives it.** The campaign's HealthSherpa agent
  (Stan — clients authorize HIS account, so he closes the sale) leaves the page
  open with **Auto mode** on; `components/hs/useAutoMode.ts` calls
  `POST /hs-auth/auto/next` every 5 s and hands the job to the extension like
  Start. The server still enforces the daily limit, one client at a time and
  hours, and writes the consent to the job history (the client's YES + answer
  with times). Extension ≥ 1.1.0 shows auto mode in its popup (Turn off there too).
- **Manual texts + "Needs reply"** (migration 069): the client window is one
  conversation (texts + key HealthSherpa moments in time order) with a message
  box — `send_manual` → kind `MANUAL`, `sent_by`, same sender/number, DNC
  blocked. **Owner's rule: only HESITATION pauses** (`looks_hesitant`: "how did
  you get my number", "not right now", "already have an agent", "why do you
  need this"…) → `needs_reply`, not queued (at the blast stage: no follow-up).
  Any other reply moves forward even with a question in it — "Oscar, is this
  free?" queues HealthSherpa with company "Oscar" (`company_from`) AND sets
  `needs_reply` for a person to answer later; any non-ack text after they're
  queued also sets it. Plain no / STOP / scam = Do-Not-Call as always. Sending
  a manual text or "No reply needed" clears it. "Needs reply" outranks every
  stage but Opted out (one count per client) and has a red badge in the header.
- **Sending number by state:** `vc.sender_for(..., state=)` picks one of our
  numbers whose area code (`app/core/area_codes.py`) is in the client's state
  (a Texas list goes out from Texas numbers); no number for that state = any
  number. The number a client already heard from always wins (sticky).
- **Re-text no-replies** (`retext`, kind `RETEXT`): one more text to clients
  still at `BLAST_SENT` who never texted back — not within 1 h of their last
  text, at most `RETEXT_MAX` (3) per client per 30 days (`RETEXT_WINDOW` — then
  it resets; only opting out ends it), same number as before, honours
  Pause/Stop and their texting hours like the blast.
- **Automation status panel** (`components/hs/HsAutomation.tsx`, `GET /hs-auth/campaigns/status`
  → `automation_status`): a SLIM bar at the top of the tab, polled every 5 s (owner
  rejected a big tile dashboard as "messing the screen"): one health line (Working /
  Needs attention / Blocked · auto mode · texting hours); alerts ONLY for things a person
  must act on (LOGIN, AUTO_OFF with clients waiting, STUCK, carrier PAUSED, CAP, LIMIT,
  NEEDS_REPLY) each with one button; clickable CHIPS (No reply, Replied YES — stage
  `YES` = `yes_at` set, any stage — HealthSherpa queue, Working now, Link sent, Opted
  out…) that open a drawer list (`StageListDrawer`, export per list; Working now opens the
  lead itself); today as a funnel; Details ▾ for hours per state / extension / problems.
  Login wait = a popup with sound + desktop notification on any tab of the page.
  Auto mode is "seen" via `hs_auto_heartbeats` (migration 070): every `/auto/next`
  poll beats (extension version, tab hidden); `/auto/off` on turning it off; not seen
  for 150 s = off (closed tab / asleep PC).
- **Text one client** (`POST /hs-auth/campaigns/add-client` → `add_client`): type in ONE
  client (name, DOB, state, mobile; same cleaning as a sheet) → a queued `BLAST` text with
  its own (editable) message, then the normal flow. Goes into a chosen campaign or the shared
  "Manual entries" campaign (one per HealthSherpa agent, created on first use). The first
  text still honours their 8 am–8 pm hours, Pause and DNC; the bulk blast skips rows that
  already have their own queued BLAST, so nobody gets it twice.
- **Archive** (its own button, NO dialog, ⓘ explains it; migration 072 `archived_at`): ONLY
  hides the campaign card from the list (owner: "just to make the UI clean") — it keeps
  texting, replying, running HealthSherpa, counting; its clients stay in the table.
  "Show archived (N)" (top right of the campaigns box) shows them; Unarchive brings one back.
- **Remove** (campaign row, managers): a draft is deleted; a campaign that texted is
  soft-removed (`hs_text_campaigns.deleted_at`, migration 071) — stopped, queued texts and
  HealthSherpa jobs cancelled, campaign + clients hidden. Its texts are KEPT on purpose so
  a late reply is still HealthSherpa's (`hs_saved_removed`: saved, nothing sent, never
  dialed; STOP still = DNC). Never hard-delete a sent campaign's texts.
- **File check** (`POST /hs-auth/campaigns/check` → `check_file`, nothing saved): runs as
  soon as a file is chosen — mandatory columns First/Last Name, DOB, State, **Phone**
  (a file without Phone is refused, also in `create`), ready / fix-data / no-phone /
  duplicate / DNC counts and the problem rows. Run + Save as draft stay disabled until OK.
- **Message library** (`components/hs/hsTemplates.ts` + `HsTemplatePicker.tsx`): the
  approved "Final Texts — Double Filter Checked" **v3** texts (ACA/Tax open "[Name],", Medicare
  "Hi [Name],", brand + STOP at the END; no picture/MMS) + two texts Stan added 2026-11-17
  marked NOT FILTER-CHECKED; styled like the PDF; picking one fills the editable message box
  (New campaign + Re-text, and the Auto Dialer). Plain characters only — no curly quotes.
- **{agent_name} rule** (`auth_texts.agent_name_for_sms`): <= 20 chars, else whole words in 17
  + "..." (three dots, never "…"), accents stripped; a non-GSM or > 306-char text falls back to
  "Your agent... with Insurance Alliance Group...". Name = the campaign's HealthSherpa agent.
- **Spam pause:** ONE carrier spam refusal (40002/40003) doesn't pause a HealthSherpa
  campaign (owner: "if messages are going, no need to show carriers blocking") — only
  `SPAM_PAUSE_MIN` (5) of the last `SPAM_WINDOW` (20) texts. The reason shows only while
  PAUSED; Stop clears it. (The Auto Dialer's own campaigns still pause on the first.)
  The pause banner has **✏️ Change message** → Settings (approved-text library + length,
  "Goes to the N not texted yet") → **Save & resume** (disabled until the message changes).
  Unsent blasts use the campaign's CURRENT template at send time.
- **Insults = abuse** (owner, 2026-11-16): `replies.ABUSE_PATTERNS` (shared by the dialer and
  these campaigns) also catches insults/slurs ("ur gay", stfu, dumbass, piss off, kys…) as
  whole words → Do-Not-Call. Tests list ordinary words that must NOT match.
- **Wrong phone → email** (owner, 2026-11-16; migration 075, extension 1.1.5): in a campaign job
  (`email_fallback`), when HealthSherpa's mobile differs from the sheet (or is missing) the
  extension copies the email from HealthSherpa's "Email link to …" button and reports
  `EMAIL_CHECK` (+`hs_email`; no email = NEEDS_REVIEW, skipped) and auto mode moves on. The tick
  texts `EMAIL_ASK` ("is <email> still your current email?" — conversational, owner: rare, no
  10DLC template); YES within `EMAIL_WAIT` (10 min) → `EMAIL_NOTE` ("check <email> … from
  HealthSherpa, sent for <campaign's HealthSherpa agent>") + status PENDING with
  `send_by="email"` → the next run opens the client via **View in Dash → profile "Resend
  authorization" → Email link** (verifies the same email). A plain "no" = skipped (NOT DNC);
  STOP / abuse still DNC; a DIFFERENT email = skipped + needs_reply (changing contact info is a
  ~30-min human job — owner); no answer = NEEDS_REVIEW `email_unconfirmed`, a later YES still
  counts. Email only — the phone is never changed. The second run counts toward the daily
  limit. Real HealthSherpa's profile button for the email run is UNVERIFIED (the fake has
  "Resend authorization"); if it differs the run stops with a `diag`. Fake: last name `Mailer`.
- Extension 1.1.7: in a campaign job, `textFirst()` before "Text link" (asks the portal for the
  MAIN text, counts down `wait_s`). Local e2e: run the API with `HS_PRE_LINK_WAIT_SECONDS=3`.
- Extension 1.1.6 (seen live 2026-11-16): HealthSherpa's waiting window says **"Refresh"** (not
  "Refresh status") and hides "Resend by email / text" behind **"Show resend options"** — the
  extension accepts both labels and opens the options first. The fake copies this.
- Extension 1.1.4: **ONE HealthSherpa tab** for every client — reuses the last job's tab,
  else any open HealthSherpa tab (the agent's logged-in one), else opens one; extra
  HealthSherpa tabs the extension itself opened (`iagOpenedTabs`, session) are closed, the
  agent's own never; the working tab stays open between clients.
- Extension 1.1.3: HealthSherpa's **State** field is a type-to-search combobox — the
  extension types the name, keeps the list open, clicks the option (keyboard fallback,
  then the 2-letter code). The fake has all three kinds: `FAKE_STATE=combo|datalist|select`.
- Who: view = managers + the campaign's HealthSherpa agent; create / start /
  pause / stop / settings / manual texts = super_admin, admin, head (`CAMPAIGN_ROLES`;
  the campaign's HealthSherpa agent may also text).
- Timers: Celery beat `hs-campaign-tick` (5 s); locally the page's poll ticks.
- Tests: `tests/unit/test_hs_text_campaigns.py` (uses local Postgres, rolled
  back). End to end: `.localpreview/verify-hs-campaigns.mjs` — start the API with
  `TELNYX_API_KEY= TELNYX_TEXML_APP_ID= TELNYX_FROM_NUMBERS=` so the simulator
  sends nothing real (the local `.env` has REAL Telnyx keys).

# CLAUDE.md — Launchpad Call Center

Guidance for AI agents (and humans) working in this repo. Read this first.

## What this is

"Launchpad" / "Insurance Alliance Group" — an insurance call-center portal (leads, deals, SMS
outreach, appointments, compliance/licensing). npm monorepo. GitHub:
`Buissn885/Launchpadacacallcenter`.

```
apps/
  frontendall/   Static HTML/CSS/JS portal (NO framework, NO build step). Each page is a
                 standalone .html with inline <style> + <script>. Served as-is.
  sms-ui/        React + Vite SPA ("SMS" workspace: Queue, Manager, Monitoring, Sales
                 Dashboard). Builds INTO apps/frontendall/sms/ (see "SPA build" below).
  backend-api/   FastAPI + SQLAlchemy + Alembic (Postgres). Routers under app/<domain>/.
  ai-engine/     AI services.
  workers/       Background workers (Celery-style tasks, e.g. SMS).
.localpreview/   Git-ignored local preview server + headless-Chrome verification scripts.
scripts/         validate-frontend.mjs and ops scripts.
```

There are effectively **two frontends**: the static `frontendall` pages and the
`sms-ui` React SPA. A change to a portal page may need to be made in BOTH, plus
the SPA must be rebuilt. See "Gotchas".

## Running locally (no backend required)

The normal way to see the app is the **local preview server** — it serves
`apps/frontendall` on http://127.0.0.1:5500 and injects two git-ignored shims so
it works with NO backend:
- `services/__preview-login.js` — lets the login page sign in locally (any
  password). The **username substring picks the role**: `admin`→tenant_admin,
  `super`→super_admin, `head`→head, `manager`→manager, `lead`/`team`→lead,
  anything else→agent. E.g. log in as `admin@launchpad.com` to get the admin views.
- `services/demo-mock.js` — mocks every REST endpoint with sample data.

```bash
python .localpreview/serve.py     # → http://127.0.0.1:5500  (then open /login.html)
```

Do NOT use a plain `python -m http.server 5500` for the portal — it skips the
shims, so the auth guard hits the (absent) backend and bounces you to login.

Running the real backend locally needs a Python venv at `apps/backend-api/venv`
(NOT checked in). On the dev Mac it exists (created with
`uv venv venv --python 3.12 && uv pip install --python venv/bin/python -r requirements.txt`),
with brew `postgresql@15` + `redis` and a git-ignored `apps/backend-api/.env`
pointing at `postgresql://sayyadazeem@localhost:5432/launchpad`. The preview
server has a proxy mode for this — **no mocks, no demo data, real login**:

```bash
.localpreview/run-local.sh      # migrates, starts uvicorn :8000 + serve.py --backend → http://127.0.0.1:5500/login.html
# local accounts: admin@local.test / Admin123! · manager@local.test / Agent123! · agent@local.test / Agent123!
```

Use this (not the mock) to verify anything that writes to the database before
pushing. Without `--backend`, serve.py is the mock/demo-data preview described
above.

## Build / validate / test

```bash
# Static frontend — no build. Just validate:
node scripts/validate-frontend.mjs        # checks broken local asset refs + mojibake

# SMS SPA (after editing anything in apps/sms-ui/):
cd apps/sms-ui && npx vite build          # outputs to ../frontendall/sms/ (index.html + assets/index-<hash>.js/.css)

# Backend (needs venv):
npm test                                  # pytest (apps/backend-api)
```

## Hard rules (do not break)

1. **NEVER edit the env-detection logic in `apps/frontendall/services/api.js`
   (~lines 4–12).** It auto-switches the API base URL (file:// → `127.0.0.1:18000`;
   port 13000 → `:18000`; live → `location.origin`). This is what keeps local
   edits from breaking the live backend connection. The user has said "don't
   touch api.js ever."
2. **`main` is shared. ALWAYS `git pull --rebase origin main` before pushing.**
   Commit/push only when asked. Deploy is automatic on push (Railway/Netlify/Docker).
3. **Cache-busting:** portal pages load scripts with a version query, e.g.
   `brand.js?v=1`, `prefs-extras.js?v=30`, `error-boundary.js?v=20`, `api.js?v=10`.
   `app-gate.js?v=2` / `announcements.js?v=2` are versioned at their injection
   site inside `prefs-extras.js`, not in the HTML. If you change one
   of these JS files you MUST bump its `?v=N` across **all** `apps/frontendall/*.html`
   or deployed/cached browsers keep serving the old file. (One-liner:
   `perl -pi -e 's/prefs-extras\.js\?v=5/prefs-extras.js?v=6/g' apps/frontendall/*.html`)
4. **SPA rebuild:** editing `apps/sms-ui/src/**` does nothing live until you
   `vite build`. The compiled bundle (`apps/frontendall/sms/assets/index-<hash>.js`)
   is what the site loads. After building, commit the new bundle + updated
   `sms/index.html` and delete the old hashed assets.

## Architecture notes / gotchas

### Colour — `apps/frontendall/brand.js` is the SINGLE SOURCE OF TRUTH
**Never hardcode a brand colour anywhere.** `brand.js` holds three hexes
(`BRAND.accent` / `accent2` / `accentHover` — currently a navy→sky blue) and
DERIVES everything else from them at runtime, writing it onto `<html>` as custom
properties: the accent family + `r,g,b` triplets, the page gradient and corner
glow, and two ramps whose names encode HSL lightness —
`--n99 … --n68` (neutral surfaces) and `--a98 … --a92` (accent-tinted washes),
each also as `--n95-rgb` / `--a93-rgb` for `rgba(var(--n95-rgb),0.5)`.
The ramps take their HUE from the active accent, so switching theme recolours
every surface, and **rebranding is a one-line change in `brand.js`.**

- It is a blocking `<script src="brand.js?v=1">` first in every page `<head>`
  (and in the SPA's `index.html` as `/brand.js`), so first paint is branded.
- Consume it as `var(--accent)`, `rgba(var(--accent-rgb),0.12)`, `var(--n95)`, …
  `<canvas>` and SVG presentation attributes can't resolve `var()` — use
  `EB_BRAND.theme().a`, `EB_BRAND.rgba(0.2)` or `EB_BRAND.css('--n95')` there
  (in the SPA: `brandColor('--accent')` from `lib/theme.ts`).
- `prefs-extras.js`, `app-gate.js` and `sms-ui/src/lib/theme.ts` used to each
  carry their own copy of the theme map — they now all forward to `EB_BRAND`.
- Theme keys are `brand | forest | indigo | rose | slate | amber` (default
  `brand`). The retired orange `warm` key is aliased to `brand` for users whose
  localStorage still holds it — see `LEGACY` / `normalize()` in brand.js.
- Colours deliberately NOT derived from the brand: status (`--up/--down/
  --pending`, hot-lead red-orange), the product-segment palette (ACA gold /
  Dental / Vision), leaderboard rank medals, `wizard.css`'s per-chapter accents
  (aca green, dv purple, auto orange) and `sms-ui/.../charts/jewel.ts`.

### Logo — `apps/frontendall/assets/`
`logo-source.png` is the master artwork; `logo.png` (full lockup), `logo-mark.png`
(IAG monogram), `favicon.png` and `apple-touch-icon.png` are derived from it.
`assets/README.md` has the exact regeneration script and crop boxes.
- Sidebar / wizard / mobile gate use the **monogram** — the full lockup's
  "ALLIANCE GROUP" line is unreadable below ~120px wide. The login card uses the
  full lockup.
- The SPA references them root-absolute (`/assets/logo-mark.png`); static pages
  use `assets/...`.
- The artwork is navy on transparent, so dark mode puts it on a white rounded
  plate — it is never recoloured.
- Gotcha: the wizard's brand link is `<a class="ch-brand" href="appointments.html">`,
  and prefs-extras' role gate hides `a[href="appointments.html"]` for admin/head.
  Those selectors carry `:not(.ch-brand)` so the gate doesn't eat the logo.

### Dark mode
Applied globally by **`apps/frontendall/prefs-extras.js`**, which injects a large
`<style>` of `html[data-mode="dark"] …` rules and toggles `html[data-mode]` from
`localStorage.ebMode`. It recolors `--text*/--border*` vars and common components.
Bespoke per-page surfaces (custom cards, icon chips) are NOT auto-covered — they
must be added to the prefs-extras dark block or they keep their light styling in
dark mode. The **sms-ui SPA has its own** dark mode (Tailwind / its index.css) —
audit BOTH codebases for any theme change.

### Sessions — BOTH API layers must refresh the token
Access tokens are short-lived (`JWT_EXPIRES_IN` / `JWT_ACCESS_TOKEN_EXPIRE_MINUTES`,
30 min by default); refresh tokens last 7 days. There are **two** API layers and
each needs its own refresh-on-401, or users get silently thrown back to the login
page mid-session once the access token ages out:
1. `apps/frontendall/services/api.js` — `handleRefresh()` (always had it).
2. `apps/sms-ui/src/lib/api.ts` — `authedFetch()`, added later; it POSTs
   `/auth/refresh` via `refreshAccessToken()` in `lib/auth.ts`, replays the
   request, and only clears the session + redirects if the refresh itself fails.
   `refreshAccessToken()` keeps ONE in-flight call, because a page load fires
   ~8 requests at once and each would otherwise trigger its own refresh.
Anything doing a raw `fetch` must go through `authedFetch`/`apiUpload`, not
`fetch` + `getAccessToken()` — that was how the multipart uploaders each grew
their own "401 → /login.html" line. BOTH socket layers pass `auth` as a callback
(`lib/socket.ts` and `services/api.js`) so reconnects re-read the current token
rather than the one captured at page load.

**Only a REJECTED refresh may end a session.** This is the rule that keeps users
from being thrown out at random, and every layer now obeys it:
  * `POST /auth/refresh` answering **401/403** = the 7-day refresh token is
    really dead -> clear tokens, go to login.
  * ANY other failure (**502/503 while the API restarts or redeploys**, 429, a
    network blip, an unparseable body) is TRANSIENT -> keep the tokens, fail
    just that one request. Treating these as "session over" is what logged
    people out mid-shift; it hit agents and head managers hardest simply
    because their pages poll (the 6s queue-offer poll, inbox badge,
    notifications) so a blip was far likelier to land on one of their requests.
  * A 401 that survives one refresh+replay is that ENDPOINT's verdict, not a
    dead session — surface the error, never refresh in a loop (`handleResponse`
    takes a `retried` flag; `authedFetch` replays exactly once).
  * A 401 carrying a token that is no longer the stored one just lost a race
    with another request's refresh — replay it with the current token instead
    of starting a second refresh (this is why exactly ONE `/auth/refresh` goes
    out per page load, not one per in-flight request).
  * `error-boundary.js`'s `safeInit()` session probe follows the same rule: it
    only bounces to login when the tokens are already gone or the probe itself
    came back 401.
Logging out must clear `access_token` + `refresh_token`, not just `ebRole`
(`ebClearSession()` in prefs-extras.js).
Regression tests (both git-ignored):
`.localpreview/verify-session-refresh.mjs` and, with backend fault injection
across roles, `.localpreview/verify-session-resilience.mjs`.

### The sidebar has THREE sources — check all three for any nav change
1. Static `<a class="sb-item" href="…">` blocks hardcoded in each `.html` page.
2. **`prefs-extras.js`** rewrites the nav at runtime: `inject*Link()` adds items
   (e.g. it used to inject Dispositions on every page), `gate*()` + CSS hide items
   by role (`html[data-role="…"]`), and an `ORDER` array reorders them.
3. **`apps/sms-ui/src/components/PortalShell.tsx`** — `WORKSPACE_LINKS` /
   `PORTAL_LINKS` arrays define the React SMS shell's sidebar (rebuild SPA to change).
Removing/adding a nav item usually means editing the static HTML **and**
prefs-extras **and** PortalShell, then bumping the cache version + rebuilding the SPA.

### Roles
`localStorage.ebRole` ∈ `agent | lead | manager | head | tenant_admin |
super_admin | dev`. Views/nav are gated by role in both prefs-extras (static) and
PortalShell/`lib/auth` (SPA).

## Compliance domain (licenses & carrier appointments)

Managed in the portal at **`settings.html` → "Licenses & Appointments"** (agent
self-service view + admin view that manages any agent). Backend under
`apps/backend-api/app/compliance/` (router/services/schemas) with models in
`app/models/compliance.py`.

- **State licenses** HAVE an effective date + expiration; state is a single-select
  dropdown of 2-letter codes.
- **Carrier appointments** have **NO effective date** (that concept is licenses-only)
  — only an expiration. State is a **multi-select** dropdown: picking N states on
  the admin form creates one appointment record per state (one POST each), keyed by
  (agent, carrier, state). Dedupe is state-scoped; there is no blocking unique
  constraint. `CarrierAppointmentCreate.effective_date` is Optional and the service
  defaults it to `today` (NOT NULL column, no migration). The agent sees their own
  appointments via `GET /compliance/me/profile`.
- **`compliance.html`** is a SEPARATE compliance console (Agent Appointments / CSV
  import / Carrier Matrix / Events / Logs). It was INTENTIONALLY left on the old
  model (still has an appointment effective date + single-state input) — do not
  "fix" it to match settings.html unless explicitly asked.
- **Dispositions** were merged into **`agent-performance.html`** (a
  Performance/Dispositions toggle at `#dispView`); `dispositions.html` is now a
  redirect stub to `agent-performance.html#dispView`.

## Training program (agent onboarding)

Lives in the **SMS SPA** at `/sms/#/training` (`apps/sms-ui/src/pages/Training.tsx`
+ `components/training/`), NOT a static page — there is no `training.html`.
Backend domain `apps/backend-api/app/training/` (router + `defaults.py`), model
`app/models/training.py` (`training_steps`, migration 052).

- Everyone signed in can read; **admin-class edits** (add / remove / rename /
  drag-reorder steps, paste a Vimeo/YouTube link or upload a video file, edit
  the script text). Agents' completion progress is per-browser (`localStorage`).
- Uploaded videos go through the same `app/calls/s3_storage.py` client as call
  recordings (Railway Buckets / any S3-compatible via `S3_ENDPOINT_URL`); with
  no S3 configured they fall back to DB bytes. Playback is
  `GET /training/steps/{id}/video` — redirects to a signed URL for S3, streams
  with Range support from DB — and accepts the JWT as `?token=` because
  `<video>` can't send headers. `/training/steps` is exempt from the 10 MB body cap.
- **Profile photos** use the same S3 client: `settings.html` still PATCHes a
  small data URL to `/auth/me`, but `app/auth/avatar.py` uploads the bytes to
  `avatars/<tenant>/<user>/<uuid>.<ext>`, keeps only `users.avatar_s3_key`
  (migration 053) and returns a 7-day signed URL as `avatar_url`. Without S3
  the data URL stays inline on the row; legacy inline photos are moved to S3
  on the next `GET /auth/me`.
- A tenant with **no rows at all** is seeded with `DEFAULT_STEPS` on first read
  (7 steps; the master call script sits under "How to Sell"). Deleting every
  step does NOT re-seed.
- `content` is line-based script markup rendered by `ScriptBlocks.tsx`
  (`# heading`, `[Agent]: quote`, `> note`, `1. item`, `- item`,
  `++ Title | body`, `:: Title | body`, `!! text`). The cheat-sheet is in the editor.
- Sidebar: the SPA shell renders it via `canSeeTraining()` (agents + admin-class
  + dev); static pages get it injected by `prefs-extras.js` (`injectTrainingLink`).

## Inbox (in-app messaging) — ONE page for every role

`inbox.html` is the single messaging page and is visible to **every** role
(the admin CSS/JS gates in `prefs-extras.js` deliberately do NOT hide it). It
lists a pinned **Team** section — in-app direct messages with every other
active user in the tenant, any role (admin ↔ agent, agent ↔ agent, admin ↔
admin) — above the customer SMS conversations. Backend `app/direct_messages/`
(`/inbox/dm/*`): `_counterpart_query` returns all active users, not just
admin ↔ agent pairs; opening a thread marks it read and emits `inapp_read` to
the reader's own socket room.

- **Sidebar unread badge** = unread customer conversations + unread DMs
  (`/inbox/dm/unread-count`). Static pages: `updateInboxBadge` in prefs-extras
  (creates the badge span if the page lacks it; refreshes on `inapp_message` /
  `inapp_read` / focus / 30s). SPA: `dmUnread` in `PortalShell.tsx`. Page
  scripts must NOT write the sidebar badge themselves (dashboard.html used to
  and blanked it).
- The list is **latest activity first** on every render (`byLatest` in
  inbox.html — realtime updates change `last_message_at` in place), and the
  page shows the unread total beside its "Inbox" title plus a count on the
  "Team" divider.
- **`applicant-inbox.html` (admin ↔ hiree SMS) and `hirees.html` are HIDDEN,
  not deleted** — no Hirees flow yet. `injectApplicantInboxLink` /
  `injectHireesLink` early-return, CSS rules hide any `a[href="applicant-inbox.html"]`
  / `a[href="hirees.html"]`, and both SPA entries hide for all roles.
  `admin-inbox.html` is a redirect stub to `inbox.html`.
- **Groups** (`/inbox/dm/groups`, models `DmGroup` / `DmGroupMember`, migration
  055) sit in their own "Groups" divider above Team. **Only `super_admin`** can
  create / rename / re-member / delete one (`_GROUP_ADMIN_ROLES`; legacy
  `tenant_admin` and `admin` cannot) — every member can read and reply. The
  picker is a free pick of tenant users; the creator is always added.
  * A group message is a `direct_messages` row with `group_id` set and
    `recipient_id` NULL, so `recipient_id` is now nullable and every 1:1 query
    filters `group_id IS NULL` or it grows a phantom "None" peer.
  * Read state can't live on the row (one row, many readers): each member has
    `DmGroupMember.last_read_at`, and unread = messages newer than
    `coalesce(last_read_at, added_at)` that they didn't send.
  * Delivery reuses the already-whitelisted `inapp_message` socket event with a
    `group_id` — deliberately, so `services/api.js` stays untouched (no `?v=`
    bump).
  * The inbox never auto-opens an UNREAD row on load — doing so would mark a
    group blast read before anyone looked at it.
  * The create/edit modal is a bespoke surface, so it carries its own
    `html[data-mode="dark"]` rules (see "Dark mode").
- **The Inbox never trusts the socket alone** (`pollInApp` in inbox.html). The
  Socket.IO connection can be down entirely — `services/api.js` asks for
  `transports: ['websocket','polling']`, and this socket.io build does NOT fall
  back when the first transport fails, so anything that blocks WebSocket kills
  realtime outright (the local preview proxy does exactly that: `serve.py` 404s
  `transport=websocket` because a plain HTTP proxy can't upgrade). When that
  happens no event ever arrives and an open thread used to sit there until you
  navigated away and back. So the page polls the OPEN in-app thread every 7s and
  repaints ONLY when the server has a message id it doesn't (a blind re-render
  would fight the user's scroll); the row list re-reads every 45s and on focus,
  which is also how group creation / membership changes arrive since those are
  never pushed. Customer SMS threads do NOT have this net — they still depend on
  the socket.
- The filter chips (All / Hot / Appointments / Replied / Dead / DNC) were
  REMOVED from inbox.html. `matchFilter` stays, hardcoded to the old "All"
  behaviour, so dead + DNC conversations remain hidden.
- `.localpreview/verify-inbox-all-roles.mjs` checks all of the above per role
  against the real backend (`run-local.sh`); `verify-inbox-groups.mjs` drives the
  group flow end-to-end (super admin creates + posts, agent replies but cannot
  manage).

## SMS pool — two ways a lead gets to an agent

The agent pool is `sms_leads WHERE status='QUEUED'`; everything downstream
(assignment, offer/accept/pass, dispositions, appointments, DNC) only reads
that table and is agnostic about how a row got there. Two producers:

1. **REPLY** (original): CSV → Campaign (`upload_batch`) → held leads → drip →
   first template via Sinch/Engage → customer replies → `lead_ingest` /
   `inbound_sync` mirror the replier into `sms_leads`.
2. **CSV_DIRECT** (`sms_leads.source`): admin uploads a CSV at
   `POST /sms/pool/upload` (router `sms_queue/routers/pool.py`, service
   `sms_queue/services/pool_ingest.py`) and rows land in `sms_leads` QUEUED
   immediately — **no SMS is ever sent**. Agents work these by phone; the whole
   CSV row is stored in `sms_leads.details` (`{fields:[[label,value],…], address}`)
   and shown on the offer popup / accepted view (`components/LeadDetails.tsx`).
   Each upload is an `sms_pool_batches` row so it can be removed as a unit.
   Limits: **20,000 rows** (`pool_ingest.MAX_ROWS`) and **30 MB**
   (`routers/pool.MAX_UPLOAD_BYTES`); the route is on the security middleware's
   `large_upload_paths` so the global 10 MB body cap doesn't apply. The UI checks
   both client-side and shows the limits under the section title.

Rules that keep #2 safe — don't undo them:
- The linked `leads` rows use `pacing_status='pooled'` (NOT `'held'`) so
  neither the campaign drip nor `ranked_held` can ever text them, and
  `on_lead_created` is never called.
- `_next_queued_lead` serves REPLY leads before CSV_DIRECT (FIFO within each).
- The choice is made **per upload** (a separate button beside Campaigns on
  `upload-leads.html` and in the SMS Manager's `LeadsTools`), never a global
  mode toggle.
- `_dispatch_sms` (agent chat sends) still passes no `kind`, so the
  first-template-only lockdown blocks them — CSV_DIRECT leads therefore have no
  chat composer; that is intentional (phone-first) until an exemption is decided.

## Commission (per-sale agent pay), company pay rules and deal source

"Commission" everywhere in the portal means **per-sale agent pay** — there is no
separate carrier-commission number. It is DERIVED, never stored:
`expenses/services.sale_pay_lines` returns one line per APPROVED deal, and three
surfaces sum those same lines, so they cannot disagree:

1. **Expenses → Agent pay** (`/sms/#/expenses`, owner only) — what the company owes.
2. **All Deals → "Total commission · whole team"** (`totals.commission_cents` on
   `/compliance/deals/today-all`, plus `commission_cents` per deal) — follows the
   page's date filter, so "Today" is the daily counter.
3. **My Deals → "You earned"** (`/compliance/deals/my-earnings`, plus
   `earned_cents` per deal on `/deals/my-today`) — the agent's own pay and their
   own tier standing (`week`) only.

### Company pay rules — `app/expenses/pay_rules.py` (migration 060)
ONE rule set per tenant (`pay_rules`, append-only versions, seeded on first read
from `DEFAULT_RULES`) pays every agent automatically. **Nothing about pay is
hard-coded** — every amount/threshold is in the `rules` JSON and edited from
Expenses → Agents → **Edit rules**. Per-agent rate entry is gone from the UI.

- **Weeks are Monday–Sunday, Eastern.** An agent's ACA COMMISSIONS for the week
  pick the tier (defaults: 0 → $20, 80 → $25, 130 → $30) and EVERY ACA commission
  that week is paid at that tier — crossing a threshold reprices the whole week.
  So a deal's commission is not fixed until its week closes.
- **Commissions are counted per APPLICATION**, not per deal row. The Log Sale
  form saves one `deals` row per person; rows from one submission share
  `deals.application_id` (NULL on older rows = its own application):
  an application marked **EAP → always 1**; a carrier listed in
  `per_member_carriers` (Anthem) **→ 1 per member**; anything else **→ 1**.
- **Dental** pays once per application by household size (= the number of people
  logged on the application; 3+ pays the larger amount). **Ancillary** pays a flat
  amount once per application. **Vision** has `vision_cents: null` = not offered
  yet = $0. None of these move the tier.
- **A closed week never moves when the rules are edited**: it is priced with the
  rules version in force when it closed, and with the tier exception active at
  that moment. Deals are still read live, so approving/blocking a deal from an
  earlier week DOES change that week (there is no stored weekly snapshot).
- **Exceptions** (`pay_exceptions`): an admin locks one agent's ACA tier until
  the end of the week / a date / no end, with a required reason; "Back to
  automatic" revokes it. Set from the agent's **Pay plan** drawer.
- **Cutover**: the latest rules row's `starts_on` (always a Monday; seeded as the
  Monday of the week the rules were first read; movable in Edit rules). Deals
  logged BEFORE it keep the old per-agent `agent_sale_rates` pricing
  (`_legacy_sale_lines`) — only there can a sale be "unrated" and pay $0
  (`unrated_sales` warnings). The legacy sale-rate endpoints still exist but have
  no UI.
- `pay_rules.price()` is the pure maths (no DB) — `tests/unit/test_pay_rules.py`
  runs the handoff's example week against it. Change the maths there first.

### Products on the Log Sale form
`PRODUCT_LIST` in `add-deal.html` is the product registry (ACA, Ancillary,
Dental, Vision). A product with an `added` date carries a **NEW** tag for
`NEW_DAYS` (30) after it and the tag then disappears on its own — a future
product only needs a row there. **Ancillary** (`deals.ancillary_count`) shows as
a coverage pill, legend entry, "Has Ancillary" filter and details row on All
Deals / My Deals. The **Leaderboard and Sales Dashboard do NOT count it yet** —
their "deals" total is still ACA + Dental + Vision.

### Deal source and announcements
- **`deals.deal_source`** (`carrier` | `eap`, migration 059) is the EAP checkbox
  in each person card on `add-deal.html` (carrier is the default; added persons
  follow Person 1 until changed). Under the pay rules it also makes the whole
  application count as ONE commission. It shows as an "EAP" tag beside the carrier
  on All Deals / My Deals, has a "Deal type" filter under More filters, and
  admins can correct it in Edit deal. `normalize_deal_source` is deliberately
  lenient (anything unknown → `carrier`) so a cached old form still logs sales.
- **Announcements** can be sent by admins AND `head` (`_ADMIN_ROLES` in
  `app/announcements/router.py`, mirrored by the role gates in `inbox.html` and
  `notifications.html`).
- `.localpreview/verify-pay-rules.mjs` and `verify-commission-eap-announce.mjs`
  check all of this against the real backend.

## Trash for sales, and disabled agents — two SHARED filters

Both rules are enforced in ONE place each, on purpose, so a screen added later
cannot forget them. Do not re-implement either per query.

### Trash (All Deals → admin delete) — migration 061
A sale is never hard-deleted. `POST /compliance/deals/{id}/trash` sets
`deals.trashed_at` / `trashed_by`; `…/restore` clears them; `GET
/compliance/deals/trash` lists them with who/when. Roles: `admin`,
`tenant_admin`, `super_admin`, `dev` (NOT `head`) — checked server-side.
- **`core/database.py` hides trashed deals from EVERY ORM select** (a
  `do_orm_execute` listener adding `with_loader_criteria(Deal, trashed_at IS
  NULL)` — full-entity, column-only and aggregate queries alike). That is how a
  trashed sale stops counting toward commissions, the weekly tier, leaderboards
  and dashboards at once. A query that must see trashed rows opts in with
  `.execution_options(include_trashed=True)` (`INCLUDE_TRASHED`) — only the
  Trash list and trash/restore do.
- Consequence: a trashed deal 404s on edit / status change until restored.
- Both actions write `deal_trashed` / `deal_restored` to `audit_logs`.
- UI (`all-deals.html`): red icon beside View → confirmation (Keep sale has
  focus) → Undo banner for 8s; the "Trash N" button in the page header opens the
  list with Restore. The modals are bespoke and carry their own dark rules.

### Disabled agents — `core/active_agents.py`
"Disabled" = `users.status = 'suspended'` (Settings → team → "Inactive users",
which is also where they are re-enabled) or `users.deleted_at` set. It is NOT
`Agent.status` — that only says whether a profile is routable, and every admin
owns an inactive profile.
- LIVE lists pull from `active_agents_query` / `active_users_query` /
  `active_user_clause`: Expenses → Agents (and its counts), the agent pickers
  (`/compliance/agents`), SMS Manager → Agent Availability and every per-agent
  SMS report, lead routing (`_available_agents`), and the Leaderboard / Sales
  Dashboard rankings (via `disabled_agent_ids`).
- HISTORY keeps the person, labelled with `labelled()` → "Name (disabled)": All
  Deals rows, ledger lines, the audit trail. Totals still include their past pay.
- Disabling also takes the user OFFLINE in the SMS queue and returns any lead
  they were only offered to the pool (`_leave_sms_queue` in admin.py).
- Re-enabling is just the status flip — nothing is deleted, so they reappear
  everywhere with history intact.
- `.localpreview/verify-trash-disabled.mjs` checks both features end to end.

## Verifying UI changes (headless Chrome over CDP)

The `.localpreview/*.mjs` scripts drive a headless Chrome via the DevTools Protocol
to seed auth, navigate, read computed styles, and screenshot — used to verify
changes against the preview server before pushing.

```bash
# 1) start the preview server (above), then launch debug Chrome:
"/c/Program Files/Google/Chrome/Application/chrome.exe" --headless=new \
  --remote-debugging-port=9231 --remote-allow-origins=* --user-data-dir="$TEMP/cr" about:blank &
# 2) a .mjs script connects to ws://127.0.0.1:9231, sets localStorage
#    (access_token='local-demo', ebRole, ebMode='dark', ebLocalUser), navigates,
#    and calls Page.captureScreenshot / Runtime.evaluate. See existing scripts.
```
`.localpreview/` is git-ignored — scratch scripts/screenshots there never get committed.

## Current state / where to begin  (as of 2026-06-18)

`main` @ `6be3e32`. Recent merged work (newest first):
- `6be3e32` remove Dashboard & Dispositions from the SMS shell + cache bump.
- `1e7ec10` carrier appointment `effective_date` made optional (fixed a 422 that
  blocked ALL appointment creates).
- `ad91f8d` remove Dashboard & Dispositions from the static sidebar.
- `02efe2e` merge Dispositions into Agent Performance.
- `7c2f289` Sales Dashboard "Total Leads" card.
- `80934dc` carrier appointments multi-state + no effective date; license state dropdown.

**Uncommitted working tree (built + verified this session, NOT yet pushed):**
- In-app **confirmation dialog** replacing the browser's native `confirm()` for
  removing a license / carrier appointment (centered modal in `settings.html`).
- **Dark-mode icon outline fix** in `prefs-extras.js`: icon chips that were solid
  white boxes (`.notif-icon`, the `⌘K` `.search kbd` badge, and `.kpi-icon`/
  `.activity-icon`) now render as transparent + white outline. Includes a
  `prefs-extras.js?v=4 → v=5` cache bump across all 34 pages.

**Stale, un-reconciled:** branch **`wip/portal-sms-pending`** holds older
dark-mode / SMS-bundle / `PortalShell.tsx` working-tree changes that diverged from
the team's newer SPA rebuild — do NOT force the stale SMS bundle; merge sms-ui
source and rebuild fresh if reviving any of it.

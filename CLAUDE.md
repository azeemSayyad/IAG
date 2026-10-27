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

## YES-reply auto dialer (Vonage) — `app/voice_dialer/`, migration 062

A client who texts YES is called automatically; only a LIVE person is bridged to
an agent, and the lead card pops on that same agent's screen. Built on the
**Vonage Communications APIs** (Voice + Messages) — NOT Vonage Business
(app.vonage.com), which has no webhooks / machine detection / transfers.

- **Lead states:** a YES makes the lead `READY_TO_CALL`; `DIALING` is the lock
  while a call is in flight. Neither is `QUEUED`, so the pool / offer popup never
  see them. A live answer jumps straight to `IN_PROGRESS` + agent `ON_CALL` — no
  Accept/Pass. Out of attempts (`max_total_attempts`) → back to `QUEUED` (pool).
- **Two switches for the agent in Lead Manager: Join queue and Ready for calls**
  (`sms_queue_agents.voice_ready`, `POST /voice/dialer/ready`). Join queue alone
  = regular pool only, NO headset line, so no Vonage minutes. Ready for calls
  opens the line; the dialer only counts agents with it on (`_ready_agents`), and
  `on_agent_line` refuses a line for anyone with it off. The line also closes
  while they work a regular pool lead (reopens when free). Leave / timeout /
  disabling the user switch it off. Manager board shows "Regular pool only".
- **Agents answer in the portal** (`components/AgentLine.tsx`, `lib/voice.ts`):
  while Available AND Ready for calls, the browser holds one Vonage Client SDK call parked in the
  agent's room (`iag-agent-<uuid>`); a live customer is TRANSFERRED into it, which
  is why the bridge is instant. Line = `sms_queue_agents.voice_leg_uuid`.
  Leaving the SPA drops the line, so while a customer is connected AgentLine
  warns on unload and opens off-app links (Log Sale) in a new tab.
- **Rules are server-side and clamped** (`service.update_settings`): 8 am–8 pm in
  the CLIENT's zone (`rules.py`; split states must satisfy every zone, unknown
  state = 11 am–8 pm ET), ≤3 tries per number per 24 h, ring 15–45 s.
  Pacing is FIXED at one call per free agent on the line (no setting — YES
  replies trickle in, so over-dialing only risks abandoned calls; the unused
  `voice_dialer_settings.pacing_ratio` column is left in place). DNC/STOP
  numbers are never dialed; never two calls to one number.
- **Every reply is called except a negative one** (owner's rule, `replies.py`):
  `classify` → YES (clear yes) | REPLY (anything else: "who is this?", "maybe",
  "not now", "where are you?", "I'm fine", "I'm good", "did you eat?", "?", a
  photo) | STOP (also "block", "do not disturb"/DND) | NEGATIVE ("no", "not
  interested", "no, I'm good") | ABUSE
  (profanity, slurs, "scam/fake", threats) | WRONG. All four negatives go on
  Do-Not-Call forever (`DNC_REASON`: OPTED_OUT / NOT_INTERESTED / ABUSIVE /
  WRONG_NUMBER). **Opting back in** (`service._opt_back_in`): a CLEAR yes from
  an opted-out number ("yes", "ok", "call me", "I'm interested", "start",
  "unstop", "I want a quote"…) deletes its Do-Not-Call rows and the lead is
  normal again (called, textable); a vague text ("cool", "hi") is only saved.
  The texts are marked `sms_messages.status` OPT_OUT / OPT_IN (shown on the
  Inbox thread) and the opt-in is audited (`ai_dnc_opt_in_by_text`, their
  words). Note `audit_logs.resource_id` is a UUID — pass a lead id or None,
  never a phone (that made every dialer audit insert fail silently).
  The dialer does NOT use the shared `block_words` list — it
  treats "who is this?" / "huh" as opt-outs, which the owner wants called.
  A client an agent already CLOSED is reopened only by a clear YES; any other
  follow-up ("thank you") is just saved (`reply_saved`). Match by phone (last 10
  digits), never by name.
- **Wait before calling** (`voice_dialer_settings.reply_delay_seconds`, default 3,
  0–30, in Dialing rules): a reply sets `next_dial_at = now + delay` and
  `_tick_later` fires the dialer when it's up (polls / Celery beat also cover it).
- While the dialer has people to call, agents on an open line are held for it:
  `_try_assign` skips them and `_reclaim_offers` pulls back an UNACCEPTED offer.
- "Voicemail" in Lead Manager's disposition list (auto-dialed leads only) puts
  the lead back on the dial list instead of closing it.
- **Connect on answer** (`VONAGE_CONNECT_ON_ANSWER`, default on — manager's choice
  "Option B"): the customer is bridged on the `answered` event (~1.5 s to the
  agent) while machine detection keeps listening; `human` only stamps
  `human_at`, and a later `machine` runs `_drop_early_voicemail` (hang up, agent
  back to AVAILABLE + their room, lead requeued, `voice:ended` reason voicemail
  closes the agent's card). Off = wait for `human` (~5 s) as before.
  **Webhook order is not fixed:** with machine detection on, Vonage asks the
  customer's ANSWER URL only AFTER detection decides (seen live: answered →
  human → answer, ~4 s later). `webhooks.answer` therefore replies to match
  where the call already is — CONNECTED → `room_only_ncco` (same call room),
  ABANDONED → abandon message, ended → "Goodbye", still in flight → bridge via
  `ncco_out`. Replying with the hold room after a bridge pulled the customer
  out: both sides connected, total silence (happened twice).
- **Bridge latency:** each call gets its own room (`call_room`) — recording a
  room is what Vonage does, and the agent's all-shift room gave 1-second files.
  Agent and customer legs are transferred IN PARALLEL over one pooled keep-alive
  httpx client (`vonage._http`); the notice is `VONAGE_RECORDING_NOTICE`
  ("This call is recorded.", spoken in the customer's NCCO before the room) —
  every word there is delay.
- **Each call keeps the agent's result**: `voice_call_attempts.disposition` is set
  from Mark… (`end_call_for_lead`, called by both agent and manager
  disposition paths) — the Called tab shows it as the call's outcome (Sale,
  Appointment set, Not interested…), and Today counts sales/appointments from it.
- **Called list is paged**: `GET /voice/dialer/calls?kind=all|talked|sales|missed&before=<created_at>&limit=30`
  (`service.call_history`) returns a page + the per-filter counts; the page loads
  30 more when the bottom scrolls into view and re-reads the loaded rows every 5 s.
- **Period picker** (Today / Yesterday / This week / This month / All time /
  Custom dates) sits on the stats card and also filters the Called list:
  `?period=…&from=YYYY-MM-DD&to=YYYY-MM-DD` on `/overview` and `/calls`
  (`service.period_bounds`; days in Eastern, weeks start Monday like agent pay).
  The header's abandoned-calls warning only fires on Today.
- The "Sorry we missed you" follow-up text from the spec is deliberately NOT
  sent — the first-template-only lockdown forbids it.
- Webhooks (public, `?k=VONAGE_WEBHOOK_TOKEN` or Vonage's signed JWT): Voice app
  answer `/api/v1/voice/vonage/answer`, event `/voice/vonage/event`; Messages
  inbound `/voice/vonage/inbound-sms`, status `/voice/vonage/sms-status`. Per-call
  URLs are built from `VOICE_CALLBACK_BASE_URL`. Env: `VONAGE_APPLICATION_ID`,
  `VONAGE_PRIVATE_KEY` (or `_PATH`), `VONAGE_SIGNATURE_SECRET`,
  `VONAGE_WEBHOOK_TOKEN`, `VONAGE_FROM_NUMBERS`, `VONAGE_TENANT_ID`.
- **Managers/admins have their own page, `/sms/#/dialer` ("Auto Dialer",
  `pages/AutoDialer.tsx`)** — switch, today's numbers, agents on the line, calls
  in progress, dial list, outcomes, recent calls, rules. Same audience as SMS
  Manager; linked from BOTH sidebars (`PortalShell.tsx` SMS_LINKS and
  `services/error-boundary.js`). Agents never use it — their side is Lead Manager.
- **Never run dialer DB code on the event loop.** `service`/`webhooks`/`simulator`
  are synchronous SQLAlchemy; every `async def` route in `voice_dialer/router.py`
  calls them through `_run` (threadpool), and the queue router's `_dialer` does
  the same. Calling them directly froze the WHOLE app during a demo: once the
  10-connection pool was busy, the blocked loop waited 10 s for a connection only
  the loop could release (1,000+ `QueuePool limit` errors).
- **Webhook parsing lives in ONE place, `webhooks.py`** — the HTTP routes in
  `router.py` are thin shells (trust check → `webhooks.*` → flush events).
- **Simulator (`simulator.py`):** with no Vonage keys and `APP_ENV != production`
  it plays Vonage — builds Vonage-format payloads (Messages inbound, Voice
  answer/event, AMD human/machine, record event) and feeds them to `webhooks.py`,
  so tests run the production code path. "Test it" box on the Auto Dialer page:
  send a text; "When the dialer calls" (live person after 2 s by default /
  voicemail / no answer / busy / let me choose); "Ignore calling hours while
  testing" (default on — testing happens outside US hours). Production never
  simulates.
- **Recordings:** Vonage deletes them after 30 days, so `store_recording` copies
  each one to the SAME place Log Sale keeps call recordings — a `deal_recordings`
  row (kind `recording`) with the audio in the Railway S3 bucket under
  `deal-recordings/<tenant>/` (inline on the row only if S3 is unavailable, Log
  Sale's own fallback). `voice_call_attempts.recording_id` → `deal_recordings.id`,
  so a dialer recording can be attached to a sale like an uploaded one. Play:
  `GET /voice/dialer/recordings/{attempt_id}?token=` (managers: any; agents:
  their own), shown as ▶ Play in Called + Inbox.
- **Opted out** card on the Auto Dialer page (`optouts.py`, `/voice/dialer/optouts`):
  the tenant's `sms_do_not_call` list made readable — who, when, reason (Texted
  STOP / Not interested / Wrong number / Blocked by admin / other), their last
  text; filters, search, the page's period, paged on scroll. Admin-class can
  **Block number** by hand and **Remove from list**; removing a real STOP
  (`OPTED_OUT`) needs `?confirm_stop=true` (409 otherwise) and the UI asks first.
  Removal never re-dials — only a new YES does. Both actions go to the audit log.
  Today's card shows "N opted out" for the period.
- **Inbox** on the Auto Dialer page (`inbox.py`, `/voice/dialer/inbox[/{lead}]`):
  everyone who texted our Vonage numbers, newest first; one read-only timeline
  per client mixing their texts with our calls (result, agent, talk, recording).
- Switched on per tenant from the Auto Dialer page (off by default). Celery
  beat `voice-dialer-tick` (3 s) is the safety net; webhooks, agent actions and
  the agent's `/voice/dialer/state` poll also tick.
- `tests/unit/test_voice_dialer.py`; `.localpreview/verify-auto-dialer.mjs` drives
  the whole flow in the browser against the real backend.

## Manual dialing (Dial drawer) — `app/voice_dialer/manual.py`, migration 063

Anyone signed in can call ANY number from the browser over Vonage. The **Dial**
button opens a right-hand drawer that overlays the page (does not resize it):
on the **Auto Dialer** page for manager-class, on **Lead Manager** for agents.
`components/DialDrawer.tsx` is mounted ONCE in `PortalShell` (opened by the
`dial:open` window event), so a call survives SMS route changes; with the drawer
closed a mini dock keeps Hang up reachable.

- Flow: `POST /voice/manual/calls` logs a `manual_calls` row → the browser does
  `serverCall({purpose:"manual", mc})` → `webhooks.answer` sees `custom_data`
  → `manual.on_answer` returns `record` + `connect` (phone; recording notice via
  the endpoint's `onAnswer` → `?leg=manual_notice`). Phone-leg events carry
  `?mc=`; the browser leg is matched by `app_leg_uuid`. Hang up ends the row
  immediately (`/end`) — Vonage's later events only fill in the exact duration.
- **Busy while dialing, no UI change:** an in-flight manual call (derived from the
  row — `live_clause`, nothing stored) is excluded in
  `queue_service._available_agents` (no pool offer, no dialer bridge),
  `on_agent_line` refuses a line, and `agent_state.manual` makes AgentLine keep
  the headset line closed. "Ready for calls" is never touched, so the line
  reopens by itself when the call ends. Lost events can't strand an agent: no
  browser leg after 90 s or 4 h in flight = not live.
- **No DNC, no calling hours, no attempt cap** (owner's rule — dialer only).
  Refused only while the user has an auto-dialed customer CONNECTED.
- **Separate log**: never in the dialer's Called list / stats. Calls tab: agents
  see their own (tap to redial); manager-class sees everyone + "Who dialed"
  filter + ▶ recording (`GET /voice/manual/calls/{id}/recording?token=`,
  manager-class only). Recordings use `service.save_recording` (same S3
  `deal-recordings/` store as the dialer).
- Caller ID: dropdown of `VONAGE_FROM_NUMBERS`, first = default, last pick kept
  in `localStorage.manualDialFrom`. Keypad sends DTMF during a call.
- One SDK session (`lib/voice.ts` `sdk()`) serves both the headset line and
  manual calls. Simulator (no keys, non-prod): `start` plays the answer webhook,
  the drawer calls `/sim-answer` after 2 s.
- `.localpreview/verify-manual-dial.mjs` checks the drawer UI per role (never
  presses Call — with real keys that rings a real phone).

## Inbound calls: client owners + hold queue — `app/voice_dialer/inbound.py`, migration 064

Team-agreed rules for a client calling one of our numbers (the admin-facing
copy is the **"How calls work"** drawer on the Auto Dialer page,
`components/DialerNotes.tsx` — keep it in step when a rule changes):

- **Owner** (`voice_client_owners`, by phone): the agent who MOST RECENTLY
  called / talked to the client — set by `manual.start`, `connect_human` and
  inbound `connect`. Managers (`MANAGER_ROLES`, incl. head) never own; their
  clients' callbacks go to any free agent. Admins reassign / remove under
  **Client owners** (`/voice/dialer/owners`); the next agent call overrides it.
- Routing (`inbound.on_call`): closed (open hours on + outside) → closed message
  with the hours; DNC / lead being worked → "we'll call you back"; owner **free**
  on the line → owner; owner **busy** (Ready for calls, AVAILABLE/ON_CALL, not
  free) → status `HOLDING` with `hold_for_user_id`, only the owner may take it
  until `max_hold_seconds` (default 300), then anyone; owner **away** / none →
  first free agent; nobody free → `HOLDING` for anyone; **nobody taking calls
  at all** → no hold, callback.
- Hold = NCCO loop `talk` + `stream` (bargeIn, ~45 s generated soft loop at
  `/voice/vonage/hold-music.wav`, or `VONAGE_HOLD_MUSIC_URL`) + 1-digit `input`
  → `/voice/vonage/hold`. **1 = call me back.** The dialer tick
  (`serve_holding`, before outbound) bridges held callers longest-first and ends
  holds past the limit if their webhook never came. `HOLDING → BRIDGING` is the
  lock so the tick and the hold webhook never both bridge one caller.
- Callback (`schedule_callback`): attempt → `MISSED` with the reason; dialer on →
  lead `READY_TO_CALL` keeping their place (`yes_at` = hold start); dialer off →
  regular pool as HOT. Hanging up on hold counts as a callback request.
- **Held callers outrank everything:** `_tick` starts no outbound call while
  anyone holds, and `dialer_reserves_agents` keeps pool offers off agents on a line.
- Open hours + longest hold are in Dialing rules ("When clients call us"):
  `voice_dialer_settings.max_hold_seconds / hours_enabled / open_days (Mon=0) /
  open_start_minute / open_end_minute / hours_tz` (US zones). Hours default OFF.
- Free agents on the line are bridged instantly (parked line), so there is no
  "ring the owner for N seconds" step.
- Tests: `tests/unit/test_voice_dialer.py` (hours); scenario + UI checks in
  `.localpreview/verify-inbound-routing.mjs`.

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

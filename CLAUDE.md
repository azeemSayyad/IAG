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
(NOT checked in / not present by default). Most UI work is done against the
preview server + demo-mock instead.

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

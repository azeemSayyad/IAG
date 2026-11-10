# IAG HealthSherpa Assistant (Chrome extension)

Runs the HealthSherpa 2027 client-authorization steps for an agent, started from
the portal page **HealthSherpa Auth** (`/sms/#/healthsherpa`, `apps/sms-ui/src/pages/HealthSherpa.tsx`).
Backend: `apps/backend-api/app/hs_auth/` (migration 067).

## How it works

1. Admin or agent uploads a sheet (.xlsx / .csv: First Name, Last Name, Date of
   Birth, State, optional Phone/Email). The server cleans names (no punctuation),
   turns dates into MM/DD/YYYY and states into codes; bad rows show as **Fix data**.
2. Agent presses **Start**, confirms they have the client's consent. The server
   counts the start against the daily limit (`HS_AUTH_DAILY_LIMIT`, default 20
   per agent per Eastern day) and returns a one-job token.
3. The portal hands the job to the extension (`window.postMessage` →
   `portal.js` → `background.js`). The extension never sees the portal login.
4. `background.js` opens a HealthSherpa tab; `hs.js` runs there, visibly (a bar
   at the bottom + a red outline on each element before it's used):
   Search Marketplace → name / DOB / state → both consent boxes → Search →
   Plan Year 2027 row (DOB must match) → Add to clients → Include and save
   Consumer Consent → Text link → waits (Refresh status every 60 s, Resend by
   text when the 15-minute link expires, `HS_AUTH_MAX_RESENDS`, default 1) →
   Finish.
5. Every step is reported to `/api/v1/hs-auth/jobs/{id}/report` with the job token.

The CLIENT taps the link and authorizes on their own phone. The extension never
does that part.

## When it stops on purpose (status → what the agent does)

| Status | Why |
|---|---|
| Not found | "No results found", no Plan Year 2027 row, or 2027 with a different DOB |
| Needs review | several 2027 matches; already in clients ("View in Dash"); HealthSherpa's phone differs from the sheet; no mobile number; window closed while waiting |
| Fix data | HealthSherpa rejected the search, or the state isn't in its list |
| Expired | the client didn't authorize after the allowed resends |
| Failed | page looks different, tab closed, not logged in for 15 min |

If HealthSherpa asks to log in (or connect the FFM account), the extension
waits; the agent logs in and it continues by itself.

## Install (agents)

Windows or Mac, Google Chrome:
1. Unzip `iag-healthsherpa-extension-<version>.zip`.
2. Open `chrome://extensions`, turn on **Developer mode**.
3. **Load unpacked** → choose the unzipped folder.
4. Refresh the portal's HealthSherpa Auth page — the badge turns green.

Build the zip: `scripts/build-hs-extension.sh`. For a fleet roll-out, publish
the same zip to the Chrome Web Store as **Unlisted** (auto-updates).

## Portal address

`common.js` → `IAG.PORTAL_HOSTS` and `manifest.json` (`host_permissions` +
the portal `content_scripts.matches`) must list the live portal host. Keep the
two in step and bump `version` in both `manifest.json` and `common.js`.

## Testing

`.localpreview/fake-healthsherpa/server.py` is a stand-in HealthSherpa built
from the screenshots; `.localpreview/verify-hs-extension.mjs` loads the
extension into headless Chrome (over CDP — Chrome 137+ ignores
`--load-extension`), maps `www.healthsherpa.com` to the fake and drives every
scenario from the portal page against the real backend.
The real HealthSherpa markup has not been seen yet (it blocks non-US access),
so the first live run with an agent should be watched.

// Auth bridge to the EXISTING portal.
// login.html writes access_token / refresh_token / ebRole into localStorage and
// this app shares that same session — same origin, same keys, no separate login.
//
// Access tokens are short-lived (JWT_ACCESS_TOKEN_EXPIRE_MINUTES / JWT_EXPIRES_IN,
// 30 minutes by default). The portal's services/api.js has always swapped an
// expired one for a fresh token via POST /auth/refresh before retrying; this app
// did NOT, so the first request after expiry threw the user out to the login page
// mid-session. refreshAccessToken() below is that missing half — lib/api.ts calls
// it on any 401 and only gives up (clearing the session) if the refresh itself
// fails, i.e. the 7-day refresh token has really run out.
const PORTAL_LOGIN = "/login.html";

export function getAccessToken(): string | null {
  return localStorage.getItem("access_token");
}

export function getRefreshToken(): string | null {
  return localStorage.getItem("refresh_token");
}

function setTokens(access: string, refresh?: string | null): void {
  localStorage.setItem("access_token", access);
  if (refresh) localStorage.setItem("refresh_token", refresh);
}

/** Drop the session. Also clears the cached role so stale admin/agent state
 *  can't drive role-gated UI after the session is gone (mirrors api.js). */
export function clearSession(): void {
  localStorage.removeItem("access_token");
  localStorage.removeItem("refresh_token");
  try { localStorage.removeItem("ebRole"); } catch { /* private mode */ }
}

/** Send the user to the portal login — never when already there, or a failed
 *  auth call on the login page would reload it forever. */
export function goToLogin(): void {
  if ((window.location.pathname || "").toLowerCase().includes("/login")) return;
  window.location.href = PORTAL_LOGIN;
}

// A page typically has several requests in flight (queue polling, counters, the
// socket handshake), so an expired token produces a BURST of 401s. Without this
// guard each one would fire its own refresh; they all share the single in-flight
// call instead, and the rest simply await its result.
let inflightRefresh: Promise<string | null> | null = null;

/** Swap the refresh token for a new access token.
 *
 *  Resolves to the new token, or to `null` ONLY when the session is really
 *  over: there is no refresh token, or the server REJECTED it (401/403).
 *
 *  Any other failure REJECTS with a `TransientRefreshError` — a 5xx / 502 while
 *  the API restarts or redeploys, a 429, a dropped connection, an unreadable
 *  body. The tokens are still good in that case, so they are kept and the
 *  caller simply sees a failed request; the next request retries the refresh.
 *  Before this, every one of those was collapsed into `null`, which api.ts
 *  treated as "session over" — wiping the tokens and bouncing to login. Agents
 *  and head managers live on the polling Queue page, so a single backend blip
 *  landing on one of their refreshes was enough to throw them out. */
export class TransientRefreshError extends Error {
  constructor(msg: string) { super(msg); this.name = "TransientRefreshError"; }
}

export function refreshAccessToken(): Promise<string | null> {
  if (inflightRefresh) return inflightRefresh;
  const refresh = getRefreshToken();
  if (!refresh) return Promise.resolve(null);

  inflightRefresh = fetch("/api/v1/auth/refresh", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: refresh }),
  })
    .then(
      async (res) => {
        if (res.status === 401 || res.status === 403) return null;   // refresh token refused → session over
        if (!res.ok) throw new TransientRefreshError(`refresh failed (${res.status})`);
        let data: { access_token?: string; refresh_token?: string } | null = null;
        try { data = await res.json(); } catch { data = null; }
        if (!data?.access_token) throw new TransientRefreshError("refresh returned no token");
        setTokens(data.access_token, data.refresh_token);
        return data.access_token;
      },
      () => { throw new TransientRefreshError("refresh failed (network)"); },
    )
    .finally(() => { inflightRefresh = null; });

  return inflightRefresh;
}

export function getRole(): string | null {
  return localStorage.getItem("ebRole");
}

function roleName(): string {
  return (getRole() || "").toLowerCase();
}

// "dev" is the developer/super-user role: full access to every page.
export function isDev(): boolean {
  return roleName() === "dev";
}

// Manager-class + admin (and dev). Allowed to see SMS Manager.
const ELEVATED_ROLES = new Set([
  "manager",
  "head",
  "tenant_admin",
  "admin",
  "super_admin",
  "dev",
]);

export function isManager(): boolean {
  return ELEVATED_ROLES.has(roleName());
}

// Roles allowed to see the admin-only Sales Dashboard (mirrors the backend
// require_role on /sales-dashboard). dev is included (sees everything).
const ADMIN_ROLES = new Set(["tenant_admin", "admin", "super_admin", "dev"]);

// STRICT admin. Deliberately does NOT include "head", because this is the check
// behind the three things a Head Manager must not inherit:
//   · the Contacts page
//   · the edit controls on the Training program (it may read it, not change it)
//   · "admins never work the queue" — a Head Manager DOES work the queue
// For plain page/nav access parity with an admin, use isAdminClass() instead.
export function isAdmin(): boolean {
  return ADMIN_ROLES.has(roleName());
}

// Admin-class INCLUDING "head" (Head Manager) — a Head Manager ranks with the
// admins everywhere except the three carve-outs listed on isAdmin() above and
// company Expenses (see isOwner). Mirrors ADMIN_OR_HEAD on the backend.
const ADMIN_CLASS_ROLES = new Set([...ADMIN_ROLES, "head"]);

export function isAdminClass(): boolean {
  return ADMIN_CLASS_ROLES.has(roleName());
}

// Owner/CEO gate — the Expenses page and nothing else. Deliberately TIGHTER than
// isAdmin(): tenant_admins and managers must not see payroll. Mirrors the backend's
// require_role("super_admin") on /expenses, including "dev" (which passes every
// role gate in this app by design).
const OWNER_ROLES = new Set(["super_admin", "dev"]);

export function isOwner(): boolean {
  return OWNER_ROLES.has(roleName());
}

// Per-page SMS visibility rules:
//   - SMS Queue:      agents + head + dev      (other admin-class do NOT see it)
//   - SMS Manager:    manager-class + admin + dev
//   - SMS Monitoring: dev only
export function canSeeQueue(): boolean {
  const r = roleName();
  // "head" is here on purpose: a Head Manager works leads alongside the agents.
  // The backend agrees — it is absent from queue_service.ADMIN_ROLES, the list
  // of roles that are refused a queue session.
  return r === "agent" || r === "dev" || r === "head";
}
export function canSeeManager(): boolean {
  return isManager();
}
export function canSeeMonitoring(): boolean {
  return isDev();
}
// Training: the agents it's for, plus admin-class (who edit it), a Head Manager
// (read-only — the edit controls key off the strict isAdmin) and dev.
export function canSeeTraining(): boolean {
  return roleName() === "agent" || isAdminClass();
}

// Where to send a user who lands on the SMS app root or a page they can't see.
// Agents/dev get the queue; admin & manager-class get their only page (Manager).
export function smsDefaultRoute(): string {
  return canSeeQueue() ? "/queue" : "/manager";
}

export function ensureAuth(): void {
  if (!getAccessToken()) {
    window.location.href = PORTAL_LOGIN;
  }
}

export function logout(): void {
  clearSession();
  window.location.href = PORTAL_LOGIN;
}

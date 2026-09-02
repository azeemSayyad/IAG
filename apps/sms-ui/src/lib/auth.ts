// Auth bridge to the EXISTING portal.
// login.html writes access_token / refresh_token / ebRole into localStorage;
// here we only READ them, so the new app shares the same session with zero
// changes to the existing login flow.
const PORTAL_LOGIN = "/login.html";

export function getAccessToken(): string | null {
  return localStorage.getItem("access_token");
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

export function isAdmin(): boolean {
  return ADMIN_ROLES.has(roleName());
}

// Per-page SMS visibility rules:
//   - SMS Queue:      agents + dev only        (admin/manager-class do NOT see it)
//   - SMS Manager:    manager-class + admin + dev
//   - SMS Monitoring: dev only
export function canSeeQueue(): boolean {
  const r = roleName();
  return r === "agent" || r === "dev";
}
export function canSeeManager(): boolean {
  return isManager();
}
export function canSeeMonitoring(): boolean {
  return isDev();
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
  localStorage.removeItem("access_token");
  localStorage.removeItem("refresh_token");
  window.location.href = PORTAL_LOGIN;
}

import { clearSession, getAccessToken, goToLogin, refreshAccessToken, TransientRefreshError } from "./auth";

// All calls go through the same /api proxy the portal already uses
// (nginx proxies /api -> backend-api:8000). The backend mounts every router
// under /api/v1, matching the portal's own api.js. Only NEW endpoints are
// added server-side; existing ones are never modified.
const BASE = "/api/v1";

// A 401 here means the access token aged out mid-session, NOT that the user is
// signed out: the refresh token is good for 7 days. So mirror the portal's
// services/api.js — refresh once, replay the request with the new token, and
// only fall back to the login page if the refresh itself fails. Before this,
// any 401 redirected immediately, which is why sessions appeared to end on
// their own after the access-token lifetime (~30 min) on SMS/Training pages.
//
// `authedFetch` is the shared primitive: it also serves the multipart uploaders,
// which can't use api() because they must let the browser set the FormData
// content-type (and its boundary) itself.
export async function authedFetch(
  path: string,
  init: RequestInit = {},
  opts: { json?: boolean } = {},
): Promise<Response> {
  const send = (token: string | null) => {
    const headers: Record<string, string> = {
      ...(opts.json ? { "Content-Type": "application/json" } : {}),
      ...(init.headers as Record<string, string> | undefined),
    };
    if (token) headers["Authorization"] = `Bearer ${token}`;
    return fetch(BASE + path, { ...init, headers });
  };

  const res = await send(getAccessToken());
  if (res.status !== 401) return res;

  // Never try to refresh a failed refresh/login — that 401 means bad
  // credentials or a genuinely expired session, not a stale access token.
  if (/^\/auth\/(login|refresh|password-reset)/.test(path)) return res;

  let fresh: string | null;
  try {
    fresh = await refreshAccessToken();
  } catch (e) {
    // Transient (API restarting, network blip): the session is still good.
    // Keep the tokens and let the caller see an ordinary failed request.
    const why = e instanceof TransientRefreshError ? e.message : String(e);
    throw new Error(`401 Unauthorized: session refresh unavailable (${why})`);
  }
  if (fresh === null) {
    // The server refused the refresh token — the 7-day session really is over.
    clearSession();
    goToLogin();
    throw new Error("Unauthorized");
  }
  // Replay once with the token the server just minted. If THAT still 401s it
  // is the endpoint's own verdict, not a dead session — return it as-is rather
  // than logging the user out.
  return send(fresh);
}

export async function api<T = unknown>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const res = await authedFetch(path, init, { json: true });

  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText}: ${text}`);
  }
  const ct = res.headers.get("content-type") || "";
  return (ct.includes("application/json") ? res.json() : res.text()) as Promise<T>;
}

/** Multipart POST (CSV / media uploads). Same session handling as api(), but no
 *  JSON content-type so the browser can set the FormData boundary. */
export async function apiUpload<T = unknown>(path: string, fd: FormData): Promise<T> {
  const res = await authedFetch(path, { method: "POST", body: fd });
  if (!res.ok) {
    let detail = "";
    try { detail = (await res.json())?.detail || ""; } catch { /* non-JSON body */ }
    throw new Error(detail || `${res.status} ${res.statusText}`);
  }
  const ct = res.headers.get("content-type") || "";
  return (ct.includes("application/json") ? res.json() : res.text()) as Promise<T>;
}

import { getAccessToken } from "./auth";

// All calls go through the same /api proxy the portal already uses
// (nginx proxies /api -> backend-api:8000). The backend mounts every router
// under /api/v1, matching the portal's own api.js. Only NEW endpoints are
// added server-side; existing ones are never modified.
const BASE = "/api/v1";

export async function api<T = unknown>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const token = getAccessToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(init.headers as Record<string, string> | undefined),
  };
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const res = await fetch(BASE + path, { ...init, headers });

  if (res.status === 401) {
    window.location.href = "/login.html";
    throw new Error("Unauthorized");
  }
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText}: ${text}`);
  }
  const ct = res.headers.get("content-type") || "";
  return (ct.includes("application/json") ? res.json() : res.text()) as Promise<T>;
}

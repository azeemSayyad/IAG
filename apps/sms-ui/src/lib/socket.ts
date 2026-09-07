import { io, type Socket } from "socket.io-client";
import { getAccessToken } from "./auth";

// Single shared connection over the SAME /socket.io path the portal already
// proxies to the backend. Survives navigation within the SPA (HashRouter),
// so an agent keeps one live connection for their whole shift.
let socket: Socket | null = null;

export function getSocket(): Socket {
  if (socket) return socket;
  // `auth` is a CALLBACK, not a literal: socket.io re-runs it for every
  // (re)connection attempt, so after lib/api.ts swaps an expired access token
  // the next reconnect handshakes with the NEW one. Captured as a value it kept
  // presenting the token from page load, and a reconnect after the token aged
  // out was rejected — the agent silently stopped receiving lead offers.
  socket = io({
    path: "/socket.io",
    transports: ["websocket", "polling"],
    auth: (cb: (data: { token: string | null }) => void) => cb({ token: getAccessToken() }),
    reconnection: true,
    reconnectionDelay: 1000,
    reconnectionDelayMax: 5000,
  });
  // The handshake query is fixed at construction, so keep it in step by hand
  // before each retry (the backend accepts the token from either place).
  socket.io.on("reconnect_attempt", () => {
    if (socket) socket.io.opts.query = { token: getAccessToken() || "" };
  });
  socket.io.opts.query = { token: getAccessToken() || "" };
  return socket;
}

export function disconnectSocket(): void {
  socket?.disconnect();
  socket = null;
}

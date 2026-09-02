import { io, type Socket } from "socket.io-client";
import { getAccessToken } from "./auth";

// Single shared connection over the SAME /socket.io path the portal already
// proxies to the backend. Survives navigation within the SPA (HashRouter),
// so an agent keeps one live connection for their whole shift.
let socket: Socket | null = null;

export function getSocket(): Socket {
  if (socket) return socket;
  const token = getAccessToken();
  socket = io({
    path: "/socket.io",
    transports: ["websocket", "polling"],
    auth: { token },
    query: { token: token || "" },
    reconnection: true,
    reconnectionDelay: 1000,
    reconnectionDelayMax: 5000,
  });
  return socket;
}

export function disconnectSocket(): void {
  socket?.disconnect();
  socket = null;
}

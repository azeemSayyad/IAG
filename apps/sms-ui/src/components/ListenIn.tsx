import { useEffect, useState } from "react";
import { endMonitor, onMonitor, setMonitorMode, startMonitor, type MonitorState } from "../lib/voice";

/* Listen in / whisper on one live call (Auto Dialer → Live calls, managers).
   Listen: you hear the agent and the client, nobody hears you.
   Whisper: only the agent hears you — the client never does. */

export type Listener = { user_id: string; name: string | null; mode: string };

export function useMonitor(): MonitorState {
  const [s, setS] = useState<MonitorState>({ monitor: null, starting: null, target: null, switching: false, error: null });
  useEffect(() => onMonitor(setS), []);
  return s;
}

export default function ListenIn({ attemptId, agentName, listeners }: {
  attemptId: string;
  agentName: string | null;
  listeners: Listener[];
}) {
  const st = useMonitor();
  const mine = st.monitor && st.monitor.attempt_id === attemptId ? st.monitor : null;
  const starting = st.starting === attemptId;
  const others = listeners.filter((l) => l.user_id !== st.monitor?.user_id); // you are shown below
  const agent = agentName || "the agent";

  return (
    <div className="mt-2">
      {others.length > 0 && (
        <div className="mb-1.5 text-xs text-ink-faint">
          {others.map((l) => `${l.name || "A manager"} ${l.mode === "whisper" ? "whispering" : "listening"}`).join(" · ")}
        </div>
      )}
      {!mine ? (
        <button
          onClick={() => startMonitor(attemptId)}
          disabled={starting || !!st.starting}
          title="Hear this call. The agent and the client can't hear you."
          className="rounded-lg border border-hairline bg-white px-2.5 py-1 text-xs font-semibold text-ink hover:bg-black/5 disabled:opacity-50"
        >
          {starting ? "Connecting…" : "🎧 Listen"}
        </button>
      ) : (
        <div className="rounded-lg bg-accent/8 px-2.5 py-2">
          <div className="text-xs font-semibold text-ink">
            {mine.status !== "LIVE"
              ? "Connecting you to the call…"
              : mine.mode === "whisper"
                ? <>Whispering — only <b>{agent}</b> hears you</>
                : <>Listening — nobody can hear you</>}
            {mine.simulated && <span className="ml-1 font-normal text-pending">(simulated, no audio)</span>}
          </div>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {mine.mode === "listen" ? (
              <button
                onClick={() => setMonitorMode("whisper")}
                disabled={st.switching || mine.status !== "LIVE"}
                title={`Talk to ${agent} only. The client can't hear you.`}
                className="rounded-lg bg-accent px-2.5 py-1 text-xs font-bold text-white hover:bg-accent-hover disabled:opacity-50"
              >
                {st.switching ? "Switching…" : `Whisper to ${agent}`}
              </button>
            ) : (
              <button
                onClick={() => setMonitorMode("listen")}
                disabled={st.switching}
                className="rounded-lg border border-hairline bg-white px-2.5 py-1 text-xs font-semibold text-ink hover:bg-black/5 disabled:opacity-50"
              >
                {st.switching ? "Switching…" : "Back to listening only"}
              </button>
            )}
            <button
              onClick={() => endMonitor()}
              className="rounded-lg border border-hairline bg-white px-2.5 py-1 text-xs font-semibold text-danger hover:bg-danger/10"
            >
              Leave
            </button>
          </div>
        </div>
      )}
      {st.error && st.target === attemptId && (
        <div className="mt-1 text-xs text-danger">{st.error}</div>
      )}
    </div>
  );
}

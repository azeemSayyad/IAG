import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { getSocket } from "../lib/socket";
import { brandColor } from "../lib/theme";

/* Live ECG-style activity strip. Polls /sms/monitoring/pulse-events for DB
   activity (polls + messages) AND reacts instantly to realtime socket events
   (broadcast/assign + new messages) so manager actions like Re-broadcast spike
   the strip live — mirroring Gamified's pulse:broadcast behavior. */

const LABELS: Record<string, string> = {
  poll_success: "POLL OK",
  poll_fail: "POLL FAIL",
  inbound: "INBOUND",
  outbound: "OUTBOUND",
  broadcast: "BROADCAST",
};
// Canvas cannot resolve var(), so the two brand-coloured series read the live
// values from /brand.js instead of restating them.
function spikeColor(type: string): string {
  switch (type) {
    case "poll_success": return "rgba(120,255,180,0.95)";
    case "poll_fail": return "rgba(255,120,120,0.95)";
    case "inbound": return "rgba(120,220,255,0.95)";
    case "outbound": return `rgba(${brandColor("--accent-2-rgb")},0.95)`;
    case "broadcast": return `rgba(${brandColor("--accent-rgb")},0.95)`;
    default: return "rgba(255,255,255,0.8)";
  }
}
const WINDOW_MS = 90_000; // span shown across the strip (right->left travel time)
const POLL_MS = 5_000;

type PulseEvent = { id: string; type: string; at: string };

export default function LivePulse() {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const eventsRef = useRef<Map<string, PulseEvent>>(new Map());
  const [evtPerMin, setEvtPerMin] = useState(0);
  const [connected, setConnected] = useState(false);

  // Pull events on an interval.
  useEffect(() => {
    let alive = true;
    async function load() {
      try {
        const r = await api<{ events: PulseEvent[] }>(
          "/sms/monitoring/pulse-events?minutes=10",
        );
        if (!alive) return;
        setConnected(true);
        for (const e of r.events) eventsRef.current.set(e.id, e);
        // count events in the last 60s
        const cutoff = Date.now() - 60_000;
        const recent = [...eventsRef.current.values()].filter(
          (e) => new Date(e.at).getTime() >= cutoff,
        );
        setEvtPerMin(recent.length);
      } catch {
        if (alive) setConnected(false);
      }
    }
    load();
    const id = setInterval(load, POLL_MS);

    // Instant spikes from realtime activity (broadcast / assignment / messages).
    const s = getSocket();
    let n = 0;
    const spike = (type: string) => {
      const key = `live-${Date.now()}-${n++}`;
      eventsRef.current.set(key, { id: key, type, at: new Date().toISOString() });
    };
    // queue_updated fires on broadcast / re-broadcast / assign / distribute, etc.
    const onQueue = () => spike("broadcast");
    const onMsg = (m: { direction?: string }) =>
      spike(m && m.direction === "INBOUND" ? "inbound" : "outbound");
    s.on("sms:queue_updated", onQueue);
    s.on("sms:lead_assigned", onQueue);
    s.on("sms:new_message", onMsg);

    return () => {
      alive = false;
      clearInterval(id);
      s.off("sms:queue_updated", onQueue);
      s.off("sms:lead_assigned", onQueue);
      s.off("sms:new_message", onMsg);
    };
  }, []);

  // Animation loop.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    let raf = 0;
    let phase = 0;

    function draw() {
      const c = canvasRef.current!;
      const dpr = window.devicePixelRatio || 1;
      const w = c.clientWidth;
      const h = c.clientHeight;
      if (c.width !== w * dpr || c.height !== h * dpr) {
        c.width = w * dpr;
        c.height = h * dpr;
      }
      ctx!.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx!.clearRect(0, 0, w, h);

      const mid = h / 2;
      const now = Date.now();
      phase += 0.06;

      // baseline waveform
      ctx!.beginPath();
      ctx!.strokeStyle = "rgba(120,255,180,0.85)";
      ctx!.lineWidth = 1.5;
      const events = [...eventsRef.current.values()];
      for (let x = 0; x <= w; x++) {
        const t = now - (1 - x / w) * WINDOW_MS;
        let y = mid + Math.sin(x * 0.06 + phase) * 1.2; // gentle idle ripple

        // add a spike near any event whose time maps close to this x
        for (const e of events) {
          const ex = ((WINDOW_MS - (now - new Date(e.at).getTime())) / WINDOW_MS) * w;
          const d = x - ex;
          if (Math.abs(d) < 14) {
            const amp = e.type.startsWith("poll") ? 22 : 30;
            y -= Math.exp(-(d * d) / 18) * amp * (e.type === "poll_fail" ? -1 : 1);
          }
        }
        if (x === 0) ctx!.moveTo(x, y);
        else ctx!.lineTo(x, y);
      }
      ctx!.stroke();

      // floating labels above spikes inside the window: type + timestamp
      for (const e of events) {
        const d = new Date(e.at);
        const age = now - d.getTime();
        if (age < 0 || age > WINDOW_MS) continue;
        const ex = ((WINDOW_MS - age) / WINDOW_MS) * w;
        const x = Math.min(ex + 4, w - 64);
        const ts = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
        ctx!.fillStyle = spikeColor(e.type);
        ctx!.font = "bold 10px ui-monospace, monospace";
        ctx!.fillText(LABELS[e.type] || e.type, x, mid - 30);
        ctx!.fillStyle = "rgba(200,230,210,0.55)";
        ctx!.font = "9px ui-monospace, monospace";
        ctx!.fillText(ts, x, mid - 18);
      }

      raf = requestAnimationFrame(draw);
    }
    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, []);

  return (
    <div className="relative h-28 overflow-hidden rounded-2xl bg-[#0a1410] shadow-inner">
      <canvas ref={canvasRef} className="h-full w-full" />
      <div className="absolute left-3 top-2 flex items-center gap-2 text-xs font-semibold">
        <span
          className={`h-2 w-2 rounded-full ${connected ? "bg-emerald-400" : "bg-red-400"}`}
        />
        <span className={connected ? "text-emerald-300" : "text-red-300"}>
          {connected ? "LIVE" : "DISCONNECTED"}
        </span>
      </div>
      <div className="absolute right-3 top-2 font-mono text-xs text-emerald-300/80">
        {evtPerMin} <span className="text-emerald-300/40">evt/min</span>
      </div>
      <div className="absolute bottom-2 left-3 font-mono text-[10px] text-emerald-300/40">
        ◂ 90s window
      </div>
      <div className="absolute bottom-2 right-3 font-mono text-[10px] text-emerald-300/40">
        now ▸ SMS QUEUE
      </div>
    </div>
  );
}

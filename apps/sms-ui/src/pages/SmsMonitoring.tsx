import { useEffect, useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api } from "../lib/api";
import LivePulse from "../components/LivePulse";
import { brandColor } from "../lib/theme";

type Stats = {
  polling: { success_rate_pct: number; last24h_polls_succeeded: number; last24h_polls_attempted: number };
  outbound: {
    success_rate_pct: number;
    last24h_messages_sent: number;
    last24h_messages_delivered: number;
    last24h_messages_failed: number;
    sending_now: number;
    sent_today: number;
    sent_all_time: number;
  };
  queue: {
    current_queued: number;
    agents_online: number;
    agents_available: number;
    agents_on_call: number;
    agents_on_break: number;
    oldest_queued_age_seconds: number;
  };
  health: { backend_uptime_ms: number; db_status: string; version: string };
};
type SeriesPoint = {
  hour: string;
  inbound: number;
  outbound: number;
  polls_ok: number;
  polls_fail: number;
};
type Failures = {
  failed_outbound_messages: {
    id: string;
    phoneNumber: string;
    message: string;
    error: string | null;
    createdAt: string | null;
  }[];
  failed_polls: { id: string; error_message: string | null; attempted_at: string | null; duration_ms: number | null }[];
};

const REFRESH_MS = 30_000;

function StatCard({
  label,
  value,
  sub,
  tone = "ink",
}: {
  label: string;
  value: string | number;
  sub?: string;
  tone?: "ink" | "success" | "danger" | "accent";
}) {
  const toneClass = {
    ink: "text-ink",
    success: "text-success",
    danger: "text-danger",
    accent: "text-accent",
  }[tone];
  return (
    <div className="glass rounded-2xl p-4">
      <div className="text-xs font-medium uppercase tracking-wide text-ink-faint">{label}</div>
      <div className={`mt-1 text-3xl font-semibold ${toneClass}`}>{value}</div>
      {sub && <div className="mt-1 text-xs text-ink-muted">{sub}</div>}
    </div>
  );
}

function hourLabel(iso: string) {
  const d = new Date(iso);
  const h = d.getHours();
  return `${h % 12 || 12} ${h < 12 ? "AM" : "PM"}`;
}

export default function SmsMonitoring() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [series, setSeries] = useState<SeriesPoint[]>([]);
  const [failures, setFailures] = useState<Failures | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);

  useEffect(() => {
    let alive = true;
    async function load() {
      try {
        const [s, t, f] = await Promise.all([
          api<Stats>("/sms/monitoring/stats"),
          api<{ points: SeriesPoint[] }>("/sms/monitoring/time-series"),
          api<Failures>("/sms/monitoring/recent-failures"),
        ]);
        if (!alive) return;
        setStats(s);
        setSeries(t.points.map((p) => ({ ...p, label: hourLabel(p.hour) } as SeriesPoint & { label: string })));
        setFailures(f);
        setUpdatedAt(new Date());
        setError(null);
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : "Failed to load");
      }
    }
    load();
    const id = setInterval(load, REFRESH_MS);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  if (error) {
    return <div className="glass mx-auto max-w-2xl rounded-2xl p-6 text-danger">Failed to load: {error}</div>;
  }
  if (!stats) return <div className="p-6 text-ink-muted">Loading…</div>;

  const data = series as (SeriesPoint & { label: string })[];

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-semibold text-ink">SMS Monitoring</h2>
        {updatedAt && (
          <span className="text-xs text-ink-faint">
            Refreshed {updatedAt.toLocaleTimeString()} · auto-refresh 30s
          </span>
        )}
      </div>

      <LivePulse />

      {/* Texts being sent / have been sent (#9) */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard
          label="Sending now"
          value={stats.outbound.sending_now}
          sub="in-flight (pending)"
          tone={stats.outbound.sending_now > 0 ? "accent" : "ink"}
        />
        <StatCard label="Sent today" value={stats.outbound.sent_today} sub="outbound messages" tone="success" />
        <StatCard label="Sent (24h)" value={stats.outbound.last24h_messages_sent} sub={`${stats.outbound.last24h_messages_delivered} delivered`} />
        <StatCard label="Sent all-time" value={stats.outbound.sent_all_time} sub="total outbound" />
      </div>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard
          label="Poll success (24h)"
          value={`${stats.polling.success_rate_pct}%`}
          sub={`${stats.polling.last24h_polls_succeeded}/${stats.polling.last24h_polls_attempted} polls`}
          tone={stats.polling.success_rate_pct >= 95 ? "success" : "danger"}
        />
        <StatCard
          label="Outbound success (24h)"
          value={`${stats.outbound.success_rate_pct}%`}
          sub={`${stats.outbound.last24h_messages_sent - stats.outbound.last24h_messages_failed} sent, ${stats.outbound.last24h_messages_failed} failed`}
          tone={stats.outbound.success_rate_pct >= 90 ? "success" : "danger"}
        />
        <StatCard
          label="Queue depth"
          value={stats.queue.current_queued}
          sub={`oldest ${Math.floor(stats.queue.oldest_queued_age_seconds / 60)} min`}
          tone="accent"
        />
        <StatCard
          label="Agents online"
          value={stats.queue.agents_online}
          sub={`${stats.queue.agents_available} avail · ${stats.queue.agents_on_call} on call · ${stats.queue.agents_on_break} break`}
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        {/* Left: charts + health */}
        <div className="space-y-4 lg:col-span-2">
          <div className="glass rounded-2xl p-4">
            <h3 className="mb-2 text-sm font-semibold text-ink">SMS Volume (24h, hourly)</h3>
            <ResponsiveContainer width="100%" height={180}>
              <LineChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: -16 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(26,31,42,0.08)" />
                <XAxis dataKey="label" tick={{ fontSize: 10, fill: "rgba(26,31,42,0.5)" }} interval={2} />
                <YAxis tick={{ fontSize: 10, fill: "rgba(26,31,42,0.5)" }} />
                <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8 }} />
                <Line type="monotone" dataKey="inbound" name="Inbound" stroke="#4f8268" strokeWidth={2} dot={false} />
                <Line type="monotone" dataKey="outbound" name="Outbound" stroke={brandColor("--accent")} strokeWidth={2} dot={false} />
              </LineChart>
            </ResponsiveContainer>
            <div className="flex justify-center gap-4 text-xs text-ink-muted">
              <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-sm" style={{ background: "#4f8268" }} />Inbound</span>
              <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-sm" style={{ background: "var(--accent)" }} />Outbound</span>
            </div>
          </div>

          <div className="glass rounded-2xl p-4">
            <h3 className="mb-2 text-sm font-semibold text-ink">Polling Success vs Failure (24h, hourly)</h3>
            <ResponsiveContainer width="100%" height={160}>
              <LineChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: -16 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(26,31,42,0.08)" />
                <XAxis dataKey="label" tick={{ fontSize: 10, fill: "rgba(26,31,42,0.5)" }} interval={2} />
                <YAxis tick={{ fontSize: 10, fill: "rgba(26,31,42,0.5)" }} />
                <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8 }} />
                <Line type="monotone" dataKey="polls_ok" name="OK" stroke="#4f8268" strokeWidth={2} dot={false} />
                <Line type="monotone" dataKey="polls_fail" name="Failed" stroke="#a3525c" strokeWidth={2} dot={false} />
              </LineChart>
            </ResponsiveContainer>
            <div className="flex justify-center gap-4 text-xs text-ink-muted">
              <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-sm" style={{ background: "#a3525c" }} />Failed</span>
              <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-sm" style={{ background: "#4f8268" }} />OK</span>
            </div>
          </div>

          <div className="glass rounded-2xl p-4">
            <h3 className="mb-3 text-sm font-semibold text-ink">System Health</h3>
            <div className="grid grid-cols-3 gap-4 text-sm">
              <div>
                <div className="text-xs uppercase tracking-wide text-ink-faint">Uptime</div>
                <div className="mt-1 font-semibold text-ink">
                  {Math.floor(stats.health.backend_uptime_ms / 1000 / 60)} min
                </div>
              </div>
              <div>
                <div className="text-xs uppercase tracking-wide text-ink-faint">Database</div>
                <div className={`mt-1 font-semibold ${stats.health.db_status === "ok" ? "text-success" : "text-danger"}`}>
                  {stats.health.db_status === "ok" ? "✓ ok" : stats.health.db_status}
                </div>
              </div>
              <div>
                <div className="text-xs uppercase tracking-wide text-ink-faint">Version</div>
                <div className="mt-1 font-mono text-ink">{stats.health.version.slice(0, 8)}</div>
              </div>
            </div>
          </div>
        </div>

        {/* Right: failures */}
        <div className="glass flex max-h-[36rem] flex-col overflow-hidden rounded-2xl">
          <h3 className="flex items-center gap-2 border-b border-hairline-soft p-4 text-sm font-semibold text-ink">
            ⚠️ Recent Failures
          </h3>
          <div className="flex-1 overflow-y-auto">
            <div className="bg-black/5 px-4 py-2 text-[11px] font-semibold uppercase text-ink-muted">
              Failed Outbound ({failures?.failed_outbound_messages.length || 0})
            </div>
            {(failures?.failed_outbound_messages.length || 0) === 0 ? (
              <div className="p-4 text-center text-xs text-ink-faint">None</div>
            ) : (
              failures!.failed_outbound_messages.map((m) => (
                <div key={m.id} className="border-b border-hairline-soft px-4 py-2 text-xs">
                  <div className="font-semibold text-ink">{m.phoneNumber}</div>
                  <div className="truncate text-ink-muted">{m.message}</div>
                  <div className="text-[10px] text-ink-faint">
                    {m.createdAt ? new Date(m.createdAt).toLocaleString() : "—"}
                  </div>
                </div>
              ))
            )}
            <div className="bg-black/5 px-4 py-2 text-[11px] font-semibold uppercase text-ink-muted">
              Failed Polls ({failures?.failed_polls.length || 0})
            </div>
            {(failures?.failed_polls.length || 0) === 0 ? (
              <div className="p-4 text-center text-xs text-ink-faint">None</div>
            ) : (
              failures!.failed_polls.map((p) => (
                <div key={p.id} className="border-b border-hairline-soft px-4 py-2 text-xs">
                  <div className="truncate text-danger">{p.error_message || "(no message)"}</div>
                  <div className="text-[10px] text-ink-faint">
                    {p.attempted_at ? new Date(p.attempted_at).toLocaleString() : "—"} · {p.duration_ms}ms
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

/* Expenses — company spend tracking. OWNER ONLY (super_admin).
 *
 * Five tabs over one date window:
 *   Overview   headline spend, period delta, monthly run-rate, trend + category split
 *   Ledger     every dated charge — add, edit, void
 *   Recurring  standing commitments (Railway, salaries) and posting them
 *   Agents     company pay rules (automatic per-sale pay) + each agent's pay plan,
 *              hourly rate per agent + logging hours, which prices into the ledger
 *   Audit      every change, from the app's existing audit_logs
 *
 * Money is integer CENTS end to end (see app/models/expense.py). This file is the
 * ONLY place cents become dollars — via money()/parseMoney() — so no arithmetic in
 * the UI ever touches a float.
 */
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  LabelList,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api } from "../lib/api";
import { brandColor } from "../lib/theme";
import { Drawer, Field, drawerCtl } from "../components/Drawer";

/* ── Types (mirror /api/v1/expenses) ─────────────────────────────────────── */
type Category = {
  id: string; slug: string; name: string;
  default_behavior: string; color: string | null; sort_order: number; is_active: boolean;
};
type Item = {
  id: string; category_id: string; category_name: string | null;
  name: string; vendor: string | null; behavior: string;
  amount_cents: number; interval: string;
  start_date: string | null; end_date: string | null; is_active: boolean;
  notes: string | null; monthly_cents: number; posted_this_period: boolean;
};
type Entry = {
  id: string; category_id: string; category_name: string | null; category_color: string | null;
  item_id: string | null; agent_id: string | null; agent_name: string | null;
  description: string; vendor: string | null; amount_cents: number;
  quantity: string | null; unit: string | null; unit_rate_cents: number | null;
  incurred_on: string; source: string; notes: string | null; voided_at: string | null;
};
type SaleRate = {
  id: string; agent_id: string;
  aca_cents: number; dental_cents: number; vision_cents: number;
  effective_at: string; note: string | null;
};
type AgentRow = {
  agent_id: string; agent_name: string; role?: string;
  current_rate_cents: number | null; rate_effective_from: string | null;
  hours: string; cost_cents: number;
  // Per-sale pay: derived from APPROVED deals, each at the rate in force when sold.
  sale_rate: SaleRate | null; sales: number; sale_pay_cents: number;
  // Approved sales no sale rate covered when they were logged — they pay $0.
  unrated_sales?: number;
  // This week under the company pay rules.
  week_commissions?: number; aca_tier?: number; aca_rate_cents?: number; exception?: boolean;
};
type PayTier = { min: number; cents: number };
type PayRuleSet = {
  aca_tiers: PayTier[]; work_days: number;
  dental_small_cents: number; dental_large_cents: number; dental_large_min: number;
  ancillary_cents: number; vision_cents: number | null;   // null = not offered yet
  per_member_carriers: string[];                          // carrier keys counted per member
};
type PayRulesResp = { rules: PayRuleSet; starts_on: string; updated_at: string; carriers: string[] };
type PayWeekStatus = {
  week_start: string; commissions: number; auto_tier: number; tier: number; rate_cents: number;
  exception: boolean; aca_cents: number; other_cents: number;
  next_tier: number | null; next_at: number | null; next_rate_cents: number | null; to_next: number | null;
};
type PayPlan = {
  agent_id: string; agent_name: string; rules: PayRuleSet; week: PayWeekStatus;
  exception: { id: string; tier: number; ends_on: string | null; reason: string } | null;
  history: (PayWeekStatus & { current: boolean })[];
};
type Summary = {
  range: { from: string; to: string };
  granularity: string;
  total_cents: number;
  entry_count: number;
  by_category: { category_id: string; name: string; color: string | null; amount_cents: number }[];
  trend: { label: string; date: string; amount_cents: number }[];
  monthly_commitment_cents: number;
  agent_hours: string;
  agent_cost_cents: number;       // hourly + per-sale
  agent_sales: number;
  agent_sale_pay_cents: number;
  agent_unrated_sales?: number;    // approved sales with no sale rate (they pay $0)
  previous_total_cents: number;
};
type RateRow = {
  id: string; agent_id: string; rate_cents_per_hour: number;
  effective_from: string; note: string | null;
};
type AuditRow = {
  id: string; action: string; resource_type: string; resource_id: string | null;
  user_name: string; details: Record<string, unknown>; created_at: string | null;
};

/* ── Money + dates ───────────────────────────────────────────────────────── */

/** Cents -> "$1,234.56". The ONLY cents->display conversion in the app. */
function money(cents: number | null | undefined): string {
  const v = (cents ?? 0) / 100;
  return v.toLocaleString("en-US", { style: "currency", currency: "USD" });
}
/** Cents -> "$1.2k" for axis ticks, where full precision is noise. */
function moneyShort(cents: number): string {
  const v = cents / 100;
  if (Math.abs(v) >= 1_000_000) return `$${(v / 1_000_000).toFixed(1)}M`;
  if (Math.abs(v) >= 1_000) return `$${(v / 1_000).toFixed(1)}k`;
  return `$${Math.round(v)}`;
}
/** "1,234.56" (or "1234.5") -> 123456 cents. Rounds; never returns a float.
 *  Malformed input ("12.3.4", ".", "") collapses to 0 rather than NaN — NaN would
 *  slip past an `amount > 0` guard (NaN > 0 and NaN <= 0 are both false) and post
 *  a null amount to the server. */
function parseMoney(text: string): number {
  const clean = (text || "").replace(/[^0-9.]/g, "");
  const n = Number(clean);
  if (!clean || !Number.isFinite(n)) return 0;
  return Math.round(n * 100);
}

/** Keeps a money/hours field numeric AS IT IS TYPED: digits, at most one dot,
 *  at most two decimals. parseMoney already refuses to produce NaN, but a field
 *  that visibly accepts "sfdsdf" and then silently saves nothing is worse than
 *  one that never lets the letter land. */
function numericInput(v: string): string {
  let out = (v || "").replace(/[^0-9.]/g, "");
  const dot = out.indexOf(".");
  if (dot !== -1) {
    out = out.slice(0, dot + 1) + out.slice(dot + 1).replace(/\./g, "");
  }
  const [whole, frac] = out.split(".");
  return frac === undefined ? whole : `${whole}.${frac.slice(0, 2)}`;
}

const BIZ_TZ = "America/New_York";
function todayISO(): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: BIZ_TZ }).format(new Date());
}
/** The Eastern calendar day (YYYY-MM-DD) an ISO timestamp falls on. */
function etDay(iso: string): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: BIZ_TZ }).format(new Date(iso));
}
function shiftISO(iso: string, days: number): string {
  const [y, m, d] = iso.split("-").map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d));
  dt.setUTCDate(dt.getUTCDate() + days);
  return dt.toISOString().slice(0, 10);
}
type PresetKey = "this_month" | "last_month" | "this_quarter" | "this_year" | "custom";
const PRESETS: { key: PresetKey; label: string }[] = [
  { key: "this_month", label: "This month" },
  { key: "last_month", label: "Last month" },
  { key: "this_quarter", label: "This quarter" },
  { key: "this_year", label: "This year" },
];
function presetRange(key: PresetKey): { from: string; to: string } {
  const t = todayISO();
  const [yy, mm] = t.split("-").map(Number);
  const pad = (n: number) => String(n).padStart(2, "0");
  switch (key) {
    case "last_month": {
      const y = mm === 1 ? yy - 1 : yy;
      const m = mm === 1 ? 12 : mm - 1;
      const first = `${y}-${pad(m)}-01`;
      return { from: first, to: shiftISO(`${yy}-${pad(mm)}-01`, -1) };
    }
    case "this_quarter": {
      const qStart = Math.floor((mm - 1) / 3) * 3 + 1;
      return { from: `${yy}-${pad(qStart)}-01`, to: t };
    }
    case "this_year":
      return { from: `${yy}-01-01`, to: t };
    case "this_month":
    default:
      return { from: `${yy}-${pad(mm)}-01`, to: t };
  }
}
/** Days in a window -> the trend bucket that keeps the series readable. */
function granularityFor(from: string, to: string): "day" | "week" | "month" {
  const [ay, am, ad] = from.split("-").map(Number);
  const [by, bm, bd] = to.split("-").map(Number);
  const days = (Date.UTC(by, bm - 1, bd) - Date.UTC(ay, am - 1, ad)) / 86_400_000 + 1;
  if (days > 180) return "month";
  if (days > 45) return "week";
  return "day";
}

/* ── Small shared pieces ─────────────────────────────────────────────────── */

function Panel({ title, sub, right, children }: {
  title: string; sub?: string; right?: ReactNode; children: ReactNode;
}) {
  return (
    <div className="glass rounded-2xl p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-base font-bold text-ink">{title}</h3>
          {sub && <p className="text-xs text-ink-muted">{sub}</p>}
        </div>
        {right}
      </div>
      <div className="mt-3">{children}</div>
    </div>
  );
}

/** A hex + alpha suffix, falling back to an accent wash for a missing colour. */
function tint(hex: string | null | undefined, alpha: string): string {
  return /^#[0-9a-f]{6}$/i.test(hex || "") ? `${hex}${alpha}` : `rgba(var(--accent-rgb),.14)`;
}

/** A headline number. No plot, so no hover layer — the value IS the chart.
 *  `color` paints the icon chip and a top rule, so the four tiles are readable
 *  at a glance; the number itself stays in ink so it never fights the colour. */
function Stat({ label, value, hint, tone, color, icon }: {
  label: string; value: string; hint?: ReactNode; tone?: "up" | "down";
  color: string; icon: string;
}) {
  return (
    <div className="glass relative overflow-hidden rounded-2xl p-4">
      <span className="absolute inset-x-0 top-0 h-1" style={{ background: color }} />
      <div className="flex items-start justify-between gap-2">
        <div className="text-[0.7rem] font-bold uppercase tracking-wide text-ink-faint">{label}</div>
        <span
          className="grid h-7 w-7 shrink-0 place-items-center rounded-lg text-sm"
          style={{ background: tint(color, "24"), border: `1px solid ${tint(color, "55")}` }}
          aria-hidden="true"
        >{icon}</span>
      </div>
      <div className="mt-1 text-2xl font-extrabold tabular-nums text-ink">{value}</div>
      {hint && (
        <div className={`mt-0.5 text-xs font-semibold ${
          tone === "up" ? "text-danger" : tone === "down" ? "text-success" : "text-ink-muted"
        }`}>{hint}</div>
      )}
    </div>
  );
}

/** The subject a drawer is acting on — an agent's name — as a coloured chip, so
 *  it reads as an identity rather than as more grey subtitle text. Getting the
 *  wrong person's rate or hours is the expensive mistake on this page. */
function SubjectChip({ name, color }: { name: string; color: string }) {
  return (
    <span
      className="inline-flex items-center rounded-full px-2 py-0.5 text-[0.72rem] font-extrabold"
      style={{ background: tint(color, "26"), color, border: `1px solid ${tint(color, "55")}` }}
    >{name}</span>
  );
}

/** Category chooser as colour-carrying chips. A <select> can't show the category
 *  colour, and the colour is how the same category is recognised in the ledger,
 *  the bars and the tiles — so the picker shows it too. */
function CategoryPicker({ cats, value, onChange }: {
  cats: Category[]; value: string; onChange: (id: string) => void;
}) {
  return (
    <div className="flex flex-wrap gap-2">
      {cats.map((c) => {
        const on = value === c.id;
        const col = c.color || "#64748B";
        return (
          <button
            key={c.id}
            type="button"
            aria-pressed={on}
            onClick={() => onChange(c.id)}
            className={`flex items-center gap-2 rounded-full border px-3 py-1.5 text-xs font-semibold transition ${
              on ? "text-ink" : "text-ink-muted hover:text-ink"
            }`}
            style={on
              ? { background: tint(col, "24"), borderColor: col }
              : { borderColor: "var(--color-hairline)" }}
          >
            <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: col }} />
            {c.name}
          </button>
        );
      })}
    </div>
  );
}

const inputCls = "rounded-lg border border-hairline bg-white px-2 py-1.5 text-xs text-ink";
const btnCls = "rounded-lg bg-accent px-3 py-1.5 text-xs font-bold text-white disabled:opacity-50";
const btnGhost = "rounded-lg border border-hairline px-3 py-1.5 text-xs font-semibold text-ink-muted hover:bg-black/5 hover:text-ink disabled:opacity-50";

const TABS = ["Overview", "Ledger", "Recurring", "Agents", "Audit"] as const;
type Tab = (typeof TABS)[number];

/* One colour per tab, from the same validated palette the categories and charts
   use — checked for lightness, chroma, colour-vision separation and contrast
   against both surfaces. Audit is indigo rather than cyan because cyan sat too
   close to the Agents green for a normal-vision reader to separate. The label
   text always carries the meaning; colour only reinforces it. */
const TAB_COLOR: Record<Tab, string> = {
  Overview: "#2563EB",
  Ledger: "#EA580C",
  Recurring: "#C026D3",
  Agents: "#059669",
  Audit: "#4F46E5",
};

/* ── Page ────────────────────────────────────────────────────────────────── */

export default function Expenses() {
  const [tab, setTab] = useState<Tab>("Overview");
  const [preset, setPreset] = useState<PresetKey>("this_month");
  const [from, setFrom] = useState(() => presetRange("this_month").from);
  const [to, setTo] = useState(() => presetRange("this_month").to);

  const [cats, setCats] = useState<Category[]>([]);
  const [sum, setSum] = useState<Summary | null>(null);
  const [entries, setEntries] = useState<Entry[]>([]);
  const [items, setItems] = useState<Item[]>([]);
  const [agents, setAgents] = useState<AgentRow[]>([]);
  const [audit, setAudit] = useState<AuditRow[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const applyPreset = (key: PresetKey) => {
    setPreset(key);
    if (key !== "custom") { const r = presetRange(key); setFrom(r.from); setTo(r.to); }
  };

  const qs = useMemo(() => {
    const p = new URLSearchParams({ from, to });
    return p.toString();
  }, [from, to]);

  const loadAll = useCallback(() => {
    const gran = granularityFor(from, to);
    Promise.all([
      api<Category[]>("/expenses/categories"),
      api<Summary>(`/expenses/summary?${qs}&granularity=${gran}`),
      api<Entry[]>(`/expenses/entries?${qs}`),
      api<Item[]>("/expenses/items"),
      api<AgentRow[]>(`/expenses/agents?${qs}`),
    ])
      .then(([c, s, e, i, a]) => {
        // Defensive: a list endpoint that ever answers with something other than an
        // array must degrade to "empty", not blank the page with a render crash.
        const arr = <T,>(v: unknown): T[] => (Array.isArray(v) ? (v as T[]) : []);
        setCats(arr<Category>(c));
        setSum(s && typeof s === "object" ? s : null);
        setEntries(arr<Entry>(e));
        setItems(arr<Item>(i));
        setAgents(arr<AgentRow>(a));
        setErr(null);
      })
      .catch((x: Error) => setErr(x.message));
  }, [qs, from, to]);

  useEffect(() => { loadAll(); }, [loadAll]);
  useEffect(() => {
    if (tab !== "Audit") return;
    api<{ items: AuditRow[] }>("/expenses/audit").then((r) => setAudit(r.items ?? [])).catch(() => {});
  }, [tab]);

  // Every mutation funnels through here so one failure path shows one message and
  // the whole window reloads — totals can never drift from the rows that made them.
  const mutate = useCallback(async (fn: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await fn();
      loadAll();
      if (tab === "Audit") {
        api<{ items: AuditRow[] }>("/expenses/audit").then((r) => setAudit(r.items ?? [])).catch(() => {});
      }
      setErr(null);
    } catch (x) {
      setErr((x as Error).message);
    } finally {
      setBusy(false);
    }
  }, [loadAll, tab]);

  const catById = useMemo(() => Object.fromEntries(cats.map((c) => [c.id, c])), [cats]);

  return (
    <div className="space-y-4">
      {/* Header: title + the one filter row that governs every tab. */}
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-extrabold text-ink">Expenses</h1>
          <p className="text-xs text-ink-muted">
            Everything the company spends — servers, staff, agent hours. Owner only.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {PRESETS.map((p) => (
            <button
              key={p.key}
              onClick={() => applyPreset(p.key)}
              className={preset === p.key
                ? "rounded-lg bg-accent px-3 py-1.5 text-xs font-bold text-white"
                : btnGhost}
            >{p.label}</button>
          ))}
          <input type="date" value={from} max={to} className={inputCls}
                 onChange={(e) => { setPreset("custom"); setFrom(e.target.value); }} />
          <span className="text-xs text-ink-faint">to</span>
          <input type="date" value={to} min={from} className={inputCls}
                 onChange={(e) => { setPreset("custom"); setTo(e.target.value); }} />
        </div>
      </div>

      {err && (
        <div className="rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-xs font-semibold text-danger">
          {err}
        </div>
      )}

      <div className="flex flex-wrap gap-1.5">
        {TABS.map((t) => {
          const col = TAB_COLOR[t];
          const on = tab === t;
          return (
            <button
              key={t}
              onClick={() => setTab(t)}
              aria-current={on ? "page" : undefined}
              className={`group flex items-center gap-2 rounded-full border px-3.5 py-2 text-sm font-bold transition ${
                on ? "shadow-sm" : "border-transparent text-ink-muted hover:text-ink"
              }`}
              style={on
                ? { background: tint(col, "24"), borderColor: tint(col, "66"), color: col }
                : undefined}
            >
              <span
                className={`h-2 w-2 shrink-0 rounded-full transition ${on ? "" : "opacity-40 group-hover:opacity-90"}`}
                style={{ background: col }}
              />
              {t}
            </button>
          );
        })}
      </div>

      {tab === "Overview" && <Overview sum={sum} from={from} to={to} onSetRates={() => setTab("Agents")} />}
      {tab === "Ledger" && (
        <Ledger entries={entries} cats={cats} busy={busy} mutate={mutate} defaultDate={to} />
      )}
      {tab === "Recurring" && (
        <Recurring items={items} cats={cats} busy={busy} mutate={mutate} />
      )}
      {tab === "Agents" && (
        <Agents rows={agents} entries={entries} busy={busy} mutate={mutate}
                defaultDate={to} catById={catById} />
      )}
      {tab === "Audit" && <Audit rows={audit} agents={agents} />}
    </div>
  );
}

/* ── Overview ────────────────────────────────────────────────────────────── */

function Overview({ sum, from, to, onSetRates }: {
  sum: Summary | null; from: string; to: string; onSetRates: () => void;
}) {
  const accent = brandColor("--accent") || "#2563EB";
  if (!sum) return <div className="glass rounded-2xl p-6 text-sm text-ink-muted">Loading…</div>;

  const delta = sum.total_cents - sum.previous_total_cents;
  const deltaPct = sum.previous_total_cents > 0
    ? Math.round((delta / sum.previous_total_cents) * 100)
    : null;

  const trend = (sum.trend ?? []).map((p) => ({ ...p, dollars: p.amount_cents / 100 }));
  // Category comparison is a BAR chart, not a donut: the job is comparing
  // magnitudes, and bars carry a name axis + a direct value label, so colour
  // reinforces identity rather than being the only thing encoding it.
  const bars = (sum.by_category ?? []).map((c) => ({
    name: c.name,
    dollars: c.amount_cents / 100,
    cents: c.amount_cents,
    color: c.color || accent,
  }));
  const barHeight = Math.max(140, bars.length * 34 + 24);

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Stat
          label="Total spend" icon="💷" color="#2563EB"
          value={money(sum.total_cents)}
          hint={`${sum.entry_count} ${sum.entry_count === 1 ? "entry" : "entries"} · ${from} → ${to}`}
        />
        <Stat
          label="vs previous period" icon={delta > 0 ? "📈" : "📉"}
          color={delta > 0 ? "#E11D48" : "#059669"}
          value={`${delta >= 0 ? "+" : "−"}${money(Math.abs(delta))}`}
          hint={deltaPct === null
            ? "no spend in the previous period"
            : `${deltaPct >= 0 ? "+" : ""}${deltaPct}% · was ${money(sum.previous_total_cents)}`}
          tone={delta > 0 ? "up" : delta < 0 ? "down" : undefined}
        />
        <Stat
          label="Monthly run-rate" icon="🔁" color="#C026D3"
          value={money(sum.monthly_commitment_cents)}
          hint="active recurring commitments"
        />
        <Stat
          label="Agent pay" icon="⏱️" color="#059669"
          value={money(sum.agent_cost_cents)}
          hint={<>
            {`${Number(sum.agent_hours || 0).toLocaleString()} h · ${(sum.agent_sales || 0).toLocaleString()} approved ${sum.agent_sales === 1 ? "sale" : "sales"}`}
            {/* A sale with no rate behind it pays $0 — say so, and where to fix it. */}
            {!!sum.agent_unrated_sales && (
              <button type="button" onClick={onSetRates}
                      className="mt-1 block text-left font-bold text-danger underline decoration-dotted underline-offset-2">
                {sum.agent_unrated_sales} logged before the pay rules started — see pay rules
              </button>
            )}
          </>}
        />
      </div>

      <Panel title="Spend over time" sub={`Bucketed by ${sum.granularity}. One series — total spend.`}>
        {trend.length === 0 ? (
          <Empty>No spend recorded in this window.</Empty>
        ) : (
          <div style={{ height: 240 }}>
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={trend} margin={{ top: 8, right: 8, bottom: 0, left: 8 }}>
                <CartesianGrid vertical={false} strokeDasharray="3 3" />
                <XAxis dataKey="label" tickLine={false} axisLine={false}
                       tick={{ fontSize: 11 }} minTickGap={16} />
                <YAxis tickLine={false} axisLine={false} width={54} tick={{ fontSize: 11 }}
                       tickFormatter={(v: number) => moneyShort(v * 100)} />
                <Tooltip
                  cursor={{ stroke: accent, strokeWidth: 1, strokeDasharray: "3 3" }}
                  formatter={(v) => [money(Math.round(Number(v) * 100)), "Spend"] as [string, string]}
                />
                {/* Flat tinted fill rather than a gradient <defs>: an id-referenced
                    gradient inside a Recharts chart is not carried into the rendered
                    SVG's defs, so the fill silently resolves to nothing. */}
                <Area type="monotone" dataKey="dollars" stroke={accent} strokeWidth={2}
                      fill={accent} fillOpacity={0.14} dot={false} activeDot={{ r: 4 }}
                      isAnimationActive={false} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        )}
      </Panel>

      <Panel title="Where it went" sub="By category, largest first. Values are labelled directly.">
        {bars.length === 0 ? (
          <Empty>Nothing to break down yet.</Empty>
        ) : (
          <div style={{ height: barHeight }}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={bars} layout="vertical" margin={{ top: 4, right: 76, bottom: 4, left: 8 }}>
                <CartesianGrid horizontal={false} strokeDasharray="3 3" />
                <XAxis type="number" hide tickFormatter={(v: number) => moneyShort(v * 100)} />
                <YAxis type="category" dataKey="name" width={150} tickLine={false} axisLine={false}
                       tick={{ fontSize: 11 }} />
                <Tooltip cursor={{ fillOpacity: 0.06 }}
                         formatter={(v) => [money(Math.round(Number(v) * 100)), "Spend"] as [string, string]} />
                <Bar dataKey="dollars" radius={[0, 4, 4, 0]} barSize={18} isAnimationActive={false}>
                  {bars.map((b) => <Cell key={b.name} fill={b.color} />)}
                  <LabelList dataKey="cents" position="right"
                             formatter={(v) => money(Number(v))}
                             style={{ fontSize: 11, fontWeight: 700, fill: "var(--color-ink-soft)" }} />
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
      </Panel>
    </div>
  );
}

function Empty({ children }: { children: ReactNode }) {
  return <div className="py-8 text-center text-sm text-ink-muted">{children}</div>;
}

/* ── Ledger ──────────────────────────────────────────────────────────────── */

function Ledger({ entries, cats, busy, mutate, defaultDate }: {
  entries: Entry[]; cats: Category[]; busy: boolean;
  mutate: (fn: () => Promise<unknown>) => Promise<void>; defaultDate: string;
}) {
  const [filter, setFilter] = useState<string>("");
  const [adding, setAdding] = useState(false);
  const blank = { category_id: "", description: "", amount: "", vendor: "", incurred_on: defaultDate, notes: "" };
  const [f, setF] = useState(blank);
  const [editing, setEditing] = useState<string | null>(null);
  const [ef, setEf] = useState({ description: "", amount: "", incurred_on: "" });

  useEffect(() => { setF((p) => ({ ...p, incurred_on: defaultDate })); }, [defaultDate]);

  const shown = filter ? entries.filter((e) => e.category_id === filter) : entries;
  const total = shown.reduce((s, e) => s + (e.voided_at ? 0 : e.amount_cents), 0);

  // Save is blocked until the three things a charge cannot exist without are set.
  const valid = !!f.category_id && !!f.description.trim() && parseMoney(f.amount) > 0;
  const close = () => { setAdding(false); setF({ ...blank, incurred_on: defaultDate }); };
  const submit = () => {
    if (!valid) return;
    mutate(() => api("/expenses/entries", {
      method: "POST",
      body: JSON.stringify({
        category_id: f.category_id,
        description: f.description.trim(),
        amount_cents: parseMoney(f.amount),
        incurred_on: f.incurred_on,
        vendor: f.vendor.trim() || null,
        notes: f.notes.trim() || null,
      }),
    })).then(close);
  };

  return (
    <Panel
      title="Ledger"
      sub={`${shown.length} ${shown.length === 1 ? "charge" : "charges"} · ${money(total)} in view`}
      right={
        <div className="flex flex-wrap items-center gap-2">
          <select className={inputCls} value={filter} onChange={(e) => setFilter(e.target.value)}>
            <option value="">All categories</option>
            {cats.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
          <button className={btnCls} disabled={busy} onClick={() => setAdding(true)}>
            + Add expense
          </button>
        </div>
      }
    >
      <Drawer
        open={adding}
        title="Add expense"
        sub="One dated charge. It lands in the ledger straight away."
        icon="🧾"
        tone={cats.find((c) => c.id === f.category_id)?.color || undefined}
        onClose={close}
        footer={
          <>
            <button className={btnGhost} onClick={close}>Cancel</button>
            <button className={btnCls} disabled={busy || !valid} onClick={submit}>
              {busy ? "Saving…" : `Save ${parseMoney(f.amount) > 0 ? money(parseMoney(f.amount)) : "expense"}`}
            </button>
          </>
        }
      >
        <Field label="Category" required plain>
          <CategoryPicker cats={cats} value={f.category_id}
                          onChange={(id) => setF({ ...f, category_id: id })} />
        </Field>
        <Field label="What was it for" required>
          <input className={drawerCtl} placeholder="e.g. Sinch SMS — August" value={f.description}
                 onChange={(e) => setF({ ...f, description: e.target.value })}
                 onKeyDown={(e) => { if (e.key === "Enter" && valid) submit(); }} />
        </Field>
        <Field label="Amount" required hint="US dollars — cents optional.">
          <div className="relative">
            <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-sm font-bold text-ink-faint">$</span>
            <input className={`${drawerCtl} pl-7 font-bold tabular-nums`}
                   inputMode="decimal" placeholder="0.00" value={f.amount}
                   onChange={(e) => setF({ ...f, amount: numericInput(e.target.value) })}
                   onKeyDown={(e) => { if (e.key === "Enter" && valid) submit(); }} />
          </div>
        </Field>
        <Field label="Date" required hint="The day the money was spent, not the day you logged it.">
          <input className={drawerCtl} type="date" value={f.incurred_on}
                 onChange={(e) => setF({ ...f, incurred_on: e.target.value })} />
        </Field>
        <Field label="Vendor">
          <input className={drawerCtl} placeholder="Optional — who was paid" value={f.vendor}
                 onChange={(e) => setF({ ...f, vendor: e.target.value })} />
        </Field>
        <Field label="Notes">
          <textarea className={drawerCtl} rows={3} placeholder="Optional — anything worth remembering later"
                    value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} />
        </Field>
      </Drawer>

      {shown.length === 0 ? (
        <Empty>No charges in this window.</Empty>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[720px] text-left text-xs">
            <thead className="text-ink-faint">
              <tr className="border-b border-hairline-soft">
                <Th>Date</Th><Th>Category</Th><Th>Description</Th><Th>Source</Th>
                <Th className="text-right">Amount</Th><Th className="text-right">Actions</Th>
              </tr>
            </thead>
            <tbody>
              {shown.map((e) => {
                const isEditing = editing === e.id;
                return (
                  <tr key={e.id} className={`border-b border-hairline-soft/60 ${e.voided_at ? "opacity-45" : ""}`}>
                    <Td>
                      {isEditing ? (
                        <input className={inputCls} type="date" value={ef.incurred_on}
                               onChange={(x) => setEf({ ...ef, incurred_on: x.target.value })} />
                      ) : e.incurred_on}
                    </Td>
                    <Td>
                      <span
                        className="inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[0.7rem] font-semibold"
                        style={{
                          background: tint(e.category_color, "1a"),
                          borderColor: tint(e.category_color, "44"),
                        }}
                      >
                        <span className="h-2 w-2 shrink-0 rounded-full"
                              style={{ background: e.category_color || "var(--color-ink-faint)" }} />
                        {e.category_name || "—"}
                      </span>
                    </Td>
                    <Td>
                      {isEditing ? (
                        <input className={`${inputCls} w-full`} value={ef.description}
                               onChange={(x) => setEf({ ...ef, description: x.target.value })} />
                      ) : (
                        <>
                          <span className="font-semibold text-ink">{e.description}</span>
                          {e.vendor && <span className="text-ink-faint"> · {e.vendor}</span>}
                          {e.quantity && e.unit_rate_cents != null && (
                            <span className="text-ink-faint">
                              {" "}· {e.quantity} {e.unit === "hour" ? "h" : e.unit} @{" "}
                              {money(e.unit_rate_cents)}/{e.unit === "hour" ? "h" : e.unit}
                            </span>
                          )}
                        </>
                      )}
                    </Td>
                    <Td><span className="text-ink-faint">{e.voided_at ? "voided" : e.source}</span></Td>
                    <Td className="text-right font-bold tabular-nums text-ink">
                      {isEditing ? (
                        <input className={`${inputCls} w-24 text-right`} inputMode="decimal" value={ef.amount}
                               onChange={(x) => setEf({ ...ef, amount: numericInput(x.target.value) })} />
                      ) : money(e.amount_cents)}
                    </Td>
                    <Td className="text-right whitespace-nowrap">
                      {e.voided_at ? (
                        <span className="text-ink-faint">—</span>
                      ) : isEditing ? (
                        <>
                          <button className={btnCls} disabled={busy} onClick={() => {
                            mutate(() => api(`/expenses/entries/${e.id}`, {
                              method: "PATCH",
                              body: JSON.stringify({
                                description: ef.description,
                                amount_cents: parseMoney(ef.amount),
                                incurred_on: ef.incurred_on,
                              }),
                            })).then(() => setEditing(null));
                          }}>Save</button>{" "}
                          <button className={btnGhost} onClick={() => setEditing(null)}>Cancel</button>
                        </>
                      ) : (
                        <>
                          <button className={btnGhost} onClick={() => {
                            setEditing(e.id);
                            setEf({
                              description: e.description,
                              amount: (e.amount_cents / 100).toFixed(2),
                              incurred_on: e.incurred_on,
                            });
                          }}>Edit</button>{" "}
                          <button
                            className="rounded-lg border border-danger/40 px-3 py-1.5 text-xs font-semibold text-danger hover:bg-danger/10 disabled:opacity-50"
                            disabled={busy}
                            title="Void this charge — the row is kept for the audit trail"
                            onClick={() => {
                              if (!window.confirm(`Void "${e.description}" (${money(e.amount_cents)})? It stays in the audit trail but leaves every total.`)) return;
                              mutate(() => api(`/expenses/entries/${e.id}`, { method: "DELETE" }));
                            }}
                          >Void</button>
                        </>
                      )}
                    </Td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

function Th({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <th className={`px-2 py-2 text-[0.68rem] font-bold uppercase tracking-wide ${className}`}>{children}</th>;
}
function Td({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <td className={`px-2 py-2 align-middle text-ink-soft ${className}`}>{children}</td>;
}

/* ── Recurring ───────────────────────────────────────────────────────────── */

function Recurring({ items, cats, busy, mutate }: {
  items: Item[]; cats: Category[]; busy: boolean;
  mutate: (fn: () => Promise<unknown>) => Promise<void>;
}) {
  const [adding, setAdding] = useState(false);
  const blank = { category_id: "", name: "", vendor: "", amount: "", interval: "monthly",
                  start_date: todayISO(), notes: "" };
  const [f, setF] = useState(blank);
  const monthly = items.filter((i) => i.is_active).reduce((s, i) => s + i.monthly_cents, 0);
  const catColor: Record<string, string> = Object.fromEntries(
    cats.map((c) => [c.id, c.color || "#64748B"]),
  );

  const valid = !!f.category_id && !!f.name.trim() && parseMoney(f.amount) > 0;
  const close = () => { setAdding(false); setF(blank); };
  const submit = () => {
    if (!valid) return;
    mutate(() => api("/expenses/items", {
      method: "POST",
      body: JSON.stringify({
        category_id: f.category_id,
        name: f.name.trim(),
        vendor: f.vendor.trim() || null,
        amount_cents: parseMoney(f.amount),
        interval: f.interval,
        behavior: "fixed_recurring",
        start_date: f.start_date,
        notes: f.notes.trim() || null,
      }),
    })).then(close);
  };
  // What this commitment will add to the monthly run-rate, shown live as it's typed.
  const previewMonthly = (() => {
    const c = parseMoney(f.amount);
    if (f.interval === "weekly") return Math.round((c * 52) / 12);
    if (f.interval === "yearly") return Math.round(c / 12);
    return c;
  })();

  return (
    <Panel
      title="Recurring commitments"
      sub={`${money(monthly)} per month committed. Posting one turns it into a real charge in the ledger.`}
      right={
        <button className={btnCls} disabled={busy} onClick={() => setAdding(true)}>
          + Add commitment
        </button>
      }
    >
      <Drawer
        open={adding}
        title="Add commitment"
        sub="A standing cost. It only becomes real money when you post it."
        icon="🔁"
        tone={cats.find((c) => c.id === f.category_id)?.color || undefined}
        onClose={close}
        footer={
          <>
            <button className={btnGhost} onClick={close}>Cancel</button>
            <button className={btnCls} disabled={busy || !valid} onClick={submit}>
              {busy ? "Saving…" : "Save commitment"}
            </button>
          </>
        }
      >
        <Field label="Category" required plain>
          <CategoryPicker cats={cats} value={f.category_id}
                          onChange={(id) => setF({ ...f, category_id: id })} />
        </Field>
        <Field label="Name" required>
          <input className={drawerCtl} placeholder="e.g. Railway deployment" value={f.name}
                 onChange={(e) => setF({ ...f, name: e.target.value })}
                 onKeyDown={(e) => { if (e.key === "Enter" && valid) submit(); }} />
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Amount" required>
            <div className="relative">
              <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-sm font-bold text-ink-faint">$</span>
              <input className={`${drawerCtl} pl-7 font-bold tabular-nums`}
                     inputMode="decimal" placeholder="0.00" value={f.amount}
                     onChange={(e) => setF({ ...f, amount: numericInput(e.target.value) })} />
            </div>
          </Field>
          <Field label="Every">
            <select className={drawerCtl} value={f.interval}
                    onChange={(e) => setF({ ...f, interval: e.target.value })}>
              <option value="monthly">Month</option>
              <option value="weekly">Week</option>
              <option value="yearly">Year</option>
            </select>
          </Field>
        </div>
        {previewMonthly > 0 && (
          <div className="rounded-lg border border-hairline bg-black/5 px-3 py-2 text-xs text-ink-muted">
            Adds <strong className="text-ink">{money(previewMonthly)}</strong> to the monthly run-rate.
          </div>
        )}
        <Field label="Starts" hint="Before this date the commitment doesn't count toward the run-rate.">
          <input className={drawerCtl} type="date" value={f.start_date}
                 onChange={(e) => setF({ ...f, start_date: e.target.value })} />
        </Field>
        <Field label="Vendor">
          <input className={drawerCtl} placeholder="Optional — who gets paid" value={f.vendor}
                 onChange={(e) => setF({ ...f, vendor: e.target.value })} />
        </Field>
        <Field label="Notes">
          <textarea className={drawerCtl} rows={3} placeholder="Optional"
                    value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} />
        </Field>
      </Drawer>

      {items.length === 0 ? (
        <Empty>No standing commitments yet — add Railway, Claude, salaries here.</Empty>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[720px] text-left text-xs">
            <thead className="text-ink-faint">
              <tr className="border-b border-hairline-soft">
                <Th>Name</Th><Th>Category</Th><Th>Interval</Th>
                <Th className="text-right">Amount</Th><Th className="text-right">Per month</Th>
                <Th className="text-right">Actions</Th>
              </tr>
            </thead>
            <tbody>
              {items.map((i) => (
                <tr key={i.id} className="border-b border-hairline-soft/60">
                  <Td>
                    <span className="font-semibold text-ink">{i.name}</span>
                    {i.vendor && <span className="text-ink-faint"> · {i.vendor}</span>}
                  </Td>
                  <Td>
                    <span
                      className="inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[0.7rem] font-semibold"
                      style={{
                        background: tint(catColor[i.category_id], "1a"),
                        borderColor: tint(catColor[i.category_id], "44"),
                      }}
                    >
                      <span className="h-2 w-2 shrink-0 rounded-full"
                            style={{ background: catColor[i.category_id] || "var(--color-ink-faint)" }} />
                      {i.category_name || "—"}
                    </span>
                  </Td>
                  <Td className="capitalize">{i.interval}</Td>
                  <Td className="text-right font-bold tabular-nums text-ink">{money(i.amount_cents)}</Td>
                  <Td className="text-right tabular-nums">{money(i.monthly_cents)}</Td>
                  <Td className="text-right whitespace-nowrap">
                    <button
                      className={i.posted_this_period ? btnGhost : btnCls}
                      disabled={busy || i.posted_this_period}
                      title={i.posted_this_period
                        ? "Already posted this month"
                        : "Create this month's charge in the ledger"}
                      onClick={() => mutate(() => api(`/expenses/items/${i.id}/post`, {
                        method: "POST", body: JSON.stringify({ incurred_on: todayISO() }),
                      }))}
                    >{i.posted_this_period ? "Posted ✓" : "Post this month"}</button>{" "}
                    <button className={btnGhost} disabled={busy}
                            title="Stop this commitment — past charges are untouched"
                            onClick={() => mutate(() => api(`/expenses/items/${i.id}`, {
                              method: "PATCH", body: JSON.stringify({ is_active: false }),
                            }))}
                    >Retire</button>
                  </Td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

/* ── Agents ──────────────────────────────────────────────────────────────── */

function Agents({ rows, entries, busy, mutate, defaultDate, catById }: {
  rows: AgentRow[]; entries: Entry[]; busy: boolean;
  mutate: (fn: () => Promise<unknown>) => Promise<void>;
  defaultDate: string; catById: Record<string, Category>;
}) {
  const [rateFor, setRateFor] = useState<AgentRow | null>(null);
  const [rate, setRate] = useState("");
  const [rateFrom, setRateFrom] = useState(todayISO());
  const [rateNote, setRateNote] = useState("");
  const [hoursFor, setHoursFor] = useState<AgentRow | null>(null);
  const [hours, setHours] = useState("");
  const [workDate, setWorkDate] = useState(defaultDate);
  // Rate history for whichever agent a drawer is open on. Fetched once per open;
  // it drives the rate-in-force preview so the drawer prices a line exactly the
  // way the server will, instead of guessing with today's rate.
  const [history, setHistory] = useState<RateRow[]>([]);
  // Per-sale pay is automatic (company pay rules); the drawer shows one agent's
  // plan and is where an exception is set.
  const [planFor, setPlanFor] = useState<AgentRow | null>(null);
  const [payRules, setPayRules] = useState<PayRulesResp | null>(null);
  const loadRules = useCallback(() => {
    api<PayRulesResp>("/expenses/pay-rules").then(setPayRules).catch(() => {});
  }, []);
  useEffect(() => { loadRules(); }, [loadRules]);

  useEffect(() => { setWorkDate(defaultDate); }, [defaultDate]);

  const openFor = rateFor || hoursFor;
  useEffect(() => {
    if (!openFor) { setHistory([]); return; }
    let live = true;
    api<RateRow[]>(`/expenses/agents/${openFor.agent_id}/rates`)
      .then((r) => { if (live) setHistory(Array.isArray(r) ? r : []); })
      .catch(() => { if (live) setHistory([]); });
    return () => { live = false; };
  }, [openFor]);

  // The rate that applies on a given day: the latest one effective on or before it.
  const rateOn = (iso: string): RateRow | null =>
    history
      .filter((h) => h.effective_from <= iso)
      .sort((a, b) => (a.effective_from < b.effective_from ? 1 : -1))[0] || null;

  const priced = hoursFor ? rateOn(workDate) : null;
  const hoursRaw = Number(hours);
  const hoursNum = Number.isFinite(hoursRaw) && hoursRaw > 0 ? hoursRaw : 0;
  const previewCost = priced ? Math.round(hoursNum * priced.rate_cents_per_hour) : 0;
  const closeRate = () => { setRateFor(null); setRate(""); setRateNote(""); };
  const closeHours = () => { setHoursFor(null); setHours(""); };

  // Finding someone is the whole job on this tab: an agent is already listed the
  // moment they exist, so "putting them on hourly pay" just means locating their
  // row and setting a rate. Search by name + a payroll filter do that.
  const [q, setQ] = useState("");
  // Every user owns a profile (admins log their own deals too), but the pay table
  // is about the sales team — so it opens on agents and "All users" is one click away.
  const [only, setOnly] = useState<"agents" | "all" | "paid" | "exception">("agents");
  const isAgent = (r: AgentRow) => r.role === "agent";

  const shown = rows.filter((r) => {
    if (q && !r.agent_name.toLowerCase().includes(q.trim().toLowerCase())) return false;
    if (only === "agents" && !isAgent(r)) return false;
    if (only === "paid" && r.current_rate_cents == null) return false;
    if (only === "exception" && !r.exception) return false;
    return true;
  });
  const exceptionCount = rows.filter((r) => r.exception).length;
  const agentCount = rows.filter(isAgent).length;
  const unratedSales = rows.reduce((n, r) => n + (r.unrated_sales || 0), 0);

  const hourLines = entries.filter((e) => e.agent_id && !e.voided_at);
  const totalCost = rows.reduce((s, r) => s + r.cost_cents + (r.sale_pay_cents || 0), 0);

  return (
    <div className="space-y-4">
      <CompanyPayRules
        data={payRules} busy={busy}
        onSave={(rules, startsOn) => {
          let ok = false;
          return mutate(async () => {
            await api("/expenses/pay-rules", { method: "PUT", body: JSON.stringify({ rules, starts_on: startsOn }) });
            ok = true;
          }).then(() => { if (ok) loadRules(); return ok; });
        }}
      />
      <Panel
        title="Agent pay"
        sub={
          `${money(totalCost)} in this window · showing ${shown.length} of ${rows.length}` +
          (unratedSales
            ? ` · ${unratedSales} approved ${unratedSales === 1 ? "sale was" : "sales were"} logged before the pay rules started (paying $0) — move the start date in Edit rules`
            : "")
        }
        right={
          <div className="flex flex-wrap items-center gap-2">
            <div className="relative">
              <svg className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-ink-faint"
                   viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                <circle cx="11" cy="11" r="7" /><path d="m21 21-4.3-4.3" />
              </svg>
              <input
                className={`${inputCls} w-48 pl-7`}
                placeholder="Search agents…"
                value={q}
                onChange={(e) => setQ(e.target.value)}
              />
              {q && (
                <button onClick={() => setQ("")} aria-label="Clear search"
                        className="absolute right-2 top-1/2 -translate-y-1/2 text-ink-faint hover:text-ink">×</button>
              )}
            </div>
            {([
              ["agents", `All agents ${agentCount}`],
              ["all", `All users ${rows.length}`],
              ["paid", "On hourly pay"],
              ["exception", `Tier exceptions ${exceptionCount}`],
            ] as const).map(([k, label]) => (
              <button
                key={k}
                onClick={() => setOnly(k)}
                className={only === k
                  ? "rounded-lg bg-accent px-3 py-1.5 text-xs font-bold text-white"
                  : btnGhost}
              >{label}</button>
            ))}
          </div>
        }
      >
        {rows.length === 0 ? (
          <Empty>No agents on this tenant yet.</Empty>
        ) : shown.length === 0 ? (
          <Empty>
            No agent matches {q ? <>“{q}”</> : "this filter"}.{" "}
            <button className="font-semibold text-accent underline"
                    onClick={() => { setQ(""); setOnly("all"); }}>Show all {rows.length} users</button>
          </Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[980px] text-left text-xs">
              <thead className="text-ink-faint">
                <tr className="border-b border-hairline-soft">
                  <Th>Agent</Th><Th>Hourly rate</Th><Th>ACA tier this week</Th>
                  <Th className="text-right">Hours</Th><Th className="text-right">Hour pay</Th>
                  <Th className="text-right">Sales</Th><Th className="text-right">Sale pay</Th>
                  <Th className="text-right">Total</Th>
                  <Th className="text-right">Actions</Th>
                </tr>
              </thead>
              <tbody>
                {shown.map((r) => (
                  <tr key={r.agent_id} className="border-b border-hairline-soft/60">
                    <Td><span className="font-semibold text-ink">{r.agent_name}</span></Td>
                    <Td>
                      {r.current_rate_cents != null ? (
                        <span className="tabular-nums">
                          {money(r.current_rate_cents)}/h
                          <span className="text-ink-faint"> from {r.rate_effective_from}</span>
                        </span>
                      ) : (
                        <span className="text-ink-faint">Not hourly</span>
                      )}
                    </Td>
                    <Td>
                      <span className="tabular-nums">
                        <strong className="text-ink">Tier {r.aca_tier ?? 1}</strong> · {dollars(r.aca_rate_cents)}
                        <span className="text-ink-faint"> · {r.week_commissions ?? 0} this week</span>
                      </span>
                      {r.exception && (
                        <span className="ml-1.5 rounded bg-amber-500/15 px-1.5 py-0.5 text-[0.6rem] font-bold uppercase text-amber-800 dark:text-amber-300">Exception</span>
                      )}
                    </Td>
                    <Td className="text-right tabular-nums">{Number(r.hours || 0).toLocaleString()}</Td>
                    <Td className="text-right tabular-nums">{money(r.cost_cents)}</Td>
                    <Td className="text-right tabular-nums">
                      {(r.sales || 0).toLocaleString()}
                      {!!r.unrated_sales && (
                        <span className="block text-[0.65rem] font-bold text-danger"
                              title="Logged before the pay rules started and with no per-agent rate, so they pay $0. Move the rules' start date earlier in Edit rules.">
                          {r.unrated_sales} no rate
                        </span>
                      )}
                    </Td>
                    <Td className="text-right tabular-nums">{money(r.sale_pay_cents || 0)}</Td>
                    <Td className="text-right font-bold tabular-nums text-ink">{money(r.cost_cents + (r.sale_pay_cents || 0))}</Td>
                    <Td className="text-right whitespace-nowrap">
                      <button className={btnGhost} onClick={() => {
                        setRateFor(r);
                        setRate(r.current_rate_cents != null ? (r.current_rate_cents / 100).toFixed(2) : "");
                        setRateFrom(todayISO());
                        setRateNote("");
                      }}>Hourly rate</button>{" "}
                      <button className={btnGhost} onClick={() => setPlanFor(r)}>Pay plan</button>{" "}
                      <button className={btnCls} disabled={busy || r.current_rate_cents == null}
                              title={r.current_rate_cents == null ? "Set an hourly rate first" : "Log hours worked"}
                              onClick={() => { setHoursFor(r); setHours(""); }}
                      >Log hours</button>
                    </Td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

      </Panel>

      {/* ── Log hours ─────────────────────────────────────────────────────── */}
      <Drawer
        open={!!hoursFor}
        title="Log hours"
        sub={hoursFor ? (
          <span className="inline-flex flex-wrap items-center gap-1.5">
            <SubjectChip name={hoursFor.agent_name} color="#059669" />
            <span>priced at the rate in force on the work date</span>
          </span>
        ) : ""}
        icon="⏱️"
        tone="#059669"
        onClose={closeHours}
        footer={
          <>
            <button className={btnGhost} onClick={closeHours}>Cancel</button>
            <button className={btnCls} disabled={busy || hoursNum <= 0 || !priced} onClick={() => {
              if (!hoursFor) return;
              mutate(() => api("/expenses/hours", {
                method: "POST",
                body: JSON.stringify({
                  agent_id: hoursFor.agent_id, work_date: workDate, hours: hoursNum,
                }),
              })).then(closeHours);
            }}>
              {busy ? "Logging…" : previewCost > 0 ? `Log ${money(previewCost)}` : "Log hours"}
            </button>
          </>
        }
      >
        <div className="grid grid-cols-2 gap-3">
          <Field label="Hours" required>
            <input className={`${drawerCtl} font-bold tabular-nums`} inputMode="decimal"
                   placeholder="7.5" value={hours}
                   onChange={(e) => setHours(numericInput(e.target.value))} />
          </Field>
          <Field label="Work date" required>
            <input className={drawerCtl} type="date" value={workDate}
                   onChange={(e) => setWorkDate(e.target.value)} />
          </Field>
        </div>

        {/* The maths, shown before it is committed — including the case where the
            agent had no rate yet on the chosen day, which the server would reject. */}
        {priced ? (
          <div className="rounded-xl border border-hairline bg-black/5 px-3 py-2.5 text-xs text-ink-muted">
            <div className="flex items-center justify-between gap-3">
              <span>
                {hoursNum || 0} h × {money(priced.rate_cents_per_hour)}/h
              </span>
              <strong className="text-sm text-ink">{money(previewCost)}</strong>
            </div>
            <div className="mt-1 text-[0.7rem] text-ink-faint">
              Rate effective {priced.effective_from}. It is snapshotted onto the line,
              so a later raise won't restate it.
            </div>
          </div>
        ) : (
          <div className="rounded-xl border border-danger/40 bg-danger/10 px-3 py-2.5 text-xs font-semibold text-danger">
            No hourly rate was in force on {workDate}. Set a rate effective on or
            before that date first.
          </div>
        )}
      </Drawer>

      {/* ── Set rate ──────────────────────────────────────────────────────── */}
      <Drawer
        open={!!rateFor}
        title="Set hourly rate"
        sub={rateFor ? (
          <span className="inline-flex flex-wrap items-center gap-1.5">
            <SubjectChip name={rateFor.agent_name} color="#2563EB" />
            <span>rates are dated, never overwritten</span>
          </span>
        ) : ""}
        icon="💵"
        tone="#2563EB"
        onClose={closeRate}
        footer={
          <>
            <button className={btnGhost} onClick={closeRate}>Cancel</button>
            <button className={btnCls} disabled={busy || parseMoney(rate) <= 0} onClick={() => {
              if (!rateFor) return;
              mutate(() => api(`/expenses/agents/${rateFor.agent_id}/rate`, {
                method: "PUT",
                body: JSON.stringify({
                  rate_cents_per_hour: parseMoney(rate),
                  effective_from: rateFrom,
                  note: rateNote.trim() || null,
                }),
              })).then(closeRate);
            }}>
              {busy ? "Saving…" : parseMoney(rate) > 0 ? `Save ${money(parseMoney(rate))}/h` : "Save rate"}
            </button>
          </>
        }
      >
        <Field label="Hourly rate" required>
          <div className="relative">
            <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-sm font-bold text-ink-faint">$</span>
            <input className={`${drawerCtl} pl-7 font-bold tabular-nums`} inputMode="decimal"
                   placeholder="0.00" value={rate} onChange={(e) => setRate(numericInput(e.target.value))} />
          </div>
        </Field>
        <Field label="Effective from" required
               hint="Hours already logged before this date keep the rate they were priced at.">
          <input className={drawerCtl} type="date" value={rateFrom}
                 onChange={(e) => setRateFrom(e.target.value)} />
        </Field>
        <Field label="Note">
          <input className={drawerCtl} placeholder="Optional — e.g. annual review"
                 value={rateNote} onChange={(e) => setRateNote(e.target.value)} />
        </Field>

        {history.length > 0 && (
          <Field label="Rate history" plain>
            <div className="divide-y divide-hairline-soft overflow-hidden rounded-xl border border-hairline">
              {history.map((h, i) => (
                <div key={h.id} className="flex items-center justify-between gap-3 px-3 py-2 text-xs">
                  <span className="text-ink-muted">
                    from {h.effective_from}
                    {i === 0 && <span className="ml-2 text-[0.65rem] font-bold text-success">CURRENT</span>}
                    {h.note && <span className="ml-2 text-ink-faint">· {h.note}</span>}
                  </span>
                  <strong className="tabular-nums text-ink">{money(h.rate_cents_per_hour)}/h</strong>
                </div>
              ))}
            </div>
          </Field>
        )}
      </Drawer>

      {/* ── Pay plan (tier, progress, exception) ──────────────────────────── */}
      <PayPlanDrawer agent={planFor} busy={busy} mutate={mutate} onClose={() => setPlanFor(null)} />

      <Panel title="Hours logged" sub="Every agent line in this window, with the rate it was priced at.">
        {hourLines.length === 0 ? (
          <Empty>No hours logged in this window.</Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[560px] text-left text-xs">
              <thead className="text-ink-faint">
                <tr className="border-b border-hairline-soft">
                  <Th>Date</Th><Th>Agent</Th><Th className="text-right">Hours</Th>
                  <Th className="text-right">Rate</Th><Th className="text-right">Cost</Th><Th>Source</Th>
                </tr>
              </thead>
              <tbody>
                {hourLines.map((e) => (
                  <tr key={e.id} className="border-b border-hairline-soft/60">
                    <Td>{e.incurred_on}</Td>
                    <Td><span className="font-semibold text-ink">{e.agent_name || "—"}</span></Td>
                    <Td className="text-right tabular-nums">{e.quantity ?? "—"}</Td>
                    <Td className="text-right tabular-nums">
                      {e.unit_rate_cents != null ? `${money(e.unit_rate_cents)}/h` : "—"}
                    </Td>
                    <Td className="text-right font-bold tabular-nums text-ink">{money(e.amount_cents)}</Td>
                    <Td>
                      <span className="text-ink-faint">
                        {catById[e.category_id]?.name || "—"} · {e.source}
                      </span>
                    </Td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </div>
  );
}

/* ── Audit ───────────────────────────────────────────────────────────────── */

const RESOURCE_LABEL: Record<string, string> = {
  expense_entry: "Expense",
  expense_item: "Commitment",
  expense_category: "Category",
  agent_rate: "Agent rate",
  agent_sale_rate: "Per-sale rate",
  pay_rules: "Pay rules",
  pay_exception: "Tier exception",
};
const ACTION_COLOR: Record<string, string> = {
  create: "#059669", update: "#2563EB", post: "#C026D3", void: "#E11D48",
};

/** Turn the stored `details` JSON into a sentence. The log is only useful if you
 *  can read it at a glance — "Ana Agent — 5 h × $12.00/h = $60.00" answers the
 *  question; `{"unit_rate_cents":1200}` makes you decode it. Anything that
 *  doesn't match a known shape falls back to compact key: value pairs, never
 *  raw JSON. */
/* ── Company pay rules + per-agent pay plan ──────────────────────────────── */
/* One rule set pays every agent (see app/expenses/pay_rules.py). Nothing here
 * computes pay — the server does; this only shows the rules and edits them. */

// Carriers offered on the Log Sale form; the per-member picker also lists any
// carrier the server has seen on a deal or that the rules already name.
const FORM_CARRIERS = ["Health First", "Ambetter", "Oscar", "BCBS", "CareSource", "UHC", "WellPoint",
  "Anthem", "Christus", "Select Health"];
const carrierKey = (c: string) => c.trim().toLowerCase().split(/\s+/).join(" ");
const dollars = (cents: number | null | undefined) => money(cents).replace(/\.00$/, "");

/** "Under 16" / "16–25" / "26+" and "Up to 79" / "80–129" / "130+" for tier i. */
function tierSpans(tiers: PayTier[], i: number, workDays: number) {
  const wd = Math.max(workDays || 5, 1);
  const lo = tiers[i].min, next = tiers[i + 1]?.min;
  const dLo = Math.ceil(lo / wd), dNext = next != null ? Math.ceil(next / wd) : null;
  return {
    daily: i === 0 ? (dNext != null ? `Under ${dNext}` : "Any") : dNext != null ? `${dLo}–${dNext - 1}` : `${dLo}+`,
    weekly: i === 0 ? (next != null ? `Up to ${next - 1}` : "Any") : next != null ? `${lo}–${next - 1}` : `${lo}+`,
  };
}

function CompanyPayRules({ data, busy, onSave }: {
  data: PayRulesResp | null; busy: boolean;
  onSave: (rules: PayRuleSet, startsOn: string) => Promise<boolean>;
}) {
  const [open, setOpen] = useState(false);
  const [tiers, setTiers] = useState<{ min: string; pay: string }[]>([]);
  const [workDays, setWorkDays] = useState("5");
  const [dSmall, setDSmall] = useState("");
  const [dLarge, setDLarge] = useState("");
  const [dMin, setDMin] = useState("3");
  const [anc, setAnc] = useState("");
  const [visionOn, setVisionOn] = useState(false);
  const [vision, setVision] = useState("");
  const [perMember, setPerMember] = useState<string[]>([]);
  const [startsOn, setStartsOn] = useState("");

  if (!data) return <Panel title="Company pay rules" sub="Loading…"><Empty>Loading the pay rules…</Empty></Panel>;
  const r = data.rules;
  const d2 = (c: number | null | undefined) => (c == null ? "" : (c / 100).toFixed(2));
  const startEdit = () => {
    setTiers(r.aca_tiers.map((t) => ({ min: String(t.min), pay: d2(t.cents) })));
    setWorkDays(String(r.work_days)); setDSmall(d2(r.dental_small_cents)); setDLarge(d2(r.dental_large_cents));
    setDMin(String(r.dental_large_min)); setAnc(d2(r.ancillary_cents));
    setVisionOn(r.vision_cents != null); setVision(d2(r.vision_cents));
    setPerMember(r.per_member_carriers); setStartsOn(data.starts_on);
    setOpen(true);
  };
  // key -> display name; the FIRST spelling wins so "Anthem" is not replaced by
  // the lowercase key the rules store.
  const byKey = new Map<string, string>();
  [...FORM_CARRIERS, ...data.carriers, ...r.per_member_carriers].forEach((c) => {
    const k = carrierKey(c);
    if (k && k !== "—" && !byKey.has(k)) byKey.set(k, c);
  });
  const carriers = Array.from(byKey.entries());
  const perMemberNames = carriers.filter(([k]) => r.per_member_carriers.includes(k)).map(([, label]) => label);
  const moneyInput = (v: string, set: (s: string) => void, disabled = false) => (
    <div className="relative">
      <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-sm font-bold text-ink-faint">$</span>
      <input className={`${drawerCtl} pl-7 font-bold tabular-nums ${disabled ? "opacity-50" : ""}`} inputMode="decimal"
             placeholder="0.00" value={v} disabled={disabled} onChange={(e) => set(numericInput(e.target.value))} />
    </div>
  );
  const save = () => onSave({
    aca_tiers: tiers.map((t, i) => ({ min: i === 0 ? 0 : parseInt(t.min, 10) || 0, cents: parseMoney(t.pay) })),
    work_days: parseInt(workDays, 10) || 5,
    dental_small_cents: parseMoney(dSmall), dental_large_cents: parseMoney(dLarge),
    dental_large_min: parseInt(dMin, 10) || 3,
    ancillary_cents: parseMoney(anc),
    vision_cents: visionOn ? parseMoney(vision) : null,
    per_member_carriers: perMember,
  }, startsOn).then((ok) => { if (ok) setOpen(false); });

  return (
    <>
      <Panel
        title="Company pay rules"
        sub="Applies to every agent automatically, including new ones"
        right={<button className={btnGhost} onClick={startEdit}>Edit rules</button>}
      >
        <div className="grid gap-4 lg:grid-cols-2">
          <div>
            <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
              <span className="text-sm font-bold text-ink">ACA — tiers by weekly total</span>
              <span className="text-[0.7rem] text-ink-faint">Resets every Monday · weekend deals count</span>
            </div>
            <div className="overflow-hidden rounded-xl border border-hairline">
              <table className="w-full text-left text-xs">
                <thead className="bg-black/5 text-[0.65rem] uppercase tracking-wide text-ink-faint">
                  <tr><Th>Tier</Th><Th>Daily avg</Th><Th>Weekly total</Th><Th className="text-right">Per commission</Th></tr>
                </thead>
                <tbody>
                  {r.aca_tiers.map((t, i) => {
                    const s = tierSpans(r.aca_tiers, i, r.work_days);
                    return (
                      <tr key={i} className="border-t border-hairline-soft">
                        <Td><span className="font-semibold text-ink">Tier {i + 1}</span></Td>
                        <Td>{s.daily}</Td><Td>{s.weekly}</Td>
                        <Td className="text-right font-extrabold tabular-nums text-ink">{dollars(t.cents)}</Td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <p className="mt-2 text-[0.7rem] leading-relaxed text-ink-muted">
              Daily average is based on a {r.work_days}-day week. When an agent unlocks a new tier, every ACA
              commission that week is repriced at the new rate.{" "}
              {perMemberNames.length ? <>{perMemberNames.join(", ")} {perMemberNames.length === 1 ? "counts" : "count"} each
              member as a commission; </> : <>Every application counts as 1 commission; </>}
              an application marked EAP always counts as 1.
            </p>
          </div>
          <div className="space-y-3">
            <div>
              <div className="mb-1.5 flex items-baseline justify-between">
                <span className="text-sm font-bold text-ink">Dental — by household size</span>
                <span className="text-[0.7rem] text-ink-faint">Read from the sale</span>
              </div>
              <div className="grid grid-cols-2 gap-2">
                <div className="rounded-xl border border-hairline px-3 py-2">
                  <div className="text-[0.7rem] text-ink-muted">
                    {r.dental_large_min - 1 <= 1 ? "1 person" : `1–${r.dental_large_min - 1} people`}
                  </div>
                  <div className="text-lg font-extrabold tabular-nums text-ink">{dollars(r.dental_small_cents)}</div>
                </div>
                <div className="rounded-xl border border-hairline px-3 py-2">
                  <div className="text-[0.7rem] text-ink-muted">Family of {r.dental_large_min}+</div>
                  <div className="text-lg font-extrabold tabular-nums text-ink">{dollars(r.dental_large_cents)}</div>
                </div>
              </div>
            </div>
            <div className="flex items-center justify-between rounded-xl border border-hairline px-3 py-2">
              <div>
                <div className="text-sm font-bold text-ink">Ancillaries</div>
                <div className="text-[0.7rem] text-ink-muted">Flat rate on every sale, any household size</div>
              </div>
              <div className="text-lg font-extrabold tabular-nums text-ink">{dollars(r.ancillary_cents)}</div>
            </div>
            <div className={`flex items-center justify-between rounded-xl border px-3 py-2 ${
              r.vision_cents == null ? "border-dashed border-hairline" : "border-hairline"}`}>
              <div>
                <div className="flex items-center gap-2 text-sm font-bold text-ink">
                  Vision
                  {r.vision_cents == null && (
                    <span className="rounded bg-black/10 px-1.5 py-0.5 text-[0.6rem] font-bold uppercase tracking-wide text-ink-muted">Not offered yet</span>
                  )}
                </div>
                <div className="text-[0.7rem] text-ink-muted">{r.vision_cents == null ? "No rate set" : "Per vision sale"}</div>
              </div>
              <div className="text-lg font-extrabold tabular-nums text-ink">{r.vision_cents == null ? "—" : dollars(r.vision_cents)}</div>
            </div>
            <div className="rounded-xl border border-accent/20 bg-accent/5 px-3 py-2 text-[0.7rem] leading-relaxed text-ink-muted">
              <strong className="text-ink">How a deal pays.</strong> ACA at the agent’s tier + dental by household
              + {dollars(r.ancillary_cents)} per ancillary. Pays approved sales logged from{" "}
              <strong className="text-ink">{data.starts_on}</strong>; earlier sales keep their old per-agent rate.
            </div>
          </div>
        </div>
      </Panel>

      <Drawer
        open={open} title="Edit company pay rules" icon="📐" tone="#2563EB" onClose={() => setOpen(false)}
        sub="One set of rules for every agent. Saving reprices the week in progress; weeks already closed never change."
        footer={<>
          <button className={btnGhost} onClick={() => setOpen(false)}>Cancel</button>
          <button className={btnCls} disabled={busy} onClick={save}>{busy ? "Saving…" : "Save rules"}</button>
        </>}
      >
        <Field label="ACA tiers — weekly total to unlock, and pay per commission" plain>
          <div className="space-y-2">
            {tiers.map((t, i) => (
              <div key={i} className="grid grid-cols-[4.5rem_1fr_1fr_2rem] items-center gap-2">
                <span className="text-xs font-bold text-ink">Tier {i + 1}</span>
                <input className={`${drawerCtl} tabular-nums ${i === 0 ? "opacity-50" : ""}`} inputMode="numeric"
                       aria-label={`Tier ${i + 1} weekly total`} disabled={i === 0} value={i === 0 ? "0" : t.min}
                       onChange={(e) => setTiers(tiers.map((x, j) => j === i ? { ...x, min: e.target.value.replace(/\D/g, "") } : x))} />
                {moneyInput(t.pay, (v) => setTiers(tiers.map((x, j) => j === i ? { ...x, pay: v } : x)))}
                {i > 0 && i === tiers.length - 1 ? (
                  <button className="text-ink-faint hover:text-danger" aria-label={`Remove tier ${i + 1}`}
                          onClick={() => setTiers(tiers.slice(0, -1))}>×</button>
                ) : <span />}
              </div>
            ))}
            {tiers.length < 8 && (
              <button className={btnGhost} onClick={() => setTiers([...tiers, { min: "", pay: "" }])}>+ Add a tier</button>
            )}
          </div>
        </Field>
        <Field label="Working days in a week" hint="Only used for the daily average shown beside each tier.">
          <input className={drawerCtl} inputMode="numeric" value={workDays}
                 onChange={(e) => setWorkDays(e.target.value.replace(/\D/g, ""))} />
        </Field>
        <div className="grid grid-cols-3 gap-3">
          <Field label="Dental — small household">{moneyInput(dSmall, setDSmall)}</Field>
          <Field label="Dental — large household">{moneyInput(dLarge, setDLarge)}</Field>
          <Field label="Large starts at (people)">
            <input className={drawerCtl} inputMode="numeric" value={dMin}
                   onChange={(e) => setDMin(e.target.value.replace(/\D/g, ""))} />
          </Field>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Ancillary — per sale">{moneyInput(anc, setAnc)}</Field>
          <Field label="Vision — per sale">
            {moneyInput(visionOn ? vision : "", setVision, !visionOn)}
          </Field>
        </div>
        <label className="flex cursor-pointer items-center gap-2 text-xs font-semibold text-ink">
          <input type="checkbox" className="h-4 w-4 accent-accent" checked={visionOn}
                 onChange={(e) => setVisionOn(e.target.checked)} />
          Vision is offered (leave off to keep it in the system with no rate)
        </label>
        <Field label="Count each member as a commission" plain
               hint="On for a carrier = an application with 3 members adds 3 commissions. EAP applications always count as 1.">
          <div className="grid grid-cols-2 gap-x-3 gap-y-1.5">
            {carriers.map(([k, label]) => (
              <label key={k} className="flex cursor-pointer items-center gap-2 text-xs text-ink">
                <input type="checkbox" className="h-4 w-4 accent-accent" checked={perMember.includes(k)}
                       onChange={(e) => setPerMember(e.target.checked ? [...perMember, k] : perMember.filter((x) => x !== k))} />
                {label}
              </label>
            ))}
          </div>
        </Field>
        <Field label="Rules pay sales logged from"
               hint="Snaps to the Monday of that week. Move it earlier to pay older sales by these rules instead of the old per-agent rates.">
          <input className={drawerCtl} type="date" value={startsOn} max={todayISO()}
                 onChange={(e) => setStartsOn(e.target.value || data.starts_on)} />
        </Field>
      </Drawer>
    </>
  );
}

function PayPlanDrawer({ agent, busy, mutate, onClose }: {
  agent: AgentRow | null; busy: boolean;
  mutate: (fn: () => Promise<unknown>) => Promise<void>;
  onClose: () => void;
}) {
  const [plan, setPlan] = useState<PayPlan | null>(null);
  const [editing, setEditing] = useState(false);
  const [tier, setTier] = useState("1");
  const [duration, setDuration] = useState<"week" | "date" | "none">("week");
  const [endsOn, setEndsOn] = useState(todayISO());
  const [reason, setReason] = useState("");
  const agentId = agent?.agent_id;

  const load = useCallback(() => {
    if (!agentId) { setPlan(null); return; }
    api<PayPlan>(`/expenses/agents/${agentId}/pay-plan`).then(setPlan).catch(() => setPlan(null));
  }, [agentId]);
  useEffect(() => { setPlan(null); setEditing(false); setReason(""); load(); }, [load]);

  const r = plan?.rules, w = plan?.week, exc = plan?.exception;
  const pct = !w || !r ? 0 : w.next_at
    ? Math.min(100, Math.round((w.commissions / w.next_at) * 100)) : 100;
  const weekLabel = (iso: string) => new Date(`${iso}T12:00:00`).toLocaleDateString("en-US", { month: "short", day: "numeric" });

  return (
    <Drawer
      open={!!agent} title="Pay plan" icon="🏷️" tone="#2563EB" onClose={onClose}
      sub={agent ? (
        <span className="inline-flex flex-wrap items-center gap-1.5">
          <SubjectChip name={agent.agent_name} color="#C026D3" />
          <span>paid for every approved sale</span>
        </span>
      ) : ""}
      footer={<button className={btnGhost} onClick={onClose}>Close</button>}
    >
      {!plan || !r || !w ? (
        <div className="text-xs text-ink-muted">Loading…</div>
      ) : (
        <>
          {exc ? (
            <div className="rounded-xl border border-amber-500/40 bg-amber-500/10 px-3 py-2.5 text-xs font-bold text-amber-800 dark:text-amber-300">
              Exception active — ACA locked to Tier {exc.tier}
              <div className="mt-0.5 font-medium">
                {exc.ends_on ? `Until ${exc.ends_on}` : "No end date"} · {exc.reason}
              </div>
            </div>
          ) : (
            <div className="rounded-xl border border-success/30 bg-success/10 px-3 py-2.5 text-xs font-bold text-success">
              ✓ Automatic — follows company pay rules
            </div>
          )}

          <div className="rounded-xl border border-accent/20 bg-accent/5 p-3">
            <div className="flex items-baseline justify-between text-[0.65rem] font-bold uppercase tracking-wide text-ink-faint">
              <span>ACA tier this week</span><span className="normal-case font-medium">resets every Monday</span>
            </div>
            <div className="mt-1 flex items-baseline justify-between">
              <span className="text-2xl font-extrabold text-ink">Tier {w.tier}</span>
              <span className="text-sm text-ink-muted"><strong className="text-lg font-extrabold text-accent">{dollars(w.rate_cents)}</strong> / ACA sale</span>
            </div>
            <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-black/10">
              <div className="h-full rounded-full bg-accent" style={{ width: `${pct}%` }} />
            </div>
            <p className="mt-2 text-xs text-ink-muted">
              {w.commissions} {w.commissions === 1 ? "commission" : "commissions"} this week
              {exc
                ? <>. The tier is locked by the exception{w.auto_tier !== w.tier && <> — the count alone would earn Tier {w.auto_tier}</>}.</>
                : w.to_next != null
                  ? <> · {w.to_next} more to reach Tier {w.next_tier} ({dollars(w.next_rate_cents)}). Unlocking it reprices every deal this week.</>
                  : <> · top tier reached.</>}
            </p>
            <div className="mt-2 grid gap-2" style={{ gridTemplateColumns: `repeat(${Math.min(r.aca_tiers.length, 4)}, minmax(0, 1fr))` }}>
              {r.aca_tiers.map((t, i) => {
                const s = tierSpans(r.aca_tiers, i, r.work_days);
                return (
                  <div key={i} className={`rounded-lg border px-2 py-1.5 ${i + 1 === w.tier ? "border-accent bg-white/60 dark:bg-white/10" : "border-hairline"}`}>
                    <div className="text-[0.65rem] text-ink-muted">Tier {i + 1}</div>
                    <div className="text-sm font-extrabold tabular-nums text-ink">{dollars(t.cents)}</div>
                    <div className="text-[0.65rem] text-ink-faint">{s.weekly}/wk</div>
                    <div className="text-[0.65rem] text-ink-faint">{s.daily.toLowerCase()}/day</div>
                  </div>
                );
              })}
            </div>
          </div>

          <Field label="Other products · set automatically" plain>
            <div className="divide-y divide-hairline-soft overflow-hidden rounded-xl border border-hairline text-xs">
              <div className="flex items-center justify-between px-3 py-2">
                <span><strong className="text-ink">Dental</strong><span className="block text-ink-faint">Family of {r.dental_large_min}+ pays {dollars(r.dental_large_cents)}, picked up from the sale</span></span>
                <strong className="tabular-nums text-ink">{dollars(r.dental_small_cents)} / {dollars(r.dental_large_cents)}</strong>
              </div>
              <div className="flex items-center justify-between px-3 py-2">
                <span><strong className="text-ink">Ancillaries</strong><span className="block text-ink-faint">Flat rate on every sale</span></span>
                <strong className="tabular-nums text-ink">{dollars(r.ancillary_cents)}</strong>
              </div>
              <div className="flex items-center justify-between px-3 py-2">
                <span><strong className="text-ink">Vision</strong><span className="block text-ink-faint">{r.vision_cents == null ? "Not offered yet" : "Per vision sale"}</span></span>
                <strong className="tabular-nums text-ink">{r.vision_cents == null ? "—" : dollars(r.vision_cents)}</strong>
              </div>
            </div>
          </Field>

          {!editing ? (
            <div className="flex flex-wrap gap-2">
              <button className={`${btnGhost} flex-1`} onClick={() => { setEditing(true); setTier(String(exc?.tier ?? w.tier)); }}>
                ✎ {exc ? "Change the exception" : "Set an exception"}
              </button>
              {exc && (
                <button className={btnGhost} disabled={busy}
                        onClick={() => mutate(() => api(`/expenses/agents/${agentId}/pay-exception`, { method: "DELETE" })).then(load)}>
                  Back to automatic
                </button>
              )}
            </div>
          ) : (
            <div className="space-y-3 rounded-xl border border-hairline p-3">
              <div className="grid grid-cols-2 gap-3">
                <Field label="ACA tier">
                  <select className={drawerCtl} value={tier} onChange={(e) => setTier(e.target.value)}>
                    {r.aca_tiers.map((t, i) => (
                      <option key={i} value={i + 1}>Always Tier {i + 1} ({dollars(t.cents)})</option>
                    ))}
                  </select>
                </Field>
                <Field label="How long">
                  <select className={drawerCtl} value={duration}
                          onChange={(e) => setDuration(e.target.value as "week" | "date" | "none")}>
                    <option value="week">Until the end of this week</option>
                    <option value="date">Until a specific date</option>
                    <option value="none">No end date</option>
                  </select>
                </Field>
              </div>
              {duration === "date" && (
                <Field label="Last day it applies">
                  <input className={drawerCtl} type="date" min={todayISO()} value={endsOn}
                         onChange={(e) => setEndsOn(e.target.value || todayISO())} />
                </Field>
              )}
              <Field label="Reason" required>
                <input className={drawerCtl} placeholder="Required — e.g. guaranteed Tier 2 during training"
                       value={reason} onChange={(e) => setReason(e.target.value)} />
              </Field>
              <div className="flex justify-end gap-2">
                <button className={btnGhost} onClick={() => setEditing(false)}>Cancel</button>
                <button className={btnCls} disabled={busy || reason.trim().length < 3}
                        onClick={() => mutate(() => api(`/expenses/agents/${agentId}/pay-exception`, {
                          method: "PUT",
                          body: JSON.stringify({
                            tier: parseInt(tier, 10), duration,
                            ends_on: duration === "date" ? endsOn : null, reason: reason.trim(),
                          }),
                        })).then(() => { setEditing(false); setReason(""); load(); })}>
                  {busy ? "Saving…" : "Save exception"}
                </button>
              </div>
            </div>
          )}

          <Field label="Tier history" plain>
            <div className="divide-y divide-hairline-soft overflow-hidden rounded-xl border border-hairline">
              {plan.history.map((h) => (
                <div key={h.week_start} className="flex items-center justify-between gap-3 px-3 py-2 text-xs">
                  <span className="text-ink-muted">
                    Week of {weekLabel(h.week_start)}
                    {h.current && <span className="ml-2 text-[0.65rem] font-bold text-success">CURRENT</span>}
                    <span className="ml-2 text-ink-faint">{h.commissions} · {money(h.aca_cents + h.other_cents)}</span>
                  </span>
                  <strong className="text-ink">Tier {h.tier} · {h.exception ? "exception" : "auto"}</strong>
                </div>
              ))}
            </div>
          </Field>
        </>
      )}
    </Drawer>
  );
}

function auditDetail(r: AuditRow, nameOf: (id?: unknown) => string): ReactNode {
  const d = (r.details || {}) as Record<string, unknown>;
  const c = (v: unknown) => money(Number(v) || 0);
  const num = (v: unknown) => (v == null ? null : Number(v));
  const when = (d.incurred_on || d.work_date || d.effective_from) as string | undefined;
  const B = ({ children }: { children: ReactNode }) =>
    <strong className="font-bold text-ink">{children}</strong>;

  if (r.resource_type === "agent_rate") {
    const before = num(d.before_cents), after = num(d.after_cents);
    if (before != null && after != null) {
      return <><B>{nameOf(d.agent_id)}</B> — {c(before)}/h → <B>{c(after)}/h</B>
        {when && <> from {when}</>}</>;
    }
    return <><B>{nameOf(d.agent_id)}</B> — <B>{c(d.rate_cents)}/h</B>{when && <> from {when}</>}</>;
  }

  if (r.resource_type === "agent_sale_rate") {
    const a = (d.after || {}) as Record<string, unknown>;
    const b = d.before as Record<string, unknown> | null | undefined;
    const trio = (x: Record<string, unknown>) =>
      `ACA ${c(x.aca)} · Dental ${c(x.dental)} · Vision ${c(x.vision)}`;
    return <><B>{nameOf(d.agent_id)}</B> — {b && <>{trio(b)} → </>}<B>{trio(a)}</B> per sale</>;
  }

  if (r.resource_type === "pay_rules") {
    const tiers = (((d.after || {}) as Record<string, unknown>).aca_tiers || []) as { min: number; cents: number }[];
    return <>Company pay rules saved — ACA{" "}
      <B>{tiers.map((t, i) => `T${i + 1} ${c(t.cents)}${i ? ` from ${t.min}` : ""}`).join(" · ")}</B>
      {d.starts_on !== d.starts_on_before && <> · now pays sales from <B>{String(d.starts_on)}</B></>}</>;
  }

  if (r.resource_type === "pay_exception") {
    if (r.action === "delete") return <><B>{nameOf(d.agent_id)}</B> — back to automatic (was Tier {String(d.tier)})</>;
    return <><B>{nameOf(d.agent_id)}</B> — ACA locked to <B>Tier {String(d.tier)}</B>{" "}
      {d.ends_on ? <>until {String(d.ends_on)}</> : <>with no end date</>} · {String(d.reason || "")}</>;
  }

  if (d.kind === "agent_hours") {
    return <><B>{nameOf(d.agent_id)}</B> — {String(d.hours)} h × {c(d.unit_rate_cents)}/h ={" "}
      <B>{c(d.amount_cents)}</B>{when && <> on {when}</>}</>;
  }

  // An edit: say what actually changed, not the whole record.
  const before = d.before as Record<string, unknown> | undefined;
  const after = d.after as Record<string, unknown> | undefined;
  if (before && after) {
    const bits: ReactNode[] = [];
    if (after.description !== undefined && after.description !== before.description) {
      bits.push(<>“{String(before.description)}” → <B>“{String(after.description)}”</B></>);
    }
    if (after.amount_cents !== undefined && after.amount_cents !== before.amount_cents) {
      bits.push(<>{c(before.amount_cents)} → <B>{c(after.amount_cents)}</B></>);
    }
    if (after.incurred_on !== undefined && after.incurred_on !== before.incurred_on) {
      bits.push(<>dated {String(before.incurred_on)} → <B>{String(after.incurred_on)}</B></>);
    }
    if (after.interval !== undefined && after.interval !== before.interval) {
      bits.push(<>{String(before.interval)} → <B>{String(after.interval)}</B></>);
    }
    if (after.is_active !== undefined && after.is_active !== before.is_active) {
      bits.push(<B>{after.is_active ? "reactivated" : "retired"}</B>);
    }
    if (after.name !== undefined && after.name !== before.name) {
      bits.push(<>“{String(before.name)}” → <B>“{String(after.name)}”</B></>);
    }
    const label = (after.description ?? before.description ?? after.name ?? before.name) as string | undefined;
    if (!bits.length) return <span className="text-ink-faint">no visible change</span>;
    return <>{label && <><B>{label}</B> — </>}{bits.map((b, i) => (
      <span key={i}>{i > 0 && " · "}{b}</span>
    ))}</>;
  }

  const label = (d.description ?? d.name) as string | undefined;
  if (label) {
    return <><B>{label}</B>
      {d.amount_cents != null && <> — <B>{c(d.amount_cents)}</B></>}
      {d.interval != null && <> / {String(d.interval)}</>}
      {when && <> on {when}</>}</>;
  }
  if (d.amount_cents != null) {
    return <><B>{c(d.amount_cents)}</B>{when && <> on {when}</>}</>;
  }
  // Unknown shape — readable pairs, never a JSON blob.
  const pairs = Object.entries(d).filter(([k]) => k !== "id" && k !== "entry_id");
  if (!pairs.length) return <span className="text-ink-faint">—</span>;
  return <>{pairs.map(([k, v]) => `${k.replace(/_/g, " ")}: ${String(v)}`).join(" · ")}</>;
}

function Audit({ rows, agents }: { rows: AuditRow[]; agents: AgentRow[] }) {
  const nameOf = (id?: unknown) => {
    const hit = agents.find((a) => a.agent_id === String(id));
    return hit ? hit.agent_name : "Agent";
  };

  return (
    <Panel title="Change log" sub="Every expense create, edit, post and void — newest first.">
      {rows.length === 0 ? (
        <Empty>No expense changes recorded yet.</Empty>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-left text-xs">
            <thead className="text-ink-faint">
              <tr className="border-b border-hairline-soft">
                <Th>When</Th><Th>Who</Th><Th>Action</Th><Th>What changed</Th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id} className="border-b border-hairline-soft/60">
                  <Td className="whitespace-nowrap">
                    {r.created_at ? new Date(r.created_at).toLocaleString() : "—"}
                  </Td>
                  <Td><span className="font-semibold text-ink">{r.user_name}</span></Td>
                  <Td className="whitespace-nowrap">
                    <span
                      className="inline-flex items-center rounded-full border px-2 py-0.5 text-[0.68rem] font-bold capitalize"
                      style={{
                        background: tint(ACTION_COLOR[r.action], "1f"),
                        borderColor: tint(ACTION_COLOR[r.action], "55"),
                        color: ACTION_COLOR[r.action] || "var(--color-ink-muted)",
                      }}
                    >{r.action}</span>
                    <span className="ml-2 text-ink-faint">
                      {RESOURCE_LABEL[r.resource_type] || r.resource_type}
                    </span>
                  </Td>
                  <Td className="text-ink-muted">{auditDetail(r, nameOf)}</Td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

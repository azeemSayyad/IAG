/* Small shared pieces of the HealthSherpa page (pages/HealthSherpa.tsx and
 * components/hs/HsCampaigns.tsx). */
import { useEffect, useRef, useState, type ReactNode } from "react";

export function fmtDob(iso: string | null) {
  if (!iso) return "—";
  const [y, m, d] = iso.split("-");
  return `${m}/${d}/${y}`;
}
export function fmtPhone(p: string | null) {
  if (!p || p.length !== 10) return p || "";
  return `(${p.slice(0, 3)}) ${p.slice(3, 6)}-${p.slice(6)}`;
}
export function ago(iso: string | null) {
  if (!iso) return "";
  const s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return new Date(iso).toLocaleDateString();
}
export function errText(e: unknown) {
  const m = e instanceof Error ? e.message : String(e);
  const json = m.match(/\{.*\}$/s);
  if (json) { try { return JSON.parse(json[0]).detail || m; } catch { /* not JSON */ } }
  return m;
}

export function Banner({ tone, children, onClose }: { tone: "danger" | "success"; children: ReactNode; onClose: () => void }) {
  const cls = tone === "danger" ? "border-danger/30 bg-danger/10 text-danger" : "border-success/30 bg-success/10 text-success";
  return (
    <div className={`flex items-start justify-between gap-3 rounded-lg border px-3 py-2 text-xs font-semibold ${cls}`}>
      <span>{children}</span>
      <button onClick={onClose} aria-label="Dismiss" className="opacity-60 hover:opacity-100">×</button>
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="py-10 text-center text-sm text-ink-muted">{children}</div>;
}

export function Menu({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);
  return (
    <div ref={ref} className="relative">
      <button className="rounded-lg px-2 py-1.5 text-xs text-ink-faint hover:bg-black/5 hover:text-ink" aria-label="More"
              onClick={() => setOpen((o) => !o)}>⋯</button>
      {open && (
        <div className="absolute right-0 z-20 mt-1 min-w-44 rounded-lg border border-hairline bg-white py-1 text-left shadow-lg"
             onClick={() => setOpen(false)}>
          {children}
        </div>
      )}
    </div>
  );
}

export function MenuItem({ children, danger, onClick }: { children: ReactNode; danger?: boolean; onClick: () => void }) {
  return (
    <button onClick={onClick}
            className={`block w-full px-3 py-1.5 text-left text-xs hover:bg-black/5 ${danger ? "text-danger" : "text-ink"}`}>
      {children}
    </button>
  );
}

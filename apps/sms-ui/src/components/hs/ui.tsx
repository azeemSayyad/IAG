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

/* A confirm dialog in the portal's style (instead of the browser's window.confirm).
 *   const [confirm, confirmEl] = useConfirm();
 *   if (!(await confirm({ title, body, confirmLabel, tone: "danger" }))) return;
 *   …render {confirmEl} somewhere in the component. */
export type ConfirmOpts = {
  title: string; body?: ReactNode; points?: ReactNode[]; note?: ReactNode;
  confirmLabel?: string; cancelLabel?: string; tone?: "danger" | "primary"; icon?: string;
  /** A second choice next to the main button (e.g. "Archive"): confirm() then resolves "alt". */
  altLabel?: string;
  /** Rich content between the text and the buttons (e.g. option cards). */
  extra?: ReactNode;
};

export type ConfirmResult = boolean | "alt";
export function useConfirm(): [(o: ConfirmOpts) => Promise<ConfirmResult>, ReactNode] {
  const [state, setState] = useState<(ConfirmOpts & { resolve: (v: ConfirmResult) => void }) | null>(null);
  const confirm = (o: ConfirmOpts) => new Promise<ConfirmResult>((resolve) => setState({ ...o, resolve }));
  const close = (v: ConfirmResult) => { state?.resolve(v); setState(null); };
  const el = state ? <ConfirmModal o={state} onClose={close} /> : null;
  return [confirm, el];
}

function ConfirmModal({ o, onClose }: { o: ConfirmOpts; onClose: (v: ConfirmResult) => void }) {
  const okRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    okRef.current?.focus();
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(false); };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [onClose]);
  const danger = o.tone === "danger";
  return (
    <div className="fixed inset-0 z-[9600] flex items-center justify-center p-4" style={{ background: "rgba(10,14,22,.55)" }}
         role="alertdialog" aria-modal="true" aria-labelledby="iag-confirm-title" data-testid="confirm-modal"
         onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(false); }}>
      <div className="w-full max-w-md overflow-hidden rounded-2xl bg-white shadow-2xl">
        <div className={`flex gap-3 p-5 ${o.extra && !o.body && !o.points?.length ? "items-center pb-3" : "items-start"}`}>
          <div className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-lg ${danger ? "bg-danger/10" : "bg-accent/10"}`}>
            {o.icon || (danger ? "🗑️" : "❓")}
          </div>
          <div className="min-w-0 flex-1">
            <h2 id="iag-confirm-title" className="text-base font-extrabold text-ink">{o.title}</h2>
            {o.body && <div className="mt-1 text-sm text-ink-soft">{o.body}</div>}
            {o.points && o.points.length > 0 && (
              <ul className="mt-3 space-y-1.5">
                {o.points.map((p, i) => (
                  <li key={i} className="flex gap-2 text-xs text-ink-soft">
                    <span className={`mt-1 h-1.5 w-1.5 shrink-0 rounded-full ${danger ? "bg-danger" : "bg-accent"}`} />
                    <span>{p}</span>
                  </li>
                ))}
              </ul>
            )}
            {o.note && !o.extra && <div className="mt-3 rounded-lg bg-pending/10 px-3 py-2 text-xs font-semibold text-pending">{o.note}</div>}
          </div>
        </div>
        {o.extra && (
          // Full width under the title (not indented by the icon).
          <div className="px-5 pb-5 [&>*]:mt-0">
            {o.extra}
            {o.note && <div className="mt-3 rounded-lg bg-pending/10 px-3 py-2 text-xs font-semibold text-pending">{o.note}</div>}
          </div>
        )}
        <div className="flex justify-end gap-2 border-t border-hairline bg-black/[0.02] px-5 py-3">
          <button className="rounded-lg border border-hairline px-3 py-1.5 text-xs font-semibold text-ink-muted hover:bg-black/5 hover:text-ink"
                  onClick={() => onClose(false)} data-testid="confirm-cancel">{o.cancelLabel || "Cancel"}</button>
          {o.altLabel && (
            <button onClick={() => onClose("alt")} data-testid="confirm-alt"
                    className="rounded-lg bg-accent px-3 py-1.5 text-xs font-bold text-white hover:bg-accent-hover">{o.altLabel}</button>
          )}
          <button ref={o.altLabel ? undefined : okRef} onClick={() => onClose(true)} data-testid="confirm-ok"
                  className={`rounded-lg px-3 py-1.5 text-xs font-bold text-white ${danger ? "bg-danger hover:opacity-90" : "bg-accent hover:bg-accent-hover"}`}>
            {o.confirmLabel || "OK"}
          </button>
        </div>
      </div>
    </div>
  );
}

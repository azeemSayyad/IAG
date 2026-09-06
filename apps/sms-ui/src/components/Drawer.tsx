/* Drawer — a right-hand slide-over panel for create/edit forms.
 *
 * Used where an inline form row would be too cramped to read (the Expenses
 * ledger, standing commitments). Deliberately plain: a scrim, a panel, a sticky
 * footer. Escape and a scrim click close it, the body is scroll-locked while it
 * is open, and the first field takes focus so it is keyboard-usable.
 *
 * Surfaces use `bg-white` (which index.css flips to the dark panel colour) and
 * `bg-black/5` for the nested field tiles, so both modes work with no extra CSS.
 */
import { useEffect, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";

export function Drawer({ open, title, sub, icon, tone, onClose, footer, children }: {
  open: boolean;
  title: string;
  /** ReactNode, not string — callers emphasise the subject (e.g. the agent's
   *  name) inside the line rather than leaving it flat grey. */
  sub?: ReactNode;
  /** Emoji/short glyph shown in the header chip. */
  icon?: string;
  /** Header wash + chip colour. Defaults to the brand accent. */
  tone?: string;
  onClose: () => void;
  footer?: ReactNode;
  children: ReactNode;
}) {
  const panelRef = useRef<HTMLDivElement>(null);

  // onClose is nearly always an inline arrow in the caller, so it gets a fresh
  // identity on EVERY render — including every keystroke in a field. Holding it
  // in a ref keeps the effects below keyed on `open` alone; with onClose in the
  // deps they tore down and re-ran per character, and the autofocus timer yanked
  // the caret back to the first field mid-typing.
  const closeRef = useRef(onClose);
  useEffect(() => { closeRef.current = onClose; });

  // Escape to close + body scroll lock, both torn down on close.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") closeRef.current(); };
    document.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [open]);

  // Focus the first control ONCE per opening, so the drawer is usable without
  // reaching for the mouse.
  useEffect(() => {
    if (!open) return;
    const t = window.setTimeout(() => {
      panelRef.current?.querySelector<HTMLElement>(
        "input:not([type=hidden]), select, textarea, [data-autofocus]",
      )?.focus();
    }, 60);
    return () => window.clearTimeout(t);
  }, [open]);

  if (!open) return null;

  // Portalled to <body> on purpose: the panels this drawer is opened from carry
  // `backdrop-filter` (.glass), and a filtered ancestor becomes the containing
  // block for `position: fixed` — so rendered in place, the drawer would be
  // offset by the card's position instead of covering the viewport.
  return createPortal(
    <div className="fixed inset-0 z-[9400] flex justify-end" role="dialog" aria-modal="true"
         aria-label={title}>
      <div className="drawer-scrim absolute inset-0" onClick={onClose} />
      <div
        ref={panelRef}
        className="drawer-panel relative flex h-full flex-col bg-white shadow-2xl sm:rounded-l-3xl"
        style={{ width: "min(480px, 100vw)", ...(tone ? { ["--drawer-tone" as string]: tone } : {}) }}
      >
        {/* Header: brand gradient wash with two soft "orbs" floating behind the
            title (pure CSS, see .drawer-head in index.css). */}
        <header className="drawer-head relative flex items-start justify-between gap-3 overflow-hidden px-5 pb-5 pt-5">
          <span className="drawer-orb drawer-orb-a" aria-hidden="true" />
          <span className="drawer-orb drawer-orb-b" aria-hidden="true" />
          <div className="relative flex items-start gap-3">
            {icon && (
              <span className="drawer-chip grid h-11 w-11 shrink-0 place-items-center rounded-2xl text-lg" aria-hidden="true">{icon}</span>
            )}
            <div>
              <h2 className="text-lg font-extrabold tracking-tight text-ink">{title}</h2>
              {sub && <p className="mt-0.5 text-xs leading-relaxed text-ink-muted">{sub}</p>}
            </div>
          </div>
          <button
            onClick={onClose}
            aria-label="Close"
            className="drawer-close relative -mr-1 -mt-1 grid h-8 w-8 place-items-center rounded-full text-ink-faint transition hover:text-ink"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                 strokeWidth="2.2" strokeLinecap="round">
              <path d="M18 6 6 18M6 6l12 12" />
            </svg>
          </button>
        </header>

        <div className="drawer-body flex-1 space-y-4 overflow-y-auto px-5 pb-6 pt-4">{children}</div>

        {footer && (
          <footer className="drawer-foot flex items-center justify-end gap-2 px-5 py-3">
            {footer}
          </footer>
        )}
      </div>
    </div>,
    document.body,
  );
}

/** One labelled row inside a Drawer. `hint` sits under the control.
 *  Wraps a <label> (so clicking the caption focuses the field) unless `plain`
 *  is set — a group of buttons must NOT be inside a label, or every click on the
 *  caption would fire the first one. */
export function Field({ label, required, hint, plain, children }: {
  label: string; required?: boolean; hint?: string; plain?: boolean; children: ReactNode;
}) {
  const Tag = (plain ? "div" : "label") as "div";
  return (
    <Tag className="block">
      <span className="mb-1.5 block text-[0.7rem] font-bold uppercase tracking-wide text-ink-faint">
        {label}
        {required && <span className="ml-1 text-danger">*</span>}
      </span>
      {children}
      {hint && <span className="mt-1 block text-[0.7rem] text-ink-faint">{hint}</span>}
    </Tag>
  );
}

/** Field control styling — a nested tile that reads correctly on both surfaces. */
export const drawerCtl =
  "drawer-ctl w-full rounded-xl border border-hairline bg-black/5 px-3.5 py-2.5 text-sm text-ink " +
  "outline-none transition focus:border-accent";

/** A floating card grouping related fields inside a Drawer: a coloured icon
 *  badge, a title, an optional one-line sub, then the fields. `tone` picks the
 *  badge/edge colour — "accent" (default), "accent2", "success" or "pending" —
 *  all theme tokens, so the cards recolour with the brand. */
export function DrawerSection({ icon, title, sub, tone = "accent", children }: {
  icon: ReactNode; title: string; sub?: string;
  tone?: "accent" | "accent2" | "success" | "pending"; children: ReactNode;
}) {
  return (
    <section className={`dsec dsec-${tone} relative rounded-2xl p-4`}>
      <header className="mb-3 flex items-center gap-2.5">
        <span className="dsec-badge grid h-8 w-8 shrink-0 place-items-center rounded-xl text-white" aria-hidden="true">{icon}</span>
        <div className="min-w-0">
          <h3 className="text-[0.82rem] font-extrabold tracking-tight text-ink">{title}</h3>
          {sub && <p className="text-[0.7rem] leading-snug text-ink-muted">{sub}</p>}
        </div>
      </header>
      <div className="space-y-3">{children}</div>
    </section>
  );
}

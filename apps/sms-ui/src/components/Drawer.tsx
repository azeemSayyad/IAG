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
        className="drawer-panel relative flex h-full flex-col bg-white shadow-2xl"
        style={{ width: "min(460px, 100vw)" }}
      >
        <header
          className="flex items-start justify-between gap-3 border-b border-hairline px-5 py-4"
          style={{
            background: tone
              ? `linear-gradient(180deg, ${tone}1f, transparent)`
              : "linear-gradient(180deg, rgba(var(--accent-rgb),.14), transparent)",
          }}
        >
          <div className="flex items-start gap-3">
            {icon && (
              <span
                className="grid h-9 w-9 shrink-0 place-items-center rounded-xl text-base"
                style={{
                  background: tone ? `${tone}24` : "rgba(var(--accent-rgb),.16)",
                  border: `1px solid ${tone ? `${tone}55` : "rgba(var(--accent-rgb),.35)"}`,
                }}
                aria-hidden="true"
              >{icon}</span>
            )}
            <div>
              <h2 className="text-base font-extrabold text-ink">{title}</h2>
              {sub && <p className="mt-0.5 text-xs text-ink-muted">{sub}</p>}
            </div>
          </div>
          <button
            onClick={onClose}
            aria-label="Close"
            className="-mr-1 -mt-1 rounded-lg p-1.5 text-ink-faint hover:bg-black/5 hover:text-ink"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                 strokeWidth="2" strokeLinecap="round">
              <path d="M18 6 6 18M6 6l12 12" />
            </svg>
          </button>
        </header>

        <div className="flex-1 space-y-4 overflow-y-auto px-5 py-4">{children}</div>

        {footer && (
          <footer className="flex items-center justify-end gap-2 border-t border-hairline px-5 py-3">
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
  "w-full rounded-lg border border-hairline bg-black/5 px-3 py-2 text-sm text-ink " +
  "outline-none focus:border-accent";

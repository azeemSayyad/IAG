/* Contacts — the company phone book. ADMIN-CLASS only.
 *
 * The job is one thing: find a person and ring them. So the number is the loudest
 * thing on each card, Call is a primary action rather than a menu item, and search
 * matches name, number, email and role at once.
 *
 * Calling uses a `tel:` link — the same handoff inbox.html uses. The in-app Sinch
 * softphone is agent-only (it needs the agent's own caller-ID number), so an admin
 * page hands off to the device instead.
 */
import React, { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { api } from "../lib/api";
import { Drawer, Field, drawerCtl } from "../components/Drawer";

type Contact = {
  id: string;
  name: string;
  phone: string | null;
  email: string | null;
  role: string | null;
  notes: string | null;
  created_at?: string | null;
};

/** Strip a number down to something a dialler accepts. */
function telHref(phone: string): string {
  return "tel:" + phone.replace(/[^+\d]/g, "");
}

/** Initials for the avatar chip — two letters at most. */
function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  return ((parts[0]?.[0] || "") + (parts[1]?.[0] || "")).toUpperCase() || "?";
}

/* A stable HUE per contact, so the same person keeps the same avatar between
   loads. Only the hue varies — .contact-avatar in index.css fixes saturation and
   lightness for each mode, so no avatar shouts louder than another. */
const AVATAR_HUES = [210, 25, 150, 300, 190, 345, 90, 265];
function avatarHue(name: string): number {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
  return AVATAR_HUES[h % AVATAR_HUES.length];
}

const inputCls = "rounded-lg border border-hairline bg-white px-2 py-1.5 text-xs text-ink";
const btnCls = "rounded-lg bg-accent px-3 py-1.5 text-xs font-bold text-white disabled:opacity-50";
const btnGhost = "rounded-lg border border-hairline px-3 py-1.5 text-xs font-semibold text-ink-muted hover:bg-black/5 hover:text-ink disabled:opacity-50";

const BLANK = { name: "", phone: "", email: "", role: "", notes: "" };

export default function Contacts() {
  const [rows, setRows] = useState<Contact[]>([]);
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Contact | null>(null);
  const [f, setF] = useState(BLANK);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(() => {
    api<Contact[]>("/contacts")
      .then((r) => { setRows(Array.isArray(r) ? r : []); setErr(null); })
      .catch((e: Error) => setErr(e.message));
  }, []);
  useEffect(() => { load(); }, [load]);

  // Filtering happens client-side: the whole book is loaded once, so typing is
  // instant and doesn't fire a request per keystroke.
  const shown = useMemo(() => {
    const t = q.trim().toLowerCase();
    if (!t) return rows;
    return rows.filter((c) =>
      [c.name, c.phone, c.email, c.role].some((v) => (v || "").toLowerCase().includes(t)));
  }, [rows, q]);

  const close = () => { setOpen(false); setEditing(null); setF(BLANK); };
  const valid = !!f.name.trim();

  const save = () => {
    if (!valid) return;
    setBusy(true);
    const body = {
      name: f.name.trim(),
      phone: f.phone.trim() || null,
      email: f.email.trim() || null,
      role: f.role.trim() || null,
      notes: f.notes.trim() || null,
    };
    const req = editing
      ? api(`/contacts/${editing.id}`, { method: "PATCH", body: JSON.stringify(body) })
      : api("/contacts", { method: "POST", body: JSON.stringify(body) });
    req.then(() => { load(); close(); setErr(null); })
      .catch((e: Error) => setErr(e.message))
      .finally(() => setBusy(false));
  };

  const remove = (c: Contact) => {
    if (!window.confirm(`Remove ${c.name} from contacts?`)) return;
    setBusy(true);
    api(`/contacts/${c.id}`, { method: "DELETE" })
      .then(load).catch((e: Error) => setErr(e.message)).finally(() => setBusy(false));
  };

  const withPhone = rows.filter((c) => c.phone).length;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-extrabold text-ink">Contacts</h1>
          <p className="text-xs text-ink-muted">
            The company phone book — agents, staff, vendors. Tap a number to call.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative">
            <svg className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-ink-faint"
                 viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <circle cx="11" cy="11" r="7" /><path d="m21 21-4.3-4.3" />
            </svg>
            <input className={`${inputCls} w-56 pl-7`} value={q}
                   placeholder="Search name, number, email…"
                   onChange={(e) => setQ(e.target.value)} />
            {q && (
              <button onClick={() => setQ("")} aria-label="Clear search"
                      className="absolute right-2 top-1/2 -translate-y-1/2 text-ink-faint hover:text-ink">×</button>
            )}
          </div>
          <button className={btnCls} onClick={() => { setEditing(null); setF(BLANK); setOpen(true); }}>
            + Add contact
          </button>
        </div>
      </div>

      {err && (
        <div className="rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-xs font-semibold text-danger">
          {err}
        </div>
      )}

      <div className="glass rounded-2xl p-4">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 className="text-base font-bold text-ink">
            {q ? `${shown.length} of ${rows.length}` : `${rows.length} ${rows.length === 1 ? "contact" : "contacts"}`}
          </h2>
          <span className="text-xs text-ink-faint">{withPhone} with a number</span>
        </div>

        {rows.length === 0 ? (
          <Empty>
            No contacts yet.{" "}
            <button className="font-semibold text-accent underline"
                    onClick={() => { setEditing(null); setF(BLANK); setOpen(true); }}>
              Add the first one
            </button>
          </Empty>
        ) : shown.length === 0 ? (
          <Empty>
            Nothing matches “{q}”.{" "}
            <button className="font-semibold text-accent underline" onClick={() => setQ("")}>
              Show all {rows.length}
            </button>
          </Empty>
        ) : (
          <div className="mt-2 divide-y divide-hairline-soft">
            {shown.map((c) => (
              <div key={c.id}
                   className="group flex flex-wrap items-center gap-x-4 gap-y-2 py-2.5">
                <span
                  className="contact-avatar grid h-9 w-9 shrink-0 place-items-center rounded-full text-[0.72rem] font-extrabold"
                  style={{ "--h": avatarHue(c.name) } as React.CSSProperties}
                  aria-hidden="true"
                >{initials(c.name)}</span>

                <div className="min-w-0 flex-1 basis-48">
                  <div className="truncate text-sm font-bold text-ink">{c.name}</div>
                  <div className="truncate text-xs text-ink-faint">
                    {c.role || "—"}
                    {c.email && (
                      <>
                        {" · "}
                        <a href={`mailto:${c.email}`} className="hover:text-accent hover:underline">
                          {c.email}
                        </a>
                      </>
                    )}
                  </div>
                </div>

                {/* The number is the point of the row, so it gets its own column
                    in tabular figures — every row's digits line up. */}
                <div className="shrink-0 tabular-nums text-sm font-semibold text-ink-soft">
                  {c.phone || <span className="text-xs font-normal text-ink-faint">no number</span>}
                </div>

                <div className="flex shrink-0 items-center gap-1">
                  {c.phone && (
                    <a href={telHref(c.phone)} title={`Call ${c.name}`}
                       className="inline-flex items-center gap-1.5 rounded-lg bg-accent px-3 py-1.5 text-xs font-bold text-white hover:bg-accent-hover">
                      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                           strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6A19.79 19.79 0 0 1 2.12 4.18 2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.13.96.36 1.9.7 2.81a2 2 0 0 1-.45 2.11L8 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.91.34 1.85.57 2.81.7A2 2 0 0 1 22 16.92Z" />
                      </svg>
                      Call
                    </a>
                  )}
                  <span className="flex gap-0.5 opacity-0 transition group-hover:opacity-100 focus-within:opacity-100">
                    <IconBtn label="Edit" onClick={() => {
                      setEditing(c);
                      setF({ name: c.name, phone: c.phone || "", email: c.email || "",
                             role: c.role || "", notes: c.notes || "" });
                      setOpen(true);
                    }}>
                      <path d="M12 20h9" /><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z" />
                    </IconBtn>
                    <IconBtn label="Remove" danger onClick={() => remove(c)}>
                      <path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6" />
                    </IconBtn>
                  </span>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <Drawer
        open={open}
        title={editing ? "Edit contact" : "Add contact"}
        sub={editing ? "Only the name is required." : "Only the name is required — add the rest whenever."}
        icon="📇"
        tone="#2563EB"
        onClose={close}
        footer={
          <>
            <button className={btnGhost} onClick={close}>Cancel</button>
            <button className={btnCls} disabled={busy || !valid} onClick={save}>
              {busy ? "Saving…" : editing ? "Save changes" : "Add contact"}
            </button>
          </>
        }
      >
        <Field label="Name" required>
          <input className={drawerCtl} placeholder="e.g. Priya Silva" value={f.name}
                 onChange={(e) => setF({ ...f, name: e.target.value })}
                 onKeyDown={(e) => { if (e.key === "Enter" && valid) save(); }} />
        </Field>
        <Field label="Mobile number" hint="Tapping it on the card dials from your device.">
          <input className={`${drawerCtl} tabular-nums`} type="tel" inputMode="tel"
                 placeholder="(555) 123-4567" value={f.phone}
                 onChange={(e) => setF({ ...f, phone: e.target.value })} />
        </Field>
        <Field label="Email">
          <input className={drawerCtl} type="email" placeholder="name@company.com" value={f.email}
                 onChange={(e) => setF({ ...f, email: e.target.value })} />
        </Field>
        <Field label="Role">
          <input className={drawerCtl} placeholder="Agent · Developer · Carrier rep…" value={f.role}
                 onChange={(e) => setF({ ...f, role: e.target.value })} list="contact-roles" />
          <datalist id="contact-roles">
            <option value="Agent" /><option value="Team Leader" /><option value="Manager" />
            <option value="Developer" /><option value="Carrier rep" /><option value="Vendor" />
          </datalist>
        </Field>
        <Field label="Notes">
          <textarea className={drawerCtl} rows={3} placeholder="Optional" value={f.notes}
                    onChange={(e) => setF({ ...f, notes: e.target.value })} />
        </Field>
      </Drawer>
    </div>
  );
}

function Empty({ children }: { children: ReactNode }) {
  return <div className="py-10 text-center text-sm text-ink-muted">{children}</div>;
}

function IconBtn({ label, danger, onClick, children }: {
  label: string; danger?: boolean; onClick: () => void; children: ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      aria-label={label}
      title={label}
      className={`rounded-lg p-1.5 hover:bg-black/5 ${danger ? "text-danger" : "text-ink-faint hover:text-ink"}`}
    >
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
           strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">{children}</svg>
    </button>
  );
}

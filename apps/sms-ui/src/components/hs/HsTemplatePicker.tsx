/* The approved message library (hsTemplates.ts) as cards in the style of the
 * "Final Texts — Double Filter Checked" PDF: coloured bar per group, CARRIER /
 * CONSUMER badges, the text in a mono box, the reader's reaction and the tip.
 * "Use this text" puts it in the message box, where it can still be edited. */
import { useState } from "react";
import { HS_TEMPLATES, charCount, type HsTemplate } from "./hsTemplates";

// The PDF's colours.
const GROUP_COLOR: Record<HsTemplate["group"], string> = { ACA: "#4a7fa5", Tax: "#2a9d8f", Medicare: "#7b5ea7" };
const BADGE: Record<string, string> = {
  LOW: "#2a9d8f", PASS: "#2a9d8f", MEDIUM: "#e9a23b", CAUTION: "#e9a23b", WATCH: "#e63946",
};
const NAVY = "#1e3557";
const READER = "#f4845f";

function Badge({ k, v }: { k: string; v: string }) {
  return (
    <span className="whitespace-nowrap rounded-full px-2 py-0.5 text-[0.6rem] font-extrabold tracking-wide text-white"
          style={{ background: BADGE[v] || "#888" }}>
      {k}: {v}
    </span>
  );
}

export function TemplatePicker({ value, onPick }: { value: string; onPick: (text: string) => void }) {
  const [open, setOpen] = useState(false);
  const [group, setGroup] = useState<"All" | HsTemplate["group"]>("All");
  const inUse = HS_TEMPLATES.find((t) => t.text === value.trim());
  const list = HS_TEMPLATES.filter((t) => group === "All" || t.group === group);

  return (
    <div className="rounded-xl border border-hairline" data-testid="hs-template-picker">
      <button type="button" onClick={() => setOpen(!open)}
              className="flex w-full items-center justify-between gap-2 rounded-xl px-3 py-2 text-left text-white"
              style={{ background: NAVY }}>
        <span>
          <span className="block text-xs font-extrabold">📚 Final Texts — Double Filter Checked</span>
          <span className="block text-[0.66rem] opacity-80">
            {inUse ? <>In use: <b>{inUse.group} · {inUse.label}</b> — pick another or edit it below</> : `${HS_TEMPLATES.length} approved texts — pick one, then edit if needed`}
          </span>
        </span>
        <span className="text-xs font-bold">{open ? "Hide ▴" : "Choose ▾"}</span>
      </button>

      {open && (
        <div className="space-y-2 p-2">
          <div className="flex flex-wrap items-center gap-1.5 text-[0.66rem]">
            {(["All", "ACA", "Tax", "Medicare"] as const).map((g) => (
              <button key={g} type="button" onClick={() => setGroup(g)}
                      className={`rounded-full px-2.5 py-0.5 font-bold ${group === g ? "text-white" : "bg-black/5 text-ink-muted hover:text-ink"}`}
                      style={group === g ? { background: g === "All" ? NAVY : GROUP_COLOR[g] } : undefined}>
                {g}
              </button>
            ))}
            <span className="ml-auto text-ink-faint">Plain characters only · one segment · counts include [Name]</span>
          </div>

          {list.map((t) => {
            const chosen = inUse?.id === t.id;
            return (
              <div key={t.id} data-testid={`hs-template-${t.id}`}
                   className="overflow-hidden rounded-xl border bg-white/70"
                   style={{ borderColor: chosen ? GROUP_COLOR[t.group] : `${GROUP_COLOR[t.group]}66`, borderLeftWidth: 6,
                            boxShadow: chosen ? `0 0 0 2px ${GROUP_COLOR[t.group]}55` : undefined }}>
                <div className="space-y-1.5 p-2.5">
                  <div className="flex flex-wrap items-center justify-between gap-1.5">
                    <div className="text-xs font-extrabold text-ink">
                      {t.group} · {t.label}{t.top && <span className="ml-1">★ TOP PICK</span>}
                    </div>
                    <div className="flex flex-wrap items-center gap-1">
                      <Badge k="CARRIER" v={t.carrier} />
                      <Badge k="CONSUMER" v={t.consumer} />
                      <span className="text-[0.62rem] tabular-nums text-ink-faint">{charCount(t.text)} chars</span>
                    </div>
                  </div>
                  <div className="rounded-lg bg-black/5 px-2.5 py-1.5 font-mono text-[0.7rem] leading-snug text-ink">{t.text}</div>
                  <div className="text-[0.68rem] text-ink-soft">
                    <b style={{ color: READER }}>{t.reader}:</b> {t.quote}
                  </div>
                  <div className="text-[0.66rem] text-ink-muted">→ {t.note}</div>
                  {t.warn && (
                    <div className="rounded-md px-2 py-1 text-[0.66rem] font-semibold"
                         style={{ background: "#e9a23b22", color: "#b7791f" }}>⚠ {t.warn}</div>
                  )}
                  <div className="flex justify-end">
                    <button type="button" disabled={chosen} onClick={() => { onPick(t.text); setOpen(false); }}
                            className="rounded-lg px-3 py-1 text-[0.7rem] font-bold text-white disabled:opacity-60"
                            style={{ background: GROUP_COLOR[t.group] }}>
                      {chosen ? "✓ In use" : "Use this text"}
                    </button>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

/** Under the message box: length + segments, like the library's counts. */
export function TextLength({ text }: { text: string }) {
  const n = charCount(text);
  const segs = n <= 160 ? 1 : Math.ceil(n / 153);
  return (
    <span className={`rounded-full px-2 py-0.5 text-[0.62rem] font-bold tabular-nums ${segs > 1 ? "bg-danger/10 text-danger" : "bg-black/5 text-ink-muted"}`}
          title="A text over 160 characters is sent as 2+ messages (costs double, and carriers are stricter)">
      {n} chars · {segs === 1 ? "1 text" : `${segs} texts — shorten it`}
    </span>
  );
}

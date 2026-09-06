/* ScriptBlocks — renders a training step's `content` (line-based script markup)
 * as the styled call-script cards: speaker quotes, customer responses, numbered
 * checklists, callouts. The same text an admin types into the editor.
 *
 *   # Heading             section heading
 *   [Agent]: "..."        speaker quote card — any [Label]: prefix
 *   > note                customer response / stage direction (italic)
 *   1. item               numbered checklist (consecutive lines group)
 *   - item                bullet list
 *   ++ Title | body       success (green) callout
 *   :: Title | body       neutral callout
 *   !! text               critical (red) callout
 *   plain line            paragraph
 */
import type { ReactNode } from "react";

type Block =
  | { t: "h"; text: string }
  | { t: "quote"; label: string; text: string }
  | { t: "note"; text: string }
  | { t: "ol"; items: string[] }
  | { t: "ul"; items: string[] }
  | { t: "callout"; tone: "good" | "neutral" | "critical"; title: string; body: string }
  | { t: "p"; text: string };

export function parseScript(src: string): Block[] {
  const out: Block[] = [];
  for (const raw of (src || "").split(/\r?\n/)) {
    const line = raw.trim();
    if (!line) continue;
    let m: RegExpMatchArray | null;
    if ((m = line.match(/^#\s+(.*)$/))) { out.push({ t: "h", text: m[1] }); continue; }
    if ((m = line.match(/^\[([^\]]+)\]:\s*(.*)$/))) { out.push({ t: "quote", label: m[1], text: m[2] }); continue; }
    if ((m = line.match(/^>\s?(.*)$/))) { out.push({ t: "note", text: m[1] }); continue; }
    if ((m = line.match(/^\d+[.)]\s+(.*)$/))) {
      const last = out[out.length - 1];
      if (last && last.t === "ol") last.items.push(m[1]); else out.push({ t: "ol", items: [m[1]] });
      continue;
    }
    if ((m = line.match(/^[-•]\s+(.*)$/))) {
      const last = out[out.length - 1];
      if (last && last.t === "ul") last.items.push(m[1]); else out.push({ t: "ul", items: [m[1]] });
      continue;
    }
    if ((m = line.match(/^(\+\+|::)\s*(.*)$/))) {
      const [title, ...rest] = m[2].split("|");
      out.push({ t: "callout", tone: m[1] === "++" ? "good" : "neutral", title: title.trim(), body: rest.join("|").trim() });
      continue;
    }
    if ((m = line.match(/^!!\s*(.*)$/))) { out.push({ t: "callout", tone: "critical", title: "", body: m[1] }); continue; }
    out.push({ t: "p", text: line });
  }
  return out;
}

export default function ScriptBlocks({ content }: { content: string }) {
  const blocks = parseScript(content);
  if (!blocks.length) return null;
  return (
    <div className="space-y-2.5">
      {blocks.map((b, i) => {
        switch (b.t) {
          case "h":
            return (
              <h3 key={i} className={`flex items-center gap-2.5 text-[0.95rem] font-extrabold text-ink ${i ? "pt-4" : ""}`}>
                <span className="h-[3px] w-5 rounded-full bg-accent" aria-hidden="true" />
                {b.text}
              </h3>
            );
          case "quote":
            return (
              <div key={i} className="tr-quote flex gap-2.5 rounded-xl border-l-[3px] border-accent px-4 py-3 text-sm leading-relaxed text-ink-soft">
                <b className="shrink-0 font-bold text-accent">[{b.label}]:</b>
                <span>{b.text}</span>
              </div>
            );
          case "note":
            return (
              <div key={i} className="tr-note rounded-lg px-4 py-2.5 text-[0.8rem] italic leading-relaxed text-ink-muted">
                {b.text}
              </div>
            );
          case "ol":
            return (
              <ol key={i} className="space-y-2">
                {b.items.map((it, j) => (
                  <li key={j} className="tr-tile relative rounded-xl py-2.5 pl-11 pr-4 text-[0.84rem] leading-relaxed text-ink-soft">
                    <span className="absolute left-3 top-2.5 grid h-5 w-5 place-items-center rounded-full bg-accent text-[0.68rem] font-bold text-white tabular-nums">
                      {j + 1}
                    </span>
                    {it}
                  </li>
                ))}
              </ol>
            );
          case "ul":
            return (
              <ul key={i} className="space-y-2">
                {b.items.map((it, j) => (
                  <li key={j} className="tr-quote rounded-xl border-l-[3px] border-accent px-4 py-2.5 text-[0.84rem] leading-relaxed text-ink-soft">
                    {it}
                  </li>
                ))}
              </ul>
            );
          case "callout":
            return <Callout key={i} tone={b.tone} title={b.title} body={b.body} />;
          default:
            return <p key={i} className="text-sm leading-relaxed text-ink-soft">{b.text}</p>;
        }
      })}
    </div>
  );
}

function Callout({ tone, title, body }: { tone: "good" | "neutral" | "critical"; title: string; body: ReactNode }) {
  const cls = {
    good: "border-success/30 bg-success/10",
    neutral: "tr-tile border-hairline",
    critical: "border-danger/30 bg-danger/10",
  }[tone];
  const titleCls = { good: "text-success", neutral: "text-ink", critical: "text-danger" }[tone];
  return (
    <div className={`rounded-xl border px-4 py-3 ${cls}`}>
      {title && <div className={`mb-1 text-[0.8rem] font-bold ${titleCls}`}>{title}</div>}
      <div className={`text-[0.84rem] leading-relaxed ${tone === "critical" ? "font-bold text-ink" : "text-ink-soft"}`}>
        {tone === "critical" && (
          <svg className="mr-1.5 inline h-4 w-4 -translate-y-px text-danger" viewBox="0 0 24 24" fill="none"
               stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0Z" />
            <path d="M12 9v4M12 17h.01" />
          </svg>
        )}
        {body}
      </div>
    </div>
  );
}

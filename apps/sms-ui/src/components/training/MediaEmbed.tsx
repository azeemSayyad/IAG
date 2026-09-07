/* MediaEmbed — the frame at the top of a training step, for whatever the step
 * carries:
 *   link   → Vimeo / YouTube become an embedded player; anything else becomes
 *            an "Open video" card (we can't iframe arbitrary sites).
 *   upload → rendered by media kind: video and audio players, an image figure,
 *            an inline PDF viewer, or a download card for documents / slides /
 *            spreadsheets. Files stream from our API (S3 or DB behind it);
 *            <video>/<iframe> can't send an Authorization header, so the token
 *            rides along as ?token= — the endpoint accepts either.
 *   none   → a "coming soon" placeholder that still holds the layout.
 */
import { getAccessToken } from "../../lib/auth";

export type VideoKind = "none" | "link" | "upload";
export type MediaKind = "video" | "audio" | "image" | "pdf" | "doc" | "slides" | "sheet" | "file";

export function embedFor(url: string): { kind: "vimeo" | "youtube"; src: string } | null {
  const vimeo = url.match(/vimeo\.com\/(?:video\/)?(\d+)(?:\/([0-9a-f]+))?/i);
  if (vimeo) {
    const h = vimeo[2] || new URL(url, "https://vimeo.com").searchParams.get("h");
    return { kind: "vimeo", src: `https://player.vimeo.com/video/${vimeo[1]}?dnt=1${h ? `&h=${h}` : ""}` };
  }
  const yt = url.match(/(?:youtu\.be\/|youtube\.com\/(?:watch\?(?:.*&)?v=|embed\/|shorts\/))([\w-]{11})/i);
  if (yt) return { kind: "youtube", src: `https://www.youtube-nocookie.com/embed/${yt[1]}?rel=0` };
  return null;
}

/** Human label for the rail / meta line. */
export function mediaLabel(kind: MediaKind | null | undefined): string {
  switch (kind) {
    case "video": return "Video";
    case "audio": return "Audio";
    case "image": return "Image";
    case "pdf": return "PDF";
    case "doc": return "Document";
    case "slides": return "Slides";
    case "sheet": return "Spreadsheet";
    default: return "File";
  }
}

export function fmtBytes(n: number): string {
  if (n >= 1e9) return `${(n / 1e9).toFixed(1)} GB`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(n / 1e3))} KB`;
}

const frame = "tr-video relative w-full overflow-hidden rounded-2xl";

export default function MediaEmbed({ kind, mediaKind, url, src, title, filename, byteSize }: {
  kind: VideoKind; mediaKind: MediaKind | null; url: string | null; src: string | null;
  title: string; filename: string | null; byteSize: number;
}) {
  if (kind === "upload" && src) {
    const token = encodeURIComponent(getAccessToken() || "");
    const inline = `${src}?token=${token}`;
    const download = `${src}?token=${token}&download=1`;
    const name = filename || "file";

    switch (mediaKind) {
      case "video":
        return (
          <div className={`${frame} aspect-video bg-black shadow-xl`}>
            <video className="h-full w-full" controls preload="metadata" playsInline src={inline} />
          </div>
        );
      case "audio":
        return (
          <div className={`${frame} tr-audio flex flex-col gap-4 border border-hairline p-5 sm:flex-row sm:items-center`}>
            <FileGlyph kind="audio" />
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-bold text-ink">{name}</div>
              <div className="text-xs text-ink-faint">Audio · {fmtBytes(byteSize)}</div>
              <audio className="mt-3 w-full" controls preload="metadata" src={inline} />
            </div>
          </div>
        );
      case "image":
        return (
          <figure className={`${frame} tr-placeholder border border-hairline`}>
            <a href={inline} target="_blank" rel="noreferrer" title="Open full size">
              <img src={inline} alt={title} className="mx-auto max-h-[70vh] w-auto max-w-full object-contain" />
            </a>
            <figcaption className="flex items-center justify-between gap-3 border-t border-hairline-soft bg-white/60 px-4 py-2 text-xs text-ink-faint">
              <span className="truncate">{name} · {fmtBytes(byteSize)}</span>
              <DownloadLink href={download} />
            </figcaption>
          </figure>
        );
      case "pdf":
        return (
          <div className={`${frame} border border-hairline bg-white`}>
            <iframe className="block h-[70vh] min-h-[420px] w-full" src={`${inline}#view=FitH`} title={name} />
            <div className="flex items-center justify-between gap-3 border-t border-hairline-soft bg-white/60 px-4 py-2 text-xs text-ink-faint">
              <span className="truncate">{name} · {fmtBytes(byteSize)}</span>
              <span className="flex shrink-0 items-center gap-3">
                <a href={inline} target="_blank" rel="noreferrer" className="font-semibold text-accent hover:underline">Open in new tab</a>
                <DownloadLink href={download} />
              </span>
            </div>
          </div>
        );
      default:
        return (
          <a href={download} className={`${frame} tr-placeholder group flex items-center gap-5 border border-hairline p-5 no-underline hover:border-accent/40`}>
            <FileGlyph kind={mediaKind || "file"} />
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-bold text-ink">{name}</div>
              <div className="text-xs text-ink-faint">{mediaLabel(mediaKind)} · {fmtBytes(byteSize)}</div>
              <div className="mt-2 text-xs font-semibold text-accent group-hover:underline">Download to open ↓</div>
            </div>
          </a>
        );
    }
  }

  if (kind === "link" && url) {
    const e = embedFor(url);
    if (e) {
      return (
        <div className={`${frame} aspect-video bg-black shadow-xl`}>
          <iframe className="absolute inset-0 h-full w-full" src={e.src} title={title}
                  allow="autoplay; fullscreen; picture-in-picture; clipboard-write" allowFullScreen />
        </div>
      );
    }
    return (
      <a href={url} target="_blank" rel="noreferrer"
         className={`${frame} tr-placeholder group flex aspect-video flex-col items-center justify-center gap-2 border border-hairline text-ink-muted hover:text-accent`}>
        <PlayGlyph />
        <span className="text-sm font-bold">Open video</span>
        <span className="max-w-[60ch] truncate px-6 text-xs text-ink-faint">{url}</span>
      </a>
    );
  }

  return (
    <div className={`${frame} tr-placeholder flex aspect-video flex-col items-center justify-center gap-2 border border-dashed border-hairline text-ink-faint`}>
      <PlayGlyph dim />
      <span className="text-sm font-bold text-ink-muted">Content coming soon</span>
      <span className="text-xs">This step's video or material hasn't been added yet.</span>
    </div>
  );
}

function DownloadLink({ href }: { href: string }) {
  return (
    <a href={href} className="inline-flex shrink-0 items-center gap-1 font-semibold text-accent hover:underline">
      <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 3v12" /><path d="m7 10 5 5 5-5" /><path d="M5 21h14" /></svg>
      Download
    </a>
  );
}

function PlayGlyph({ dim }: { dim?: boolean }) {
  return (
    <span className={`grid h-14 w-14 place-items-center rounded-full ${dim ? "bg-black/5" : "bg-accent/12 group-hover:bg-accent/20"}`}>
      <svg className={`h-6 w-6 translate-x-px ${dim ? "text-ink-faint" : "text-accent"}`} viewBox="0 0 24 24" fill="currentColor">
        <path d="M8 5v14l11-7z" />
      </svg>
    </span>
  );
}

/* Big file-type tile: a colour per kind so a PDF, a deck and a sheet read
   differently at a glance (these are file-type conventions, not brand colours). */
const GLYPH: Record<string, { bg: string; label: string }> = {
  audio:  { bg: "linear-gradient(135deg,#7C3AED,#A78BFA)", label: "♪" },
  pdf:    { bg: "linear-gradient(135deg,#DC2626,#F87171)", label: "PDF" },
  doc:    { bg: "linear-gradient(135deg,#2563EB,#60A5FA)", label: "DOC" },
  slides: { bg: "linear-gradient(135deg,#EA580C,#FB923C)", label: "PPT" },
  sheet:  { bg: "linear-gradient(135deg,#059669,#34D399)", label: "XLS" },
  file:   { bg: "linear-gradient(135deg,#475569,#94A3B8)", label: "FILE" },
};

function FileGlyph({ kind }: { kind: string }) {
  const g = GLYPH[kind] || GLYPH.file;
  return (
    <span className="grid h-16 w-16 shrink-0 place-items-center rounded-2xl text-[0.8rem] font-extrabold tracking-wide text-white shadow-lg"
          style={{ background: g.bg }} aria-hidden="true">
      {g.label}
    </span>
  );
}

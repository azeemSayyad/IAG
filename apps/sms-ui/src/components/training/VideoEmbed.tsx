/* VideoEmbed — one 16:9 frame for whatever a training step's video is:
 *   link   → Vimeo / YouTube become an embedded player; anything else becomes
 *            an "Open video" card (we can't iframe arbitrary sites).
 *   upload → the native player, streamed from our API (S3 or DB behind it).
 *            <video> can't send an Authorization header, so the token rides
 *            along as ?token= — the endpoint accepts either.
 *   none   → a "coming soon" placeholder that still holds the layout.
 */
import { getAccessToken } from "../../lib/auth";

export type VideoKind = "none" | "link" | "upload";

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

export default function VideoEmbed({ kind, url, src, title }: {
  kind: VideoKind; url: string | null; src: string | null; title: string;
}) {
  const frame = "tr-video relative aspect-video w-full overflow-hidden rounded-2xl";

  if (kind === "upload" && src) {
    const token = getAccessToken() || "";
    return (
      <div className={`${frame} bg-black shadow-xl`}>
        <video className="h-full w-full" controls preload="metadata" playsInline
               src={`${src}?token=${encodeURIComponent(token)}`} />
      </div>
    );
  }

  if (kind === "link" && url) {
    const e = embedFor(url);
    if (e) {
      return (
        <div className={`${frame} bg-black shadow-xl`}>
          <iframe className="absolute inset-0 h-full w-full" src={e.src} title={title}
                  allow="autoplay; fullscreen; picture-in-picture; clipboard-write" allowFullScreen />
        </div>
      );
    }
    return (
      <a href={url} target="_blank" rel="noreferrer"
         className={`${frame} tr-placeholder group flex flex-col items-center justify-center gap-2 border border-hairline text-ink-muted hover:text-accent`}>
        <PlayGlyph />
        <span className="text-sm font-bold">Open video</span>
        <span className="max-w-[60ch] truncate px-6 text-xs text-ink-faint">{url}</span>
      </a>
    );
  }

  return (
    <div className={`${frame} tr-placeholder flex flex-col items-center justify-center gap-2 border border-dashed border-hairline text-ink-faint`}>
      <PlayGlyph dim />
      <span className="text-sm font-bold text-ink-muted">Video coming soon</span>
      <span className="text-xs">This step's video hasn't been added yet.</span>
    </div>
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

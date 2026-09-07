/* Training — the agent training program.
 *
 * Agents work through an ordered list of steps (a video or other material plus,
 * where it applies, the call script under it) and tick them off; progress is
 * per browser. Admins flip on "Edit program" to add, remove, rename and
 * drag-reorder steps, paste a Vimeo/YouTube link or upload a file (video,
 * audio, image, PDF, Word, PowerPoint, Excel), and edit the script text.
 *
 * Data lives in /training/steps (backend/app/training). Uploads go to S3 when
 * it's configured, DB bytes otherwise — the page doesn't care which. The API
 * fields are still named video_* from the video-only first version; the
 * `media_kind` it returns is what decides how an upload is shown.
 */
import { Fragment, useCallback, useEffect, useMemo, useRef, useState, type DragEvent, type ReactNode } from "react";
import { api, apiUpload } from "../lib/api";
import { isAdmin } from "../lib/auth";
import { Drawer, DrawerSection, Field, drawerCtl } from "../components/Drawer";
import ScriptBlocks from "../components/training/ScriptBlocks";
import MediaEmbed, { embedFor, fmtBytes, mediaLabel, type MediaKind, type VideoKind } from "../components/training/MediaEmbed";

type Step = {
  id: string;
  position: number;
  title: string;
  description: string | null;
  content: string | null;
  video_kind: VideoKind;
  video_url: string | null;
  video_filename: string | null;
  video_byte_size: number;
  video_storage: "s3" | "db" | null;
  video_src: string | null;
  media_kind: MediaKind | null;
};

const ACCEPT = [
  "video/*", "audio/*", "image/*", ".pdf",
  ".doc", ".docx", ".rtf", ".txt", ".md", ".ppt", ".pptx", ".key", ".xls", ".xlsx", ".csv",
].join(",");

const PROGRESS_KEY = "ebTrainingProgress";
const btnCls = "rounded-lg bg-accent px-3 py-1.5 text-xs font-bold text-white hover:bg-accent-hover disabled:opacity-50";
const btnGhost = "rounded-lg border border-hairline px-3 py-1.5 text-xs font-semibold text-ink-muted hover:bg-black/5 hover:text-ink disabled:opacity-50";

function loadDone(): string[] {
  try { return JSON.parse(localStorage.getItem(PROGRESS_KEY) || "[]"); } catch { return []; }
}
function saveDone(ids: string[]) {
  try { localStorage.setItem(PROGRESS_KEY, JSON.stringify(ids)); } catch { /* private mode */ }
}

async function uploadVideo(stepId: string, file: File): Promise<Step> {
  const fd = new FormData();
  fd.append("file", file, file.name);
  // apiUpload refreshes an expired access token and replays the upload, so a
  // long file transfer started near the end of a token's life still lands.
  return apiUpload<Step>(`/training/steps/${stepId}/video`, fd);
}

type Form = { title: string; description: string; content: string; videoMode: VideoKind; videoUrl: string; file: File | null };
const BLANK: Form = { title: "", description: "", content: "", videoMode: "none", videoUrl: "", file: null };

export default function Training() {
  const admin = isAdmin();
  const [steps, setSteps] = useState<Step[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [active, setActive] = useState<string | null>(null);
  const [done, setDone] = useState<string[]>(loadDone);
  const [editMode, setEditMode] = useState(false);
  const [drawer, setDrawer] = useState<null | { step: Step | null }>(null);
  const [f, setF] = useState<Form>(BLANK);
  const [busy, setBusy] = useState<string | null>(null);
  const [dragId, setDragId] = useState<string | null>(null);
  const [overId, setOverId] = useState<string | null>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  const load = useCallback(() => {
    api<Step[]>("/training/steps")
      .then((r) => { setSteps(Array.isArray(r) ? r : []); setErr(null); })
      .catch((e: Error) => setErr(e.message));
  }, []);
  useEffect(() => { load(); }, [load]);

  // Keep the selection valid as steps come and go; default to the first step
  // that isn't done yet (or the first one).
  useEffect(() => {
    if (!steps || !steps.length) { setActive(null); return; }
    if (active && steps.some((s) => s.id === active)) return;
    setActive((steps.find((s) => !done.includes(s.id)) || steps[0]).id);
  }, [steps, active, done]);

  const current = useMemo(() => steps?.find((s) => s.id === active) || null, [steps, active]);
  const idx = steps && current ? steps.findIndex((s) => s.id === current.id) : -1;
  const total = steps?.length || 0;
  const doneCount = steps ? steps.filter((s) => done.includes(s.id)).length : 0;
  const pct = total ? Math.round((doneCount / total) * 100) : 0;

  const goTo = (id: string) => {
    setActive(id);
    panelRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  };
  const markDone = () => {
    if (!current) return;
    const next = done.includes(current.id) ? done : [...done, current.id];
    setDone(next); saveDone(next);
    if (steps && idx < steps.length - 1) goTo(steps[idx + 1].id);
  };
  const unmark = () => {
    if (!current) return;
    const next = done.filter((x) => x !== current.id);
    setDone(next); saveDone(next);
  };

  // ---- admin: edit / add ----
  const openEdit = (s: Step | null) => {
    setF(s ? {
      title: s.title, description: s.description || "", content: s.content || "",
      videoMode: s.video_kind, videoUrl: s.video_url || "", file: null,
    } : BLANK);
    setDrawer({ step: s });
  };
  const closeDrawer = () => { setDrawer(null); setF(BLANK); };
  const valid = !!f.title.trim() && (f.videoMode !== "link" || !!f.videoUrl.trim());

  const save = async () => {
    if (!drawer || !valid) return;
    setBusy("save");
    try {
      const body: Record<string, unknown> = {
        title: f.title.trim(),
        description: f.description.trim() || null,
        content: f.content.trim() || null,
      };
      // A link replaces any uploaded file; "none" clears both. When a file is
      // chosen the upload below sets the kind, so leave video_url untouched.
      if (f.videoMode === "link") body.video_url = f.videoUrl.trim();
      else if (f.videoMode === "none") body.video_url = "";
      let saved: Step = drawer.step
        ? await api<Step>(`/training/steps/${drawer.step.id}`, { method: "PATCH", body: JSON.stringify(body) })
        : await api<Step>("/training/steps", { method: "POST", body: JSON.stringify({ ...body, position: idx >= 0 ? idx + 1 : undefined }) });
      if (f.videoMode === "upload" && f.file) {
        setBusy("upload");
        saved = await uploadVideo(saved.id, f.file);
      }
      load();
      setActive(saved.id);
      closeDrawer();
      setErr(null);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const removeStep = async (s: Step) => {
    if (!window.confirm(`Remove "${s.title}" from the program?`)) return;
    setBusy("delete");
    try {
      await api(`/training/steps/${s.id}`, { method: "DELETE" });
      load(); setErr(null);
    } catch (e) { setErr((e as Error).message); } finally { setBusy(null); }
  };

  const removeVideo = async () => {
    if (!drawer?.step) return;
    setBusy("removeVideo");
    try {
      await api(`/training/steps/${drawer.step.id}/video`, { method: "DELETE" });
      setF({ ...f, videoMode: "none", videoUrl: "", file: null });
      load(); setErr(null);
    } catch (e) { setErr((e as Error).message); } finally { setBusy(null); }
  };

  // ---- admin: reorder (drag on desktop, arrows everywhere) ----
  const persistOrder = (next: Step[]) => {
    setSteps(next.map((s, i) => ({ ...s, position: i })));
    api<Step[]>("/training/steps/reorder", { method: "POST", body: JSON.stringify({ ids: next.map((s) => s.id) }) })
      .then((r) => { if (Array.isArray(r)) setSteps(r); })
      .catch((e: Error) => { setErr(e.message); load(); });
  };
  const move = (id: string, dir: -1 | 1) => {
    if (!steps) return;
    const i = steps.findIndex((s) => s.id === id);
    const j = i + dir;
    if (i < 0 || j < 0 || j >= steps.length) return;
    const next = steps.slice();
    [next[i], next[j]] = [next[j], next[i]];
    persistOrder(next);
  };
  const onDrop = (targetId: string) => {
    if (!steps || !dragId || dragId === targetId) { setDragId(null); setOverId(null); return; }
    const next = steps.slice();
    const from = next.findIndex((s) => s.id === dragId);
    const [moved] = next.splice(from, 1);
    const to = next.findIndex((s) => s.id === targetId);
    next.splice(to, 0, moved);
    setDragId(null); setOverId(null);
    persistOrder(next);
  };

  return (
    <div className="space-y-4">
      {/* ── Header ── */}
      <div className="tr-hero glass relative overflow-hidden rounded-2xl p-5 sm:p-6">
        <div className="relative flex flex-wrap items-center justify-between gap-4">
          <div className="min-w-0">
            <div className="mb-1 inline-flex items-center gap-2 text-[0.68rem] font-bold uppercase tracking-[0.14em] text-accent">
              <span className="h-[2px] w-5 rounded-full bg-accent" aria-hidden="true" />
              Agent onboarding
            </div>
            <h1 className="text-2xl font-extrabold tracking-tight text-ink">Training Program</h1>
            <p className="mt-1 max-w-[56ch] text-sm text-ink-muted">
              Work through each step in order — watch the video, read what's under it, and mark it complete.
            </p>
          </div>
          <div className="flex items-center gap-4">
            <ProgressRing pct={pct} />
            <div className="text-sm">
              <div className="font-extrabold text-ink tabular-nums">{doneCount} of {total} complete</div>
              <div className="text-xs text-ink-faint">{pct === 100 ? "You're all set — welcome to the team." : pct ? "Keep going." : "Start with step 1."}</div>
            </div>
            {admin && (
              <button
                className={`ml-1 inline-flex items-center gap-2 rounded-full border px-3.5 py-1.5 text-xs font-bold transition ${
                  editMode ? "border-accent bg-accent text-white" : "border-hairline text-ink-muted hover:text-ink"
                }`}
                onClick={() => setEditMode((v) => !v)}
                aria-pressed={editMode}
              >
                <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M12 20h9" /><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z" />
                </svg>
                {editMode ? "Done editing" : "Edit program"}
              </button>
            )}
          </div>
        </div>
      </div>

      {err && (
        <div className="rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-xs font-semibold text-danger">{err}</div>
      )}

      {steps === null ? (
        <div className="glass rounded-2xl p-10 text-center text-sm text-ink-muted">Loading the program…</div>
      ) : steps.length === 0 ? (
        <div className="glass rounded-2xl p-10 text-center text-sm text-ink-muted">
          No training steps yet.
          {admin && <> <button className="font-semibold text-accent underline" onClick={() => openEdit(null)}>Add the first step</button></>}
        </div>
      ) : (
        // minmax(0,…) on BOTH layouts: without it the rail's fixed-width chips (a
        // scrolling row below lg) set the grid's min-content width and the whole
        // page overflowed sideways on tablets/phones.
        <div className="grid grid-cols-[minmax(0,1fr)] items-start gap-4 lg:grid-cols-[300px_minmax(0,1fr)]">
          {/* ── Rail ── */}
          <nav className="glass min-w-0 rounded-2xl p-2 lg:sticky lg:top-[calc(var(--topbar-h,64px)+16px)]" aria-label="Training steps">
            <div className="flex gap-1.5 overflow-x-auto pb-1 lg:flex-col lg:overflow-visible lg:pb-0">
              {steps.map((s, i) => {
                const isDone = done.includes(s.id);
                const isActive = s.id === active;
                return (
                  <div
                    key={s.id}
                    draggable={editMode}
                    onDragStart={(e: DragEvent) => { setDragId(s.id); e.dataTransfer.effectAllowed = "move"; }}
                    onDragOver={(e: DragEvent) => { if (editMode && dragId) { e.preventDefault(); setOverId(s.id); } }}
                    onDragLeave={() => setOverId((v) => (v === s.id ? null : v))}
                    onDrop={(e: DragEvent) => { e.preventDefault(); onDrop(s.id); }}
                    onDragEnd={() => { setDragId(null); setOverId(null); }}
                    className={`group relative flex min-w-[220px] shrink-0 items-center gap-3 rounded-xl border px-3 py-2.5 text-left transition lg:min-w-0 lg:shrink ${
                      isActive ? "border-accent/25 bg-accent/10" : "border-transparent hover:bg-black/5"
                    } ${overId === s.id && dragId !== s.id ? "ring-2 ring-accent/50" : ""} ${dragId === s.id ? "opacity-40" : ""}`}
                  >
                    {editMode && (
                      <span className="hidden cursor-grab text-ink-faint active:cursor-grabbing lg:block" title="Drag to reorder" aria-hidden="true">
                        <svg className="h-4 w-4" viewBox="0 0 24 24" fill="currentColor"><circle cx="9" cy="6" r="1.6"/><circle cx="15" cy="6" r="1.6"/><circle cx="9" cy="12" r="1.6"/><circle cx="15" cy="12" r="1.6"/><circle cx="9" cy="18" r="1.6"/><circle cx="15" cy="18" r="1.6"/></svg>
                      </span>
                    )}
                    <button onClick={() => goTo(s.id)} className="flex min-w-0 flex-1 items-center gap-3 text-left">
                      <span className={`grid h-7 w-7 shrink-0 place-items-center rounded-full text-[0.72rem] font-bold tabular-nums transition ${
                        isDone ? "bg-success text-white" : isActive ? "bg-accent text-white" : "bg-black/8 text-ink-muted"
                      }`}>
                        {isDone ? (
                          <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"><path d="M20 6 9 17l-5-5" /></svg>
                        ) : i + 1}
                      </span>
                      <span className="min-w-0">
                        <span className={`block truncate text-[0.82rem] font-semibold leading-tight ${isActive ? "text-accent" : "text-ink"}`}>{s.title}</span>
                        <span className="mt-0.5 block text-[0.68rem] text-ink-faint">
                          {s.video_kind === "none" ? "Coming soon" : s.video_kind === "upload" ? mediaLabel(s.media_kind) : (embedFor(s.video_url || "")?.kind === "youtube" ? "YouTube" : embedFor(s.video_url || "") ? "Vimeo" : "Video link")}
                          {s.content ? " · Script" : ""}
                        </span>
                      </span>
                    </button>
                    {editMode && (
                      <span className="flex shrink-0 items-center gap-0.5">
                        <MiniBtn label="Move up" disabled={i === 0} onClick={() => move(s.id, -1)}><path d="m18 15-6-6-6 6" /></MiniBtn>
                        <MiniBtn label="Move down" disabled={i === steps.length - 1} onClick={() => move(s.id, 1)}><path d="m6 9 6 6 6-6" /></MiniBtn>
                        <MiniBtn label="Remove step" danger onClick={() => removeStep(s)}><path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6" /></MiniBtn>
                      </span>
                    )}
                  </div>
                );
              })}
            </div>
            {editMode && (
              <button onClick={() => openEdit(null)}
                      className="mt-1.5 flex w-full items-center justify-center gap-1.5 rounded-xl border border-dashed border-hairline py-2.5 text-xs font-bold text-ink-muted hover:border-accent hover:text-accent">
                <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round"><path d="M12 5v14M5 12h14" /></svg>
                Add step {idx >= 0 ? `after ${idx + 1}` : ""}
              </button>
            )}
          </nav>

          {/* ── Content ── */}
          <div ref={panelRef} className="glass min-w-0 rounded-2xl p-5 sm:p-7 lg:p-8">
            {current && (
              <>
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="mb-2 inline-flex items-center gap-2.5 text-[0.68rem] font-bold uppercase tracking-[0.14em] text-accent">
                      <span className="h-[2px] w-5 rounded-full bg-accent" aria-hidden="true" />
                      Step {idx + 1} of {total}
                      {done.includes(current.id) && (
                        <span className="rounded-full bg-success/15 px-2 py-0.5 text-[0.62rem] tracking-normal text-success">Completed</span>
                      )}
                    </div>
                    <h2 className="text-xl font-extrabold tracking-tight text-ink sm:text-2xl">{current.title}</h2>
                    {current.description && <p className="mt-1.5 max-w-[68ch] text-sm leading-relaxed text-ink-muted">{current.description}</p>}
                  </div>
                  {editMode && (
                    <button className={btnGhost} onClick={() => openEdit(current)}>
                      Edit step
                    </button>
                  )}
                </div>

                <div className="mt-5">
                  <MediaEmbed kind={current.video_kind} mediaKind={current.media_kind} url={current.video_url} src={current.video_src}
                              title={current.title} filename={current.video_filename} byteSize={current.video_byte_size} />
                  {editMode && current.video_kind === "upload" && current.video_storage && (
                    <div className="mt-2 text-right text-[0.7rem] text-ink-faint">
                      {current.video_storage === "s3" ? "Stored in bucket" : <span className="text-pending">Stored in database (not in bucket)</span>}
                    </div>
                  )}
                </div>

                {current.content && (
                  <div className="mt-7">
                    <ScriptBlocks content={current.content} />
                  </div>
                )}

                <div className="mt-8 flex flex-wrap items-center justify-between gap-3 border-t border-hairline-soft pt-5">
                  <button className={btnGhost} disabled={idx <= 0} onClick={() => steps[idx - 1] && goTo(steps[idx - 1].id)}>
                    ← Back
                  </button>
                  <div className="flex items-center gap-2">
                    {done.includes(current.id) ? (
                      <>
                        <button className={btnGhost} onClick={unmark}>Mark incomplete</button>
                        {idx < total - 1 && <button className={btnCls} onClick={() => goTo(steps[idx + 1].id)}>Next step →</button>}
                      </>
                    ) : (
                      <button className={`${btnCls} px-4 py-2`} onClick={markDone}>
                        {idx < total - 1 ? "Mark complete & continue →" : "Mark complete"}
                      </button>
                    )}
                  </div>
                </div>
              </>
            )}
          </div>
        </div>
      )}

      {/* ── Admin: add / edit drawer ── */}
      <Drawer
        open={!!drawer}
        title={drawer?.step ? "Edit step" : "Add step"}
        sub={drawer?.step ? <>Changes go live for every agent as soon as you save.</> : <>It's added right after the step you're viewing.</>}
        icon="🎓"
        onClose={closeDrawer}
        footer={
          <>
            <button className={btnGhost} onClick={closeDrawer} disabled={!!busy}>Cancel</button>
            <button className="btn-glow inline-flex items-center gap-2 rounded-xl px-4 py-2 text-xs font-bold text-white disabled:opacity-50" disabled={!!busy || !valid} onClick={save}>
              {(busy === "save" || busy === "upload") && <Spinner />}
              {busy === "upload" ? "Uploading file…" : busy === "save" ? "Saving…" : drawer?.step ? "Save changes" : "Add step"}
            </button>
          </>
        }
      >
        {(busy === "save" || busy === "upload") && (
          <div className="drawer-busy absolute inset-0 z-10 flex flex-col items-center justify-center gap-3 rounded-none" aria-live="polite">
            <Spinner large />
            <span className="text-sm font-bold text-ink">{busy === "upload" ? "Uploading file…" : "Saving…"}</span>
            {busy === "upload" && f.file && (
              <span className="text-xs text-ink-muted">{f.file.name} · {fmtBytes(f.file.size)} — please keep this window open.</span>
            )}
          </div>
        )}
        <DrawerSection tone="accent" title="Basics" sub="What the agent sees in the step list."
                       icon={<svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 20h9" /><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z" /></svg>}>
          <Field label="Title" required>
            <input className={drawerCtl} value={f.title} placeholder="e.g. How to use HealthSherpa"
                   onChange={(e) => setF({ ...f, title: e.target.value })} />
          </Field>
          <Field label="Short description" hint="One line under the title — what the agent gets out of this step.">
            <input className={drawerCtl} value={f.description} placeholder="Optional"
                   onChange={(e) => setF({ ...f, description: e.target.value })} />
          </Field>
        </DrawerSection>

        <DrawerSection tone="accent2" title="Video or material" sub="Paste a video link, or upload a video, audio, image, PDF, Word, PowerPoint or Excel file. It sits at the top of the step."
                       icon={<svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="6" width="13" height="12" rx="2" /><path d="m16 10 5-3v10l-5-3Z" /></svg>}>
        <Field label="Source" plain>
          <div className="dseg flex rounded-xl p-1 text-xs font-semibold">
            {(["none", "link", "upload"] as VideoKind[]).map((m) => (
              <button key={m} type="button" onClick={() => setF({ ...f, videoMode: m })}
                      className={`flex flex-1 items-center justify-center gap-1.5 rounded-lg py-2 transition ${f.videoMode === m ? "dseg-on text-white" : "text-ink-muted hover:text-ink"}`}>
                {m === "none" && <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round"><circle cx="12" cy="12" r="9" /><path d="m5.5 5.5 13 13" /></svg>}
                {m === "link" && <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round"><path d="M10 13a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-1 1" /><path d="M14 11a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1-1" /></svg>}
                {m === "upload" && <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round"><path d="M12 3v12" /><path d="m7 8 5-5 5 5" /><path d="M5 17v2a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-2" /></svg>}
                {m === "none" ? "None yet" : m === "link" ? "Video link" : "Upload file"}
              </button>
            ))}
          </div>
          {f.videoMode === "link" && (
            <div className="mt-2">
              <input className={drawerCtl} value={f.videoUrl} placeholder="https://vimeo.com/… or https://youtu.be/…"
                     onChange={(e) => setF({ ...f, videoUrl: e.target.value })} />
              <span className="mt-1 block text-[0.7rem] text-ink-faint">
                {f.videoUrl.trim() ? (embedFor(f.videoUrl) ? `Plays inline (${embedFor(f.videoUrl)!.kind}).` : "Not Vimeo/YouTube — shown as an \"Open video\" link.") : "Vimeo and YouTube links play inside the page."}
              </span>
            </div>
          )}
          {f.videoMode === "upload" && (
            <div className="mt-2 space-y-2">
              {drawer?.step?.video_kind === "upload" && !f.file && (
                <div className="flex items-center justify-between gap-2 rounded-lg border border-hairline px-3 py-2 text-xs">
                  <span className="truncate text-ink">
                    <b>{drawer.step.video_filename}</b> <span className="text-ink-faint">· {mediaLabel(drawer.step.media_kind)} · {fmtBytes(drawer.step.video_byte_size)}</span>
                  </span>
                  <button type="button" className="shrink-0 font-semibold text-danger hover:underline" disabled={!!busy} onClick={removeVideo}>Remove</button>
                </div>
              )}
              <label className="ddrop flex cursor-pointer flex-col items-center justify-center gap-2 rounded-2xl px-3 py-5 text-xs text-ink-muted">
                <span className="ddrop-icon grid h-10 w-10 place-items-center rounded-2xl text-white" aria-hidden="true">
                  <svg className="h-4.5 w-4.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 3v12" /><path d="m7 8 5-5 5 5" /><path d="M5 17v2a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-2" /></svg>
                </span>
                {f.file
                  ? <span className="max-w-full truncate text-ink"><b>{f.file.name}</b> · {fmtBytes(f.file.size)}</span>
                  : <span className="font-semibold text-ink">{drawer?.step?.video_kind === "upload" ? "Choose a replacement file…" : "Choose a file"}</span>}
                {!f.file && <span className="text-[0.68rem] text-ink-faint">Video · audio · image · PDF · Word · PowerPoint · Excel</span>}
                <input type="file" accept={ACCEPT} className="hidden"
                       onChange={(e) => setF({ ...f, file: e.target.files?.[0] || null })} />
              </label>
              <span className="block text-[0.7rem] text-ink-faint">
                Videos, audio, images and PDFs open right on the page; Word, PowerPoint and Excel files are offered as a download. Large files can take a minute to upload.
              </span>
            </div>
          )}
        </Field>
        </DrawerSection>

        <DrawerSection tone="success" title="Script & notes" sub="Shown under the video. Leave empty for video-only steps."
                       icon={<svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"><path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9Z" /><path d="M14 3v6h6" /><path d="M8 13h8M8 17h6" /></svg>}>
          <textarea className={`${drawerCtl} font-mono text-[0.78rem] leading-relaxed`} rows={12} value={f.content}
                    placeholder={"# Phase 1 — Introduction\n[Agent]: \"Hello, my name is…\"\n> Customer Response\n1. First verification question"}
                    onChange={(e) => setF({ ...f, content: e.target.value })} spellCheck={false} aria-label="Script / notes under the video" />
          <details className="rounded-xl border border-hairline bg-white/60 px-3 py-2 text-[0.72rem] text-ink-muted">
            <summary className="cursor-pointer font-semibold text-ink">Formatting cheat-sheet</summary>
            <div className="mt-2 grid grid-cols-[auto_1fr] items-center gap-x-3 gap-y-1.5">
              {([
                ["# Title", "section heading"], ["[Agent]: text", "speaker quote card (any [Label]:)"],
                ["> text", "customer response / stage direction"], ["1. text", "numbered checklist"],
                ["- text", "bullet"], ["++ Title | body", "green callout"],
                [":: Title | body", "neutral callout"], ["!! text", "red \"must do\" callout"],
              ] as [string, string][]).map(([k, v]) => (
                <Fragment key={k}>
                  <code className="rounded-md bg-accent/10 px-1.5 py-0.5 font-mono text-[0.68rem] font-semibold text-accent">{k}</code>
                  <span>{v}</span>
                </Fragment>
              ))}
            </div>
          </details>
        </DrawerSection>
        {f.content.trim() && (
          <DrawerSection tone="pending" title="Preview" sub="Exactly how agents will see the script."
                         icon={<svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z" /><circle cx="12" cy="12" r="3" /></svg>}>
            <ScriptBlocks content={f.content} />
          </DrawerSection>
        )}
      </Drawer>
    </div>
  );
}

function Spinner({ large }: { large?: boolean }) {
  const s = large ? "h-9 w-9 border-[3px]" : "h-3.5 w-3.5 border-2";
  return (
    <span className={`inline-block ${s} animate-spin rounded-full border-current border-t-transparent ${large ? "text-accent" : ""}`}
          role="status" aria-label="Working" />
  );
}

function ProgressRing({ pct }: { pct: number }) {
  const r = 22, c = 2 * Math.PI * r;
  return (
    <span className="relative grid h-14 w-14 shrink-0 place-items-center">
      <svg className="absolute inset-0 -rotate-90" viewBox="0 0 56 56" aria-hidden="true">
        <circle cx="28" cy="28" r={r} fill="none" stroke="currentColor" strokeWidth="4" className="text-black/10" />
        <circle cx="28" cy="28" r={r} fill="none" stroke="currentColor" strokeWidth="4" strokeLinecap="round"
                className="text-accent transition-[stroke-dasharray] duration-500"
                strokeDasharray={`${(pct / 100) * c} ${c}`} />
      </svg>
      <span className="relative text-[0.72rem] font-extrabold text-ink tabular-nums">{pct}%</span>
    </span>
  );
}

function MiniBtn({ label, danger, disabled, onClick, children }: {
  label: string; danger?: boolean; disabled?: boolean; onClick: () => void; children: ReactNode;
}) {
  return (
    <button onClick={onClick} disabled={disabled} aria-label={label} title={label}
            className={`rounded-md p-1 disabled:opacity-30 ${danger ? "text-danger hover:bg-danger/10" : "text-ink-faint hover:bg-black/5 hover:text-ink"}`}>
      <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">{children}</svg>
    </button>
  );
}

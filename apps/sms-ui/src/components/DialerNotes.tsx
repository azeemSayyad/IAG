import { useState, type ReactNode } from "react";
import { Drawer } from "./Drawer";

/* "How calls work" — the Auto Dialer's rule book for admins, in a drawer.
   Every number in here comes from the live settings, so it never drifts
   from what the system actually does. Keep it in step with
   app/voice_dialer/inbound.py, service.py and manual.py. */

export type NotesSettings = {
  ring_seconds: number;
  max_attempts_24h: number;
  max_total_attempts: number;
  retry_minutes: number;
  reply_delay_seconds: number;
  window_start_hour: number;
  window_end_hour: number;
  max_hold_seconds: number;
  hours_enabled: boolean;
  hours_label: string;
  hold_offer_seconds: number;
  recording_notice: string;
};

const hour = (h: number) => (h === 12 ? "12 pm" : h < 12 ? `${h} am` : `${h - 12} pm`);
const minutes = (s: number) => (s % 60 === 0 ? `${s / 60} min` : `${Math.floor(s / 60)} min ${s % 60} s`);

function Section({ icon, title, children }: { icon: string; title: string; children: ReactNode }) {
  return (
    <section className="rounded-2xl border border-hairline bg-white p-4">
      <h3 className="mb-2 flex items-center gap-2 text-sm font-extrabold tracking-tight text-ink">
        <span aria-hidden>{icon}</span>
        {title}
      </h3>
      <div className="space-y-1.5 text-[13px] leading-relaxed text-ink-soft">{children}</div>
    </section>
  );
}

function Step({ n, children }: { n: number; children: ReactNode }) {
  return (
    <div className="flex gap-2.5">
      <span className="mt-0.5 grid h-5 w-5 flex-shrink-0 place-items-center rounded-full bg-accent/12 text-[11px] font-bold text-accent">{n}</span>
      <div>{children}</div>
    </div>
  );
}

export default function DialerNotes({ s }: { s: NotesSettings }) {
  const [open, setOpen] = useState(false);
  const hold = minutes(s.max_hold_seconds);
  return (
    <>
      <button
        onClick={() => setOpen(true)}
        title="How calls are routed, step by step"
        className="flex h-9 items-center gap-1.5 rounded-lg border border-hairline bg-white px-3 text-sm font-semibold text-ink-muted shadow-sm transition-all hover:-translate-y-px hover:text-ink hover:shadow-md"
      >
        <span aria-hidden>📘</span> How calls work
      </button>
      <Drawer open={open} title="How calls work" sub="The rules the dialer follows — numbers are your current settings." icon="📘" onClose={() => setOpen(false)}>
        <Section icon="📲" title="Client texts us → auto dialer calls">
          <Step n={1}>Any reply is called — "call me", "who are you?", "where are you?", "I'm fine", "did you eat?", anything — except a negative one. <b>STOP, "no", "not interested", "block", "do not disturb" / DND, abusive or vulgar words, and "wrong number"</b> go on Do-Not-Call — never called or texted.</Step>
          <Step n={2}><b>Opting back in:</b> if someone on Do-Not-Call later texts a <b>clear yes</b> ("Yes", "Ok", "Call me", "I'm interested", "Start", "Unstop", "I want a quote"…) they come off the list, are a normal lead again and are called right away. A vague text ("cool", "hi") is only saved. Their Inbox thread marks both moments.</Step>
          <Step n={3}>We wait <b>{s.reply_delay_seconds} s</b> after the reply, then call from the number they texted.</Step>
          <Step n={4}>Only between <b>{hour(s.window_start_hour)} and {hour(s.window_end_hour)} in the client's time zone</b>. Ring time {s.ring_seconds} s.</Step>
          <Step n={5}>One call per free agent on the line — never more calls than agents. Never two calls to the same number.</Step>
          <Step n={6}>A live answer goes to a free agent at once; the client's <b>own agent is picked first</b> if they're free. Voicemail is hung up and retried.</Step>
          <Step n={7}>Missed → retry after <b>{s.retry_minutes} min</b>, at most <b>{s.max_attempts_24h} tries a day</b> and <b>{s.max_total_attempts} in total</b>; then the lead goes to the regular pool.</Step>
        </Section>

        <Section icon="👤" title="Client owner">
          <p>Every client belongs to <b>the agent who most recently called or talked to them</b> — a manual call, an auto-dialed call, or a callback they took.</p>
          <p><b>Managers and admins never own a client.</b> If a manager called them, their callback goes to any free agent.</p>
          <p>An admin can change the owner under <b>Client owners</b> on this page. The next agent who calls the client becomes the owner again.</p>
        </Section>

        <Section icon="📞" title="Client calls us back">
          <Step n={1}>{s.hours_enabled ? <>Outside open hours (<b>{s.hours_label}</b>) they hear the closed message with those hours, then the call ends.</> : <>Open hours are <b>off</b> — calls are taken at any time. Turn them on in Dialing rules.</>}</Step>
          <Step n={2}><b>Owner free on the line</b> → straight to the owner, their card pops.</Step>
          <Step n={3}><b>Owner taking calls but busy</b> → on hold for the owner. If the owner isn't free within <b>{hold}</b>, the next free agent takes it.</Step>
          <Step n={4}><b>Owner offline, on break or not taking calls</b> (or no owner) → the first free agent.</Step>
          <Step n={5}><b>Nobody free</b> → on hold with music. The free agent takes the <b>longest-waiting caller first</b>.</Step>
          <Step n={6}>On hold they can <b>press 1 at any time</b> to be called back instead. It's offered every ~{s.hold_offer_seconds} s.</Step>
          <Step n={7}>After <b>{hold}</b> on hold with nobody free → "we'll call you back". Hanging up on hold counts the same.</Step>
          <Step n={8}><b>Nobody taking calls at all</b> → no hold; "we'll call you back" straight away.</Step>
        </Section>

        <Section icon="↩️" title="Our call-back after a missed call">
          <p>With the dialer <b>on</b>: they go on the dial list, ahead of newer ones (their place in line is when they called). The usual calling hours apply.</p>
          <p>With the dialer <b>off</b>: they go to the regular pool as a <b>HOT</b> lead for an agent to call by hand.</p>
          <p>Callers on Do-Not-Call, or whose lead an agent is working right now, are not put on hold.</p>
        </Section>

        <Section icon="⏸️" title="Who gets an agent first">
          <p><b>1. Callers on hold</b> — while anyone is holding, the dialer starts no new calls and agents on the line get no pool offers.</p>
          <p><b>2. Auto-dialer calls</b> to people who replied.</p>
          <p><b>3. The regular pool</b> (offer popup) — agents without "Ready for calls", or when the dialer has nothing to do.</p>
        </Section>

        <Section icon="☎️" title="Manual dialing (Dial button)">
          <p>Anyone can call any 10-digit US number. <b>No Do-Not-Call, calling-hours or try limits</b> — those are for the auto dialer only.</p>
          <p>While an agent is on a manual call they get <b>no pool offers and no dialer calls</b>; their line comes back by itself when the call ends.</p>
          <p>Every manual call is recorded. Agents see their own calls; managers see everyone's and play the recordings. The agent who dials a client becomes their owner.</p>
        </Section>

        <Section icon="🎙️" title="Recordings">
          <p>
            {s.recording_notice
              ? <>Every connected call starts with "<b>{s.recording_notice}</b>"</>
              : <>Call recording is <b>off</b>.</>}{" "}
            Recordings are copied to our storage right away (Vonage deletes theirs after 30 days) and can be attached to a sale.
          </p>
        </Section>
      </Drawer>
    </>
  );
}

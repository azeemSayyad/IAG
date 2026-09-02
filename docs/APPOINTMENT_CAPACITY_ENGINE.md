# Appointment Capacity Engine — Specification

**Status:** Proposed (not yet implemented)
**Owner:** Platform / AI
**Scope:** Turn a daily lead CSV into fully-booked agent calendars with minimum wasted leads.

The system must behave like an **intelligent appointment-capacity engine**, not a
lead-blaster. It continuously measures real appointment capacity and releases only
enough leads to fill it.

**Goals, in order:** (1) keep all agents fully booked, (2) minimize wasted leads,
(3) prioritize same-day appointments, (4) auto-scale outreach to booking performance.

---

## Resolved decisions (read first)

1. **Same-day first; future-day only as a threshold-gated fallback** (Component 10).
2. **"Fully booked" and "zero waste" can't both be 100%.** The wave loop
   (Component 5) keeps the overshoot to ~one wave — the accepted balance.
3. **Reply-lag is real.** Front-load outreach and stop new sends mid-afternoon
   (`OUTREACH_CUTOFF_HOUR`, Component 2).
4. **Capacity is the ceiling, not CSV size.** 40 agents × ~16 slots ≈ ~640
   bookable/day. A 100k CSV books up to the ceiling and **preserves the rest** —
   that preservation *is* the waste-minimization. Large CSVs = a lead reservoir.

---

# The 12 Components

## 1. Daily CSV Upload

**Purpose:** Each day's CSV is that day's lead pool, and the upload *starts* the
day's campaign — but it must NOT blast everyone.

**Step by step:**
1. Admin uploads the daily CSV (existing flow).
2. `bulk_import_leads_from_csv` (`app/ingestion/services/csv_import.py`) inserts
   the leads **but no longer rpushes every lead** to `queue:outbound_sms` (today
   it does, at the `pipe.rpush("queue:outbound_sms", ...)` loop).
3. Imported leads land in a **held pool** with `lifecycle_stage = "pending_outreach"`.
4. The import emits one event that triggers the **Capacity Engine kickoff**
   (Component 2 → Component 5 Wave 1).
5. Dedup, per-row validation, and timezone resolution stay as-is.

**Result:** the CSV becomes a controlled reservoir, not an instant blast.

---

## 2. Capacity Engine

**Purpose:** The brain. A continuous feedback loop that scales outreach to real
appointment capacity.

**Step by step (runs on upload, then every `PACING_CYCLE_MINUTES`):**
1. Compute **remaining same-day slots** (per state — Component 3).
2. Compute **leads needed** to fill them using measured funnel rates:
   ```
   leads_needed = remaining_slots_today / (reply_rate × book_rate) × (1 + WAVE_BUFFER)
   ```
3. Compute **in-flight** = leads already messaged today, not yet resolved
   (no reply / not booked / not dead).
4. **Release** = `max(0, leads_needed − in_flight)` (handed to Component 5).
5. Apply the **reply-lag cutoff**: after `OUTREACH_CUTOFF_HOUR` (lead-local),
   stop releasing *new first-touch* leads so replies still have time to convert
   same-day. Bookings, waitlist, and refills continue until TCPA quiet hours.
6. Loop. As slots fill, `remaining_slots_today → 0`, so release → 0 and outreach
   **auto-stops** (Component 11).

**Funnel rates:** `reply_rate` and `book_rate|reply` are **measured** from a
rolling 14–30 day window; until enough history exists, use configured defaults.

---

## 3. State-Based Capacity

**Purpose:** A lead can only be booked with an agent **licensed in its state**, so
capacity and release are computed **per state**.

**Step by step:**
1. For each state `S`, find licensed agents via
   `distribution.booking_agents_for_state(db, tenant, S)`.
2. `remaining_slots_today(S) = Σ over those agents of (today's open slots)` via
   `booking/services/availability.get_available_slots_for_agent(agent, today)`.
3. Run the Capacity Engine (Component 2) **independently per state**.
4. Never release more leads in `S` than `S` can service (prevents generating
   interest you can't fulfill).
5. **Zero-licensed-state leads** → never messaged; held + admin alert.

---

## 4. Lead Scoring Buckets

**Purpose:** The best leads should consume the scarce appointment inventory.

**Step by step:**
1. Sort the held pool into buckets by quality:
   - **Hot** — high `lead_score` + high intent (conversion/booking probability).
   - **Warm** — mid score.
   - **Cold** — low score.
2. Release order: **Hot → Warm → Cold**.
3. Tiebreakers within a bucket:
   1. Higher intent indicators.
   2. Licensed-state match (Component 3).
   3. More recently uploaded.
4. **Aging boost:** older untouched leads get a gradual priority bump so the
   backlog never rots (relevant if unused leads carry over).

---

## 5. Wave Release System

**Purpose:** Hit "fully booked" without over-messaging — by releasing in waves and
watching results, instead of one blast.

**Step by step:**
1. **Wave 1** fires on CSV upload: release ~`leads_needed` of the top bucket
   (Component 4), per state.
2. **Top-up waves** every `PACING_CYCLE_MINUTES` (≈15 min): recompute the
   **gap to full** (Component 2) and release just enough more top leads to cover it.
3. Each wave carries a small `PACING_WAVE_BUFFER` (e.g. +10%) so conversion
   variance still fills the day.
4. Waves taper automatically as the calendar fills; stop at the cutoff
   (Component 2) and when full (Component 11).
5. Overshoot is bounded to ~one wave — overflow interested leads go to the
   Waitlist (Component 7), not the trash.

---

## 6. Same-Day First Booking

**Purpose:** Every appointment offered over SMS is for **today** (until the
fallback in Component 10 triggers).

**Step by step:**
1. Under `SAME_DAY_PACING_ENABLED`, constrain the SMS booking slot generator
   (`orchestrator._union_slots_for_agents` → `generate_ny_anchored_slots`) to a
   **today-only horizon** (today it spans "the next several business days").
2. Slots offered = today's open, future-time slots from licensed agents.
3. Lead-local time is shown to the lead; agent calendar view stays Eastern.
4. The agent's manual calendar (Appointments page) is unaffected by this flag.

---

## 7. Waitlist

**Purpose:** An interested lead with no open slot must never be discarded.

**Step by step:**
1. Lead replies "Yes" but `remaining_slots_today(S) == 0`.
2. Set `ai_status = "awaiting_slot"`; preserve conversation history + booking intent.
3. The lead enters the **waitlist** for its state.
4. Waitlisted leads are **always worked before any untouched lead** when capacity
   appears (Component 8) and are first in the next day's pool.

---

## 8. Cancellation Refill

**Purpose:** Every freed slot gets another chance to generate revenue.

**Step by step (reuse the existing 5-minute "emergency fill" beat):**
1. A same-day appointment is cancelled / marked no-show.
2. The slot **returns to inventory instantly** and capacity recomputes.
3. Offer it to the **highest-priority waitlisted lead** in that state (Component 7).
4. If no waitlisted lead accepts, **release additional new leads** (Component 5).
5. Repeat until the slot is filled or the day closes.

---

## 9. Agent Load Balancer

**Purpose:** Distribute appointments evenly — no overloaded or idle agents.

**Step by step:**
1. When assigning a booking among the licensed-and-free agents for a slot, score
   each by capacity utilization (use `booking/services/assignment.py`:
   `calculate_agent_score`, `daily_capacity`).
2. Prefer the **least-utilized** eligible agent → even distribution + equal sales
   opportunity.
3. Keep within each agent's `daily_capacity`.

---

## 10. Future-Day Fallback (only after waitlist threshold)

**Purpose:** Rescue interested leads when today is genuinely full — without making
future-day the default.

**Step by step (gated by `FUTURE_DAY_FALLBACK_ENABLED`):**
1. Trigger **only** when BOTH:
   - today's same-day capacity for `S` is completely exhausted, AND
   - the waitlist depth in `S` **exceeds** remaining same-day inventory (the
     threshold).
2. Only then expand the booking offer (Component 6) to the next business day(s).
3. Future-day is a fallback, never the primary path.

---

## 11. Auto Stop Outreach When Full

**Purpose:** Stop burning leads the moment the day is filled.

**Step by step:**
1. When inventory reaches **target utilization** (per state):
   - Stop releasing new leads (Component 5 → 0).
   - Continue active conversations.
   - Continue booking already-interested leads.
   - Continue servicing the waitlist (Component 7) and refills (Component 8).
2. **Preserve** the unused held leads — carry to tomorrow's pool or leave in the
   reservoir. Nothing extra is messaged.

---

## 12. Real-Time Fill Percentage Dashboard

**Purpose:** Make "are today's appointments full / how many leads wasted" visible
and tunable.

**Step by step — surface, per state and overall:**
1. Today's slots / booked / **fill %**.
2. In-flight conversations.
3. Leads worked vs CSV size.
4. **Wasted-lead counter** (messaged but never serviceable).
5. **Shortfall to full** (more bookings needed).
6. Waitlist depth.
7. Live updates via the existing Socket.IO realtime channel.

---

## Configuration

| Flag / setting | Purpose | Default |
|---|---|---|
| `SAME_DAY_PACING_ENABLED` | Master switch for the whole engine | `false` |
| `OUTREACH_CUTOFF_HOUR` | Lead-local hour to stop new first-touch sends | 16 |
| `PACING_CYCLE_MINUTES` | Top-up wave interval | 15 |
| `PACING_WAVE_BUFFER` | Safety overshoot per wave (fraction) | 0.10 |
| `PACING_DEFAULT_REPLY_RATE` | Funnel default until measured | tune |
| `PACING_DEFAULT_BOOK_RATE` | Funnel default until measured | tune |
| `FUTURE_DAY_FALLBACK_ENABLED` | Allow Component 10 | `true` |
| `TARGET_UTILIZATION` | Fill % that counts as "full" (e.g. 1.0) | 1.0 |

All existing safety guards still apply underneath: TCPA quiet hours, per-lead/day
+ per-tenant/hour + global rate limits, and the sender-number pool.

---

## Build order (all behind `SAME_DAY_PACING_ENABLED`, default off)

1. **Component 1** — stop the blast on import; leads → `pending_outreach`.
2. **Components 2 + 3 + 5 (dry-run)** — capacity engine + per-state + Wave 1,
   shipped **log-only** first (compute and log what *would* be released; no sends).
3. **Component 4** — scoring buckets feed the release.
4. **Component 6** — same-day-only booking offer.
5. **Components 7 + 8** — waitlist + cancellation refill.
6. **Component 9** — agent load balancing on assignment.
7. **Component 10** — future-day fallback.
8. **Component 11** — auto-stop at target utilization.
9. **Component 12** — real-time dashboard; replace default funnel rates with
   measured ones.

### Rollout
1. Steps 1–2 in dry-run/log-only → validate daily math vs real capacity, zero
   behavior change.
2. Enable Component 6 + turn pacing on for one tenant → watch fill %.
3. Add Components 7–8, then 9–10.
4. Add Components 11–12.

---

## Business outcome

Agents stay fully booked; same-day utilization stays very high; lead waste is
minimized; cancellation impact is minimized; high-intent leads are prioritized;
outreach auto-scales to capacity; and daily CSVs of tens/hundreds of thousands of
leads are processed without overwhelming operations — the excess is preserved,
not burned.

---

# Upcoming Roadmap — "Call Me Now" (Instant Agent Connect)

**Idea:** While the AI texts a lead about booking, also offer them an option to
**talk to a licensed agent right now**. If they opt in, the system finds an
available licensed agent, sends that agent a **real-time accept/decline
notification**, and connects the call live. Hot leads convert at peak intent
instead of waiting for a future slot, and idle agents get filled between booked
appointments.

> Depends on the voice-calling stack (Sinch WebRTC softphone + call APIs). This
> is a roadmap item, gated on that being live.

### How it fits the capacity engine
It is a **parallel live lane** next to scheduled appointments, governed by the
same rules: state-licensing, agent presence/availability, and load balancing. A
live call consumes an agent's time, so it dynamically reduces that agent's
same-day booked-slot capacity. The hottest leads (top `lead_score`) are routed to
call-now when agents are free; everyone else is booked normally.

### Step by step
1. **Offer in the SMS** — append an opt-in to the booking message, e.g. *"Or
   reply NOW to talk to an agent right away."* Shown only in business hours and
   only when at least one licensed agent is actually available (never promise a
   call you cannot deliver).
2. **Detect intent** — the conversation engine recognizes "NOW" / "call me" /
   "talk now" (existing LLM intent detection).
3. **Find an agent** — agents licensed for the lead's state
   (`booking_agents_for_state`), filtered to those **online + available now**
   (presence system; the 30-second agent-presence beat already exists), picked by
   least-utilization (load balancer).
4. **Real-time notify** — push an **Accept / Decline** notification to the chosen
   agent over Socket.IO with a short countdown (e.g. 30–60s): *"📞 [Lead, State]
   wants a call NOW."* On decline/timeout, cascade to the next available agent.
5. **Place the call** — on Accept, initiate the call via the voice stack
   (Sinch / WebRTC), with recording disclosure + consent; text the lead
   *"Connecting you now — your phone will ring."*
6. **Reserve capacity** — mark the agent busy so the engine does not also assign
   them a booked slot at that moment; log the call-now event.
7. **Outcome** — booked/sold → disposition; no-answer → fall back to booking a
   slot; update a **separate call-now conversion metric**.

### Guardrails
- Business hours + TCPA quiet hours only.
- Only offered when a licensed agent is genuinely available right now.
- Max one concurrent call-now per agent; per-lead frequency cap; opt-out honored.
- Graceful fallback to normal same-day booking when no agent can take it.

### Capacity-engine synergy
Idle agents (no booked appointment at that moment) are the prime targets for
call-now routing — so this **raises utilization toward 100%** by filling the gaps
between scheduled appointments with live conversations, while the pacing engine
keeps the scheduled calendar full.

### Metrics
call-now offered / accepted / connected / converted, agent response time,
abandonment rate — tracked separately from SMS-booking conversion.

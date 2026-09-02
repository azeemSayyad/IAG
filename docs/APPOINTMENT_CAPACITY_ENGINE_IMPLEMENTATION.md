# Appointment Capacity Engine — Implementation Plan

**Companion to:** `docs/APPOINTMENT_CAPACITY_ENGINE.md` (the spec / 12 components).
**Goal of this doc:** a build-ready, phase-by-phase engineering plan — exact files,
function signatures, DB migrations, config, Celery tasks, tests, and acceptance
criteria — so the engine can be implemented end to end without ambiguity.

## Guiding principles (non-negotiable)

1. **Everything behind `SAME_DAY_PACING_ENABLED` (default `false`).** With the flag
   off, behavior is byte-for-byte today's behavior. The live SMS→booking pipeline
   must never regress.
2. **Dry-run first.** The release engine ships in **log-only** mode before it can
   enqueue a single real message.
3. **Per-state always.** Every capacity/release calculation is keyed by state.
4. **Graceful fallback.** Missing ML model → rule-based scores; missing history →
   configured default rates. The engine always runs.
5. **Reuse, don't rebuild.** Lean on existing primitives (`booking_agents_for_state`,
   `get_available_slots_for_agent`, `assignment.calculate_agent_score`,
   `queue:outbound_sms`, the 5-min emergency-fill beat, Socket.IO `emit_to_tenant`).
6. **Tested at every phase** (backend unit + integration + a dry-run validation;
   browser tests for the dashboard). Local-only; no real SMS (blank Engage creds).

---

## A. Data model & migrations

**Alembic migration `008_capacity_engine.py`** (new head after 007):

`leads` table — add columns:
| Column | Type | Purpose |
|---|---|---|
| `released_at` | timestamptz null | when the pacer released the lead to outreach |
| `wave_id` | varchar(64) null | which release wave (audit/metrics) |
| `priority_score` | float default 0 | computed ranking score (scoring service) |
| `pacing_status` | varchar(30) null | `held` / `released` / `awaiting_slot` / `booked` / `parked` |

Reuse existing fields: `lifecycle_stage` (set `"pending_outreach"`), `ai_status`
(`"awaiting_slot"`), `lead_score`, `conversion_probability`, `booking_probability`,
`state`, `custom_fields`, `tags`.

**No new tables required for v1** — per-state runtime metrics live in Redis
(below). (Optional later: a `pacing_daily_metrics` table for historical charts.)

**Redis keys** (namespaced per tenant + day, e.g. `pace:{tenant}:{YYYYMMDD}:{state}:*`):
- `:slots_total`, `:slots_open`, `:booked`, `:in_flight`, `:released`, `:waitlist`
- `:fill_pct`, `:wasted`, `:shortfall`
- existing `queue:outbound_sms` is the actuator.

---

## B. Configuration (add to `app/core/config.py` Settings)

```
SAME_DAY_PACING_ENABLED: bool = False        # master switch
PACING_DRY_RUN: bool = True                  # log-only; no real enqueue
PACING_CYCLE_MINUTES: int = 15
OUTREACH_CUTOFF_HOUR: int = 16               # lead-local
PACING_WAVE_BUFFER: float = 0.10
PACING_SHOW_FLOOR: float = 0.5               # cap over-booking
TARGET_UTILIZATION: float = 1.0
FUTURE_DAY_FALLBACK_ENABLED: bool = True
PACING_DEFAULT_REPLY_RATE: float = 0.20
PACING_DEFAULT_BOOK_RATE: float = 0.50
PACING_DEFAULT_SHOW_RATE: float = 0.80
PACING_FUNNEL_WINDOW_DAYS: int = 21
```

All read via pydantic settings (env-overridable). Existing guards stay: TCPA,
rate limiter, sender pool.

---

## C. New module layout

```
app/pacing/
  __init__.py
  capacity.py     # Component 3 — per-state open same-day slots
  funnel.py       # Component 2 — EMA reply/book/show rates (+ defaults)
  scoring.py      # Component 4 — rank/bucket leads (rule-based v1, ML v2)
  release.py      # Components 2+5 — controller: compute + (dry-run) enqueue
  waitlist.py     # Components 7+8 — park + refill
  metrics.py      # Component 12 — write Redis counters + emit Socket.IO
  events.py       # extract contacted/replied/booked/shown for rates+training
app/ml/                       # Phase 11 (optional upgrade)
  registry.py     # load versioned model artifacts
  lead_scoring.py # conversion propensity
  propensity.py   # P(reply), P(book|reply), P(show)
workers/app/tasks/pacing.py   # Celery beat tasks: tick + refill hook
```

---

## D. Phase-by-phase build

Each phase lists: **files**, **logic**, **tests**, **acceptance**.

### Phase 0 — Scaffolding & safety
- **Files:** migration `008`; config flags (§B); empty `app/pacing/` package;
  `app/pacing/events.py` with the funnel/training query.
- **Logic:** `events.contacted_replied_booked_shown(db, tenant, state, since)` →
  counts from `messages` (sender direction), `appointments` (created/start_time),
  `appointment_dispositions` (won/shown/no_show).
- **Tests:** migration up/down; `events` returns sane counts on seed data.
- **Acceptance:** flag off → zero behavior change; migration reversible.

### Phase 1 — Component 1: stop the blast
- **Files:** `app/ingestion/services/csv_import.py` (the `pipe.rpush("queue:outbound_sms", ...)` loop ~line 272); small-file path in `events.on_lead_created`.
- **Logic:** if `SAME_DAY_PACING_ENABLED`: set inserted leads
  `lifecycle_stage="pending_outreach"`, `pacing_status="held"`, and **skip** the
  rpush. Emit one `pacing:import_complete` signal (Redis pub or direct call) with
  `(tenant_id, count, states)`. Else: current behavior unchanged.
- **Tests:** import 1k rows flag-on → 0 jobs in `queue:outbound_sms`, all leads
  `held`; flag-off → identical to today (jobs enqueued).
- **Acceptance:** no leads auto-messaged when flag on.

### Phase 2 — Components 3 + 2(rates): capacity & funnel services
- **Files:** `app/pacing/capacity.py`, `app/pacing/funnel.py`.
- **Signatures:**
  ```
  capacity.slots_open_today(db, tenant_id, state) -> int
  capacity.slots_by_state(db, tenant_id) -> dict[state,int]
  funnel.rates(db, tenant_id, state) -> {reply, book, show}   # EMA, default-backed
  ```
- **Logic:** `slots_open_today` = Σ over `booking_agents_for_state(...)` of
  `len(get_available_slots_for_agent(agent, today))` (future-time, minus booked,
  capped by `daily_capacity`). `funnel.rates` = EMA over `PACING_FUNNEL_WINDOW_DAYS`
  from `events`, falling back to `PACING_DEFAULT_*`.
- **Tests:** seed agents+licenses+appointments → assert per-state slot counts;
  rates within [0,1] and equal defaults when no history.
- **Acceptance:** numbers match a hand computation on seed data.

### Phase 3 — Component 4: lead scoring & buckets
- **Files:** `app/pacing/scoring.py`.
- **Signatures:**
  ```
  scoring.score_leads(db, lead_ids) -> None            # writes priority_score
  scoring.ranked_held(db, tenant_id, state, limit) -> list[Lead]   # Hot->Warm->Cold
  ```
- **Logic v1 (rule-based):** `priority_score` from `lead_score`,
  `conversion_probability`/`booking_probability`, completeness of `custom_fields`,
  licensed-state match, upload recency, + aging boost for old held leads.
  Buckets: Hot ≥70, Warm 40–69, Cold <40. (v2 replaces with ML — Phase 11.)
- **Tests:** ordering is Hot→Warm→Cold with correct tiebreakers; aging boost moves
  an old lead up over time.
- **Acceptance:** `ranked_held` returns best-first deterministically.

### Phase 4 — Components 2 + 5: release engine + controller (DRY-RUN)
- **Files:** `app/pacing/release.py`, `workers/app/tasks/pacing.py`, beat entry.
- **Signatures:**
  ```
  release.compute(db, tenant_id, state) -> ReleasePlan   # leads_needed, in_flight, n
  release.run_cycle(db, tenant_id) -> CycleReport        # all states
  tasks.pacing_tick()                                    # Celery beat
  ```
- **Logic:** implement the §4 math from the spec:
  `target_bookings = slots_open / max(show,FLOOR)`;
  `leads_needed = ceil(target_bookings / (reply*book) * (1+BUFFER))`;
  `release = max(0, leads_needed - in_flight)`; pick `scoring.ranked_held(...)`.
  **If `PACING_DRY_RUN`: only log the plan** (who/how many would be released) +
  write metrics; **do not** enqueue. Else rpush + mark `released`/`released_at`/`wave_id`.
  Respect `OUTREACH_CUTOFF_HOUR` (no new first-touch after) and `TARGET_UTILIZATION`
  (release=0 when full → Component 11 falls out for free).
- **Beat:** add `pacing_tick` every `PACING_CYCLE_MINUTES` to `celery_app.py` (both
  `workers/app` and `workers/workers` copies); kickoff on `import_complete`.
- **Tests:** with fixed rates+slots, `compute` returns the hand-computed `release`;
  dry-run never touches the queue; cutoff hour suppresses new release; full state
  → 0.
- **Acceptance:** a 10k-lead dry-run logs a credible per-state daily plan, queue
  untouched.

### Phase 5 — Component 6: same-day-only booking offer
- **Files:** `app/ai/services/orchestrator.py` (`_union_slots_for_agents` →
  `generate_ny_anchored_slots`), `app/core/timezones.py` if needed.
- **Logic:** when `SAME_DAY_PACING_ENABLED`, pass a **today-only horizon** so the
  SMS offer lists only today's open slots; else keep multi-day. Future-day allowed
  only via Phase 8.
- **Tests:** booking flow with flag on offers only today's times; flag off
  unchanged (regression: existing booking tests still pass).
- **Acceptance:** AI booking offers same-day slots only (flag on).

### Phase 6 — Components 7 + 8: waitlist + cancellation refill
- **Files:** `app/pacing/waitlist.py`; hook in orchestrator booking path; extend the
  5-min emergency-fill beat.
- **Logic:** on "Yes" with `slots_open_today(S)==0` → `ai_status="awaiting_slot"`,
  `pacing_status="awaiting_slot"`, keep conversation. Refill: on cancel/no-show the
  freed slot → offer to highest-priority `awaiting_slot` lead in S (re-engage via
  orchestrator), else trigger `release.run_cycle` for S.
- **Tests:** simulate full state → interested lead waitlisted (not dropped); cancel
  an appointment → waitlisted lead is re-offered first.
- **Acceptance:** no interested lead is ever discarded; freed slots refill.

### Phase 7 — Component 9: agent load balancer
- **Files:** booking assignment path (`booking/services/assignment.py` already has
  `calculate_agent_score`); ensure the same-day booking selects the least-utilized
  eligible+licensed+free agent.
- **Tests:** 5 bookings across 5 free agents → even spread, none over `daily_capacity`.
- **Acceptance:** appointment distribution variance across agents is minimal.

### Phase 8 — Component 10: future-day fallback (gated)
- **Files:** orchestrator slot horizon; `release.py`/`capacity.py` threshold check.
- **Logic:** only when `slots_open_today(S)==0` AND `waitlist_depth(S) >
  remaining_inventory(S)` AND `FUTURE_DAY_FALLBACK_ENABLED` → expand offer to next
  business day(s).
- **Tests:** fallback stays off until both conditions true; then offers tomorrow.
- **Acceptance:** future-day never the default; only the rescue path.

### Phase 9 — Component 11: auto-stop (verification phase)
- Mostly emergent from Phase 4 (`release=0` at `TARGET_UTILIZATION`). Add an explicit
  guard + metric `outreach_stopped=true`.
- **Tests:** at 100% fill, no new releases; conversations/waitlist/refill continue.

### Phase 10 — Component 12: real-time dashboard
- **Files:** `app/pacing/metrics.py`; a read endpoint `GET /admin/pacing/metrics`;
  frontend panel (new card on dashboard.html or a dedicated `pacing.html`).
- **Logic:** metrics writes Redis counters each cycle and `emit_to_tenant(tenant,
  "pacing_updated", payload)`. Endpoint returns per-state + overall: slots/booked/
  fill %, in-flight, worked-vs-CSV, wasted, shortfall, waitlist depth.
- **Tests:** endpoint returns live numbers; browser test renders + updates on the
  realtime event; no console errors.
- **Acceptance:** operator sees today's fill % per state, live.

### Phase 11 — ML models (upgrade; optional but specified)
- **Files:** `app/ml/registry.py`, `app/ml/lead_scoring.py`, `app/ml/propensity.py`;
  offline training job (separate, e.g. `scripts/train_pacing_models.py`).
- **Logic:** train GBT models from `events` history → conversion, P(reply),
  P(book|reply), P(show); serve behind `scoring.py`/`funnel.py` with rule-based/EMA
  fallback. Per-lead greedy expected-value selection (spec §4) replaces aggregate
  rates. Nightly retrain + shadow-eval before promotion.
- **Tests:** model service returns calibrated probabilities; fallback path on
  missing artifact; selection beats aggregate on a backtest.
- **Acceptance:** scoring/funnel use measured models; engine degrades gracefully.

---

## E. Testing strategy

- **Unit:** capacity math, funnel EMA, scoring order, release computation, cutoff,
  auto-stop, waitlist/refill, fallback gate.
- **Integration (local, blank SMS creds):** import → dry-run plan → enable →
  controlled enqueue → orchestrator books same-day → metrics update. Assert queue
  volume tracks capacity, not CSV size.
- **Dry-run validation:** run a 10k synthetic CSV; confirm logged daily plan ≈
  capacity; zero real sends.
- **Browser:** dashboard renders per-state fill %, updates on realtime event.
- **Regression:** the existing SMS→booking pipeline tests pass with flag OFF
  (no regression) and with flag ON (same-day variant).
- **Load:** 100k-row import completes; pacer cycles stay within send budget.

## F. Rollout & kill-switch

1. Ship Phases 0–4 with `PACING_DRY_RUN=true` → validate math vs real capacity,
   **zero** behavior change.
2. Flip `PACING_DRY_RUN=false` + `SAME_DAY_PACING_ENABLED=true` for **one tenant**;
   watch fill % and wasted-lead counter for a few days.
3. Add Phases 5–8, then 9–10.
4. Add Phase 11 (ML) last.
- **Kill-switch:** `SAME_DAY_PACING_ENABLED=false` instantly restores the current
  blast-and-book pipeline (import re-enqueues normally).

## G. Definition of done (acceptance for the whole engine)

- Uploading a CSV does **not** mass-message; outreach volume tracks **per-state
  same-day capacity**, not row count.
- Agents reach near-full same-day utilization; wasted-lead counter stays low.
- Interested leads with no slot are waitlisted and later booked; freed slots refill.
- Appointments are distributed evenly across agents.
- Future-day only triggers past the waitlist threshold.
- Operators see live per-state fill % and shortfall.
- Flag OFF = exactly today's behavior (verified by regression).

## H. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Pacer under-fills (empty slots) | Wave buffer + top-up loop + over-booking by show-rate; alert on shortfall |
| Pacer over-fills (overshoot/waste) | Buffer capped to one wave; overflow → waitlist, not trash |
| Reply lag wastes late sends | `OUTREACH_CUTOFF_HOUR` from latency data |
| Bad/short CSV | Shortfall alert; carry held leads forward |
| Model/ratedata missing | Rule-based + default-rate fallbacks |
| Regression risk | Flag-gated + dry-run + full regression suite, kill-switch |
| Multi-tenant fairness | All keys + budgets namespaced per tenant |

## I. Ordered task checklist

1. [ ] Migration 008 + config flags (Phase 0)
2. [ ] `events.py` funnel/training extraction (Phase 0)
3. [ ] Stop-the-blast in csv_import + on_lead_created (Phase 1)
4. [ ] `capacity.py` + `funnel.py` (Phase 2)
5. [ ] `scoring.py` rule-based buckets (Phase 3)
6. [ ] `release.py` + `pacing_tick` beat, dry-run (Phase 4)
7. [ ] Dashboard metrics + endpoint + panel (Phase 10, can parallel Phase 4 for dry-run visibility)
8. [ ] Same-day-only booking horizon (Phase 5)
9. [ ] Waitlist + refill (Phase 6)
10. [ ] Agent load balancing (Phase 7)
11. [ ] Future-day fallback gate (Phase 8)
12. [ ] Auto-stop verification (Phase 9)
13. [ ] ML model upgrade + training job (Phase 11)
14. [ ] Full regression (flag on/off) + load test, then staged rollout (Section F)

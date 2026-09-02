"""Appointment Capacity Engine — same-day lead pacing.

This package turns a daily lead CSV into fully-booked agent calendars with minimum
wasted leads. It is entirely inert unless ``settings.SAME_DAY_PACING_ENABLED`` is
true; with the flag off, the live import → SMS → booking pipeline is unchanged.

Modules:
  events.py    — funnel/training event extraction (contacted/replied/booked/shown)
  capacity.py  — per-state open same-day appointment slots
  funnel.py    — measured reply/book/show rates (EMA) with configured defaults
  scoring.py   — rank/bucket held leads (rule-based; ML upgrade in app/ml)
  release.py   — the controller: compute + (dry-run) release waves
  waitlist.py  — park interested-but-unslotted leads; refill cancellations
  metrics.py   — per-state Redis counters + realtime dashboard payload

See docs/APPOINTMENT_CAPACITY_ENGINE.md (spec) and
docs/APPOINTMENT_CAPACITY_ENGINE_IMPLEMENTATION.md (build plan).
"""

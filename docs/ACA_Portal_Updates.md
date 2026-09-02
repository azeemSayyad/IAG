# ACA Portal Updates — change list & plan

Source: `docs/ACA_Portal_Updates.pdf` (agent-portal review). This is the
searchable companion to that PDF. **No code in this doc.**

Legend: **[BUG]** broken · **[CHANGE]** behavior/UI change · **[Q]** open question ·
**[CONFIRM]** pending user confirmation · **[DESIGN]** needs a spec decision.

> Guardrail: do **not** modify the working upload-CSV → appointment-booking
> pipeline. Agent-slot items (10–15) are *adjacent* — verify the call graph and
> reuse the existing correct engine; do not edit shared booking/SMS code.

## 1) Agent calendar / appointments tab
1. [CHANGE] Working hours 9–6 → **10–7**
2. [CHANGE] Default schedule view = **Day view**
3. [BUG] Week view doesn't show all appointments for the day
4. [BUG] Raw `&middot;` entities + poor English ("2 appointments · today", "Upcoming · this week")
5. [CHANGE] Remove "Today" tab → header reads "June 4, 2026 (Today)"
6. [CHANGE] Remove "Minimize" control
7. [BUG] Duplicate `< >` arrows — remove the pair in the tan/beige day box
8. [CHANGE] Remove beige day-box; clicking an appointment opens detail with agent actions: Finish, Reschedule (must text client), Reminder (must actually send)
9. [CHANGE] Add "Add Appointment" button for manual agent-created appointments

## 2) Available-slots view (agent-side booking)
10. [CHANGE] Show current day + future-day toggle (never past); not "next 3 days"
11. [BUG] Hours wrong (shows 9:00–4:30) → 10AM–7PM
12. [BUG] Double-booking risk — booked times not excluded
13. [BUG] Assumes 30-min appointments (not the real length)
14. [BUG] "Book this slot" does nothing
15. [Q] Does it respect timezone, and which one?

## 3) Conversations / inbox / chat
16. [BUG] AI is not working
17. [BUG] Chat history is not working
18. [CHANGE] AI Suggestions panel is fake (hardcoded) → make real or remove
19. [BUG/CHANGE] Deal Extraction: buttons don't work; remove "Extraction confidence"; fix raw `&mdash;`
20. [CHANGE] Agent should see only conversations of leads they have appointments with; must NOT see full AI chat history (private/proprietary)
21. [BUG] Header buttons (call / clock / "…") don't work
22. [BUG] "Ask the Brain" features don't work

## 4) Notifications
23. [BUG] Badge shows 7 (page + sidebar) but tab shows 0 — counts mismatched

## 5) Agent dashboard ("Good morning, Alex")
24. [BUG] Dummy data on live dashboard → must show real data
25. [CHANGE] Greeting + "your day at a glance" metrics belong at the top (above Compliance bar)

## 6) Compliance page
26. [BUG] "Agent Appointments" page UI is off, no X/close button

## 7) Login / auth
27. [CHANGE] Remove "Account locked. Try again in 14 minutes" lockout
28. [Q] Does Forgot Password actually work (email sent)?
29. [Q] Does Remember me work?

## 8) Deal flow / ACA Marketplace — design & pending
30. [CONFIRM] Disposition popup should not exist; derive from whether agent logged a deal — "CONFIRM TOMORROW"
31. [DESIGN] ACA Marketplace "Lock the sale" flow — how it works end-to-end
32. [DESIGN] Dental/Vision/Care: log deal on both this portal and the dental/vision portal without the agent submitting twice

## Root-cause clusters
- **AI dead (16,18,19,22 + fake snapshot/suggestions):** no LLM in prod — `OLLAMA_BASE_URL` points at localhost. One infra decision fixes most.
- **Dummy/placeholder UI (18,24, entity bugs):** hardcoded mock content + un-rendered HTML entities; frontend-only cleanup.
- **Stale agent slot view (10–15):** agent calendar uses an older slot generator (9–4:30, 30-min, next-3-days) not aligned to the corrected ET 10–7 engine the SMS pipeline already uses.

## Open questions before coding
1. Real appointment duration?
2. Confirm "hide already-booked times from available slots."
3. Slot timezone shown to agent = ET?
4. AI strategy: stand up a real LLM, or remove/hide AI panels for now?
5. Disposition auto-creation — pending your confirmation.
6. Dental/Vision dual-submission spec (2nd portal / API / manual?).
7. Password-reset email provider, or hide "Forgot password" for now?
8. Confirm agent-visibility rule (only leads they have appointments with; hide AI history).

## Phased plan (frontend/agent-portal only; local; no push until approved)
- Phase 0: lock decisions (questions above)
- Phase 1: pure cosmetic (entities, wording, remove Today/Minimize/dup-arrows/beige box, dashboard layout)
- Phase 2: login/auth (remove lockout; verify/wire Remember-me + Forgot-password)
- Phase 3: notifications count fix
- Phase 4: dashboard real data
- Phase 5: compliance page UI + close
- Phase 6: agent calendar (verify-first): day default, 10–7, week shows all, appointment detail (Finish/Reschedule+text/Reminder+text), Add Appointment
- Phase 7: available slots (verify-first): current-day + future toggle, 10–7, exclude booked, real duration, make Book-this-slot work, timezone
- Phase 8: conversations access control + header buttons + Deal Extraction cleanup
- Phase 9: AI features (depends on Q4)
- Phase 10: deal/marketplace (depends on Q5–Q6 + "confirm tomorrow")

"""Company pay rules — the worked example from the pay-rates handoff, plus the
edges that decide real money (repricing, EAP, per-member carriers, exceptions,
rule edits never touching a closed week)."""
import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.expenses import pay_rules as pr

ET = ZoneInfo("America/New_York")
AGENT = uuid.uuid4()
MON = date(2026, 9, 28)                       # a Monday
NOW_IN_WEEK = datetime(2026, 10, 2, 22, 0, tzinfo=ET).astimezone(timezone.utc)   # Friday night
NOW_NEXT_WEEK = datetime(2026, 10, 6, 9, 0, tzinfo=ET).astimezone(timezone.utc)


def version(rules=None, created=datetime(2026, 9, 1, tzinfo=timezone.utc)):
    return SimpleNamespace(rules=pr.validate_rules(rules or pr.DEFAULT_RULES), starts_on=MON, created_at=created)


class Book:
    """Builds deals the way the Log Sale form does: one row per person."""

    def __init__(self):
        self.deals, self.household, self.n = [], {}, 0

    def app(self, day, members=1, carrier="Ambetter", eap=False, aca=True, dental=False,
            ancillary=False, vision=False, agent=AGENT):
        app_id = uuid.uuid4()
        self.household[app_id] = members
        for _ in range(members):
            self.n += 1
            self.deals.append(SimpleNamespace(
                id=uuid.uuid4(), agent_id=agent, application_id=app_id, carrier=carrier,
                deal_source="eap" if eap else "carrier",
                aca_count=1 if aca else 0, dental_count=1 if dental else 0,
                vision_count=1 if vision else 0, ancillary_count=1 if ancillary else 0,
                created_at=(datetime(MON.year, MON.month, MON.day, 9, tzinfo=ET)
                            + timedelta(days=day, seconds=self.n)).astimezone(timezone.utc)))
        return self

    def many(self, day, count, **kw):
        for _ in range(count):
            self.app(day, **kw)
        return self

    def price(self, now=NOW_IN_WEEK, versions=None, excs=None):
        lines, weeks = pr.price(self.deals, self.household, excs or {}, versions or [version()], now)
        return lines, weeks[(AGENT, MON)]


def test_handoff_example_week_reprices_the_whole_week():
    b = Book().many(0, 60)                                   # Mon-Wed: 60 applications
    _, w = b.price()
    assert (w.commissions, w.tier, w.aca_cents) == (60, 1, 60 * 2000)

    b.many(3, 17).app(3, members=3, carrier="Anthem")        # Thu: 17 + an Anthem app with 3 members
    _, w = b.price()
    assert (w.commissions, w.tier, w.aca_cents) == (80, 2, 80 * 2500)   # all 80 repriced

    b.many(4, 49).app(4, members=4, eap=True)                # Fri: 50 applications, one EAP with 4 members
    lines, w = b.price()
    assert (w.commissions, w.tier, w.aca_cents) == (130, 3, 390000)     # $3,900
    assert sum(l.cents for l in lines) == 390000             # the per-deal lines add up to the week


def test_eap_always_counts_once_even_for_a_per_member_carrier():
    _, w = Book().app(0, members=4, carrier="Anthem", eap=True).price()
    assert w.commissions == 1


def test_standard_application_counts_once_whatever_its_size():
    _, w = Book().app(0, members=5, carrier="Oscar").price()
    assert w.commissions == 1


def test_per_member_carrier_is_a_setting():
    rules = dict(pr.DEFAULT_RULES, per_member_carriers=["Anthem", "Oscar"])
    _, w = Book().app(0, members=5, carrier="Oscar").price(versions=[version(rules)])
    assert w.commissions == 5


def test_dental_by_household_ancillary_flat_vision_unpaid_and_none_move_the_tier():
    b = (Book().app(0, members=2, aca=False, dental=True)            # household of 2 -> $20
               .app(0, members=4, aca=False, dental=True)            # household of 4 -> $40
               .app(0, members=3, aca=False, ancillary=True)         # flat $20, once
               .app(0, aca=False, vision=True))                      # not offered yet -> $0
    lines, w = b.price()
    assert w.commissions == 0 and w.aca_cents == 0
    assert w.other_cents == 2000 + 4000 + 2000
    assert sum(l.cents for l in lines) == 8000


def test_tier_2_agent_selling_aca_plus_dental_to_a_family_of_4_earns_65():
    b = Book().many(0, 80).app(1, members=4, dental=True)            # 81 commissions -> Tier 2
    lines, w = b.price()
    family = [l for l in lines if l.created_at.astimezone(ET).date() == MON + timedelta(days=1)]
    assert w.tier == 2 and sum(l.cents for l in family) == 2500 + 4000


def test_exception_locks_the_tier_and_ends_on_its_own():
    exc = SimpleNamespace(id=uuid.uuid4(), tier=3, ends_on=MON + timedelta(days=6), revoked_at=None,
                          created_at=datetime(2026, 9, 29, tzinfo=timezone.utc))
    b = Book().many(0, 10)
    _, w = b.price(excs={AGENT: [exc]})
    assert (w.auto_tier, w.tier, w.aca_cents) == (1, 3, 10 * 3000)
    # revoked before the week closed -> back to automatic
    exc.revoked_at = datetime(2026, 10, 1, tzinfo=timezone.utc)
    _, w = b.price(excs={AGENT: [exc]})
    assert w.tier == 1


def test_editing_the_rules_never_alters_a_closed_week():
    b = Book().many(0, 10)
    old = version()
    new = version(dict(pr.DEFAULT_RULES, aca_tiers=[{"min": 0, "cents": 9900}]),
                  created=NOW_NEXT_WEEK - timedelta(hours=1))        # saved AFTER the week closed
    _, closed = b.price(now=NOW_NEXT_WEEK, versions=[old, new])
    assert closed.closed and closed.aca_cents == 10 * 2000
    # ...but an edit made while the week is still open does reprice it
    live = version(dict(pr.DEFAULT_RULES, aca_tiers=[{"min": 0, "cents": 9900}]),
                   created=NOW_IN_WEEK - timedelta(hours=1))
    _, open_week = b.price(now=NOW_IN_WEEK, versions=[old, live])
    assert open_week.aca_cents == 10 * 9900


def test_weekend_deals_count_and_the_count_resets_on_monday():
    b = Book().many(5, 3).many(6, 2)                                  # Sat + Sun
    b.app(7)                                                          # next Monday
    lines, weeks = pr.price(b.deals, b.household, {}, [version()], NOW_NEXT_WEEK)
    assert weeks[(AGENT, MON)].commissions == 5
    assert weeks[(AGENT, MON + timedelta(days=7))].commissions == 1


@pytest.mark.parametrize("bad", [
    {"aca_tiers": []},
    {"aca_tiers": [{"min": 0, "cents": 2000}, {"min": 0, "cents": 2500}]},   # must rise
    {"aca_tiers": [{"min": 0, "cents": -1}]},
    {"dental_large_min": 1},
])
def test_rules_are_validated(bad):
    with pytest.raises(ValueError):
        pr.validate_rules(dict(pr.DEFAULT_RULES, **bad))


def test_trashed_deals_are_filtered_from_every_select():
    """core/database hides deals in Trash from any ORM select unless it opts in —
    full-entity, column-only and aggregate queries alike."""
    from sqlalchemy import func, select
    from sqlalchemy.dialects import postgresql

    from app.core.database import INCLUDE_TRASHED, SessionLocal
    from app.models.compliance import Deal

    seen = []
    db = SessionLocal()

    @__import__("sqlalchemy").event.listens_for(db.bind, "before_cursor_execute")
    def grab(conn, cursor, statement, params, context, executemany):
        seen.append(statement)
        raise RuntimeError("stop before touching the database")

    def sql_of(q):
        seen.clear()
        with pytest.raises(Exception):
            q.all()
        return seen[-1] if seen else ""

    try:
        for q in (db.query(Deal), db.query(Deal.id, Deal.agent_id),
                  db.query(Deal.agent_id, func.count(Deal.id)).group_by(Deal.agent_id)):
            assert "trashed_at IS NULL" in sql_of(q)
        assert "trashed_at IS NULL" not in sql_of(db.query(Deal).execution_options(**{INCLUDE_TRASHED: True}))
    finally:
        __import__("sqlalchemy").event.remove(db.bind, "before_cursor_execute", grab)
        db.close()

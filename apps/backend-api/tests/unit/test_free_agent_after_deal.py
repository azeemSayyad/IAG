"""Unit tests for release.free_agent_after_deal — the Add Deal -> agent-available
capacity-engine hook. Deps (engine flag, DB, queue_service, drip) are mocked so the
orchestration is verified without a database or Redis."""
import pytest


class _FakeQuery:
    def __init__(self, result):
        self._r = result

    def filter(self, *a, **k):
        return self

    def first(self):
        return self._r


class _FakeDB:
    def __init__(self, agent):
        self._agent = agent
        self.closed = False

    def query(self, *a, **k):
        return _FakeQuery(self._agent)

    def close(self):
        self.closed = True


class _FakeAgent:
    def __init__(self, user_id):
        self.user_id = user_id


@pytest.mark.unit
def test_engine_off_is_noop(monkeypatch):
    from app.core import engine_flags
    from app.pacing import release
    monkeypatch.setattr(engine_flags, "engine_enabled", lambda name: False)
    assert release.free_agent_after_deal("t1", "a1") == {"skipped": "engine_off"}


@pytest.mark.unit
def test_marks_credited_agent_available_and_drips(monkeypatch):
    from app.core import engine_flags, database
    from app.sms_queue.services import queue_service, inbound_sync
    from app.pacing import release

    monkeypatch.setattr(engine_flags, "engine_enabled", lambda name: True)
    fake_db = _FakeDB(_FakeAgent("user-99"))
    monkeypatch.setattr(database, "get_db", lambda: iter([fake_db]))

    seen = {}
    def fake_join(db, tenant_id, user_id):
        seen["join"] = (tenant_id, user_id)
        return ({"status": "AVAILABLE"}, [{"event": "sms:lead_assigned", "lead_id": "L1"}])
    monkeypatch.setattr(queue_service, "join", fake_join)
    monkeypatch.setattr(inbound_sync, "_flush_events", lambda evts: seen.update(flushed=len(evts)))
    monkeypatch.setattr(release, "drip_cycle", lambda db, tenant_id: {"released": 7})

    out = release.free_agent_after_deal("tenant-1", "agent-1")
    assert seen["join"] == ("tenant-1", "user-99")   # agent_id mapped to its user_id
    assert out == {"agent_user_id": "user-99", "assigned": 1, "released": 7}
    assert seen["flushed"] == 1
    assert fake_db.closed is True                      # session always closed


@pytest.mark.unit
def test_unknown_agent_skips(monkeypatch):
    from app.core import engine_flags, database
    from app.pacing import release
    monkeypatch.setattr(engine_flags, "engine_enabled", lambda name: True)
    fake_db = _FakeDB(None)
    monkeypatch.setattr(database, "get_db", lambda: iter([fake_db]))
    assert release.free_agent_after_deal("t1", "a1") == {"skipped": "no_agent_user"}
    assert fake_db.closed is True


@pytest.mark.unit
def test_drip_failure_is_swallowed(monkeypatch):
    """A drip error must not blow up the hook — the agent is still freed/assigned."""
    from app.core import engine_flags, database
    from app.sms_queue.services import queue_service, inbound_sync
    from app.pacing import release

    monkeypatch.setattr(engine_flags, "engine_enabled", lambda name: True)
    fake_db = _FakeDB(_FakeAgent("user-1"))
    monkeypatch.setattr(database, "get_db", lambda: iter([fake_db]))
    monkeypatch.setattr(queue_service, "join", lambda db, t, u: ({"status": "AVAILABLE"}, []))
    monkeypatch.setattr(inbound_sync, "_flush_events", lambda evts: None)
    def boom(db, tenant_id):
        raise RuntimeError("drip exploded")
    monkeypatch.setattr(release, "drip_cycle", boom)

    out = release.free_agent_after_deal("t1", "a1")
    assert out == {"agent_user_id": "user-1", "assigned": 0, "released": None}
    assert fake_db.closed is True

from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from app.ai.services import reply_polling


def test_poll_provider_replies_processes_reply_through_ai(monkeypatch):
    tenant_id = uuid4()
    lead = SimpleNamespace(id=uuid4(), tenant_id=tenant_id)
    conversation = SimpleNamespace(id=uuid4())
    db = MagicMock()
    emitted = []
    confirmed = []

    monkeypatch.setattr(
        reply_polling.communication_service,
        "fetch_replies",
        lambda: {
            "success": True,
            "replies": [
                {
                    "reply_id": "reply_123",
                    "message_sid": "msg_123",
                    "from": "+15551230000",
                    "body": "Yes, book me",
                }
            ],
        },
    )
    monkeypatch.setattr(
        reply_polling.communication_service,
        "confirm_replies",
        lambda reply_ids: confirmed.extend(reply_ids) or {"success": True, "confirmed": len(reply_ids)},
    )
    monkeypatch.setattr(reply_polling.redis_service.client, "set", lambda *args, **kwargs: True)
    monkeypatch.setattr(reply_polling, "_find_lead_by_reply_phone", lambda db, phone: lead)
    monkeypatch.setattr(reply_polling, "_get_or_create_conversation", lambda db, lead: conversation)
    monkeypatch.setattr(
        reply_polling,
        "process_incoming_message",
        lambda **kwargs: {"success": True, "response": "Great i am available at\nWed, Jun 3 at 2:00 PM EDT"},
    )
    monkeypatch.setattr(reply_polling, "on_lead_replied", lambda db, lead, conversation: None)
    monkeypatch.setattr(reply_polling, "_emit_to_tenant_safe", lambda tenant, event, payload: emitted.append((tenant, event, payload)))

    result = reply_polling.poll_provider_replies_once(db)

    assert result == {"processed": 1, "confirmed": 1, "failed": 0, "skipped": 0, "available": 1}
    assert confirmed == ["reply_123"]
    db.commit.assert_called_once()
    assert emitted[0][0] == str(tenant_id)
    assert emitted[0][1] == "engage_cloud_inbound_processed"
    assert emitted[0][2]["reply_id"] == "reply_123"


def test_poll_provider_replies_skips_duplicate_reply(monkeypatch):
    db = MagicMock()
    confirmed = []

    monkeypatch.setattr(
        reply_polling.communication_service,
        "fetch_replies",
        lambda: {
            "success": True,
            "replies": [{"reply_id": "reply_seen", "from": "+15551230000", "body": "Yes"}],
        },
    )
    monkeypatch.setattr(
        reply_polling.communication_service,
        "confirm_replies",
        lambda reply_ids: confirmed.extend(reply_ids) or {"success": True, "confirmed": len(reply_ids)},
    )
    monkeypatch.setattr(reply_polling.redis_service.client, "set", lambda *args, **kwargs: False)

    result = reply_polling.poll_provider_replies_once(db)

    assert result == {"processed": 0, "confirmed": 0, "failed": 0, "skipped": 1, "available": 1}
    assert confirmed == []
    db.commit.assert_not_called()

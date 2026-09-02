from types import SimpleNamespace

from app.ai.services.prompts import get_booking_message, get_confirmation_message, get_outreach_message
from app.ai.services.orchestrator import _build_slots_message, _infer_intent, _match_slot_selection


def test_primary_outreach_template_is_exact():
    assert (
        get_outreach_message("Sarah")
        == "hey Sarah it's Michael. Your coverage might be flagged possible lapse. $0/mo before close. Reply Yes, takes 2 min."
    )


def test_booking_template_is_slots_only():
    message = get_booking_message([
        "Wed, Jun 3 at 9:00 AM EDT",
        "Wed, Jun 3 at 11:00 AM EDT",
        "Wed, Jun 3 at 1:00 PM EDT",
    ])

    assert message == (
        "Great i am available at\n"
        "Wed, Jun 3 at 9:00 AM EDT\n"
        "Wed, Jun 3 at 11:00 AM EDT\n"
        "Wed, Jun 3 at 1:00 PM EDT"
    )
    assert "1." not in message
    assert "Reply" not in message


def test_active_slot_message_is_slots_only():
    slots = [
        {"index": 1, "label": "Wed, Jun 3 at 9:00 AM EDT"},
        {"index": 2, "label": "Wed, Jun 3 at 11:00 AM EDT"},
        {"index": 3, "label": "Wed, Jun 3 at 1:00 PM EDT"},
    ]

    message = _build_slots_message(SimpleNamespace(first_name="Sarah"), slots)

    assert message == (
        "Great i am available at\n"
        "Wed, Jun 3 at 9:00 AM EDT\n"
        "Wed, Jun 3 at 11:00 AM EDT\n"
        "Wed, Jun 3 at 1:00 PM EDT"
    )
    assert "1." not in message
    assert "Reply" not in message


def test_slot_label_reply_selects_matching_slot():
    conversation = SimpleNamespace(
        ai_context={
            "booking_slots": [
                {"index": 1, "label": "Wed, Jun 3 at 9:00 AM EDT"},
                {"index": 2, "label": "Wed, Jun 3 at 11:00 AM EDT"},
                {"index": 3, "label": "Wed, Jun 3 at 1:00 PM EDT"},
            ]
        }
    )

    assert _match_slot_selection("Wed, Jun 3 at 11:00 AM EDT", conversation) == 2


def test_natural_slot_replies_select_matching_slot():
    conversation = SimpleNamespace(
        ai_context={
            "booking_slots": [
                {"index": 1, "label": "Wed, Jun 3 at 2:00 PM EDT", "start_time": "2026-06-03T18:00:00+00:00"},
                {"index": 2, "label": "Wed, Jun 3 at 4:00 PM EDT", "start_time": "2026-06-03T20:00:00+00:00"},
                {"index": 3, "label": "Fri, Jun 5 at 9:00 AM EDT", "start_time": "2026-06-05T13:00:00+00:00"},
            ]
        }
    )

    assert _match_slot_selection("I will go with first one", conversation) == 1
    assert _match_slot_selection("option 2", conversation) == 2
    assert _match_slot_selection("I will go with 2 pm", conversation) == 1
    assert _match_slot_selection("4 pm works", conversation) == 2
    assert _match_slot_selection("two pm is good", conversation) == 1
    assert _match_slot_selection("second one works", conversation) == 2
    assert _match_slot_selection("the Friday morning one", conversation) == 3
    assert _match_slot_selection("earliest please", conversation) == 1
    assert _match_slot_selection("last one", conversation) == 3
    assert _match_slot_selection("afternoon works", conversation) is None


def test_date_only_slot_reply_selects_unique_matching_date():
    conversation = SimpleNamespace(
        ai_context={
            "booking_slots": [
                {"index": 1, "label": "Wed, Jun 3 at 3:00 PM EDT", "start_time": "2026-06-03T19:00:00+00:00"},
                {"index": 2, "label": "Fri, Jun 5 at 9:00 AM EDT", "start_time": "2026-06-05T13:00:00+00:00"},
                {"index": 3, "label": "Fri, Jun 5 at 11:00 AM EDT", "start_time": "2026-06-05T15:00:00+00:00"},
            ]
        }
    )

    assert _match_slot_selection("June 3", conversation) == 1
    assert _match_slot_selection("Jun 3rd works", conversation) == 1
    assert _match_slot_selection("6/3", conversation) == 1
    assert _match_slot_selection("June 5", conversation) is None


def test_positive_customer_replies_trigger_booking():
    conversation = SimpleNamespace(status="active", ai_context={})

    for reply in [
        "Yes",
        "Yeah let's go",
        "sounds good",
        "I'm in",
        "go ahead",
        "send me the times",
        "what times do you have?",
        "book me",
        "set it up please",
        "sure that works for me",
    ]:
        assert _infer_intent(reply, None, conversation) in {"BOOK_NOW", "SKEPTICAL_BOOKING"}


def test_confirmation_template_is_exact():
    assert get_confirmation_message("Sarah", "Wed, Jun 3 at 9:00 AM EDT") == "Great i will reach out"

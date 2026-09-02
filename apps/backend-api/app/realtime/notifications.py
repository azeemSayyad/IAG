"""
Live Notifications Service (Step 10.3)

Events:
- New booking
- Cancellation
- Reassignment
- Reminders
- Lead replied
- Agent status change
"""

from datetime import datetime, timezone
from typing import Dict, List, Optional
from uuid import UUID

from app.realtime.websocket import (
    emit_to_tenant,
    emit_to_user,
    emit_to_agent,
    emit_to_role,
)
from app.realtime.pubsub import (
    publish_notification,
    publish_booking_event,
    publish_agent_event,
)


class NotificationType:
    """Notification type constants."""
    # Booking events
    NEW_BOOKING = "new_booking"
    BOOKING_CANCELLED = "booking_cancelled"
    BOOKING_RESCHEDULED = "booking_rescheduled"
    BOOKING_REASSIGNED = "booking_reassigned"
    BOOKING_REMINDER = "booking_reminder"

    # Lead events
    LEAD_CREATED = "lead_created"
    LEAD_UPDATED = "lead_updated"
    LEAD_REPLIED = "lead_replied"
    LEAD_QUALIFIED = "lead_qualified"
    LEAD_SCORED = "lead_scored"
    LEAD_STATUS_CHANGED = "lead_status_changed"

    # AI events
    AI_RESPONSE_GENERATED = "ai_response_generated"
    AI_INTENT_DETECTED = "ai_intent_detected"
    AI_OBJECTION_HANDLED = "ai_objection_handled"
    AI_OUTREACH_SENT = "ai_outreach_sent"

    # Agent events
    AGENT_STATUS_CHANGE = "agent_status_change"
    AGENT_UTILIZATION_ALERT = "agent_utilization_alert"

    # Conversation events
    CONVERSATION_STATE_CHANGED = "conversation_state_changed"
    CONVERSATION_STARTED = "conversation_started"
    CONVERSATION_ENDED = "conversation_ended"

    # System events
    SYSTEM_ALERT = "system_alert"
    WORKFLOW_COMPLETED = "workflow_completed"
    QUEUE_ALERT = "queue_alert"


async def notify_new_booking(tenant_id: str, appointment_data: Dict):
    """
    Notify about a new booking.

    Sends to:
    - All tenant admins
    - Assigned agent
    """
    notification = {
        "type": NotificationType.NEW_BOOKING,
        "title": "New Booking",
        "message": f"New appointment booked for {appointment_data.get('lead_name', 'Unknown')}",
        "data": appointment_data,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # Emit to tenant admins
    await emit_to_role(tenant_id, "tenant_admin", "notification", notification)

    # Emit to assigned agent
    agent_id = appointment_data.get("agent_id")
    if agent_id:
        await emit_to_agent(agent_id, "notification", notification)

    # Publish to Redis
    await publish_booking_event(tenant_id, "new", appointment_data)


async def notify_booking_cancelled(tenant_id: str, appointment_data: Dict):
    """
    Notify about a booking cancellation.

    Sends to:
    - All tenant admins
    - Assigned agent
    """
    notification = {
        "type": NotificationType.BOOKING_CANCELLED,
        "title": "Booking Cancelled",
        "message": f"Appointment cancelled for {appointment_data.get('lead_name', 'Unknown')}",
        "data": appointment_data,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    await emit_to_role(tenant_id, "tenant_admin", "notification", notification)

    agent_id = appointment_data.get("agent_id")
    if agent_id:
        await emit_to_agent(agent_id, "notification", notification)

    await publish_booking_event(tenant_id, "cancelled", appointment_data)


async def notify_booking_rescheduled(tenant_id: str, appointment_data: Dict):
    """
    Notify about a booking reschedule.

    Sends to:
    - All tenant admins
    - Assigned agent
    """
    notification = {
        "type": NotificationType.BOOKING_RESCHEDULED,
        "title": "Booking Rescheduled",
        "message": f"Appointment rescheduled for {appointment_data.get('lead_name', 'Unknown')}",
        "data": appointment_data,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    await emit_to_role(tenant_id, "tenant_admin", "notification", notification)

    agent_id = appointment_data.get("agent_id")
    if agent_id:
        await emit_to_agent(agent_id, "notification", notification)

    await publish_booking_event(tenant_id, "rescheduled", appointment_data)


async def notify_booking_reassigned(tenant_id: str, old_agent_id: str, new_agent_id: str, appointment_data: Dict):
    """
    Notify about a booking reassignment.

    Sends to:
    - All tenant admins
    - Old agent
    - New agent
    """
    notification = {
        "type": NotificationType.BOOKING_REASSIGNED,
        "title": "Booking Reassigned",
        "message": f"Appointment reassigned to new agent",
        "data": appointment_data,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    await emit_to_role(tenant_id, "tenant_admin", "notification", notification)
    await emit_to_agent(old_agent_id, "notification", notification)
    await emit_to_agent(new_agent_id, "notification", notification)

    await publish_booking_event(tenant_id, "reassigned", appointment_data)


async def notify_booking_reminder(tenant_id: str, agent_id: str, appointment_data: Dict):
    """
    Notify about an upcoming appointment reminder.

    Sends to:
    - Assigned agent
    """
    reminder_type = appointment_data.get("reminder_type", "unknown")
    notification = {
        "type": NotificationType.BOOKING_REMINDER,
        "title": f"Appointment Reminder ({reminder_type})",
        "message": f"Appointment in {reminder_type} with {appointment_data.get('lead_name', 'Unknown')}",
        "data": appointment_data,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    await emit_to_agent(agent_id, "notification", notification)


async def notify_lead_replied(tenant_id: str, lead_data: Dict):
    """
    Notify about a lead reply.

    Sends to:
    - All tenant admins
    """
    notification = {
        "type": NotificationType.LEAD_REPLIED,
        "title": "Lead Replied",
        "message": f"{lead_data.get('lead_name', 'Unknown')} replied to outreach",
        "data": lead_data,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    await emit_to_role(tenant_id, "tenant_admin", "notification", notification)
    await publish_notification(tenant_id, notification)


async def notify_lead_qualified(tenant_id: str, lead_data: Dict):
    """
    Notify about a qualified lead.

    Sends to:
    - All tenant admins
    """
    notification = {
        "type": NotificationType.LEAD_QUALIFIED,
        "title": "Lead Qualified",
        "message": f"{lead_data.get('lead_name', 'Unknown')} is qualified and ready for booking",
        "data": lead_data,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    await emit_to_role(tenant_id, "tenant_admin", "notification", notification)
    await publish_notification(tenant_id, notification)


async def notify_agent_status_change(tenant_id: str, agent_data: Dict):
    """
    Notify about an agent status change.

    Sends to:
    - All tenant admins
    """
    notification = {
        "type": NotificationType.AGENT_STATUS_CHANGE,
        "title": "Agent Status Changed",
        "message": f"Agent {agent_data.get('agent_name', 'Unknown')} is now {agent_data.get('status', 'unknown')}",
        "data": agent_data,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    await emit_to_role(tenant_id, "tenant_admin", "notification", notification)
    await publish_agent_event(tenant_id, agent_data.get("agent_id", ""), "status_change", agent_data)


async def notify_system_alert(tenant_id: str, alert_data: Dict):
    """
    Notify about a system alert.

    Sends to:
    - All users in tenant
    """
    notification = {
        "type": NotificationType.SYSTEM_ALERT,
        "title": "System Alert",
        "message": alert_data.get("message", "System alert"),
        "data": alert_data,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    await emit_to_tenant(tenant_id, "notification", notification)


async def notify_workflow_completed(tenant_id: str, workflow_data: Dict):
    """
    Notify about a completed workflow.

    Sends to:
    - All tenant admins
    """
    notification = {
        "type": NotificationType.WORKFLOW_COMPLETED,
        "title": "Workflow Completed",
        "message": f"Workflow {workflow_data.get('workflow_name', 'Unknown')} completed for {workflow_data.get('lead_name', 'Unknown')}",
        "data": workflow_data,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    await emit_to_role(tenant_id, "tenant_admin", "notification", notification)


# --- Lead Event Notifications ---

async def notify_lead_created(tenant_id: str, lead_data: Dict):
    """
    Notify about a new lead.

    Sends to:
    - All tenant admins
    - Managers
    """
    notification = {
        "type": NotificationType.LEAD_CREATED,
        "title": "New Lead",
        "message": f"New lead: {lead_data.get('first_name', '')} {lead_data.get('last_name', '')} from {lead_data.get('source', 'unknown')}",
        "data": lead_data,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    await emit_to_role(tenant_id, "tenant_admin", "notification", notification)
    await emit_to_role(tenant_id, "manager", "notification", notification)
    await publish_notification(tenant_id, notification)


async def notify_lead_updated(tenant_id: str, lead_data: Dict, changes: Dict):
    """
    Notify about a lead update.

    Sends to:
    - All tenant admins
    """
    notification = {
        "type": NotificationType.LEAD_UPDATED,
        "title": "Lead Updated",
        "message": f"Lead {lead_data.get('first_name', '')} {lead_data.get('last_name', '')} updated",
        "data": {**lead_data, "changes": changes},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    await emit_to_role(tenant_id, "tenant_admin", "notification", notification)


async def notify_lead_scored(tenant_id: str, lead_data: Dict):
    """
    Notify about a lead score change.

    Sends to:
    - All tenant admins
    """
    notification = {
        "type": NotificationType.LEAD_SCORED,
        "title": "Lead Score Updated",
        "message": f"Lead {lead_data.get('first_name', '')} scored {lead_data.get('lead_score', 0)} ({lead_data.get('tier', 'unknown')})",
        "data": lead_data,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    await emit_to_role(tenant_id, "tenant_admin", "notification", notification)


async def notify_lead_status_changed(tenant_id: str, lead_data: Dict, old_status: str, new_status: str):
    """
    Notify about a lead status change.

    Sends to:
    - All tenant admins
    - Managers
    """
    notification = {
        "type": NotificationType.LEAD_STATUS_CHANGED,
        "title": "Lead Status Changed",
        "message": f"Lead {lead_data.get('first_name', '')} moved from {old_status} to {new_status}",
        "data": {**lead_data, "old_status": old_status, "new_status": new_status},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    await emit_to_role(tenant_id, "tenant_admin", "notification", notification)
    await emit_to_role(tenant_id, "manager", "notification", notification)


# --- AI Event Notifications ---

async def notify_ai_response_generated(tenant_id: str, conversation_id: str, response_data: Dict):
    """
    Notify about an AI-generated response.

    Sends to:
    - All tenant admins (for monitoring)
    """
    notification = {
        "type": NotificationType.AI_RESPONSE_GENERATED,
        "title": "AI Response Generated",
        "message": f"AI generated response for conversation {conversation_id[:8]}...",
        "data": {
            "conversation_id": conversation_id,
            "response_length": response_data.get("length", 0),
            "was_validated": response_data.get("validated", False),
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    await emit_to_role(tenant_id, "tenant_admin", "notification", notification)


async def notify_ai_intent_detected(tenant_id: str, conversation_id: str, intent_data: Dict):
    """
    Notify about detected intent.

    Sends to:
    - All tenant admins
    """
    notification = {
        "type": NotificationType.AI_INTENT_DETECTED,
        "title": "Intent Detected",
        "message": f"Intent: {intent_data.get('intent', 'unknown')} (confidence: {intent_data.get('confidence', 0):.0%})",
        "data": {
            "conversation_id": conversation_id,
            "intent": intent_data.get("intent"),
            "confidence": intent_data.get("confidence"),
            "method": intent_data.get("method"),
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    await emit_to_role(tenant_id, "tenant_admin", "notification", notification)


async def notify_ai_outreach_sent(tenant_id: str, lead_id: str, outreach_data: Dict):
    """
    Notify about AI outreach sent.

    Sends to:
    - All tenant admins
    """
    notification = {
        "type": NotificationType.AI_OUTREACH_SENT,
        "title": "AI Outreach Sent",
        "message": f"AI sent outreach to lead {outreach_data.get('lead_name', 'Unknown')}",
        "data": {
            "lead_id": lead_id,
            "campaign_id": outreach_data.get("campaign_id"),
            "tone": outreach_data.get("tone"),
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    await emit_to_role(tenant_id, "tenant_admin", "notification", notification)


# --- Conversation Event Notifications ---

async def notify_conversation_state_changed(tenant_id: str, conversation_id: str, state_data: Dict):
    """
    Notify about conversation state change.

    Sends to:
    - All tenant admins
    """
    notification = {
        "type": NotificationType.CONVERSATION_STATE_CHANGED,
        "title": "Conversation State Changed",
        "message": f"Conversation moved from {state_data.get('previous_state')} to {state_data.get('new_state')}",
        "data": {
            "conversation_id": conversation_id,
            "previous_state": state_data.get("previous_state"),
            "new_state": state_data.get("new_state"),
            "event": state_data.get("event"),
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    await emit_to_role(tenant_id, "tenant_admin", "notification", notification)


# --- Agent Event Notifications ---

async def notify_agent_utilization_alert(tenant_id: str, agent_data: Dict):
    """
    Notify about agent utilization issues.

    Sends to:
    - All tenant admins
    - Managers
    """
    notification = {
        "type": NotificationType.AGENT_UTILIZATION_ALERT,
        "title": "Agent Utilization Alert",
        "message": f"Agent {agent_data.get('agent_name', 'Unknown')} utilization: {agent_data.get('utilization', 0):.0%}",
        "data": agent_data,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    await emit_to_role(tenant_id, "tenant_admin", "notification", notification)
    await emit_to_role(tenant_id, "manager", "notification", notification)


# --- System Event Notifications ---

async def notify_queue_alert(tenant_id: str, queue_data: Dict):
    """
    Notify about queue issues.

    Sends to:
    - All tenant admins
    """
    notification = {
        "type": NotificationType.QUEUE_ALERT,
        "title": "Queue Alert",
        "message": f"Queue {queue_data.get('queue_name', 'Unknown')} has {queue_data.get('size', 0)} pending jobs",
        "data": queue_data,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    await emit_to_role(tenant_id, "tenant_admin", "notification", notification)

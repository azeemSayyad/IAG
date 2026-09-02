"""
Workflow Engine (Step 8.1)

Event-driven workflow engine for automated follow-ups.

Supports:
- Event triggers (lead_created, no_reply, missed_appointment, etc.)
- Action chains (send_sms, wait, retry, etc.)
- State machine for workflow progression
"""

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Callable
from enum import Enum
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.lead import Lead
from app.models.conversation import Conversation
from app.models.appointment import Appointment
from app.core.redis import redis_service
from app.core.audit import log_ai_action


class WorkflowEvent(str, Enum):
    """Events that can trigger workflows."""
    LEAD_CREATED = "lead_created"
    LEAD_REPLIED = "lead_replied"
    NO_REPLY = "no_reply"
    APPOINTMENT_BOOKED = "appointment_booked"
    APPOINTMENT_MISSED = "appointment_missed"
    APPOINTMENT_COMPLETED = "appointment_completed"
    DISPOSITION_SET = "disposition_set"
    LEAD_QUALIFIED = "lead_qualified"
    LEAD_COLD = "lead_cold"


class WorkflowAction(str, Enum):
    """Actions that workflows can perform."""
    SEND_SMS = "send_sms"
    WAIT = "wait"
    RETRY = "retry"
    ESCALATE = "escalate"
    UPDATE_STATUS = "update_status"
    ASSIGN_CAMPAIGN = "assign_campaign"
    BOOK_APPOINTMENT = "book_appointment"


class WorkflowState:
    """Represents the current state of a workflow."""

    def __init__(
        self,
        workflow_id: str,
        lead_id: str,
        tenant_id: str,
        current_step: int = 0,
        data: Dict = None,
    ):
        self.workflow_id = workflow_id
        self.lead_id = lead_id
        self.tenant_id = tenant_id
        self.current_step = current_step
        self.data = data or {}
        self.created_at = datetime.now(timezone.utc)
        self.updated_at = datetime.now(timezone.utc)

    def to_dict(self) -> dict:
        return {
            "workflow_id": self.workflow_id,
            "lead_id": self.lead_id,
            "tenant_id": self.tenant_id,
            "current_step": self.current_step,
            "data": self.data,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    def advance(self):
        """Move to the next step."""
        self.current_step += 1
        self.updated_at = datetime.now(timezone.utc)

    def set_data(self, key: str, value):
        """Update workflow data."""
        self.data[key] = value
        self.updated_at = datetime.now(timezone.utc)


# Workflow definitions
WORKFLOWS = {
    "no_reply": {
        "name": "No Reply Follow-up",
        "trigger": WorkflowEvent.NO_REPLY,
        "steps": [
            {"action": "wait", "duration_hours": 24},
            {"action": "send_sms", "template": "no_reply_1"},
            {"action": "wait", "duration_hours": 24},
            {"action": "send_sms", "template": "no_reply_2"},
            {"action": "wait", "duration_hours": 24},
            {"action": "send_sms", "template": "no_reply_3"},
            {"action": "update_status", "status": "nurture"},
        ],
    },
    "missed_appointment": {
        "name": "Missed Appointment Follow-up",
        "trigger": WorkflowEvent.APPOINTMENT_MISSED,
        "steps": [
            {"action": "wait", "duration_minutes": 30},
            {"action": "send_sms", "template": "missed_appointment"},
            {"action": "wait", "duration_hours": 24},
            {"action": "send_sms", "template": "reschedule_offer"},
            {"action": "wait", "duration_hours": 48},
            {"action": "send_sms", "template": "final_reschedule"},
            {"action": "update_status", "status": "nurture"},
        ],
    },
    "cold_nurture": {
        "name": "Cold Lead Nurture",
        "trigger": WorkflowEvent.LEAD_COLD,
        "steps": [
            {"action": "wait", "duration_days": 7},
            {"action": "send_sms", "template": "nurture_1"},
            {"action": "wait", "duration_days": 14},
            {"action": "send_sms", "template": "nurture_2"},
            {"action": "wait", "duration_days": 30},
            {"action": "send_sms", "template": "nurture_3"},
        ],
    },
    "post_win": {
        "name": "Post-Win Onboarding",
        "trigger": WorkflowEvent.APPOINTMENT_COMPLETED,
        "steps": [
            {"action": "send_sms", "template": "thank_you"},
            {"action": "wait", "duration_hours": 24},
            {"action": "send_sms", "template": "onboarding_info"},
        ],
    },
}


class WorkflowEngine:
    """Manages workflow execution."""

    def __init__(self, db: Session):
        self.db = db

    def start_workflow(
        self,
        workflow_name: str,
        lead_id: str,
        tenant_id: str,
        data: Dict = None,
    ) -> Optional[WorkflowState]:
        """
        Start a new workflow for a lead.
        """
        if workflow_name not in WORKFLOWS:
            return None

        workflow_id = f"{workflow_name}:{lead_id}:{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"

        state = WorkflowState(
            workflow_id=workflow_id,
            lead_id=lead_id,
            tenant_id=tenant_id,
            data=data or {},
        )

        # Store in Redis
        self._save_state(state)

        # Log
        log_ai_action(
            tenant_id=tenant_id,
            action="workflow_started",
            resource_type="lead",
            resource_id=lead_id,
            details={"workflow": workflow_name, "workflow_id": workflow_id},
        )

        return state

    def process_workflow(self, state: WorkflowState) -> Dict:
        """
        Process the current step of a workflow.
        """
        workflow_name = state.workflow_id.split(":")[0]
        workflow = WORKFLOWS.get(workflow_name)

        if not workflow:
            return {"success": False, "error": "Unknown workflow"}

        steps = workflow["steps"]
        if state.current_step >= len(steps):
            return {"success": True, "status": "completed"}

        step = steps[state.current_step]
        action = step["action"]

        result = self._execute_action(state, step)

        if result.get("success", False):
            state.advance()
            self._save_state(state)

        return result

    def _execute_action(self, state: WorkflowState, step: Dict) -> Dict:
        """Execute a workflow action."""
        action = step["action"]

        if action == "wait":
            return self._execute_wait(state, step)
        elif action == "send_sms":
            return self._execute_send_sms(state, step)
        elif action == "update_status":
            return self._execute_update_status(state, step)
        elif action == "retry":
            return {"success": True, "action": "retry"}
        else:
            return {"success": False, "error": f"Unknown action: {action}"}

    def _execute_wait(self, state: WorkflowState, step: Dict) -> Dict:
        """Execute a wait action."""
        duration_hours = step.get("duration_hours", 0)
        duration_minutes = step.get("duration_minutes", 0)
        duration_days = step.get("duration_days", 0)

        total_seconds = (
            duration_hours * 3600 +
            duration_minutes * 60 +
            duration_days * 86400
        )

        # Schedule next processing
        next_time = datetime.now(timezone.utc) + timedelta(seconds=total_seconds)
        state.set_data("next_action_at", next_time.isoformat())

        # Add to Redis delayed queue
        redis_service.client.zadd(
            "workflow:delayed",
            {state.workflow_id: next_time.timestamp()},
        )

        return {"success": True, "action": "wait", "next_at": next_time.isoformat()}

    def _execute_send_sms(self, state: WorkflowState, step: Dict) -> Dict:
        """Execute send SMS action."""
        from app.ai.services.prompts import get_followup_message, get_outreach_message
        from app.ai.services.communication_provider import send_sms_to_lead

        lead = self.db.query(Lead).filter(Lead.id == state.lead_id, Lead.deleted_at.is_(None)).first()
        if not lead:
            return {"success": False, "error": "Lead not found"}

        # Queue-Only Mode: no automated workflow sends while booking autopilot is paused.
        from app.core.sending import is_autopilot_paused
        if is_autopilot_paused(str(lead.tenant_id)):
            return {"success": False, "skipped": "autopilot_paused"}

        template = step.get("template", "default")
        message = self._get_message(template, lead)

        result = send_sms_to_lead(
            phone=lead.phone,
            message=message,
            tenant_id=state.tenant_id,
            lead_id=state.lead_id,
        )

        return {"success": result.get("success", False), "action": "send_sms", "message": message[:50]}

    def _execute_update_status(self, state: WorkflowState, step: Dict) -> Dict:
        """Execute update status action."""
        new_status = step.get("status", "nurture")

        lead = self.db.query(Lead).filter(Lead.id == state.lead_id).first()
        if lead:
            lead.status = new_status
            self.db.commit()

        return {"success": True, "action": "update_status", "new_status": new_status}

    def _get_message(self, template: str, lead: Lead) -> str:
        """Get message for template."""
        from app.ai.services.prompts import get_followup_message, get_outreach_message

        if template == "no_reply_1":
            return get_followup_message(lead.first_name, followup_number=1)
        elif template == "no_reply_2":
            return get_followup_message(lead.first_name, followup_number=2)
        elif template == "no_reply_3":
            return get_followup_message(lead.first_name, followup_number=3)
        elif template == "missed_appointment":
            return f"Hey {lead.first_name}! We missed you today. Want to reschedule?"
        elif template == "reschedule_offer":
            return f"Hi {lead.first_name}! Your spot is still available. Want to book a new time?"
        elif template == "final_reschedule":
            return f"Hi {lead.first_name}, last chance to reschedule. Let me know!"
        elif template == "thank_you":
            return f"Thank you, {lead.first_name}! We're excited to have you."
        elif template == "onboarding_info":
            return f"Hi {lead.first_name}! Here's what to expect next..."
        elif template == "nurture_1":
            return get_outreach_message(lead.first_name, tone="friendly", tenant_id=lead.tenant_id)
        elif template == "nurture_2":
            return get_outreach_message(lead.first_name, tone="professional", tenant_id=lead.tenant_id)
        elif template == "nurture_3":
            return get_outreach_message(lead.first_name, tone="urgent", tenant_id=lead.tenant_id)
        else:
            return f"Hi {lead.first_name}! Just checking in."

    def _save_state(self, state: WorkflowState):
        """Save workflow state to Redis."""
        key = f"workflow:state:{state.workflow_id}"
        redis_service.client.set(key, str(state.to_dict()), ex=86400 * 30)  # 30 days

    def get_state(self, workflow_id: str) -> Optional[Dict]:
        """Get workflow state from Redis."""
        key = f"workflow:state:{workflow_id}"
        data = redis_service.client.get(key)
        if data:
            import json
            return json.loads(data.replace("'", '"'))
        return None

    def get_active_workflows(self, lead_id: str) -> List[Dict]:
        """Get all active workflows for a lead."""
        pattern = f"workflow:state:*:{lead_id}:*"
        keys = redis_service.client.keys(pattern)
        workflows = []
        for key in keys:
            data = redis_service.client.get(key)
            if data:
                import json
                workflows.append(json.loads(data.replace("'", '"')))
        return workflows


def process_delayed_workflows(db: Session) -> Dict:
    """
    Process all delayed workflows that are ready to execute.

    Called by a worker/cron job.
    """
    now = datetime.now(timezone.utc).timestamp()

    # Get ready workflows from sorted set
    ready = redis_service.client.zrangebyscore("workflow:delayed", 0, now)

    engine = WorkflowEngine(db)
    processed = 0
    failed = 0

    for workflow_id in ready:
        state_data = engine.get_state(workflow_id)
        if state_data:
            state = WorkflowState(**state_data)
            result = engine.process_workflow(state)
            if result.get("success"):
                processed += 1
            else:
                failed += 1

        # Remove from delayed queue
        redis_service.client.zrem("workflow:delayed", workflow_id)

    return {
        "total_ready": len(ready),
        "processed": processed,
        "failed": failed,
    }

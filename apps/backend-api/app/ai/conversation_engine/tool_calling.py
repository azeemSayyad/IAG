"""
Tool Calling System (Step 36.5)

Enables AI to trigger actions:
- search_slots — Find available appointment slots
- book_appointment — Book an appointment
- reschedule — Reschedule an appointment
- cancel_appointment — Cancel an appointment
- update_lead — Update lead information
- add_to_suppression — Add to do-not-contact list
- switch_campaign — Move lead to different campaign
- escalate_to_agent — Transfer to human agent

The AI outputs structured tool calls which are parsed and executed.
"""

import json
import logging
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any, Callable
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.lead import Lead
from app.models.appointment import Appointment
from app.models.conversation import Conversation

logger = logging.getLogger(__name__)


# Tool definitions for LLM prompt
TOOL_DEFINITIONS = """
You have access to the following tools. To use a tool, respond with a JSON block:

```tool
{"tool": "tool_name", "args": {"param": "value"}}
```

Available tools:

1. search_slots — Find available appointment times
   Args: date (optional, YYYY-MM-DD), agent_id (optional)
   Example: ```tool
{"tool": "search_slots", "args": {"date": "2026-05-23"}}
```

2. book_appointment — Book an appointment for the customer
   Args: slot_key (required, format: YYYYMMDD_HHMM), agent_id (optional)
   Example: ```tool
{"tool": "book_appointment", "args": {"slot_key": "20260523_1400"}}
```

3. reschedule — Reschedule an existing appointment
   Args: appointment_id (optional, uses current if not provided)
   Example: ```tool
{"tool": "reschedule", "args": {}}
```

4. cancel_appointment — Cancel an appointment
   Args: reason (optional)
   Example: ```tool
{"tool": "cancel_appointment", "args": {"reason": "customer request"}}
```

5. update_lead — Update lead information
   Args: field (required), value (required)
   Fields: email, state, city, zip_code, tags, notes
   Example: ```tool
{"tool": "update_lead", "args": {"field": "email", "value": "john@example.com"}}
```

6. add_to_suppression — Stop all contact with this lead
   Args: reason (optional)
   Example: ```tool
{"tool": "add_to_suppression", "args": {"reason": "customer opt-out"}}
```

7. escalate_to_agent — Transfer conversation to a human agent
   Args: reason (optional)
   Example: ```tool
{"tool": "escalate_to_agent", "args": {"reason": "complex question"}}
```

8. move_to_nurture — Move lead to nurture campaign
   Args: reason (optional)
   Example: ```tool
{"tool": "move_to_nurture", "args": {"reason": "not ready now"}}
```

IMPORTANT: Only use tools when the customer's request requires an action. For general conversation, respond normally without tools.
"""


class ToolCall:
    """Represents a parsed tool call from the AI."""

    def __init__(self, tool_name: str, args: Dict[str, Any]):
        self.tool_name = tool_name
        self.args = args
        self.result: Optional[Dict] = None
        self.success: bool = False
        self.error: Optional[str] = None

    def to_dict(self) -> Dict:
        return {
            "tool": self.tool_name,
            "args": self.args,
            "result": self.result,
            "success": self.success,
            "error": self.error,
        }


class ToolExecutor:
    """Executes tool calls from the AI."""

    def __init__(self, db: Session, tenant_id: str):
        self.db = db
        self.tenant_id = tenant_id
        self._handlers: Dict[str, Callable] = {
            "search_slots": self._search_slots,
            "book_appointment": self._book_appointment,
            "reschedule": self._reschedule,
            "cancel_appointment": self._cancel_appointment,
            "update_lead": self._update_lead,
            "add_to_suppression": self._add_to_suppression,
            "escalate_to_agent": self._escalate_to_agent,
            "move_to_nurture": self._move_to_nurture,
        }

    def parse_tool_calls(self, response: str) -> List[ToolCall]:
        """
        Parse tool calls from AI response.

        Looks for ```tool blocks in the response.
        """
        tool_calls = []

        # Pattern: ```tool\n{json}\n```
        pattern = r'```tool\s*\n(\{[^`]+\})\s*\n```'
        matches = re.findall(pattern, response, re.DOTALL)

        for match in matches:
            try:
                data = json.loads(match)
                tool_name = data.get("tool")
                args = data.get("args", {})

                if tool_name and tool_name in self._handlers:
                    tool_calls.append(ToolCall(tool_name, args))
                else:
                    logger.warning(f"Unknown tool: {tool_name}")

            except json.JSONDecodeError as e:
                logger.warning(f"Failed to parse tool call: {e}")
                continue

        return tool_calls

    async def execute_tool_calls(
        self,
        tool_calls: List[ToolCall],
        lead: Lead,
        conversation: Conversation,
        appointment: Optional[Appointment] = None,
    ) -> List[ToolCall]:
        """
        Execute parsed tool calls.

        Args:
            tool_calls: List of parsed ToolCall objects
            lead: Current lead
            conversation: Current conversation
            appointment: Current appointment (if any)

        Returns:
            List of ToolCall objects with results
        """
        for tc in tool_calls:
            try:
                handler = self._handlers.get(tc.tool_name)
                if handler:
                    tc.result = await handler(
                        lead=lead,
                        conversation=conversation,
                        appointment=appointment,
                        **tc.args,
                    )
                    tc.success = True
                else:
                    tc.error = f"Unknown tool: {tc.tool_name}"

            except Exception as e:
                logger.error(f"Tool execution failed for {tc.tool_name}: {e}")
                tc.error = str(e)
                tc.success = False

        return tool_calls

    def get_tool_definitions(self) -> str:
        """Get tool definitions for inclusion in LLM prompt."""
        return TOOL_DEFINITIONS

    async def _search_slots(
        self,
        lead: Lead,
        conversation: Conversation,
        appointment: Optional[Appointment] = None,
        date: Optional[str] = None,
        agent_id: Optional[str] = None,
        **kwargs,
    ) -> Dict:
        """Search for available appointment slots."""
        from app.booking.services.availability import get_merged_available_slots
        from datetime import date as date_type

        today = date_type.today()
        try:
            target_date = date_type.fromisoformat(date) if date else today
        except (TypeError, ValueError):
            target_date = today
        if target_date < today:
            target_date = today
        slots = get_merged_available_slots(
            self.db, self.tenant_id, target_date
        )

        return {
            "slots": [
                {
                    "key": s.key,
                    "start_display": s.start_time.strftime("%I:%M %p").lstrip("0"),
                    "date_display": s.start_time.strftime("%A, %B %d"),
                    "start_time": s.start_time.isoformat(),
                    "end_time": s.end_time.isoformat(),
                }
                for s in slots[:10]
            ],
            "total": len(slots),
            "date": target_date.isoformat(),
        }

    async def _book_appointment(
        self,
        lead: Lead,
        conversation: Conversation,
        appointment: Optional[Appointment] = None,
        slot_key: Optional[str] = None,
        agent_id: Optional[str] = None,
        **kwargs,
    ) -> Dict:
        """Book an appointment."""
        from app.booking.services.booking import start_booking_flow, process_slot_selection

        if not slot_key:
            return {"success": False, "error": "slot_key is required"}

        # Parse slot key (format: YYYYMMDD_HHMM)
        try:
            start_time = datetime.strptime(slot_key, "%Y%m%d_%H%M")
            start_time = start_time.replace(tzinfo=timezone.utc)
            end_time = start_time + __import__("datetime").timedelta(minutes=15)
        except ValueError:
            return {"success": False, "error": f"Invalid slot_key format: {slot_key}"}

        # Find agent
        if not agent_id:
            from app.booking.services.assignment import assign_agent
            agent = assign_agent(
                self.db, self.tenant_id, start_time, end_time
            )
            if not agent:
                return {"success": False, "error": "No agents available for this slot"}
            agent_id = str(agent.id)

        # Create appointment
        appointment = Appointment(
            tenant_id=self.tenant_id,
            lead_id=lead.id,
            agent_id=UUID(agent_id),
            conversation_id=conversation.id,
            start_time=start_time,
            end_time=end_time,
            status="confirmed",
            booking_source="ai",
        )
        self.db.add(appointment)

        # Update lead
        lead.status = "booked"

        # Update conversation
        conversation.status = "booked"

        self.db.commit()

        return {
            "success": True,
            "appointment_id": str(appointment.id),
            "start_time": start_time.isoformat(),
            "agent_id": agent_id,
        }

    async def _reschedule(
        self,
        lead: Lead,
        conversation: Conversation,
        appointment: Optional[Appointment] = None,
        **kwargs,
    ) -> Dict:
        """Reschedule an appointment."""
        if not appointment:
            return {"success": False, "error": "No appointment to reschedule"}

        from app.booking.services.booking import cancel_booking

        result = cancel_booking(
            self.db, self.tenant_id, appointment.id, reason="rescheduled"
        )

        if result.success:
            # Start new booking flow
            from app.booking.services.booking import start_booking_flow
            booking_result = start_booking_flow(
                self.db, self.tenant_id, lead, conversation
            )
            return {
                "success": True,
                "cancelled": True,
                "new_options": booking_result.data if booking_result.success else None,
            }

        return {"success": False, "error": result.message}

    async def _cancel_appointment(
        self,
        lead: Lead,
        conversation: Conversation,
        appointment: Optional[Appointment] = None,
        reason: Optional[str] = None,
        **kwargs,
    ) -> Dict:
        """Cancel an appointment."""
        if not appointment:
            return {"success": False, "error": "No appointment to cancel"}

        from app.booking.services.booking import cancel_booking

        result = cancel_booking(
            self.db, self.tenant_id, appointment.id, reason=reason
        )

        return {"success": result.success, "message": result.message}

    async def _update_lead(
        self,
        lead: Lead,
        conversation: Conversation,
        appointment: Optional[Appointment] = None,
        field: Optional[str] = None,
        value: Optional[str] = None,
        **kwargs,
    ) -> Dict:
        """Update lead information."""
        if not field or value is None:
            return {"success": False, "error": "field and value are required"}

        allowed_fields = ["email", "state", "city", "zip_code", "tags", "notes"]
        if field not in allowed_fields:
            return {"success": False, "error": f"Cannot update field: {field}"}

        setattr(lead, field, value)
        self.db.commit()

        return {"success": True, "field": field, "value": value}

    async def _add_to_suppression(
        self,
        lead: Lead,
        conversation: Conversation,
        appointment: Optional[Appointment] = None,
        reason: Optional[str] = None,
        **kwargs,
    ) -> Dict:
        """Silently stop contacting the lead (no suppression list, no message)."""
        lead.status = "unqualified"
        conversation.status = "stopped"
        self.db.commit()

        return {"success": True, "message": "Lead marked unqualified; messaging stopped"}

    async def _escalate_to_agent(
        self,
        lead: Lead,
        conversation: Conversation,
        appointment: Optional[Appointment] = None,
        reason: Optional[str] = None,
        **kwargs,
    ) -> Dict:
        """Escalate conversation to human agent."""
        conversation.status = "escalated"
        self.db.commit()

        return {
            "success": True,
            "message": "Conversation escalated to human agent",
            "reason": reason,
        }

    async def _move_to_nurture(
        self,
        lead: Lead,
        conversation: Conversation,
        appointment: Optional[Appointment] = None,
        reason: Optional[str] = None,
        **kwargs,
    ) -> Dict:
        """Move lead to nurture campaign."""
        from app.followup.services.nurture import move_to_nurture

        result = move_to_nurture(self.db, lead.id, self.tenant_id)
        return result

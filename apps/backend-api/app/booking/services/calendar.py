"""
Calendar Integration (Step 6.6)

Google Calendar integration for appointment sync.

Supports:
- Creating calendar events
- Updating events
- Deleting events
- Getting availability
- OAuth2 authentication flow
"""

from datetime import datetime, timedelta
from typing import Dict, Optional, List
from uuid import UUID

from app.core.config import settings
from app.core.audit import log_ai_action


class CalendarEvent:
    """Represents a calendar event."""

    def __init__(
        self,
        title: str,
        start_time: datetime,
        end_time: datetime,
        description: str = None,
        location: str = None,
        attendees: list = None,
    ):
        self.title = title
        self.start_time = start_time
        self.end_time = end_time
        self.description = description
        self.location = location
        self.attendees = attendees or []

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat(),
            "description": self.description,
            "location": self.location,
            "attendees": self.attendees,
        }

    def to_google_event(self) -> dict:
        """Convert to Google Calendar API event format."""
        event = {
            "summary": self.title,
            "start": {
                "dateTime": self.start_time.isoformat(),
                "timeZone": "UTC",
            },
            "end": {
                "dateTime": self.end_time.isoformat(),
                "timeZone": "UTC",
            },
        }

        if self.description:
            event["description"] = self.description

        if self.location:
            event["location"] = self.location

        if self.attendees:
            event["attendees"] = [{"email": email} for email in self.attendees]

        return event


class GoogleCalendarService:
    """
    Google Calendar integration.

    Uses Google Calendar API v3 with OAuth2 authentication.
    Requires GOOGLE_CALENDAR_CREDENTIALS environment variable (JSON string)
    or credentials file at configured path.
    """

    def __init__(self):
        self._service = None
        self._credentials = None

    def is_configured(self) -> bool:
        """Check if Google Calendar is configured."""
        return bool(getattr(settings, 'GOOGLE_CALENDAR_CREDENTIALS', None))

    def _get_credentials(self):
        """Get or refresh OAuth2 credentials."""
        if self._credentials:
            return self._credentials

        try:
            from google.oauth2.credentials import Credentials
            from google.auth.transport.requests import Request
            import json
            import os

            creds_data = getattr(settings, 'GOOGLE_CALENDAR_CREDENTIALS', None)
            if not creds_data:
                return None

            # Parse credentials JSON
            if os.path.isfile(creds_data):
                with open(creds_data, 'r') as f:
                    creds_info = json.load(f)
            else:
                creds_info = json.loads(creds_data)

            self._credentials = Credentials.from_authorized_user_info(
                creds_info,
                scopes=["https://www.googleapis.com/auth/calendar"]
            )

            # Refresh if expired
            if self._credentials.expired and self._credentials.refresh_token:
                self._credentials.refresh(Request())

            return self._credentials

        except Exception:
            return None

    def _get_service(self):
        """Get or create Google Calendar API service."""
        if self._service:
            return self._service

        try:
            from googleapiclient.discovery import build

            credentials = self._get_credentials()
            if not credentials:
                return None

            self._service = build("calendar", "v3", credentials=credentials)
            return self._service

        except Exception:
            return None

    async def create_event(
        self,
        calendar_id: str,
        event: CalendarEvent,
        tenant_id: str = None,
        lead_id: str = None,
    ) -> Dict:
        """
        Create a calendar event.

        Args:
            calendar_id: Google Calendar ID (e.g., 'primary')
            event: CalendarEvent object
            tenant_id: Tenant ID for audit logging
            lead_id: Lead ID for audit logging

        Returns:
            Dict with success status and event details
        """
        service = self._get_service()
        if not service:
            return {"success": False, "error": "Google Calendar not configured or credentials invalid"}

        try:
            google_event = event.to_google_event()
            result = service.events().insert(calendarId=calendar_id, body=google_event).execute()

            if tenant_id:
                log_ai_action(
                    tenant_id=tenant_id,
                    action="calendar_event_created",
                    resource_type="appointment",
                    resource_id=lead_id,
                    details={"calendar_event_id": result.get("id"), "title": event.title},
                )

            return {
                "success": True,
                "event_id": result.get("id"),
                "html_link": result.get("htmlLink"),
                "status": result.get("status"),
            }

        except Exception as e:
            return {"success": False, "error": str(e)}

    async def update_event(
        self,
        calendar_id: str,
        event_id: str,
        event: CalendarEvent,
    ) -> Dict:
        """
        Update an existing calendar event.

        Args:
            calendar_id: Google Calendar ID
            event_id: Google Calendar event ID
            event: Updated CalendarEvent object

        Returns:
            Dict with success status
        """
        service = self._get_service()
        if not service:
            return {"success": False, "error": "Google Calendar not configured"}

        try:
            google_event = event.to_google_event()
            result = service.events().update(
                calendarId=calendar_id,
                eventId=event_id,
                body=google_event,
            ).execute()

            return {
                "success": True,
                "event_id": result.get("id"),
                "status": result.get("status"),
            }

        except Exception as e:
            return {"success": False, "error": str(e)}

    async def delete_event(
        self,
        calendar_id: str,
        event_id: str,
    ) -> Dict:
        """
        Delete a calendar event.

        Args:
            calendar_id: Google Calendar ID
            event_id: Google Calendar event ID

        Returns:
            Dict with success status
        """
        service = self._get_service()
        if not service:
            return {"success": False, "error": "Google Calendar not configured"}

        try:
            service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
            return {"success": True}

        except Exception as e:
            return {"success": False, "error": str(e)}

    async def get_events(
        self,
        calendar_id: str,
        start_time: datetime,
        end_time: datetime,
        max_results: int = 100,
    ) -> Dict:
        """
        Get events from a calendar.

        Args:
            calendar_id: Google Calendar ID
            start_time: Start of time range
            end_time: End of time range
            max_results: Maximum number of events to return

        Returns:
            Dict with success status and list of events
        """
        service = self._get_service()
        if not service:
            return {"success": False, "error": "Google Calendar not configured"}

        try:
            events_result = service.events().list(
                calendarId=calendar_id,
                timeMin=start_time.isoformat() + "Z",
                timeMax=end_time.isoformat() + "Z",
                maxResults=max_results,
                singleEvents=True,
                orderBy="startTime",
            ).execute()

            events = events_result.get("items", [])
            return {
                "success": True,
                "events": [
                    {
                        "id": event.get("id"),
                        "title": event.get("summary"),
                        "start": event.get("start", {}).get("dateTime"),
                        "end": event.get("end", {}).get("dateTime"),
                        "description": event.get("description"),
                        "location": event.get("location"),
                    }
                    for event in events
                ],
                "total": len(events),
            }

        except Exception as e:
            return {"success": False, "error": str(e)}

    async def check_availability(
        self,
        calendar_id: str,
        start_time: datetime,
        end_time: datetime,
    ) -> Dict:
        """
        Check if a time slot is available (no conflicting events).

        Args:
            calendar_id: Google Calendar ID
            start_time: Start of time slot
            end_time: End of time slot

        Returns:
            Dict with availability status and conflicting events
        """
        result = await self.get_events(calendar_id, start_time, end_time)

        if not result.get("success"):
            return result

        events = result.get("events", [])
        is_available = len(events) == 0

        return {
            "success": True,
            "is_available": is_available,
            "conflicting_events": events if not is_available else [],
        }


# Singleton
calendar_service = GoogleCalendarService()


def create_appointment_event(
    lead_name: str,
    agent_name: str,
    start_time: datetime,
    end_time: datetime,
    notes: str = None,
) -> CalendarEvent:
    """
    Create a CalendarEvent for an appointment.

    Args:
        lead_name: Lead's full name
        agent_name: Agent's full name
        start_time: Appointment start time
        end_time: Appointment end time
        notes: Optional notes

    Returns:
        CalendarEvent object
    """
    title = f"Insurance Call: {lead_name}"
    description = f"Appointment with {lead_name}\nAgent: {agent_name}"
    if notes:
        description += f"\nNotes: {notes}"

    return CalendarEvent(
        title=title,
        start_time=start_time,
        end_time=end_time,
        description=description,
    )

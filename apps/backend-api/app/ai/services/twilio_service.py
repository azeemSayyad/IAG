"""
Backward-compatible import shim.

The app now uses Engage Clouds as the primary communication provider. Existing
imports are retained here to avoid breaking older modules while they migrate.
"""

from app.ai.services.communication_provider import (  # noqa: F401
    EngageCloudService,
    communication_service,
    send_sms_to_lead,
)

twilio_service = communication_service

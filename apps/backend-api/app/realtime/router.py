"""
Realtime Router

Endpoints:
- GET /realtime/online — Get online users
- POST /realtime/notify — Send notification
- GET /realtime/status — Check realtime service status
- GET /realtime/presence — Get agent presence
- POST /realtime/presence/heartbeat — Agent heartbeat
- GET /realtime/presence/agents — Get all agent presence
"""

from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_tenant_id, get_current_active_user
from app.models.user import User
from app.realtime.websocket import manager
from app.realtime.notifications import (
    notify_new_booking,
    notify_booking_cancelled,
    notify_lead_replied,
    notify_system_alert,
)
from app.realtime.presence import PresenceManager

router = APIRouter(prefix="/realtime", tags=["realtime"])

presence_manager = PresenceManager()


class NotificationRequest(BaseModel):
    type: str
    title: str
    message: str
    data: Dict = {}


class HeartbeatRequest(BaseModel):
    agent_id: str
    metadata: Dict = {}


@router.get("/online")
async def get_online_users(
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Get list of online users for the tenant."""
    users = manager.get_online_users(tenant_id)
    return {
        "online_users": users,
        "total": len(users),
    }


@router.get("/status")
async def realtime_status(
    current_user: User = Depends(get_current_active_user),
):
    """Check realtime service status."""
    return {
        "status": "ok",
        "websocket": "active",
        "total_connections": sum(len(conns) for conns in manager.tenant_connections.values()),
    }


@router.post("/notify")
async def send_notification(
    request: NotificationRequest,
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Send a notification to the tenant."""
    await notify_system_alert(tenant_id, {
        "message": request.message,
        "type": request.type,
        "data": request.data,
    })
    return {"success": True, "message": "Notification sent"}


# --- Agent Presence Endpoints (Phase 39 + 44) ---

@router.post("/presence/heartbeat")
async def agent_heartbeat(
    request: HeartbeatRequest,
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Process agent heartbeat."""
    result = presence_manager.heartbeat(request.agent_id, request.metadata)
    return result


@router.get("/presence/{agent_id}")
async def get_agent_presence(
    agent_id: str,
    current_user: User = Depends(get_current_active_user),
):
    """Get presence status for a specific agent."""
    presence = presence_manager.get_presence(agent_id)
    if not presence:
        raise HTTPException(status_code=404, detail="Agent presence not found")
    return presence


@router.get("/presence/agents/all")
async def get_all_agent_presence(
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Get presence for all agents in the tenant."""
    agents = presence_manager.get_all_presence(tenant_id)
    return {
        "agents": agents,
        "total": len(agents),
    }


@router.get("/presence/agents/online")
async def get_online_agents(
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Get online agents in the tenant."""
    agents = presence_manager.get_online_agents(tenant_id)
    return {
        "agents": agents,
        "total": len(agents),
    }


@router.get("/presence/agents/available")
async def get_available_agents(
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Get available agents (online, no active call)."""
    agents = presence_manager.get_available_agents(tenant_id)
    return {
        "agents": agents,
        "total": len(agents),
    }


@router.get("/presence/stats")
async def get_presence_stats(
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Get tenant-level presence statistics."""
    stats = presence_manager.get_tenant_stats(tenant_id)
    return stats

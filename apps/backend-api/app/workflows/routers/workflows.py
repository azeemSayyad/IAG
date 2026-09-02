"""
Workflow Orchestration Router (Phase 43)

Exposes workflow registration, execution, monitoring, and event triggers.
"""

from typing import Optional, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_active_user, get_tenant_id
from app.core.permissions import require_role
from app.models.user import User
from app.workflows.engine import WorkflowEngine, EventType

router = APIRouter(prefix="/workflows", tags=["workflows"])


# --- Dependency injection factory (request-scoped) ---

def get_workflow_engine(db: Session = Depends(get_db)):
    return WorkflowEngine(db)


# --- Pydantic Models ---

class WorkflowCreate(BaseModel):
    workflow_id: str
    name: str
    description: str = ""
    trigger_event: str
    nodes: Dict[str, Any]


class WorkflowStart(BaseModel):
    workflow_id: str
    lead_id: str
    context: Dict[str, Any] = {}


# --- Workflow Management ---

@router.post("/", status_code=status.HTTP_201_CREATED)
def register_workflow(
    data: WorkflowCreate,
    engine: WorkflowEngine = Depends(get_workflow_engine),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Register a new workflow definition."""
    require_role(current_user, ["tenant_admin", "super_admin"])

    workflow_def = {
        "workflow_id": data.workflow_id,
        "name": data.name,
        "description": data.description,
        "trigger_event": data.trigger_event,
        "nodes": data.nodes,
        "tenant_id": tenant_id,
        "enabled": True,
    }

    engine.register_workflow(workflow_def)
    return {"message": "Workflow registered", "workflow_id": data.workflow_id}


@router.get("/", status_code=status.HTTP_200_OK)
def list_workflows(
    engine: WorkflowEngine = Depends(get_workflow_engine),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """List all registered workflows for the tenant."""
    import json
    redis = engine.redis
    workflows = []

    for key in redis.scan_iter("workflow:def:*"):
        data = redis.get(key)
        if data:
            wf = json.loads(data)
            if wf.get("tenant_id") == tenant_id:
                workflows.append({
                    "workflow_id": wf.get("workflow_id"),
                    "name": wf.get("name"),
                    "description": wf.get("description"),
                    "trigger_event": wf.get("trigger_event"),
                    "enabled": wf.get("enabled"),
                    "node_count": len(wf.get("nodes", {})),
                })

    return {"workflows": workflows, "total": len(workflows)}


@router.get("/{workflow_id}", status_code=status.HTTP_200_OK)
def get_workflow(
    workflow_id: str,
    engine: WorkflowEngine = Depends(get_workflow_engine),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Get a specific workflow definition."""
    workflow = engine.get_workflow(workflow_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return workflow


# --- Workflow Execution ---

@router.post("/start", status_code=status.HTTP_201_CREATED)
def start_workflow(
    data: WorkflowStart,
    engine: WorkflowEngine = Depends(get_workflow_engine),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Start a workflow instance for a lead."""
    instance = engine.start_workflow(
        workflow_id=data.workflow_id,
        lead_id=data.lead_id,
        tenant_id=tenant_id,
        context=data.context,
    )
    if not instance:
        raise HTTPException(status_code=400, detail="Failed to start workflow")
    return {
        "instance_id": instance.instance_id,
        "workflow_id": data.workflow_id,
        "status": instance.status,
    }


@router.get("/instances/{instance_id}", status_code=status.HTTP_200_OK)
def get_workflow_instance(
    instance_id: str,
    engine: WorkflowEngine = Depends(get_workflow_engine),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Get the status of a workflow instance."""
    instance = engine._load_instance(instance_id)
    if not instance:
        raise HTTPException(status_code=404, detail="Instance not found")
    return {
        "instance_id": instance.instance_id,
        "workflow_id": instance.workflow_id,
        "lead_id": instance.lead_id,
        "status": instance.status,
        "current_node": instance.current_node_id,
        "context": instance.context,
        "started_at": instance.started_at.isoformat() if instance.started_at else None,
        "completed_at": instance.completed_at.isoformat() if instance.completed_at else None,
        "error": instance.error,
    }


# --- Event Triggers ---

@router.post("/trigger/{event_type}", status_code=status.HTTP_200_OK)
async def trigger_event(
    event_type: str,
    lead_id: str = Query(...),
    context: Dict[str, Any] = {},
    engine: WorkflowEngine = Depends(get_workflow_engine),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Trigger all workflows matching an event type."""
    try:
        event = EventType(event_type)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid event type. Valid types: {[e.value for e in EventType]}"
        )

    instances = await engine.handle_event(
        event_type=event,
        lead_id=lead_id,
        tenant_id=tenant_id,
        context=context,
    )

    return {
        "event_type": event_type,
        "workflows_triggered": len(instances),
        "instances": [
            {"instance_id": i.instance_id, "workflow_id": i.workflow_id}
            for i in instances
        ],
    }


# --- Queue Management ---

@router.get("/queue/status", status_code=status.HTTP_200_OK)
def get_queue_status(
    engine: WorkflowEngine = Depends(get_workflow_engine),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Get workflow queue status."""
    import json
    redis = engine.redis

    queue_size = redis.zcard("workflow:queue")
    active_count = 0

    for key in redis.scan_iter("workflow:instance:*"):
        data = redis.get(key)
        if data:
            inst = json.loads(data)
            if inst.get("status") == "running" and inst.get("tenant_id") == tenant_id:
                active_count += 1

    return {
        "queue_size": queue_size,
        "active_instances": active_count,
    }


@router.post("/queue/process", status_code=status.HTTP_200_OK)
async def process_queue(
    engine: WorkflowEngine = Depends(get_workflow_engine),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Process pending workflow queue items."""
    require_role(current_user, ["tenant_admin", "super_admin"])

    processed = engine.process_queue()
    return {"processed": processed}

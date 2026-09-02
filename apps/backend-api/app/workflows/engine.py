"""
Workflow Orchestration Engine (Phase 43)

Enterprise-grade workflow automation:

Step 43.1 — Workflow Builder
    Visual workflow definition with nodes and connections

Step 43.2 — Workflow Nodes
    - send_sms: Send SMS message
    - wait: Wait for duration
    - ai_classify: Classify intent with AI
    - assign_agent: Assign to best agent
    - book_appointment: Book appointment
    - update_lead: Update lead fields
    - condition: Branch based on conditions
    - webhook: Call external webhook

Step 43.3 — Branching Logic
    IF/ELSE based on intent, sentiment, lead data

Step 43.4 — Retry Policies
    Exponential backoff, retry windows, max attempts

Step 43.5 — Event Triggers
    Trigger on: lead_created, reply_received, no_show, idle_agent, appointment_booked
"""

import json
import logging
import math
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any, Callable
from uuid import UUID, uuid4
from enum import Enum

from sqlalchemy.orm import Session

from app.core.redis import redis_service

logger = logging.getLogger(__name__)


# --- Node Types ---

class NodeType(str, Enum):
    """Workflow node types."""
    START = "start"
    END = "end"
    SEND_SMS = "send_sms"
    WAIT = "wait"
    AI_CLASSIFY = "ai_classify"
    ASSIGN_AGENT = "assign_agent"
    BOOK_APPOINTMENT = "book_appointment"
    UPDATE_LEAD = "update_lead"
    CONDITION = "condition"
    WEBHOOK = "webhook"
    DELAY = "delay"
    PARALLEL = "parallel"
    MERGE = "merge"


# --- Event Types ---

class EventType(str, Enum):
    """Workflow trigger events."""
    LEAD_CREATED = "lead_created"
    REPLY_RECEIVED = "reply_received"
    NO_SHOW = "no_show"
    IDLE_AGENT = "idle_agent"
    APPOINTMENT_BOOKED = "appointment_booked"
    APPOINTMENT_COMPLETED = "appointment_completed"
    LEAD_SCORED = "lead_scored"
    CAMPAIGN_MATCHED = "campaign_matched"
    MANUAL_TRIGGER = "manual_trigger"
    SCHEDULED = "scheduled"


# --- Retry Policy ---

class RetryPolicy:
    """Defines retry behavior for failed nodes."""

    def __init__(
        self,
        max_retries: int = 3,
        initial_delay_seconds: int = 60,
        max_delay_seconds: int = 3600,
        backoff_multiplier: float = 2.0,
        retry_on: List[str] = None,
    ):
        self.max_retries = max_retries
        self.initial_delay_seconds = initial_delay_seconds
        self.max_delay_seconds = max_delay_seconds
        self.backoff_multiplier = backoff_multiplier
        self.retry_on = retry_on or ["timeout", "error", "rate_limit"]

    def get_delay(self, attempt: int) -> int:
        """Calculate delay for given attempt number."""
        delay = self.initial_delay_seconds * (self.backoff_multiplier ** attempt)
        return min(int(delay), self.max_delay_seconds)

    def should_retry(self, attempt: int, error_type: str) -> bool:
        """Determine if should retry."""
        if attempt >= self.max_retries:
            return False
        return error_type in self.retry_on

    def to_dict(self) -> Dict:
        return {
            "max_retries": self.max_retries,
            "initial_delay_seconds": self.initial_delay_seconds,
            "max_delay_seconds": self.max_delay_seconds,
            "backoff_multiplier": self.backoff_multiplier,
            "retry_on": self.retry_on,
        }


# --- Workflow Node ---

class WorkflowNode:
    """A single node in a workflow."""

    def __init__(
        self,
        node_id: str,
        node_type: NodeType,
        config: Dict[str, Any] = None,
        next_nodes: List[str] = None,
        condition: Dict[str, Any] = None,
        retry_policy: RetryPolicy = None,
    ):
        self.node_id = node_id
        self.node_type = node_type
        self.config = config or {}
        self.next_nodes = next_nodes or []
        self.condition = condition  # For CONDITION nodes
        self.retry_policy = retry_policy or RetryPolicy()

    def to_dict(self) -> Dict:
        return {
            "node_id": self.node_id,
            "node_type": self.node_type.value,
            "config": self.config,
            "next_nodes": self.next_nodes,
            "condition": self.condition,
            "retry_policy": self.retry_policy.to_dict(),
        }


# --- Workflow Definition ---

class WorkflowDefinition:
    """Defines a complete workflow."""

    def __init__(
        self,
        workflow_id: str,
        name: str,
        description: str,
        trigger_event: EventType,
        nodes: Dict[str, WorkflowNode],
        tenant_id: str,
        enabled: bool = True,
        metadata: Dict[str, Any] = None,
    ):
        self.workflow_id = workflow_id
        self.name = name
        self.description = description
        self.trigger_event = trigger_event
        self.nodes = nodes
        self.tenant_id = tenant_id
        self.enabled = enabled
        self.metadata = metadata or {}
        self.created_at = datetime.now(timezone.utc).isoformat()

    def get_start_node(self) -> Optional[WorkflowNode]:
        """Get the start node of the workflow."""
        for node in self.nodes.values():
            if node.node_type == NodeType.START:
                return node
        return None

    def get_node(self, node_id: str) -> Optional[WorkflowNode]:
        """Get a node by ID."""
        return self.nodes.get(node_id)

    def to_dict(self) -> Dict:
        return {
            "workflow_id": self.workflow_id,
            "name": self.name,
            "description": self.description,
            "trigger_event": self.trigger_event.value,
            "nodes": {k: v.to_dict() for k, v in self.nodes.items()},
            "tenant_id": self.tenant_id,
            "enabled": self.enabled,
            "metadata": self.metadata,
            "created_at": self.created_at,
        }


# --- Workflow Instance ---

class WorkflowInstance:
    """A running instance of a workflow."""

    def __init__(
        self,
        instance_id: str,
        workflow_id: str,
        lead_id: str,
        tenant_id: str,
        current_node_id: Optional[str] = None,
        status: str = "running",
        context: Dict[str, Any] = None,
        retry_count: int = 0,
        error: Optional[str] = None,
    ):
        self.instance_id = instance_id
        self.workflow_id = workflow_id
        self.lead_id = lead_id
        self.tenant_id = tenant_id
        self.current_node_id = current_node_id
        self.status = status  # running, completed, failed, waiting, paused
        self.context = context or {}
        self.retry_count = retry_count
        self.error = error
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.completed_at: Optional[str] = None

    def to_dict(self) -> Dict:
        return {
            "instance_id": self.instance_id,
            "workflow_id": self.workflow_id,
            "lead_id": self.lead_id,
            "tenant_id": self.tenant_id,
            "current_node_id": self.current_node_id,
            "status": self.status,
            "context": self.context,
            "retry_count": self.retry_count,
            "error": self.error,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }


# --- Workflow Engine ---

class WorkflowEngine:
    """
    Executes workflows with full orchestration.

    Features:
    - Node execution with type-specific handlers
    - Conditional branching
    - Retry with exponential backoff
    - Event-driven triggers
    - Parallel execution
    - Context passing between nodes
    """

    # Redis keys
    INSTANCE_KEY = "workflow:instance:"
    DEFINITION_KEY = "workflow:def:"
    QUEUE_KEY = "workflow:queue"

    def __init__(self, db: Session):
        self.db = db
        self.redis = redis_service
        self._node_handlers: Dict[NodeType, Callable] = {
            NodeType.START: self._handle_start,
            NodeType.END: self._handle_end,
            NodeType.SEND_SMS: self._handle_send_sms,
            NodeType.WAIT: self._handle_wait,
            NodeType.AI_CLASSIFY: self._handle_ai_classify,
            NodeType.ASSIGN_AGENT: self._handle_assign_agent,
            NodeType.BOOK_APPOINTMENT: self._handle_book_appointment,
            NodeType.UPDATE_LEAD: self._handle_update_lead,
            NodeType.CONDITION: self._handle_condition,
            NodeType.WEBHOOK: self._handle_webhook,
            NodeType.DELAY: self._handle_delay,
        }

    # --- Workflow Management ---

    def register_workflow(self, definition: WorkflowDefinition) -> bool:
        """Register a workflow definition."""
        data = json.dumps(definition.to_dict())
        self.redis.client.set(f"{self.DEFINITION_KEY}{definition.workflow_id}", data)
        logger.info(f"Registered workflow: {definition.name}")
        return True

    def get_workflow(self, workflow_id: str) -> Optional[WorkflowDefinition]:
        """Get a workflow definition."""
        data = self.redis.client.get(f"{self.DEFINITION_KEY}{workflow_id}")
        if data:
            return self._parse_workflow(json.loads(data))
        return None

    # --- Instance Management ---

    def start_workflow(
        self,
        workflow_id: str,
        lead_id: str,
        tenant_id: str,
        context: Dict[str, Any] = None,
    ) -> Optional[WorkflowInstance]:
        """
        Start a new workflow instance.

        Args:
            workflow_id: Workflow definition ID
            lead_id: Lead UUID
            tenant_id: Tenant ID
            context: Initial context data

        Returns:
            WorkflowInstance or None
        """
        definition = self.get_workflow(workflow_id)
        if not definition:
            logger.error(f"Workflow not found: {workflow_id}")
            return None

        if not definition.enabled:
            logger.warning(f"Workflow disabled: {workflow_id}")
            return None

        start_node = definition.get_start_node()
        if not start_node:
            logger.error(f"No start node in workflow: {workflow_id}")
            return None

        instance = WorkflowInstance(
            instance_id=str(uuid4()),
            workflow_id=workflow_id,
            lead_id=lead_id,
            tenant_id=tenant_id,
            current_node_id=start_node.node_id,
            context=context or {},
        )

        # Save instance
        self._save_instance(instance)

        # Execute first node
        self._execute_next(instance, definition)

        return instance

    def execute_node(self, instance_id: str) -> Optional[WorkflowInstance]:
        """
        Execute the current node of a workflow instance.

        Called when a node completes or a wait expires.
        """
        instance = self._load_instance(instance_id)
        if not instance:
            return None

        definition = self.get_workflow(instance.workflow_id)
        if not definition:
            instance.status = "failed"
            instance.error = "Workflow definition not found"
            self._save_instance(instance)
            return instance

        self._execute_next(instance, definition)
        return instance

    # --- Node Execution ---

    def _execute_next(self, instance: WorkflowInstance, definition: WorkflowDefinition) -> None:
        """Execute the current node and move to next."""
        current_node = definition.get_node(instance.current_node_id)
        if not current_node:
            instance.status = "completed"
            instance.completed_at = datetime.now(timezone.utc).isoformat()
            self._save_instance(instance)
            return

        # Execute current node
        try:
            result = self._execute_node(current_node, instance)

            if result.get("status") == "waiting":
                instance.status = "waiting"
                self._save_instance(instance)
                return

            if result.get("status") == "failed":
                # Check retry policy
                if current_node.retry_policy.should_retry(
                    instance.retry_count, result.get("error_type", "error")
                ):
                    instance.retry_count += 1
                    delay = current_node.retry_policy.get_delay(instance.retry_count)
                    self._schedule_retry(instance, delay)
                    return
                else:
                    instance.status = "failed"
                    instance.error = result.get("error", "Node execution failed")
                    self._save_instance(instance)
                    return

            # Move to next node
            next_nodes = result.get("next_nodes", current_node.next_nodes)
            if next_nodes:
                instance.current_node_id = next_nodes[0]
                instance.retry_count = 0
                self._save_instance(instance)
                # Continue execution
                self._execute_next(instance, definition)
            else:
                instance.status = "completed"
                instance.completed_at = datetime.now(timezone.utc).isoformat()
                self._save_instance(instance)

        except Exception as e:
            logger.error(f"Node execution error: {e}")
            instance.status = "failed"
            instance.error = str(e)
            self._save_instance(instance)

    def _execute_node(self, node: WorkflowNode, instance: WorkflowInstance) -> Dict:
        """Execute a single node."""
        handler = self._node_handlers.get(node.node_type)
        if not handler:
            return {"status": "failed", "error": f"No handler for node type: {node.node_type}"}

        return handler(node, instance)

    # --- Node Handlers ---

    def _handle_start(self, node: WorkflowNode, instance: WorkflowInstance) -> Dict:
        """Handle start node."""
        return {"status": "success", "next_nodes": node.next_nodes}

    def _handle_end(self, node: WorkflowNode, instance: WorkflowInstance) -> Dict:
        """Handle end node."""
        return {"status": "success", "next_nodes": []}

    def _handle_send_sms(self, node: WorkflowNode, instance: WorkflowInstance) -> Dict:
        """Handle send SMS node."""
        from app.ai.services.communication_provider import send_sms_to_lead
        from app.models.lead import Lead

        message_template = node.config.get("message", "")
        message = self._interpolate(message_template, instance.context)

        lead = self.db.query(Lead).filter(Lead.id == instance.lead_id).first()
        if not lead:
            return {"status": "failed", "error": "Lead not found"}

        try:
            result = send_sms_to_lead(
                phone=lead.phone,
                lead_id=str(lead.id),
                message=message,
                tenant_id=instance.tenant_id,
            )

            if result.get("success"):
                instance.context["last_sms_sent"] = datetime.now(timezone.utc).isoformat()
                instance.context["last_sms_provider"] = result.get("provider")
                instance.context["last_sms_message_sid"] = result.get("message_sid")
                return {"status": "success", "next_nodes": node.next_nodes}
            else:
                return {"status": "failed", "error": result.get("error") or "SMS send failed", "error_type": "error"}

        except Exception as e:
            return {"status": "failed", "error": str(e), "error_type": "error"}

    def _handle_wait(self, node: WorkflowNode, instance: WorkflowInstance) -> Dict:
        """Handle wait node."""
        duration_seconds = node.config.get("duration_seconds", 3600)

        # Schedule continuation
        execute_at = datetime.now(timezone.utc) + timedelta(seconds=duration_seconds)
        self.redis.client.zadd(
            self.QUEUE_KEY,
            {instance.instance_id: execute_at.timestamp()},
        )

        return {"status": "waiting"}

    def _handle_ai_classify(self, node: WorkflowNode, instance: WorkflowInstance) -> Dict:
        """Handle AI classify node."""
        from app.intent.services.classifier import classify_intent

        text = instance.context.get("last_message", "")
        if not text:
            return {"status": "failed", "error": "No message to classify"}

        try:
            import asyncio
            result = asyncio.run(classify_intent(text=text))
            intent = result.intent.value if result else "unknown"
            confidence = result.confidence if result else 0

            instance.context["classified_intent"] = intent
            instance.context["intent_confidence"] = confidence

            # Route based on intent
            if node.condition:
                for branch in node.condition.get("branches", []):
                    if intent in branch.get("intents", []):
                        return {"status": "success", "next_nodes": [branch["node_id"]]}

            return {"status": "success", "next_nodes": node.next_nodes}

        except Exception as e:
            return {"status": "failed", "error": str(e), "error_type": "error"}

    def _handle_assign_agent(self, node: WorkflowNode, instance: WorkflowInstance) -> Dict:
        """Handle assign agent node."""
        from app.booking.services.assignment import assign_agent

        try:
            now = datetime.now(timezone.utc)
            end_time = now + timedelta(minutes=15)

            agent = assign_agent(
                self.db, instance.tenant_id, now, end_time
            )

            if agent:
                instance.context["assigned_agent_id"] = str(agent.id)
                return {"status": "success", "next_nodes": node.next_nodes}
            else:
                return {"status": "failed", "error": "No agents available", "error_type": "error"}

        except Exception as e:
            return {"status": "failed", "error": str(e), "error_type": "error"}

    def _handle_book_appointment(self, node: WorkflowNode, instance: WorkflowInstance) -> Dict:
        """Handle book appointment node."""
        from app.models.appointment import Appointment

        agent_id = instance.context.get("assigned_agent_id")
        if not agent_id:
            return {"status": "failed", "error": "No agent assigned"}

        try:
            now = datetime.now(timezone.utc)
            appointment = Appointment(
                tenant_id=instance.tenant_id,
                lead_id=UUID(instance.lead_id),
                agent_id=UUID(agent_id),
                start_time=now,
                end_time=now + timedelta(minutes=15),
                status="confirmed",
                booking_source="workflow",
            )
            self.db.add(appointment)
            self.db.commit()

            instance.context["appointment_id"] = str(appointment.id)
            return {"status": "success", "next_nodes": node.next_nodes}

        except Exception as e:
            return {"status": "failed", "error": str(e), "error_type": "error"}

    def _handle_update_lead(self, node: WorkflowNode, instance: WorkflowInstance) -> Dict:
        """Handle update lead node."""
        from app.models.lead import Lead

        field = node.config.get("field")
        value = node.config.get("value")

        if not field:
            return {"status": "failed", "error": "No field specified"}

        # Interpolate value
        value = self._interpolate(str(value), instance.context)

        lead = self.db.query(Lead).filter(Lead.id == instance.lead_id).first()
        if not lead:
            return {"status": "failed", "error": "Lead not found"}

        try:
            setattr(lead, field, value)
            self.db.commit()
            return {"status": "success", "next_nodes": node.next_nodes}

        except Exception as e:
            return {"status": "failed", "error": str(e), "error_type": "error"}

    def _handle_condition(self, node: WorkflowNode, instance: WorkflowInstance) -> Dict:
        """Handle condition node (branching)."""
        condition = node.condition or {}
        field = condition.get("field")
        operator = condition.get("operator", "equals")
        value = condition.get("value")
        branches = condition.get("branches", {})

        # Get field value from context
        actual = instance.context.get(field)

        # Evaluate condition
        result = False
        if operator == "equals":
            result = actual == value
        elif operator == "not_equals":
            result = actual != value
        elif operator == "contains":
            result = value in str(actual) if actual else False
        elif operator == "greater_than":
            result = (actual or 0) > value
        elif operator == "less_than":
            result = (actual or 0) < value
        elif operator == "in":
            result = actual in value if value else False

        # Route to appropriate branch
        if result:
            next_node = branches.get("true", node.next_nodes[0] if node.next_nodes else None)
        else:
            next_node = branches.get("false", node.next_nodes[1] if len(node.next_nodes) > 1 else None)

        return {"status": "success", "next_nodes": [next_node] if next_node else []}

    def _handle_webhook(self, node: WorkflowNode, instance: WorkflowInstance) -> Dict:
        """Handle webhook node."""
        import httpx

        url = node.config.get("url")
        method = node.config.get("method", "POST")
        headers = node.config.get("headers", {})
        body = node.config.get("body", {})

        # Interpolate
        url = self._interpolate(url, instance.context)
        body = self._interpolate(json.dumps(body), instance.context)

        try:
            with httpx.Client(timeout=30) as client:
                if method == "GET":
                    response = client.get(url, headers=headers)
                else:
                    response = client.post(url, headers=headers, json=json.loads(body))

                instance.context["webhook_status"] = response.status_code
                instance.context["webhook_response"] = response.text[:500]

                if response.status_code < 400:
                    return {"status": "success", "next_nodes": node.next_nodes}
                else:
                    return {"status": "failed", "error": f"Webhook returned {response.status_code}", "error_type": "error"}

        except Exception as e:
            return {"status": "failed", "error": str(e), "error_type": "timeout"}

    def _handle_delay(self, node: WorkflowNode, instance: WorkflowInstance) -> Dict:
        """Handle delay node (short wait)."""
        return self._handle_wait(node, instance)

    # --- Helpers ---

    def _interpolate(self, template: str, context: Dict) -> str:
        """Interpolate template with context variables."""
        result = template
        for key, value in context.items():
            result = result.replace(f"{{{key}}}", str(value))
        return result

    def _save_instance(self, instance: WorkflowInstance) -> None:
        """Save instance to Redis."""
        self.redis.client.set(
            f"{self.INSTANCE_KEY}{instance.instance_id}",
            json.dumps(instance.to_dict()),
            ex=86400,  # 24h TTL
        )

    def _load_instance(self, instance_id: str) -> Optional[WorkflowInstance]:
        """Load instance from Redis."""
        data = self.redis.client.get(f"{self.INSTANCE_KEY}{instance_id}")
        if data:
            d = json.loads(data)
            return WorkflowInstance(
                instance_id=d["instance_id"],
                workflow_id=d["workflow_id"],
                lead_id=d["lead_id"],
                tenant_id=d["tenant_id"],
                current_node_id=d.get("current_node_id"),
                status=d.get("status", "running"),
                context=d.get("context", {}),
                retry_count=d.get("retry_count", 0),
                error=d.get("error"),
            )
        return None

    def _schedule_retry(self, instance: WorkflowInstance, delay_seconds: int) -> None:
        """Schedule a retry for a failed node."""
        execute_at = datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)
        self.redis.client.zadd(
            self.QUEUE_KEY,
            {instance.instance_id: execute_at.timestamp()},
        )
        instance.status = "waiting"
        self._save_instance(instance)

    def _parse_workflow(self, data: Dict) -> WorkflowDefinition:
        """Parse workflow definition from dict."""
        nodes = {}
        for node_id, node_data in data.get("nodes", {}).items():
            retry_data = node_data.get("retry_policy", {})
            retry_policy = RetryPolicy(
                max_retries=retry_data.get("max_retries", 3),
                initial_delay_seconds=retry_data.get("initial_delay_seconds", 60),
                backoff_multiplier=retry_data.get("backoff_multiplier", 2.0),
            )

            nodes[node_id] = WorkflowNode(
                node_id=node_id,
                node_type=NodeType(node_data["node_type"]),
                config=node_data.get("config", {}),
                next_nodes=node_data.get("next_nodes", []),
                condition=node_data.get("condition"),
                retry_policy=retry_policy,
            )

        return WorkflowDefinition(
            workflow_id=data["workflow_id"],
            name=data["name"],
            description=data["description"],
            trigger_event=EventType(data["trigger_event"]),
            nodes=nodes,
            tenant_id=data["tenant_id"],
            enabled=data.get("enabled", True),
            metadata=data.get("metadata", {}),
        )

    # --- Event Handling ---

    def handle_event(self, event_type: EventType, lead_id: str, tenant_id: str, data: Dict = None) -> List[WorkflowInstance]:
        """
        Handle an event by triggering matching workflows.

        Args:
            event_type: Type of event
            lead_id: Lead UUID
            tenant_id: Tenant ID
            data: Event data

        Returns:
            List of started workflow instances
        """
        instances = []

        # Find workflows matching this event
        for key in self.redis.client.keys(f"{self.DEFINITION_KEY}*"):
            workflow_data = self.redis.client.get(key)
            if workflow_data:
                definition = self._parse_workflow(json.loads(workflow_data))
                if definition.trigger_event == event_type and definition.enabled and definition.tenant_id == tenant_id:
                    instance = self.start_workflow(
                        workflow_id=definition.workflow_id,
                        lead_id=lead_id,
                        tenant_id=tenant_id,
                        context=data or {},
                    )
                    if instance:
                        instances.append(instance)

        return instances

    def process_queue(self) -> int:
        """Process delayed workflow queue."""
        now = datetime.now(timezone.utc).timestamp()

        # Get ready items
        ready = self.redis.client.zrangebyscore(self.QUEUE_KEY, 0, now, start=0, num=10)

        processed = 0
        for instance_id in ready:
            self.redis.client.zrem(self.QUEUE_KEY, instance_id)
            instance = self.execute_node(instance_id)
            if instance:
                processed += 1

        return processed

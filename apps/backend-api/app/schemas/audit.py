from pydantic import BaseModel
from typing import Optional, Dict, Any
from uuid import UUID
from datetime import datetime


class AuditLogResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    user_id: Optional[UUID]
    action: str
    resource_type: str
    resource_id: Optional[UUID]
    details: Dict[str, Any]
    ip_address: Optional[str]
    user_agent: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True

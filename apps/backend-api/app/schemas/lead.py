from pydantic import BaseModel
from typing import Optional, List
from uuid import UUID
from datetime import datetime


class LeadCreate(BaseModel):
    source: str
    first_name: str
    last_name: str
    phone: str
    email: Optional[str] = None
    state: Optional[str] = None
    city: Optional[str] = None
    zip_code: Optional[str] = None
    timezone: Optional[str] = None
    campaign_id: Optional[UUID] = None
    tags: List[str] = []
    custom_fields: dict = {}


class LeadUpdate(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    state: Optional[str] = None
    city: Optional[str] = None
    zip_code: Optional[str] = None
    timezone: Optional[str] = None
    status: Optional[str] = None
    ai_status: Optional[str] = None  # active | paused | stopped | escalated
    lead_score: Optional[int] = None
    campaign_id: Optional[UUID] = None
    tags: Optional[List[str]] = None
    custom_fields: Optional[dict] = None


class LeadResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    source: str
    first_name: str
    last_name: str
    phone: str
    email: Optional[str]
    state: Optional[str]
    city: Optional[str]
    zip_code: Optional[str]
    timezone: Optional[str]
    lead_score: int
    status: str
    ai_status: Optional[str] = None
    campaign_id: Optional[UUID]
    assigned_agent_id: Optional[UUID] = None
    tags: List[str]
    custom_fields: dict
    last_contacted_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

from pydantic import BaseModel, Field
from typing import Optional, Any
from uuid import UUID
from datetime import datetime
from decimal import Decimal


class AppointmentCreate(BaseModel):
    lead_id: UUID
    agent_id: UUID
    start_time: datetime
    end_time: datetime
    conversation_id: Optional[UUID] = None


class AppointmentUpdate(BaseModel):
    status: Optional[str] = None
    disposition: Optional[str] = None
    notes: Optional[str] = None
    call_duration_seconds: Optional[int] = None


class AppointmentResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    lead_id: UUID
    agent_id: UUID
    conversation_id: Optional[UUID]
    start_time: datetime
    end_time: datetime
    status: str
    disposition: Optional[str]
    notes: Optional[str]
    call_duration_seconds: Optional[int]
    reminder_24h_sent: bool
    reminder_1h_sent: bool
    reminder_15m_sent: bool
    cancelled_reason: Optional[str]
    rescheduled_from: Optional[UUID]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class DispositionOption(BaseModel):
    key: str
    label: str
    description: str
    outcome_category: str
    customer_picked_up: bool
    insurance_sold: bool


class AppointmentDispositionCreate(BaseModel):
    disposition_key: str
    customer_picked_up: Optional[bool] = None
    insurance_sold: Optional[bool] = None
    notes: Optional[str] = Field(default=None, max_length=4000)
    call_duration_seconds: Optional[int] = Field(default=None, ge=0)
    sale_carrier: Optional[str] = Field(default=None, max_length=120)
    sale_product: Optional[str] = Field(default=None, max_length=120)
    premium_amount: Optional[Decimal] = Field(default=None, ge=0)
    policy_number: Optional[str] = Field(default=None, max_length=120)
    extra: Optional[dict[str, Any]] = None


class AppointmentDispositionResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    appointment_id: UUID
    lead_id: UUID
    agent_id: UUID
    submitted_by_user_id: Optional[UUID]
    disposition_key: str
    disposition_label: str
    outcome_category: str
    customer_picked_up: bool
    insurance_sold: bool
    customer_name: str
    customer_phone: str
    appointment_start_time: datetime
    appointment_end_time: datetime
    agent_name: Optional[str]
    notes: Optional[str]
    call_duration_seconds: Optional[int]
    sale_carrier: Optional[str]
    sale_product: Optional[str]
    premium_amount: Optional[Decimal]
    policy_number: Optional[str]
    extra: Optional[dict[str, Any]]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

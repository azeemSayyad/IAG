from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


VALID_US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "IA",
    "ID", "IL", "IN", "KS", "KY", "LA", "MA", "MD", "ME", "MI", "MN", "MO",
    "MS", "MT", "NC", "ND", "NE", "NH", "NJ", "NM", "NV", "NY", "OH", "OK",
    "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VA", "VT", "WA", "WI",
    "WV", "WY", "DC",
}


def normalize_state(value: str) -> str:
    state = (value or "").strip().upper()
    if state not in VALID_US_STATES:
        raise ValueError("state_code must be a valid US state abbreviation")
    return state


class NpnUpdate(BaseModel):
    npn: str = Field(min_length=1, max_length=50)

    @field_validator("npn")
    @classmethod
    def strip_npn(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("npn must not be empty")
        return v


class StateLicenseCreate(BaseModel):
    agent_id: UUID
    state_code: str
    license_number: str
    # Effective date is no longer collected in the UI — default to today server-side
    # (the column is NOT NULL), mirroring carrier appointments.
    effective_date: Optional[date] = None
    expiration_date: Optional[date] = None
    status: str = "ACTIVE"

    @field_validator("state_code")
    @classmethod
    def valid_state(cls, value: str) -> str:
        return normalize_state(value)

    @field_validator("status")
    @classmethod
    def normalize_status(cls, value: str) -> str:
        return (value or "ACTIVE").strip().upper()


class StateLicenseUpdate(BaseModel):
    license_number: Optional[str] = None
    effective_date: Optional[date] = None
    expiration_date: Optional[date] = None
    status: Optional[str] = None

    @field_validator("status")
    @classmethod
    def normalize_status(cls, value: Optional[str]) -> Optional[str]:
        return value.strip().upper() if value else value


class StateLicenseResponse(StateLicenseCreate):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    created_at: datetime
    updated_at: datetime


class CarrierAppointmentCreate(BaseModel):
    agent_id: UUID
    carrier_name: str = Field(min_length=1, max_length=120)
    state_code: str
    appointment_number: Optional[str] = None
    # Carrier appointments have no effective date (that concept belongs to state
    # licenses). Optional so the form can omit it; the service defaults it.
    effective_date: Optional[date] = None
    expiration_date: Optional[date] = None
    status: str = "ACTIVE"

    @field_validator("state_code")
    @classmethod
    def valid_state(cls, value: str) -> str:
        return normalize_state(value)

    @field_validator("status")
    @classmethod
    def normalize_status(cls, value: str) -> str:
        return (value or "ACTIVE").strip().upper()


class CarrierAppointmentUpdate(BaseModel):
    carrier_name: Optional[str] = None
    appointment_number: Optional[str] = None
    effective_date: Optional[date] = None
    expiration_date: Optional[date] = None
    status: Optional[str] = None

    @field_validator("status")
    @classmethod
    def normalize_status(cls, value: Optional[str]) -> Optional[str]:
        return value.strip().upper() if value else value


class CarrierAppointmentResponse(CarrierAppointmentCreate):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    carrier_key: str
    created_at: datetime
    updated_at: datetime


class DealProduct(BaseModel):
    """One product line for a person's deal, with full plan detail."""
    product: str = Field(min_length=1, max_length=40)   # ACA | Dental | Vision
    carrier: Optional[str] = Field(default=None, max_length=120)   # Dental/Vision have no carrier
    tier: Optional[str] = None
    plan_name: Optional[str] = None
    premium: Optional[Decimal] = None
    effective_date: Optional[str] = None


class DealSubmitRequest(BaseModel):
    agent_id: UUID
    lead_id: Optional[UUID] = None
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    customer_dob: Optional[str] = None
    customer_email: Optional[str] = None
    customer_address: Optional[str] = None
    customer_city: Optional[str] = None
    customer_zip: Optional[str] = None
    customer_gender: Optional[str] = None
    customer_marital_status: Optional[str] = None
    customer_tobacco: Optional[str] = None
    customer_income: Optional[str] = None
    customer_ssn: Optional[str] = None
    # carrier is optional: in the multi-product (per-person) flow each product
    # carries its own carrier, and the service derives the top-level one.
    carrier: Optional[str] = Field(default=None, max_length=120)
    state: str
    plan_type: Optional[str] = None
    premium: Optional[Decimal] = None
    # Single-product counts (kept for back-compat). In multi-product mode the
    # service derives these 0/1 flags from `products`.
    aca_count: int = Field(default=1, ge=0)
    dental_count: int = Field(default=0, ge=0)
    vision_count: int = Field(default=0, ge=0)
    # Full per-product plan detail for THIS person (one deal per person).
    products: Optional[List[DealProduct]] = None
    # Call recording uploaded on the form before submit (compliance gate).
    # Optional here so existing/non-form callers stay valid; the frontend gate
    # is what enforces it.
    recording_id: Optional[UUID] = None
    # Up to 4 recordings; the first is the primary recording_id. The frontend gate
    # requires at least one. Optional here so non-form callers stay valid.
    recording_ids: Optional[List[UUID]] = None

    @field_validator("state")
    @classmethod
    def valid_state(cls, value: str) -> str:
        return normalize_state(value)


class DealRevalidateRequest(BaseModel):
    agent_id: Optional[UUID] = None
    carrier: Optional[str] = Field(default=None, min_length=1, max_length=120)
    state: Optional[str] = None

    @field_validator("state")
    @classmethod
    def valid_optional_state(cls, value: Optional[str]) -> Optional[str]:
        return normalize_state(value) if value else value


class DealResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    agent_id: UUID
    lead_id: Optional[UUID]
    customer_name: Optional[str]
    customer_phone: Optional[str] = None
    carrier: str
    state: str
    plan_type: Optional[str]
    premium: Optional[Decimal]
    aca_count: int = 1
    dental_count: int = 0
    vision_count: int = 0
    status: str
    approval_decision: Optional[str]
    approval_reason: Optional[str]
    submitted_at: datetime
    created_at: datetime


class DealApprovalLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    deal_id: UUID
    agent_id: UUID
    carrier: str
    state: str
    decision: str
    reason: str
    created_at: datetime


class ComplianceEventCreate(BaseModel):
    agent_id: Optional[UUID] = None
    appointment_id: Optional[UUID] = None
    deal_id: Optional[UUID] = None
    event_type: str
    carrier: Optional[str] = None
    state: Optional[str] = None
    message: str
    severity: str = "info"

    @field_validator("state")
    @classmethod
    def valid_optional_state(cls, value: Optional[str]) -> Optional[str]:
        return normalize_state(value) if value else value


class ComplianceEventResponse(ComplianceEventCreate):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    resolved: bool
    created_at: datetime


class ApprovalDecisionResponse(BaseModel):
    decision: str
    reason: str
    deal: DealResponse
    approval_log: DealApprovalLogResponse


class ComplianceDashboardResponse(BaseModel):
    appointments_expiring_60d: int
    appointments_expired: int
    agents_missing_access: int
    compliance_alerts: int
    high_risk_deals: int
    approval_rate: float


class ComplianceAnalyticsResponse(BaseModel):
    total_decisions: int
    approved: int
    not_approved: int
    flagged: int
    approval_rate: float
    carrier_approval: dict
    state_approval: dict
    compliance_violations: int
    expired_appointments: int
    high_risk_deals: int

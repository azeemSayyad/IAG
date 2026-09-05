"""Work contact book schemas. Only `name` is required."""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _clean(v: Optional[str]) -> Optional[str]:
    v = (v or "").strip()
    return v or None


class ContactCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    phone: Optional[str] = Field(default=None, max_length=40)
    email: Optional[str] = Field(default=None, max_length=255)
    role: Optional[str] = Field(default=None, max_length=80)
    notes: Optional[str] = None

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("name is required")
        return v

    @field_validator("phone", "email", "role", "notes")
    @classmethod
    def blank_to_none(cls, v: Optional[str]) -> Optional[str]:
        return _clean(v)


class ContactUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    phone: Optional[str] = Field(default=None, max_length=40)
    email: Optional[str] = Field(default=None, max_length=255)
    role: Optional[str] = Field(default=None, max_length=80)
    notes: Optional[str] = None

    @field_validator("phone", "email", "role", "notes")
    @classmethod
    def blank_to_none(cls, v: Optional[str]) -> Optional[str]:
        return _clean(v)


class ContactResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    phone: Optional[str] = None
    email: Optional[str] = None
    role: Optional[str] = None
    notes: Optional[str] = None
    created_at: Optional[datetime] = None

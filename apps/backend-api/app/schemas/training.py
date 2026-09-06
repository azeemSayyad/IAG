"""Training program schemas."""

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _clean(v: Optional[str]) -> Optional[str]:
    v = (v or "").strip()
    return v or None


class TrainingStepCreate(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    description: Optional[str] = None
    content: Optional[str] = None
    video_url: Optional[str] = None
    # Insert position (0-based); omitted → appended at the end.
    position: Optional[int] = Field(default=None, ge=0)

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("title is required")
        return v

    @field_validator("description", "content", "video_url")
    @classmethod
    def blank_to_none(cls, v: Optional[str]) -> Optional[str]:
        return _clean(v)


class TrainingStepUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=160)
    description: Optional[str] = None
    content: Optional[str] = None
    # Setting a link replaces an uploaded video; sending "" clears the link.
    video_url: Optional[str] = None

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip()
        if not v:
            raise ValueError("title cannot be blank")
        return v


class TrainingReorder(BaseModel):
    ids: list[UUID] = Field(min_length=1)


class TrainingStepResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    position: int
    title: str
    description: Optional[str] = None
    content: Optional[str] = None
    video_kind: Literal["none", "link", "upload"]
    video_url: Optional[str] = None
    video_filename: Optional[str] = None
    video_content_type: Optional[str] = None
    video_byte_size: int = 0
    # 's3' | 'db' for uploads — tells an admin whether the file is in the bucket.
    video_storage: Optional[str] = None
    # Playable URL for uploaded videos (the streaming endpoint); None otherwise.
    video_src: Optional[str] = None
    updated_at: Optional[datetime] = None

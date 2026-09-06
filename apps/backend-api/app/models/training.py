"""Agent training program — an ordered list of admin-editable steps.

Each step is a card in the Training page: a title, an optional blurb, a video
(either an external link such as Vimeo/YouTube, or a file uploaded to our S3
bucket — DB bytes as the no-S3 fallback, like deal recordings), and free-form
"script" content rendered under the video (the call script lives here).

Soft-deleted (`deleted_at`) so an admin can undo a removal. Ordered by
`position`, which the reorder endpoint rewrites as a whole.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, LargeBinary, String, Text
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


class TrainingStep(Base):
    __tablename__ = "training_steps"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)

    position = Column(Integer, nullable=False, default=0, server_default="0")
    title = Column(String(160), nullable=False)
    description = Column(Text, nullable=True)
    # Line-based script markup rendered under the video (see the frontend
    # renderer): "[Agent]: ..." quote cards, "> " responses, "1. " checklists…
    content = Column(Text, nullable=True)

    # 'none' | 'link' | 'upload'
    video_kind = Column(String(10), nullable=False, default="none", server_default="none")
    video_url = Column(Text, nullable=True)                 # external link (link)
    video_filename = Column(String(255), nullable=True)     # uploaded file (upload)
    video_content_type = Column(String(100), nullable=True)
    video_byte_size = Column(Integer, nullable=False, default=0, server_default="0")
    video_storage = Column(String(10), nullable=True)       # 's3' | 'db'
    video_s3_bucket = Column(String(255), nullable=True)
    video_s3_key = Column(String(512), nullable=True)
    video_data = Column(LargeBinary, nullable=True)         # DB fallback only

    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc),
    )
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("idx_training_steps_tenant_position", "tenant_id", "position"),
    )

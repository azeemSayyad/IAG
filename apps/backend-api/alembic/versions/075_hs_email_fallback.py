"""HealthSherpa campaigns: wrong phone -> confirm email -> send the link by email

Revision ID: 075
Revises: 074
Create Date: 2026-11-16

Owner (2026-11-16): when HealthSherpa's mobile differs from our sheet, the
extension copies the email HealthSherpa has and stops; we text the client to
confirm it (10 min), then the link goes by HealthSherpa's email button.
hs_auth_requests: hs_email, email_asked_at, email_confirmed_at, send_by. Idempotent.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "075"
down_revision: Union[str, None] = "074"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

COLS = (
    ("hs_email", sa.String(200)),
    ("email_asked_at", sa.DateTime(timezone=True)),
    ("email_confirmed_at", sa.DateTime(timezone=True)),
    ("send_by", sa.String(10)),
)


def upgrade() -> None:
    have = {c["name"] for c in inspect(op.get_bind()).get_columns("hs_auth_requests")}
    for name, typ in COLS:
        if name not in have:
            op.add_column("hs_auth_requests", sa.Column(name, typ, nullable=True))


def downgrade() -> None:
    have = {c["name"] for c in inspect(op.get_bind()).get_columns("hs_auth_requests")}
    for name, _ in COLS:
        if name in have:
            op.drop_column("hs_auth_requests", name)

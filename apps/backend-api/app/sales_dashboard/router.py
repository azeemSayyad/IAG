"""Sales Dashboard — admin-only read endpoints."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_tenant_id, require_role
from app.models.user import User
from app.sales_dashboard import service

router = APIRouter(prefix="/sales-dashboard", tags=["sales-dashboard"])

_require_admin = require_role("tenant_admin", "super_admin", "admin")


@router.get("/overview")
def overview(
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    _user: User = Depends(_require_admin),
) -> dict:
    return service.get_overview(db, tenant_id, from_, to)

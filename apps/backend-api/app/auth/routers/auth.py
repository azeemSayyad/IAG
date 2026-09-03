from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_role
from app.core.security import (
    hash_password,
    verify_password,
    create_access_token,
    create_refresh_token,
    create_password_reset_token,
    decode_password_reset_token,
    decode_token,
    get_current_user,
    check_account_lockout,
    record_failed_login,
    reset_failed_login,
)
from app.models.user import User
from app.models.tenant import Tenant
from app.core.audit import log_login, log_create
from typing import Optional
from pydantic import BaseModel
from app.schemas.auth import (
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    RefreshRequest,
    PasswordResetRequest,
    PasswordResetConfirm,
    ChangePasswordRequest,
    UserResponse,
)


class UpdateProfileRequest(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    preferences: Optional[dict] = None
    avatar_url: Optional[str] = None  # small base64 data URL ("" clears it)

router = APIRouter(prefix="/auth", tags=["auth"])


def _normalise_email(value: str | None) -> str:
    """Emails are STORED lowercased (bootstrap_admin + admin create-user both
    call .lower()), so every lookup must lower-case too. Without this a user
    who types "Admin@iag.com" — or whose browser autofill capitalises the first
    letter — gets "Invalid credentials" for a password that is perfectly correct.
    """
    return (value or "").strip().lower()

# Self-service signup is CLOSED. This route used to be fully unauthenticated and
# handed anyone on the internet a brand-new tenant plus a tenant_admin account on
# it — a public admin-account factory the moment the API has a domain. Nothing in
# the portal calls it; real accounts are created by an existing admin through
# POST /admin/users, and the very first one by scripts/bootstrap_admin.py.
#
# It is gated to "dev" rather than deleted so the tenant-provisioning logic stays
# available for onboarding a new tenant, and so existing tests keep a way in.
@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(
    request: RegisterRequest,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("dev")),
):
    # Check if email already exists
    email = _normalise_email(request.email)
    existing = db.query(User).filter(User.email == email).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    # Create tenant
    tenant = Tenant(name=request.company_name)
    db.add(tenant)
    db.flush()

    # Create user as tenant_admin
    user = User(
        tenant_id=tenant.id,
        email=email,
        password_hash=hash_password(request.password),
        first_name=request.first_name,
        last_name=request.last_name,
        role="tenant_admin",
        status="active",
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    # Audit log
    log_create(
        tenant_id=str(tenant.id),
        user_id=str(user.id),
        resource_type="user",
        resource_id=str(user.id),
        details={"action": "register", "company": request.company_name},
    )

    token_data = {"sub": str(user.id), "tenant_id": str(user.tenant_id), "role": user.role}
    return TokenResponse(
        access_token=create_access_token(token_data),
        refresh_token=create_refresh_token(token_data),
    )


@router.post("/login", response_model=TokenResponse)
def login(request: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(
        User.email == _normalise_email(request.email), User.deleted_at.is_(None)
    ).first()
    if not user or not verify_password(request.password, user.password_hash):
        if user:
            record_failed_login(user, db)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    # Account lockout disabled per product decision — no auto-lock after failed
    # attempts. A successful login still resets the failed-attempt counter below.
    # check_account_lockout(user)

    if user.status != "active":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account inactive")

    # Reset failed attempts on successful login
    reset_failed_login(user, db)
    user.last_login_at = datetime.now(timezone.utc)
    db.commit()

    # Audit log
    log_login(tenant_id=str(user.tenant_id), user_id=str(user.id))

    token_data = {"sub": str(user.id), "tenant_id": str(user.tenant_id), "role": user.role}
    return TokenResponse(
        access_token=create_access_token(token_data),
        refresh_token=create_refresh_token(token_data),
    )


@router.post("/refresh", response_model=TokenResponse)
def refresh_token(request: RefreshRequest, db: Session = Depends(get_db)):
    payload = decode_token(request.refresh_token)
    if payload.get("type") != "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")

    user_id = payload.get("sub")
    user = db.query(User).filter(User.id == user_id, User.deleted_at.is_(None)).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    token_data = {"sub": str(user.id), "tenant_id": str(user.tenant_id), "role": user.role}
    return TokenResponse(
        access_token=create_access_token(token_data),
        refresh_token=create_refresh_token(token_data),
    )


@router.post("/password-reset-request")
def request_password_reset(request: PasswordResetRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(
        User.email == _normalise_email(request.email), User.deleted_at.is_(None)
    ).first()
    # Always return success to prevent email enumeration
    if user:
        reset_token = create_password_reset_token(user.email)
        # In production: send email with reset_token
        # For now: log it
        print(f"Password reset token for {user.email}: {reset_token}")
    return {"message": "If the email exists, a reset link has been sent."}


@router.post("/password-reset-confirm")
def confirm_password_reset(request: PasswordResetConfirm, db: Session = Depends(get_db)):
    email = _normalise_email(decode_password_reset_token(request.token))
    user = db.query(User).filter(User.email == email, User.deleted_at.is_(None)).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid reset token")

    user.password_hash = hash_password(request.new_password)
    user.failed_login_attempts = 0
    user.locked_until = None
    db.commit()

    return {"message": "Password reset successfully."}


@router.post("/change-password")
def change_password(
    request: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not verify_password(request.current_password, current_user.password_hash):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect")

    current_user.password_hash = hash_password(request.new_password)
    db.commit()

    return {"message": "Password changed successfully."}


@router.get("/me", response_model=UserResponse)
def get_me(current_user: User = Depends(get_current_user)):
    return current_user


@router.patch("/me", response_model=UserResponse)
def update_profile(
    request: UpdateProfileRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if request.first_name is not None:
        current_user.first_name = request.first_name
    if request.last_name is not None:
        current_user.last_name = request.last_name
    if request.preferences is not None:
        current_user.preferences = request.preferences
    if request.avatar_url is not None:
        av = request.avatar_url.strip()
        if av == "":
            current_user.avatar_url = None            # allow clearing the photo
        else:
            # Accept only small image data URLs. The frontend resizes to a tiny
            # square before upload, so anything over ~1.5MB is rejected to keep
            # the row (and every /auth/me response) lean.
            if not av.startswith("data:image/"):
                raise HTTPException(status_code=422, detail="avatar_url must be an image data URL")
            if len(av) > 1_500_000:
                raise HTTPException(status_code=413, detail="Image too large — please use a smaller photo")
            current_user.avatar_url = av
    db.commit()
    db.refresh(current_user)
    return current_user


@router.post("/logout")
def logout(current_user: User = Depends(get_current_user)):
    # In a production system, you'd blacklist the token in Redis
    return {"message": "Logged out successfully."}

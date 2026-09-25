"""A Head Manager may onboard and manage agents only (admin /admin/users)."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.admin.routers.admin import _assignable_roles, _ensure_can_manage


def _u(role):
    return SimpleNamespace(role=role)


def test_head_can_only_assign_agent():
    assert _assignable_roles(_u("head")) == {"agent"}


def test_admin_roles_unchanged():
    assert _assignable_roles(_u("super_admin")) == {"agent", "head", "super_admin"}
    assert "dev" in _assignable_roles(_u("dev"))


def test_head_can_manage_agent():
    _ensure_can_manage(_u("head"), _u("agent"))


@pytest.mark.parametrize("target", ["head", "super_admin", "tenant_admin", "lead", "manager"])
def test_head_cannot_manage_non_agents(target):
    with pytest.raises(HTTPException) as exc:
        _ensure_can_manage(_u("head"), _u(target))
    assert exc.value.status_code == 403


def test_super_admin_can_manage_anyone():
    _ensure_can_manage(_u("super_admin"), _u("head"))

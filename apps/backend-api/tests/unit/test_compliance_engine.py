from datetime import date, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.compliance import services
from app.core.permissions import Permission, user_has_permission
from app.models.agent import Agent
from app.models.compliance import AgentCarrierAppointment, AgentStateLicense
from app.models.user import User


class FakeQuery:
    def __init__(self, result=None, rows=None):
        self.result = result
        self.rows = rows or []

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def first(self):
        return self.result

    def all(self):
        return self.rows

    def count(self):
        return len(self.rows) if self.rows else (1 if self.result else 0)


class FakeDB:
    def __init__(self, mapping):
        self.mapping = mapping

    def query(self, model):
        value = self.mapping.get(model)
        if isinstance(value, list):
            return FakeQuery(rows=value)
        return FakeQuery(result=value)


def make_rows():
    tenant_id = str(uuid4())
    agent_id = uuid4()
    today = date.today()
    agent = SimpleNamespace(id=agent_id, tenant_id=tenant_id)
    license_row = SimpleNamespace(
        id=uuid4(),
        tenant_id=tenant_id,
        agent_id=agent_id,
        state_code="NV",
        status="ACTIVE",
        effective_date=today - timedelta(days=10),
        expiration_date=today + timedelta(days=90),
    )
    appointment = SimpleNamespace(
        id=uuid4(),
        tenant_id=tenant_id,
        agent_id=agent_id,
        carrier_name="Cigna",
        carrier_key="cigna",
        state_code="NV",
        status="ACTIVE",
        effective_date=today - timedelta(days=10),
        expiration_date=today + timedelta(days=90),
    )
    return tenant_id, agent_id, agent, license_row, appointment


def test_evaluate_deal_approves_exact_active_carrier_state():
    tenant_id, agent_id, agent, license_row, appointment = make_rows()
    db = FakeDB({
        Agent: agent,
        AgentStateLicense: license_row,
        AgentCarrierAppointment: appointment,
    })

    decision = services.evaluate_deal(db, tenant_id, agent_id, "Cigna", "NV")

    assert decision.decision == services.APPROVED
    # Approval is now license-based; the reason names the active state license.
    assert "Active NV license found" in decision.reason


def test_evaluate_deal_blocks_missing_state_license():
    tenant_id, agent_id, agent, _, appointment = make_rows()
    db = FakeDB({
        Agent: agent,
        AgentStateLicense: None,
        AgentCarrierAppointment: appointment,
    })

    decision = services.evaluate_deal(db, tenant_id, agent_id, "Cigna", "NV")

    assert decision.decision == services.NOT_APPROVED
    assert "active state license" in decision.reason


def test_evaluate_deal_approves_with_license_even_without_appointment():
    # License-only rule: an active state license approves the deal even when the
    # agent has no carrier appointment on file (the appointment no longer blocks).
    tenant_id, agent_id, agent, license_row, _ = make_rows()
    db = FakeDB({
        Agent: agent,
        AgentStateLicense: license_row,
        AgentCarrierAppointment: None,
    })

    decision = services.evaluate_deal(db, tenant_id, agent_id, "Aetna", "TX")

    assert decision.decision == services.APPROVED
    assert "Active TX license found" in decision.reason
    assert "no Aetna appointment on file" in decision.reason


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (" Cigna ", "cigna"),
        ("Blue   Cross", "blue cross"),
        ("AETNA", "aetna"),
    ],
)
def test_carrier_key_normalizes_for_matrix_matching(raw, expected):
    assert services.carrier_key(raw) == expected


def test_compliance_rbac_permissions_are_scoped_by_role():
    agent_user = User(role="agent")
    manager_user = User(role="manager")
    admin_user = User(role="tenant_admin")

    assert user_has_permission(agent_user, Permission.COMPLIANCE_READ)
    assert not user_has_permission(agent_user, Permission.COMPLIANCE_MANAGE)
    assert user_has_permission(manager_user, Permission.COMPLIANCE_READ)
    assert not user_has_permission(manager_user, Permission.COMPLIANCE_MANAGE)
    assert user_has_permission(admin_user, Permission.COMPLIANCE_READ)
    assert user_has_permission(admin_user, Permission.COMPLIANCE_MANAGE)

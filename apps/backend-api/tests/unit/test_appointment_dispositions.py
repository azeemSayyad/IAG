from datetime import datetime, timezone
from types import SimpleNamespace

from app.appointments.routers.appointments import DISPOSITION_OPTIONS, _build_disposition_pdf


def test_required_disposition_options_are_available():
    assert set(DISPOSITION_OPTIONS) == {
        "sale",
        "appointment_set",
        "medicare",
        "attempted",
        "couldnt_sell",
        "unqualified",
        "wrong_number",
    }
    assert DISPOSITION_OPTIONS["sale"]["customer_picked_up"] is True
    assert DISPOSITION_OPTIONS["sale"]["insurance_sold"] is True
    assert DISPOSITION_OPTIONS["attempted"]["customer_picked_up"] is False
    assert DISPOSITION_OPTIONS["wrong_number"]["lead_status"] == "unqualified"


def test_disposition_pdf_export_is_valid_pdf_bytes():
    row = SimpleNamespace(
        appointment_start_time=datetime(2026, 6, 3, 19, 0, tzinfo=timezone.utc),
        agent_name="Michael Agent",
        customer_name="Sai Test",
        customer_phone="+15513596301",
        disposition_label="Sale",
        customer_picked_up=True,
        insurance_sold=True,
        notes="Customer picked up and insurance was sold.",
    )

    pdf = _build_disposition_pdf("Appointment Disposition Report", [row])

    assert pdf.startswith(b"%PDF-1.4")
    assert b"Appointment Disposition Report" in pdf
    assert b"Sai Test" in pdf
    assert b"%%EOF" in pdf

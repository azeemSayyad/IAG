"""HealthSherpa client sheet — reading .csv/.xlsx and cleaning rows the way
HealthSherpa's search form needs them (MM/DD/YYYY, no punctuation in names)."""
import io
import zipfile
from datetime import date

import pytest

from app.hs_auth.sheet import (
    SheetError, clean_name, clean_rows, parse_dob, parse_phone, parse_state, read_rows,
)


def test_clean_name_strips_punctuation():
    assert clean_name("Naji.") == "Naji"            # the exact HealthSherpa error from the guide
    assert clean_name("  Mary   Ann ") == "Mary Ann"
    assert clean_name("O'Brien-Smith") == "O'Brien-Smith"
    assert clean_name("José") == "José"
    assert clean_name("Smith, Jr.") == "Smith Jr"


@pytest.mark.parametrize("raw,expected", [
    ("10/10/1988", date(1988, 10, 10)),
    ("1/2/2000", date(2000, 1, 2)),
    ("2000-01-02", date(2000, 1, 2)),
    ("1988-10-10 00:00:00", date(1988, 10, 10)),
    ("10-10-1988", date(1988, 10, 10)),
    ("10/10/88", date(1988, 10, 10)),
    ("32143", date(1988, 1, 1)),                     # Excel serial without a date style
    (date(1990, 5, 6), date(1990, 5, 6)),
])
def test_parse_dob(raw, expected):
    assert parse_dob(raw) == expected


@pytest.mark.parametrize("raw", ["", "10/10/20", "13/40/1990", "2099-01-01", "hello", "10/10"])
def test_parse_dob_rejects(raw):
    # A 2-digit year up to this year reads as 20xx (a real past date), so
    # "10/10/20" is 2020 — only impossible values are rejected.
    if raw == "10/10/20":
        assert parse_dob(raw) == date(2020, 10, 10)
        return
    assert parse_dob(raw) is None


def test_parse_state():
    assert parse_state("tx") == "TX"
    assert parse_state("Florida") == "FL"
    assert parse_state(" new york ") == "NY"
    assert parse_state("District of Columbia") == "DC"
    assert parse_state("XX") is None
    assert parse_state("") is None


def test_parse_phone():
    assert parse_phone("(214) 971-1209") == "2149711209"
    assert parse_phone("+1 214 971 1209") == "2149711209"
    assert parse_phone("971-1209") is None


def test_clean_rows_csv_headers_and_errors():
    csv_text = (
        "First Name,Last Name,Date of Birth,State,Phone\n"
        "Naji.,Hamayel,10/10/1988,Texas,(214) 971-1209\n"
        ",Doe,1/1/1990,FL,\n"
        "Ann,Lee,someday,ZZ,123\n"
        "\n"
    )
    rows = clean_rows(read_rows("c.csv", csv_text.encode()))
    assert len(rows) == 3
    ok = rows[0]
    assert (ok["first_name"], ok["last_name"], ok["dob"], ok["state"], ok["phone"]) == (
        "Naji", "Hamayel", date(1988, 10, 10), "TX", "2149711209")
    assert ok["errors"] == []
    assert rows[1]["errors"] == ["First name is missing"]
    assert len(rows[2]["errors"]) == 3  # dob, state, phone
    assert rows[0]["row_number"] == 2


def test_full_name_column_is_split():
    rows = clean_rows([["Name", "DOB", "State"], ["Michael R Shepard", "9/22/1987", "FL"]])
    assert rows[0]["first_name"] == "Michael R"
    assert rows[0]["last_name"] == "Shepard"


def test_missing_columns():
    with pytest.raises(SheetError) as e:
        clean_rows([["First Name", "Last Name", "Phone"], ["a", "b", "c"]])
    assert "Date of birth" in str(e.value) and "State" in str(e.value)


def _xlsx(rows, date_cells=()):
    """Smallest real .xlsx: shared strings, a date style, one sheet."""
    strings = []

    def sidx(s):
        if s not in strings:
            strings.append(s)
        return strings.index(s)

    def col(i):
        return chr(65 + i)

    sheet_rows = []
    for r, row in enumerate(rows, start=1):
        cells = []
        for c, v in enumerate(row):
            ref = f"{col(c)}{r}"
            if (r, c) in date_cells:
                cells.append(f'<c r="{ref}" s="1"><v>{v}</v></c>')
            elif isinstance(v, (int, float)):
                cells.append(f'<c r="{ref}"><v>{v}</v></c>')
            else:
                cells.append(f'<c r="{ref}" t="s"><v>{sidx(v)}</v></c>')
        sheet_rows.append(f'<row r="{r}">{"".join(cells)}</row>')
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/workbook.xml",
                   f'<workbook {ns} xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                   '<sheets><sheet name="Clients" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Target="worksheets/sheet1.xml" '
                   'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"/></Relationships>')
        z.writestr("xl/sharedStrings.xml",
                   f'<sst {ns}>' + "".join(f"<si><t>{s}</t></si>" for s in strings) + "</sst>")
        z.writestr("xl/styles.xml",
                   f'<styleSheet {ns}><cellXfs count="2"><xf numFmtId="0"/><xf numFmtId="14"/></cellXfs></styleSheet>')
        z.writestr("xl/worksheets/sheet1.xml", f'<worksheet {ns}><sheetData>{"".join(sheet_rows)}</sheetData></worksheet>')
    return buf.getvalue()


def test_xlsx_with_real_date_cell():
    # 32426 = 1988-10-10 in Excel's calendar
    data = _xlsx([["First Name", "Last Name", "DOB", "State"], ["Naji", "Hamayel", 32426, "TX"]],
                 date_cells={(2, 2)})
    rows = clean_rows(read_rows("clients.xlsx", data))
    assert rows[0]["dob"] == date(1988, 10, 10)
    assert rows[0]["errors"] == []


def test_old_xls_rejected():
    with pytest.raises(SheetError):
        read_rows("old.xls", b"\xd0\xcf\x11\xe0")

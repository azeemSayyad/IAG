"""Read an uploaded client sheet (.csv or .xlsx) and clean every row.

.xlsx is read with the standard library (zipfile + XML) on purpose: adding a
spreadsheet package means a new unpinned dependency on every Railway deploy.
We only need the first sheet's cell values, which is a small, stable part of
the format.

Cleaning mirrors what HealthSherpa's search form accepts:
  * names: letters, spaces, hyphens and apostrophes only — a stray period
    ("Naji.") makes HealthSherpa refuse the search;
  * date of birth: a real past date, sent to HealthSherpa as MM/DD/YYYY;
  * state: a US state or DC, stored as its 2-letter code.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from datetime import date, datetime, timedelta
from typing import Optional
from xml.etree import ElementTree as ET

STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon",
    "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia",
    "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}
_STATE_BY_NAME = {v.lower(): k for k, v in STATES.items()}

# Header spellings we accept (lower-cased, spaces/underscores/dots removed).
_HEADERS = {
    "first_name": {"firstname", "first", "fname", "givenname", "clientfirstname", "applicantfirstname"},
    "last_name": {"lastname", "last", "lname", "surname", "familyname", "clientlastname", "applicantlastname"},
    "full_name": {"name", "fullname", "clientname", "applicant", "applicantname", "client"},
    "dob": {"dob", "dateofbirth", "birthdate", "birthday", "dateofbirthmmddyyyy", "birth"},
    "state": {"state", "coveragestate", "st", "stateofresidence"},
    "phone": {"phone", "phonenumber", "mobile", "cell", "cellphone", "mobilephone", "primaryphone", "phone1"},
    "email": {"email", "emailaddress", "e-mail", "mail"},
}

MAX_ROWS = 2000


class SheetError(ValueError):
    """The file as a whole can't be used (wrong type, no header, …)."""


def _norm_header(h: str) -> str:
    return re.sub(r"[\s_.\-/()]+", "", (h or "").strip().lower())


# ---------------------------------------------------------------- reading

def read_rows(filename: str, raw: bytes) -> list[list]:
    """Return the sheet as a list of rows (first row = headers)."""
    name = (filename or "").lower()
    if name.endswith(".xlsx") or raw[:2] == b"PK":
        return _read_xlsx(raw)
    if name.endswith(".xls"):
        raise SheetError("Old .xls files aren't supported — in Excel use File → Save As → .xlsx or .csv.")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    return [row for row in csv.reader(io.StringIO(text))]


_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _col_index(ref: str) -> int:
    letters = re.match(r"[A-Z]+", ref or "A").group(0)
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _read_xlsx(raw: bytes) -> list[list]:
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        raise SheetError("This file isn't a valid Excel (.xlsx) file.")
    names = set(zf.namelist())

    shared: list[str] = []
    if "xl/sharedStrings.xml" in names:
        root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
        for si in root.findall("m:si", _NS):
            shared.append("".join(t.text or "" for t in si.iter(f"{{{_NS['m']}}}t")))

    # Which number formats are dates, so a DOB stored as a date serial reads as a date.
    date_styles: set[int] = set()
    if "xl/styles.xml" in names:
        st = ET.fromstring(zf.read("xl/styles.xml"))
        custom = {}
        for nf in st.findall("m:numFmts/m:numFmt", _NS):
            custom[int(nf.get("numFmtId"))] = nf.get("formatCode") or ""
        builtin_dates = set(range(14, 23)) | {45, 46, 47}
        xfs = st.find("m:cellXfs", _NS)
        if xfs is not None:
            for i, xf in enumerate(xfs.findall("m:xf", _NS)):
                fid = int(xf.get("numFmtId") or 0)
                code = custom.get(fid, "").lower()
                if fid in builtin_dates or (code and re.search(r"[dmy]", re.sub(r"\"[^\"]*\"|\[[^\]]*\]", "", code))):
                    date_styles.add(i)

    # First worksheet in workbook order.
    sheet_path = "xl/worksheets/sheet1.xml"
    if "xl/workbook.xml" in names and "xl/_rels/workbook.xml.rels" in names:
        wb = ET.fromstring(zf.read("xl/workbook.xml"))
        first = wb.find("m:sheets/m:sheet", _NS)
        rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
        if first is not None:
            rid = first.get(f"{{{_REL_NS}}}id")
            for rel in rels:
                if rel.get("Id") == rid:
                    target = rel.get("Target") or ""
                    target = target.lstrip("/")
                    sheet_path = target if target.startswith("xl/") else f"xl/{target}"
    if sheet_path not in names:
        raise SheetError("Couldn't find a worksheet in this Excel file.")

    root = ET.fromstring(zf.read(sheet_path))
    out: list[list] = []
    for row in root.iter(f"{{{_NS['m']}}}row"):
        values: dict[int, object] = {}
        for c in row.findall("m:c", _NS):
            idx = _col_index(c.get("r") or "A")
            t = c.get("t")
            v = c.find("m:v", _NS)
            if t == "s" and v is not None:
                val: object = shared[int(v.text)] if v.text and int(v.text) < len(shared) else ""
            elif t == "inlineStr":
                val = "".join(x.text or "" for x in c.iter(f"{{{_NS['m']}}}t"))
            elif v is None:
                val = ""
            elif t in ("str", "e", "b"):
                val = v.text or ""
            else:
                txt = v.text or ""
                style = int(c.get("s") or 0)
                if style in date_styles:
                    try:
                        val = _excel_serial_to_date(float(txt))
                    except ValueError:
                        val = txt
                else:
                    val = txt[:-2] if txt.endswith(".0") else txt
            values[idx] = val
        if values:
            width = max(values) + 1
            out.append([values.get(i, "") for i in range(width)])
    return out


def _excel_serial_to_date(serial: float) -> date:
    # Excel's day 0 is 1899-12-30 (it counts the fictitious 1900-02-29).
    return (datetime(1899, 12, 30) + timedelta(days=int(serial))).date()


# ---------------------------------------------------------------- cleaning

def clean_name(raw: object) -> str:
    s = str(raw or "").strip()
    s = re.sub(r"[^A-Za-zÀ-ÖØ-öø-ÿ' \-]", " ", s)   # periods, commas, digits… -> space
    s = re.sub(r"\s+", " ", s).strip(" -'")
    return s[:80]


def parse_dob(raw: object) -> Optional[date]:
    if isinstance(raw, date):
        d = raw
    else:
        s = str(raw or "").strip()
        if not s:
            return None
        s = s.split(" ")[0].split("T")[0]  # "1988-10-10 00:00:00" / ISO datetimes
        d = None
        if re.fullmatch(r"\d{5}(\.0+)?", s):  # Excel serial that lost its date style
            d = _excel_serial_to_date(float(s))
        else:
            for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y", "%m.%d.%Y", "%Y/%m/%d", "%m/%d/%y", "%m-%d-%y", "%m%d%Y"):
                try:
                    d = datetime.strptime(s, fmt).date()
                except ValueError:
                    continue
                if fmt.endswith("%y") and d.year > date.today().year:
                    d = d.replace(year=d.year - 100)  # "%y" put a 1980s birthday in 2080
                break
    if d is None:
        return None
    today = date.today()
    if d >= today or d.year < 1900:
        return None
    return d


def parse_state(raw: object) -> Optional[str]:
    s = re.sub(r"[^A-Za-z ]", "", str(raw or "")).strip()
    if not s:
        return None
    if len(s) == 2 and s.upper() in STATES:
        return s.upper()
    return _STATE_BY_NAME.get(s.lower())


def parse_phone(raw: object) -> Optional[str]:
    digits = re.sub(r"\D", "", str(raw or ""))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits if len(digits) == 10 else None


def parse_email(raw: object) -> Optional[str]:
    s = str(raw or "").strip()
    return s[:200] if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", s) else None


def clean_rows(rows: list[list]) -> list[dict]:
    """Map headers, clean each row. Every returned dict has `errors` (list)."""
    # Skip blank lines above the header.
    while rows and not any(str(c).strip() for c in rows[0]):
        rows = rows[1:]
    if not rows:
        raise SheetError("The file is empty.")
    header = [_norm_header(str(h)) for h in rows[0]]
    col: dict[str, int] = {}
    for key, spellings in _HEADERS.items():
        for i, h in enumerate(header):
            if h in spellings and key not in col:
                col[key] = i
    has_name = ("first_name" in col and "last_name" in col) or "full_name" in col
    missing = []
    if not has_name:
        missing.append("First name + Last name")
    if "dob" not in col:
        missing.append("Date of birth")
    if "state" not in col:
        missing.append("State")
    if missing:
        raise SheetError(
            "Missing column(s): " + ", ".join(missing)
            + ". The first row must be headers like: First Name, Last Name, DOB, State, Phone."
        )

    body = [r for r in rows[1:] if any(str(c).strip() for c in r)]
    if len(body) > MAX_ROWS:
        raise SheetError(f"Too many rows ({len(body)}). Up to {MAX_ROWS} per upload.")

    def cell(r, key):
        i = col.get(key)
        return r[i] if i is not None and i < len(r) else ""

    out = []
    for n, r in enumerate(body, start=2):
        if "first_name" in col and "last_name" in col:
            first, last = clean_name(cell(r, "first_name")), clean_name(cell(r, "last_name"))
        else:
            parts = clean_name(cell(r, "full_name")).split(" ")
            first, last = (" ".join(parts[:-1]), parts[-1]) if len(parts) > 1 else (parts[0], "")
        dob_raw, state_raw, phone_raw = cell(r, "dob"), cell(r, "state"), cell(r, "phone")
        dob, state = parse_dob(dob_raw), parse_state(state_raw)
        phone = parse_phone(phone_raw) if "phone" in col else None
        email = parse_email(cell(r, "email")) if "email" in col else None
        errors = []
        if not first:
            errors.append("First name is missing")
        if not last:
            errors.append("Last name is missing")
        if dob is None:
            errors.append(f"Date of birth '{dob_raw}' isn't a valid past date" if str(dob_raw).strip() else "Date of birth is missing")
        if state is None:
            errors.append(f"State '{state_raw}' isn't a US state" if str(state_raw).strip() else "State is missing")
        if "phone" in col and str(phone_raw).strip() and phone is None:
            errors.append(f"Phone '{phone_raw}' isn't a 10-digit US number")
        out.append({
            "row_number": n, "first_name": first, "last_name": last, "dob": dob,
            "state": state, "phone": phone, "email": email, "errors": errors,
        })
    return out

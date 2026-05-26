"""
SAP flat-file ingester. Mimics a CSV export from an ABAP report — the shape
sustainability teams typically receive from corporate IT (as opposed to live
IDoc / BAPI / OData feeds, which require an SAP system to test against;
see SOURCES.md for the choice).

Handles real-world quirks:
- Semicolon OR comma delimiters (German Excel locale defaults to ;)
- Mixed German/English headers (Werk/Plant, Menge/Quantity, etc.)
- DD.MM.YYYY (German) OR YYYY-MM-DD (ISO) dates
- German decimal comma OR English decimal point
- BOM (utf-8-sig)

Required canonical columns:
  Werk, Material, Buchungsdatum, Belegnummer, Menge, Mengeneinheit

ActivityType is NOT in the file. The parser yields each row with the raw
`Material` code; the base ingester resolves it via MaterialCodeMapping
(org-scoped). Unmapped material -> material_unknown flag.
"""
from __future__ import annotations

import csv
import io
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Iterator

from apps.emissions.models import IngestionBatch

from .base import BaseIngester, ParseError, ParsedRow


REQUIRED_COLUMNS = {
    "Werk", "Material", "Buchungsdatum", "Belegnummer", "Menge", "Mengeneinheit",
}

# English variants -> canonical German column name. Real SAP exports vary;
# this covers the most common English-localized header set.
COLUMN_ALIASES = {
    "Plant": "Werk",
    "Material_Number": "Material",
    "MaterialNumber": "Material",
    "Posting_Date": "Buchungsdatum",
    "PostingDate": "Buchungsdatum",
    "Document_Number": "Belegnummer",
    "DocumentNumber": "Belegnummer",
    "Quantity": "Menge",
    "Unit_of_Measure": "Mengeneinheit",
    "UoM": "Mengeneinheit",
    "Cost_Center": "Kostenstelle",
    "CostCenter": "Kostenstelle",
}

DATE_FORMATS = ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y")


def _parse_number(s: str) -> Decimal:
    """Handle English (1,234.56), German (1.234,56), and plain (1234.56) decimals."""
    s = s.strip()
    if "," in s and "." in s:
        # mixed: rightmost separator wins as the decimal
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")  # German with thousands
        else:
            s = s.replace(",", "")  # English with thousands
    elif "," in s:
        s = s.replace(",", ".")  # German without thousands
    return Decimal(s)


def _parse_date(s: str) -> date | None:
    s = s.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _detect_delimiter(text: str) -> str:
    first_line = text.split("\n", 1)[0]
    return ";" if first_line.count(";") > first_line.count(",") else ","


class SapFlatFileIngester(BaseIngester):
    source_system = IngestionBatch.SOURCE_SAP

    def parse(self, file_bytes: bytes) -> Iterator[ParsedRow | ParseError]:
        text = file_bytes.decode("utf-8-sig")
        delimiter = _detect_delimiter(text)
        reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)

        if reader.fieldnames is None:
            yield ParseError(0, "Empty CSV — no header row", raw_row="")
            return

        # Build observed-name -> canonical-name map
        rename = {f: COLUMN_ALIASES.get(f, f) for f in reader.fieldnames}
        canonical_seen = set(rename.values())
        missing = REQUIRED_COLUMNS - canonical_seen
        if missing:
            yield ParseError(
                0,
                f"Missing required columns: {sorted(missing)} (detected delimiter: {delimiter!r})",
                raw_row=list(reader.fieldnames),
            )
            return

        for i, raw_row in enumerate(reader, start=1):
            normalized = {rename[k]: v for k, v in raw_row.items()}
            yield self._parse_one(i, normalized, raw_row)

    def _parse_one(self, idx: int, row: dict, raw_row: dict) -> ParsedRow | ParseError:
        period = _parse_date(row["Buchungsdatum"])
        if period is None:
            return ParseError(idx, f"Unparseable Buchungsdatum: {row['Buchungsdatum']!r}", raw_row=raw_row)

        try:
            qty = _parse_number(row["Menge"])
        except (InvalidOperation, AttributeError):
            return ParseError(idx, f"Unparseable Menge: {row.get('Menge')!r}", raw_row=raw_row)

        if qty < 0:
            return ParseError(idx, f"Negative Menge: {qty}", raw_row=raw_row)

        plant_code = row["Werk"].strip()
        material_code = row["Material"].strip()
        doc_number = row["Belegnummer"].strip()
        if not doc_number:
            return ParseError(idx, "Missing Belegnummer (natural key)", raw_row=raw_row)

        return ParsedRow(
            activity_type_code=None,  # resolved by base via MaterialCodeMapping
            material_code=material_code,
            period_start=period,
            period_end=period,  # SAP postings are single-date events
            original_quantity=qty,
            original_unit=row["Mengeneinheit"].strip(),
            source_row_identifier=doc_number,
            source_payload=raw_row,
            facility_plant_code=plant_code,
        )

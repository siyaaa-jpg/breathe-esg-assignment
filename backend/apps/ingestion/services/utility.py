"""
Utility CSV ingester. Expects a bill-summary export — one row per meter per
billing period. See SOURCES.md for why we chose this shape over interval data
or PDFs.

Required columns:
  account_number, meter_id, service_address,
  billing_period_start, billing_period_end, total_kwh, unit

Optional columns (kept in source_payload, ignored at compute time):
  tariff_class, total_cost, currency

All rows in a utility CSV use ActivityType `electricity_grid`. Facility is
resolved by case-insensitive name match against `service_address`.
"""
from __future__ import annotations

import csv
import io
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Iterator

from apps.emissions.models import IngestionBatch

from .base import BaseIngester, ParseError, ParsedRow


REQUIRED_COLUMNS = {
    "account_number", "meter_id", "service_address",
    "billing_period_start", "billing_period_end",
    "total_kwh", "unit",
}


class UtilityCsvIngester(BaseIngester):
    source_system = IngestionBatch.SOURCE_UTILITY

    def parse(self, file_bytes: bytes) -> Iterator[ParsedRow | ParseError]:
        # utf-8-sig tolerates the BOM that Excel adds when exporting as CSV-UTF8.
        text = file_bytes.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))

        if reader.fieldnames is None:
            yield ParseError(0, "Empty CSV — no header row found", raw_row="")
            return

        missing = REQUIRED_COLUMNS - set(reader.fieldnames)
        if missing:
            yield ParseError(
                0,
                f"Missing required columns: {sorted(missing)}",
                raw_row=list(reader.fieldnames),
            )
            return

        for row_index, row in enumerate(reader, start=1):
            yield self._parse_one(row_index, row)

    def _parse_one(self, row_index: int, row: dict) -> ParsedRow | ParseError:
        try:
            quantity = Decimal(row["total_kwh"].strip())
        except (InvalidOperation, AttributeError, KeyError):
            return ParseError(row_index, f"Could not parse total_kwh: {row.get('total_kwh')!r}", raw_row=row)

        if quantity < 0:
            return ParseError(row_index, f"Negative consumption: {quantity}", raw_row=row)

        try:
            period_start = date.fromisoformat(row["billing_period_start"].strip())
            period_end = date.fromisoformat(row["billing_period_end"].strip())
        except (ValueError, AttributeError, KeyError) as e:
            return ParseError(row_index, f"Bad billing period: {e}", raw_row=row)

        if period_start >= period_end:
            return ParseError(
                row_index,
                f"period_start ({period_start}) must be before period_end ({period_end})",
                raw_row=row,
            )

        # Natural key for dedup: account + meter + billing period.
        identifier = f"{row['account_number'].strip()}|{row['meter_id'].strip()}|{period_start.isoformat()}"

        return ParsedRow(
            activity_type_code="electricity_grid",
            period_start=period_start,
            period_end=period_end,
            original_quantity=quantity,
            original_unit=row["unit"].strip(),
            source_row_identifier=identifier,
            source_payload=row,
            facility_name=row["service_address"].strip(),
        )

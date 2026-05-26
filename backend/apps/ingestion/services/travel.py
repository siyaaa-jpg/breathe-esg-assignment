"""
Travel JSON ingester. Mimics a SAP Concur Expense Reports v4 API response,
simplified to the three line-item types we handle: flight, hotel, ground.

Top-level shape:
{
  "report_id":      "ER-2025-08-001",
  "employee_email": "j.doe@example.com",
  "submitted_at":   "2025-08-30T14:23:00Z",
  "items": [
    {"type": "flight", "transaction_date": "2025-08-15",
     "origin_iata": "FRA", "destination_iata": "JFK",
     "cabin_class": "economy", "ticket_number": "T-001"},
    {"type": "hotel",  "transaction_date": "2025-08-15",
     "nights": 5, "city": "New York", "country": "US"},
    {"type": "ground", "transaction_date": "2025-08-15",
     "category": "taxi", "distance_km": 18.5}
  ]
}

Each row represents one passenger * one item (i.e., personal travel for the
employee on the report). Distance for flights is computed from IATA codes
via great-circle haversine; if either IATA is unknown to us, the row is
flagged `airport_unknown` and quantity stays 0 — analyst can fill in.
"""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import Iterator

from apps.emissions.models import IngestionBatch

from .airports import distance_between_iata
from .base import BaseIngester, ParseError, ParsedRow


SHORT_HAUL_KM = 1500
MEDIUM_HAUL_KM = 3700


class TravelJsonIngester(BaseIngester):
    source_system = IngestionBatch.SOURCE_TRAVEL

    def parse(self, file_bytes: bytes) -> Iterator[ParsedRow | ParseError]:
        try:
            payload = json.loads(file_bytes.decode("utf-8-sig"))
        except json.JSONDecodeError as e:
            yield ParseError(0, f"Invalid JSON: {e}", raw_row="")
            return

        report_id = payload.get("report_id")
        if not report_id:
            yield ParseError(0, "Missing required field 'report_id'", raw_row=payload)
            return

        items = payload.get("items")
        if not isinstance(items, list):
            yield ParseError(0, "'items' must be a list", raw_row=payload)
            return

        for idx, item in enumerate(items, start=1):
            yield self._parse_item(idx, item, report_id)

    def _parse_item(self, idx: int, item: dict, report_id: str) -> ParsedRow | ParseError:
        item_type = item.get("type")

        date_str = item.get("transaction_date")
        if not date_str:
            return ParseError(idx, "Missing transaction_date", raw_row=item)
        try:
            txn_date = date.fromisoformat(date_str)
        except (ValueError, TypeError):
            return ParseError(idx, f"Bad transaction_date: {date_str!r}", raw_row=item)

        # Identifier preference: ticket_number > ref > positional index
        ident_key = item.get("ticket_number") or item.get("ref") or f"item-{idx}"
        identifier = f"{report_id}|{ident_key}"

        if item_type == "flight":
            return self._parse_flight(idx, item, identifier, txn_date)
        if item_type == "hotel":
            return self._parse_hotel(idx, item, identifier, txn_date)
        if item_type == "ground":
            return self._parse_ground(idx, item, identifier, txn_date)
        return ParseError(idx, f"Unknown item type {item_type!r}", raw_row=item)

    def _parse_flight(self, idx, item, identifier, txn_date):
        origin = item.get("origin_iata")
        destination = item.get("destination_iata")
        if not origin or not destination:
            return ParseError(idx, "Flight missing origin_iata or destination_iata", raw_row=item)

        flags: list[str] = []

        # Use explicit distance_km if provided (Concur sometimes gives it);
        # otherwise compute from IATA codes.
        explicit_distance = item.get("distance_km")
        if explicit_distance is not None:
            distance = Decimal(str(explicit_distance))
        else:
            d = distance_between_iata(origin, destination)
            if d is None:
                # IATA unknown — create row with no activity classification,
                # quantity 0, flag, so analyst can enter distance manually.
                return ParsedRow(
                    activity_type_code=None,
                    period_start=txn_date,
                    period_end=txn_date,
                    original_quantity=Decimal("0"),
                    original_unit="passenger_km",
                    source_row_identifier=identifier,
                    source_payload=item,
                    extra_flags=["airport_unknown"],
                )
            distance = Decimal(str(round(d, 2)))

        if distance <= 0:
            return ParseError(idx, f"Non-positive flight distance: {distance}", raw_row=item)

        if distance < SHORT_HAUL_KM:
            activity_code = "flight_short_haul"
        elif distance < MEDIUM_HAUL_KM:
            activity_code = "flight_medium_haul"
        else:
            activity_code = "flight_long_haul"

        # Cabin class — only economy factors are seeded; flag upgrades so analyst can adjust
        cabin = (item.get("cabin_class") or "economy").lower()
        if cabin not in ("economy", "coach"):
            flags.append("cabin_class_upgrade")

        return ParsedRow(
            activity_type_code=activity_code,
            period_start=txn_date,
            period_end=txn_date,
            original_quantity=distance,
            original_unit="passenger_km",  # 1 passenger per row by convention
            source_row_identifier=identifier,
            source_payload=item,
            extra_flags=flags,
        )

    def _parse_hotel(self, idx, item, identifier, txn_date):
        nights = item.get("nights")
        if not isinstance(nights, (int, float)) or nights <= 0:
            return ParseError(idx, f"Bad nights value: {nights!r}", raw_row=item)

        return ParsedRow(
            activity_type_code="hotel_night",
            period_start=txn_date,
            period_end=txn_date,
            original_quantity=Decimal(str(nights)),
            original_unit="night",
            source_row_identifier=identifier,
            source_payload=item,
            region_override=item.get("country"),
        )

    def _parse_ground(self, idx, item, identifier, txn_date):
        category = (item.get("category") or "").lower()
        if category not in ("taxi", "rental"):
            return ParseError(idx, f"Bad ground category {category!r} (need 'taxi' or 'rental')", raw_row=item)

        distance_km = item.get("distance_km")
        if not isinstance(distance_km, (int, float)) or distance_km <= 0:
            return ParseError(idx, f"Bad distance_km: {distance_km!r}", raw_row=item)

        activity_code = "taxi_ride" if category == "taxi" else "rental_car"
        return ParsedRow(
            activity_type_code=activity_code,
            period_start=txn_date,
            period_end=txn_date,
            original_quantity=Decimal(str(distance_km)),
            original_unit="km",
            source_row_identifier=identifier,
            source_payload=item,
        )

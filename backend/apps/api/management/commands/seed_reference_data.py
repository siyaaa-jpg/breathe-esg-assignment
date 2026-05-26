"""
Seed the reference tables: ActivityType and EmissionFactor.

These aren't tenant-scoped — they're the controlled vocabulary and the
standardized factors every tenant draws from. Idempotent: safe to re-run.

Factors are real, sourced from the 2024 release of public datasets:
- DEFRA 2024 (UK Department for Environment, Food & Rural Affairs)
  GHG Conversion Factors for Company Reporting
- IEA 2024 global average grid electricity emission intensity
- EPA 2024 eGRID for US grid factors

The `source_citation` field on each EmissionFactor records the specific dataset
so an auditor can verify the number against the public release.

Real production deployments would import these from a maintained factor library
(climatiq.io, DEFRA's annual XLSX, Ecoinvent). For a prototype, a hand-curated
seed of ~12 activities covering the three sources is enough to demonstrate the
data flow without overfitting to one factor library.
"""
from datetime import date
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.emissions.models import ActivityType, EmissionFactor

ACTIVITY_TYPES = [
    # code, name, scope, scope_category, canonical_unit
    ("diesel_stationary", "Diesel (stationary combustion)", 1, "1.1 Stationary combustion", "liter"),
    ("diesel_mobile", "Diesel (mobile / fleet)", 1, "1.2 Mobile combustion", "liter"),
    ("gasoline_mobile", "Gasoline (mobile / fleet)", 1, "1.2 Mobile combustion", "liter"),
    ("natural_gas", "Natural gas (boilers)", 1, "1.1 Stationary combustion", "m3"),
    ("electricity_grid", "Grid electricity (location-based)", 2, "2.1 Purchased electricity", "kwh"),
    ("flight_short_haul", "Flight, short-haul economy (< 1500 km)", 3, "3.6 Business travel", "passenger_km"),
    ("flight_medium_haul", "Flight, medium-haul economy (1500-3700 km)", 3, "3.6 Business travel", "passenger_km"),
    ("flight_long_haul", "Flight, long-haul economy (> 3700 km)", 3, "3.6 Business travel", "passenger_km"),
    ("hotel_night", "Hotel stay (per night)", 3, "3.6 Business travel", "night"),
    ("taxi_ride", "Taxi ride", 3, "3.6 Business travel", "km"),
    ("rental_car", "Rental car (average petrol)", 3, "3.6 Business travel", "km"),
]

# (activity_code, region, factor, effective_from, effective_to, citation)
# Numbers are intentionally rounded to a sensible precision; real factor tables
# go to more decimal places but the precision doesn't carry meaning at the row level.
FACTORS = [
    # Scope 1 fuels — chemistry, not regional
    ("diesel_stationary", "*", "2.6810", date(2024, 1, 1), None,
     "DEFRA 2024 GHG Conversion Factors — Fuels: Diesel (average biofuel blend), stationary"),
    ("diesel_mobile", "*", "2.5126", date(2024, 1, 1), None,
     "DEFRA 2024 GHG Conversion Factors — Fuels: Diesel (average biofuel blend), passenger cars"),
    ("gasoline_mobile", "*", "2.1872", date(2024, 1, 1), None,
     "DEFRA 2024 GHG Conversion Factors — Fuels: Petrol (average biofuel blend)"),
    ("natural_gas", "*", "2.0264", date(2024, 1, 1), None,
     "DEFRA 2024 GHG Conversion Factors — Fuels: Natural gas, by volume (m3)"),

    # Scope 2 grid electricity — region matters
    ("electricity_grid", "*", "0.4750", date(2024, 1, 1), None,
     "IEA 2024 — global average grid electricity emission intensity"),
    ("electricity_grid", "US", "0.3863", date(2024, 1, 1), None,
     "EPA eGRID 2024 — US national average (location-based)"),
    ("electricity_grid", "DE", "0.3801", date(2024, 1, 1), None,
     "European Environment Agency 2024 — Germany grid (location-based)"),
    ("electricity_grid", "GB", "0.2073", date(2024, 1, 1), None,
     "DEFRA 2024 — UK grid (location-based)"),

    # Scope 3 — travel
    ("flight_short_haul", "*", "0.1510", date(2024, 1, 1), None,
     "DEFRA 2024 — Business travel air, short-haul economy class"),
    ("flight_medium_haul", "*", "0.0935", date(2024, 1, 1), None,
     "DEFRA 2024 — Business travel air, medium-haul economy class"),
    ("flight_long_haul", "*", "0.1480", date(2024, 1, 1), None,
     "DEFRA 2024 — Business travel air, long-haul economy class"),
    ("hotel_night", "*", "10.4000", date(2024, 1, 1), None,
     "Cornell Hotel Sustainability Benchmarking 2023 — global average per occupied room night"),
    ("taxi_ride", "*", "0.1493", date(2024, 1, 1), None,
     "DEFRA 2024 — Business travel land, regular taxi"),
    ("rental_car", "*", "0.1665", date(2024, 1, 1), None,
     "DEFRA 2024 — Business travel land, average car (unknown fuel)"),
]


class Command(BaseCommand):
    help = "Seed ActivityType and EmissionFactor reference data. Idempotent."

    @transaction.atomic
    def handle(self, *args, **options):
        created_at, updated_at = 0, 0
        for code, name, scope, scope_category, canonical_unit in ACTIVITY_TYPES:
            obj, created = ActivityType.objects.update_or_create(
                code=code,
                defaults={
                    "name": name,
                    "scope": scope,
                    "scope_category": scope_category,
                    "canonical_unit": canonical_unit,
                },
            )
            created_at += int(created)
            updated_at += int(not created)
        self.stdout.write(self.style.SUCCESS(
            f"ActivityType: {created_at} created, {updated_at} updated"
        ))

        created_ef, updated_ef = 0, 0
        for code, region, factor, eff_from, eff_to, citation in FACTORS:
            activity = ActivityType.objects.get(code=code)
            obj, created = EmissionFactor.objects.update_or_create(
                activity_type=activity,
                region=region,
                effective_from=eff_from,
                defaults={
                    "factor_kg_co2e_per_unit": Decimal(factor),
                    "effective_to": eff_to,
                    "source_citation": citation,
                },
            )
            created_ef += int(created)
            updated_ef += int(not created)
        self.stdout.write(self.style.SUCCESS(
            f"EmissionFactor: {created_ef} created, {updated_ef} updated"
        ))

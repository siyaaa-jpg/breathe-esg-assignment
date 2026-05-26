"""
Test SAP ingester: column aliasing, German dates/decimals, plant/material
lookups, flag policy. Same documentation-by-test style as test_utility.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from apps.accounts.models import Organization
from apps.emissions.models import (
    ActivityType,
    EmissionRecord,
    Facility,
    MaterialCodeMapping,
    PlantCodeMapping,
)
from apps.ingestion.services.sap import SapFlatFileIngester, _parse_number

User = get_user_model()


SAP_CSV = b"""Werk;Material;Materialkurztext;Buchungsdatum;Belegnummer;Menge;Mengeneinheit;Kostenstelle
DE03;FUEL-DIESEL-001;Diesel B7 Standard;28.07.2025;4900012345;15000.00;L;CC-PROD-01
DE03;FUEL-GASOLINE-002;Benzin Super E10;05.08.2025;4900012346;850.00;L;CC-FLEET-02
US01;FUEL-DIESEL-001;Diesel #2;15.08.2025;4900012347;3000.00;USG;CC-PROD-03
DE03;UNKNOWN-MAT-X;Unknown lubricant;10.08.2025;4900012348;500.00;L;CC-MAINT-01
DE99;FUEL-DIESEL-001;Diesel B7 Standard;12.08.2025;4900012349;800.00;L;CC-PROD-05
DE03;FUEL-NATURAL-GAS-001;Erdgas H;01.08.2025;4900012350;2500.00;M3;CC-HEAT-01
DE03;FUEL-DIESEL-001;Diesel B7 Standard;invalid-date;4900012351;100.00;L;CC-PROD-01
"""


class SapIngesterTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_reference_data", verbosity=0)
        cls.org = Organization.objects.create(name="Test Co", slug="test")
        cls.user = User.objects.create_user(email="s@s.test", password="x", organization=cls.org)
        frankfurt = Facility.objects.create(
            organization=cls.org, name="Frankfurt Plant 3", country="DE", facility_type=Facility.PLANT,
        )
        dallas = Facility.objects.create(
            organization=cls.org, name="Dallas Distribution Center", country="US", facility_type=Facility.PLANT,
        )
        PlantCodeMapping.objects.create(organization=cls.org, sap_plant_code="DE03", facility=frankfurt)
        PlantCodeMapping.objects.create(organization=cls.org, sap_plant_code="US01", facility=dallas)
        MaterialCodeMapping.objects.create(
            organization=cls.org, sap_material_code="FUEL-DIESEL-001",
            activity_type=ActivityType.objects.get(code="diesel_mobile"),
        )
        MaterialCodeMapping.objects.create(
            organization=cls.org, sap_material_code="FUEL-GASOLINE-002",
            activity_type=ActivityType.objects.get(code="gasoline_mobile"),
        )
        MaterialCodeMapping.objects.create(
            organization=cls.org, sap_material_code="FUEL-NATURAL-GAS-001",
            activity_type=ActivityType.objects.get(code="natural_gas"),
        )

    def test_batch_counts(self):
        batch = SapFlatFileIngester().ingest(file_bytes=SAP_CSV, filename="sap.csv", user=self.user)
        # 7 rows:
        #   4 clean    (diesel-DE03, gasoline-DE03, diesel-US01 with USG normalized, natgas-DE03)
        #   2 flagged  (UNKNOWN-MAT-X -> material_unknown, DE99 -> facility_unknown)
        #   1 failed   (invalid date)
        self.assertEqual(batch.row_count_total, 7)
        self.assertEqual(batch.row_count_succeeded, 4)
        self.assertEqual(batch.row_count_flagged, 2)
        self.assertEqual(batch.row_count_failed, 1)

    def test_us_gallons_normalized_to_liters(self):
        SapFlatFileIngester().ingest(file_bytes=SAP_CSV, filename="sap.csv", user=self.user)
        usg_record = EmissionRecord.objects.get(source_row_identifier="4900012347")
        self.assertEqual(usg_record.original_quantity, Decimal("3000.00"))
        self.assertEqual(usg_record.original_unit, "USG")
        # 3000 US gal -> 11356.2353520... liters
        self.assertAlmostEqual(float(usg_record.quantity), 3000 * 3.785411784, places=2)
        self.assertEqual(usg_record.unit, "liter")

    def test_german_date_parsed(self):
        SapFlatFileIngester().ingest(file_bytes=SAP_CSV, filename="sap.csv", user=self.user)
        rec = EmissionRecord.objects.get(source_row_identifier="4900012345")
        # Buchungsdatum was "28.07.2025" -> July 28
        self.assertEqual(rec.period_start.isoformat(), "2025-07-28")
        self.assertEqual(rec.period_start, rec.period_end)  # SAP postings are single-date

    def test_material_unknown_creates_unmapped_mapping(self):
        SapFlatFileIngester().ingest(file_bytes=SAP_CSV, filename="sap.csv", user=self.user)
        rec = EmissionRecord.objects.get(source_row_identifier="4900012348")
        self.assertIsNone(rec.activity_type)
        self.assertIsNone(rec.quantity)
        self.assertIsNone(rec.co2e_kg)
        self.assertIn("material_unknown", rec.flag_reasons)
        # And a placeholder MaterialCodeMapping was created so admin sees it
        self.assertTrue(MaterialCodeMapping.objects.filter(
            organization=self.org, sap_material_code="UNKNOWN-MAT-X", activity_type__isnull=True
        ).exists())

    def test_facility_unknown_creates_unmapped_plant_mapping(self):
        SapFlatFileIngester().ingest(file_bytes=SAP_CSV, filename="sap.csv", user=self.user)
        rec = EmissionRecord.objects.get(source_row_identifier="4900012349")
        self.assertIsNone(rec.facility)
        self.assertIn("facility_unknown", rec.flag_reasons)
        # Activity is still classified (FUEL-DIESEL-001 is mapped), so co2e is computed using the global factor
        self.assertIsNotNone(rec.co2e_kg)
        self.assertEqual(rec.emission_factor.region, "*")
        # And the unmapped plant code was lazy-created
        self.assertTrue(PlantCodeMapping.objects.filter(
            organization=self.org, sap_plant_code="DE99", facility__isnull=True
        ).exists())

    def test_invalid_date_is_hard_error(self):
        batch = SapFlatFileIngester().ingest(file_bytes=SAP_CSV, filename="sap.csv", user=self.user)
        self.assertEqual(len(batch.parse_errors), 1)
        self.assertIn("invalid-date", batch.parse_errors[0]["message"])


class NumberParserTest(TestCase):
    """The _parse_number helper handles English, German, and plain decimals."""

    def test_plain_decimal(self):
        self.assertEqual(_parse_number("1234.56"), Decimal("1234.56"))

    def test_english_thousands(self):
        self.assertEqual(_parse_number("1,234.56"), Decimal("1234.56"))

    def test_german_thousands(self):
        self.assertEqual(_parse_number("1.234,56"), Decimal("1234.56"))

    def test_german_no_thousands(self):
        self.assertEqual(_parse_number("1234,56"), Decimal("1234.56"))


class SapColumnAliasTest(TestCase):
    """Parser accepts English column headers as aliases for German canonical names."""

    @classmethod
    def setUpTestData(cls):
        call_command("seed_reference_data", verbosity=0)
        cls.org = Organization.objects.create(name="Test Co", slug="test")
        cls.user = User.objects.create_user(email="a@a.test", password="x", organization=cls.org)

    def test_english_headers_accepted(self):
        english_csv = b"""Plant;Material;Posting_Date;Document_Number;Quantity;UoM
DE03;FUEL-DIESEL-001;2025-08-01;4900099999;100.00;L
"""
        batch = SapFlatFileIngester().ingest(file_bytes=english_csv, filename="en.csv", user=self.user)
        self.assertEqual(batch.row_count_total, 1)
        # No mapping created, so this row gets material_unknown + facility_unknown
        self.assertEqual(batch.row_count_flagged, 1)

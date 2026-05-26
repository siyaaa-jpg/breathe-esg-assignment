"""
End-to-end test for the utility CSV ingester.

This isn't aiming for line coverage — it's a documentation-by-test for the
seven row outcomes documented in DECISIONS.md and the flag policy in MODEL.md.
If the test breaks, either the policy changed (update the doc + this test) or
a regression slipped in (fix the code).
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from apps.accounts.models import Organization
from apps.emissions.models import EmissionRecord, Facility
from apps.ingestion.services.base import DuplicateUploadError
from apps.ingestion.services.utility import UtilityCsvIngester

User = get_user_model()

UTILITY_CSV = b"""account_number,meter_id,service_address,billing_period_start,billing_period_end,total_kwh,unit,tariff_class,total_cost,currency
ACC-10293,M-001,Frankfurt Plant 3,2025-07-28,2025-08-27,142500.00,kWh,Industrial-A,18525.00,EUR
ACC-10293,M-002,Frankfurt Plant 3,2025-07-28,2025-08-27,2.85,MWh,Industrial-A,371.05,EUR
ACC-10293,M-003,Munich Office,2025-07-28,2025-08-27,8420.50,kWh,Commercial-B,1095.00,EUR
ACC-10294,M-009,Unknown Site,2025-08-01,2025-08-31,5000,kWh,Commercial-B,650,EUR
ACC-10293,M-004,Frankfurt Plant 3,2025-08-01,2025-08-31,9999.99,therms,Industrial-A,1300,EUR
ACC-10293,M-005,Frankfurt Plant 3,2025-09-30,2025-09-01,500,kWh,Commercial-B,65,EUR
ACC-10293,M-006,Frankfurt Plant 3,2025-08-01,2025-08-31,-50,kWh,Commercial-B,0,EUR
"""


class UtilityIngesterTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_reference_data", verbosity=0)
        cls.org = Organization.objects.create(name="Test Co", slug="test")
        cls.user = User.objects.create_user(email="t@t.test", password="x", organization=cls.org)
        Facility.objects.create(
            organization=cls.org, name="Frankfurt Plant 3", country="DE", facility_type=Facility.PLANT,
        )
        Facility.objects.create(
            organization=cls.org, name="Munich Office", country="DE", facility_type=Facility.OFFICE,
        )

    def test_batch_counts(self):
        batch = UtilityCsvIngester().ingest(file_bytes=UTILITY_CSV, filename="u.csv", user=self.user)
        # 7 data rows:
        #   3 clean (kWh, MWh-normalized, Munich)         -> succeeded
        #   2 soft flags (facility_unknown, unit_unknown) -> flagged
        #   2 hard errors (inverted period, negative kWh) -> failed
        self.assertEqual(batch.row_count_total, 7)
        self.assertEqual(batch.row_count_succeeded, 3)
        self.assertEqual(batch.row_count_flagged, 2)
        self.assertEqual(batch.row_count_failed, 2)
        self.assertEqual(len(batch.parse_errors), 2)

    def test_mwh_is_normalized_to_kwh(self):
        UtilityCsvIngester().ingest(file_bytes=UTILITY_CSV, filename="u.csv", user=self.user)
        rec = EmissionRecord.objects.get(original_unit="MWh")
        self.assertEqual(rec.original_quantity, Decimal("2.85"))
        self.assertEqual(rec.quantity, Decimal("2850"))
        self.assertEqual(rec.unit, "kwh")
        # And co2e is computed: 2850 kWh * Germany factor 0.3801 = ~1083.3 kg
        self.assertAlmostEqual(float(rec.co2e_kg), 2850 * 0.3801, places=2)

    def test_unknown_unit_flagged_and_null_quantity(self):
        UtilityCsvIngester().ingest(file_bytes=UTILITY_CSV, filename="u.csv", user=self.user)
        rec = EmissionRecord.objects.get(original_unit="therms")
        self.assertEqual(rec.status, EmissionRecord.STATUS_FLAGGED)
        self.assertIn("unit_unknown", rec.flag_reasons)
        self.assertIsNone(rec.quantity)
        self.assertIsNone(rec.co2e_kg)
        self.assertIsNone(rec.emission_factor)

    def test_unknown_facility_flagged_uses_global_factor(self):
        UtilityCsvIngester().ingest(file_bytes=UTILITY_CSV, filename="u.csv", user=self.user)
        rec = EmissionRecord.objects.get(source_row_identifier__startswith="ACC-10294")
        self.assertIsNone(rec.facility)
        self.assertIn("facility_unknown", rec.flag_reasons)
        self.assertEqual(rec.status, EmissionRecord.STATUS_FLAGGED)
        # With no facility we don't know region, so we use the '*' factor (0.475)
        self.assertEqual(rec.emission_factor.region, "*")

    def test_germany_factor_picked_for_german_facility(self):
        UtilityCsvIngester().ingest(file_bytes=UTILITY_CSV, filename="u.csv", user=self.user)
        rec = EmissionRecord.objects.filter(
            facility__name="Frankfurt Plant 3", original_unit="kWh", status=EmissionRecord.STATUS_PENDING
        ).first()
        self.assertIsNotNone(rec)
        self.assertEqual(rec.emission_factor.region, "DE")

    def test_duplicate_file_rejected(self):
        UtilityCsvIngester().ingest(file_bytes=UTILITY_CSV, filename="a.csv", user=self.user)
        with self.assertRaises(DuplicateUploadError):
            UtilityCsvIngester().ingest(file_bytes=UTILITY_CSV, filename="b.csv", user=self.user)

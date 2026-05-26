"""
Tests for the EmissionRecord read/edit/transition API. Happy-path coverage
of the main flows the analyst uses; edge cases live in the service-layer
tests (apps/emissions/tests/...).
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from rest_framework.test import APITestCase

from apps.accounts.models import Organization
from apps.emissions.models import EmissionRecord, EmissionRecordEdit, Facility

User = get_user_model()


UTILITY_CSV = b"""account_number,meter_id,service_address,billing_period_start,billing_period_end,total_kwh,unit,tariff_class,total_cost,currency
ACC-1,M-1,Frankfurt Plant 3,2025-07-28,2025-08-27,100.00,kWh,,0,EUR
ACC-1,M-2,Frankfurt Plant 3,2025-07-28,2025-08-27,50.00,therms,,0,EUR
"""


class RecordsApiTest(APITestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_reference_data", verbosity=0)
        cls.org = Organization.objects.create(name="Test Co", slug="test")
        cls.user = User.objects.create_user(email="u@u.test", password="x", organization=cls.org)
        Facility.objects.create(
            organization=cls.org, name="Frankfurt Plant 3", country="DE", facility_type=Facility.PLANT,
        )

    def setUp(self):
        self.client.force_authenticate(user=self.user)
        # Upload to get a batch with records
        self.client.post(
            "/api/uploads/",
            {"source": "utility", "file": SimpleUploadedFile("u.csv", UTILITY_CSV)},
            format="multipart",
        )
        self.clean = EmissionRecord.objects.get(original_unit="kWh")
        self.flagged = EmissionRecord.objects.get(original_unit="therms")

    def test_me_returns_user_and_org(self):
        response = self.client.get("/api/me/")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["email"], "u@u.test")
        self.assertEqual(body["organization"]["slug"], "test")

    def test_records_list_filters_by_status(self):
        response = self.client.get("/api/records/?status=flagged")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["results"][0]["id"], str(self.flagged.id))

    def test_record_detail_includes_source_payload_and_edits(self):
        response = self.client.get(f"/api/records/{self.clean.id}/")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("source_payload", body)
        self.assertEqual(body["source_payload"]["account_number"], "ACC-1")
        self.assertEqual(body["edits"], [])

    def test_patch_fixes_unit_unknown_and_demotes_to_pending(self):
        # The 'therms' row was flagged unit_unknown. Analyst sets unit to kWh.
        response = self.client.patch(
            f"/api/records/{self.flagged.id}/edit/",
            {"original_unit": "kWh", "reason": "Source had wrong unit; verified against the bill"},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["status"], "pending")
        self.assertNotIn("unit_unknown", body["flag_reasons"])
        self.assertEqual(Decimal(body["quantity"]), Decimal("50.0000"))
        self.assertIsNotNone(body["co2e_kg"])

        # Audit log: one edit row for original_unit, one for status
        edits = EmissionRecordEdit.objects.filter(emission_record=self.flagged).order_by("edited_at")
        self.assertEqual(edits.count(), 2)
        self.assertEqual(edits[0].field_name, "original_unit")
        self.assertEqual(edits[1].field_name, "status")
        self.assertEqual(edits[0].reason, "Source had wrong unit; verified against the bill")

    def test_patch_requires_reason(self):
        response = self.client.patch(
            f"/api/records/{self.clean.id}/edit/",
            {"original_unit": "kWh"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_transition_approve_then_lock(self):
        # approve from pending
        response = self.client.post(
            f"/api/records/{self.clean.id}/transition/",
            {"action": "approve", "reason": "Verified against utility bill"},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["status"], "approved")

        # lock from approved
        response = self.client.post(
            f"/api/records/{self.clean.id}/transition/",
            {"action": "lock", "reason": "Q3 period closed"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "locked")
        self.assertIsNotNone(body["locked_at"])
        self.assertEqual(body["locked_by_email"], "u@u.test")

    def test_transition_invalid_lock_from_pending(self):
        response = self.client.post(
            f"/api/records/{self.clean.id}/transition/",
            {"action": "lock", "reason": ""},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Cannot", response.json()["error"])

    def test_cannot_edit_locked_record(self):
        # approve + lock first
        self.client.post(f"/api/records/{self.clean.id}/transition/", {"action": "approve"}, format="json")
        self.client.post(f"/api/records/{self.clean.id}/transition/", {"action": "lock"}, format="json")

        response = self.client.patch(
            f"/api/records/{self.clean.id}/edit/",
            {"original_quantity": "200", "reason": "trying to edit"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

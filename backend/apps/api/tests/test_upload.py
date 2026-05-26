"""
HTTP-layer test for /api/uploads/. Covers auth, request shape, and the
success path. Service-layer behavior is tested in apps.ingestion.tests.
"""
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from rest_framework.test import APITestCase

from apps.accounts.models import Organization
from apps.emissions.models import Facility

User = get_user_model()

UTILITY_CSV = b"""account_number,meter_id,service_address,billing_period_start,billing_period_end,total_kwh,unit,tariff_class,total_cost,currency
ACC-10293,M-001,Frankfurt Plant 3,2025-07-28,2025-08-27,142500.00,kWh,Industrial-A,18525.00,EUR
"""


class UploadEndpointTest(APITestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_reference_data", verbosity=0)
        cls.org = Organization.objects.create(name="Test Co", slug="test")
        cls.user = User.objects.create_user(email="u@u.test", password="x", organization=cls.org)
        Facility.objects.create(
            organization=cls.org, name="Frankfurt Plant 3", country="DE", facility_type=Facility.PLANT,
        )

    def test_unauthenticated_request_is_rejected(self):
        response = self.client.post("/api/uploads/", {})
        self.assertEqual(response.status_code, 403)

    def test_missing_source_returns_400(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.post(
            "/api/uploads/",
            {"file": SimpleUploadedFile("u.csv", UTILITY_CSV)},
            format="multipart",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("source", response.json()["error"].lower())

    def test_unknown_source_returns_400(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.post(
            "/api/uploads/",
            {"source": "made_up", "file": SimpleUploadedFile("u.csv", UTILITY_CSV)},
            format="multipart",
        )
        self.assertEqual(response.status_code, 400)

    def test_successful_upload_returns_batch_summary(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.post(
            "/api/uploads/",
            {"source": "utility", "file": SimpleUploadedFile("u.csv", UTILITY_CSV)},
            format="multipart",
        )
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["source_system"], "utility")
        self.assertEqual(body["row_count_total"], 1)
        self.assertEqual(body["row_count_succeeded"], 1)
        self.assertEqual(body["status"], "completed")

    def test_duplicate_upload_returns_409(self):
        self.client.force_authenticate(user=self.user)
        upload_args = {"source": "utility", "file": SimpleUploadedFile("u.csv", UTILITY_CSV)}
        self.client.post("/api/uploads/", upload_args, format="multipart")
        # Second time with the same bytes
        response = self.client.post(
            "/api/uploads/",
            {"source": "utility", "file": SimpleUploadedFile("u.csv", UTILITY_CSV)},
            format="multipart",
        )
        self.assertEqual(response.status_code, 409)

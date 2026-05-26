"""
Test travel ingester: JSON parsing, flight distance bucketing, IATA lookup,
hotel/ground category routing, flag policy.
"""
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from apps.accounts.models import Organization
from apps.emissions.models import EmissionRecord
from apps.ingestion.services.airports import distance_between_iata, haversine_km
from apps.ingestion.services.travel import TravelJsonIngester

User = get_user_model()


TRAVEL_JSON = b"""
{
  "report_id": "ER-2025-08-001",
  "employee_email": "j.doe@example.com",
  "submitted_at": "2025-08-30T14:23:00Z",
  "items": [
    {"type": "flight", "transaction_date": "2025-08-15", "origin_iata": "FRA", "destination_iata": "JFK", "cabin_class": "economy", "ticket_number": "T-001"},
    {"type": "flight", "transaction_date": "2025-08-20", "origin_iata": "JFK", "destination_iata": "FRA", "cabin_class": "business", "ticket_number": "T-002"},
    {"type": "hotel", "transaction_date": "2025-08-15", "nights": 5, "city": "New York", "country": "US", "ref": "H-001"},
    {"type": "ground", "transaction_date": "2025-08-15", "category": "taxi", "distance_km": 18.5, "ref": "G-001"},
    {"type": "ground", "transaction_date": "2025-08-19", "category": "rental", "distance_km": 220.0, "ref": "G-002"},
    {"type": "flight", "transaction_date": "2025-08-22", "origin_iata": "XYZ", "destination_iata": "JFK", "cabin_class": "economy", "ticket_number": "T-003"}
  ]
}
"""


class TravelIngesterTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_reference_data", verbosity=0)
        cls.org = Organization.objects.create(name="Test Co", slug="test")
        cls.user = User.objects.create_user(email="t@t.test", password="x", organization=cls.org)

    def test_batch_counts(self):
        batch = TravelJsonIngester().ingest(file_bytes=TRAVEL_JSON, filename="tr.json", user=self.user)
        # 6 items:
        #   4 pending (FRA-JFK economy, hotel, taxi, rental)
        #   2 flagged (JFK-FRA business -> cabin_class_upgrade, XYZ -> airport_unknown)
        #   0 failed
        self.assertEqual(batch.row_count_total, 6)
        self.assertEqual(batch.row_count_succeeded, 4)
        self.assertEqual(batch.row_count_flagged, 2)
        self.assertEqual(batch.row_count_failed, 0)

    def test_long_haul_flight_bucketing(self):
        TravelJsonIngester().ingest(file_bytes=TRAVEL_JSON, filename="tr.json", user=self.user)
        # FRA -> JFK is ~6200 km, should be flight_long_haul
        rec = EmissionRecord.objects.get(source_row_identifier="ER-2025-08-001|T-001")
        self.assertEqual(rec.activity_type.code, "flight_long_haul")
        self.assertGreater(float(rec.quantity), 6000)
        self.assertLess(float(rec.quantity), 6500)

    def test_business_class_flagged(self):
        TravelJsonIngester().ingest(file_bytes=TRAVEL_JSON, filename="tr.json", user=self.user)
        rec = EmissionRecord.objects.get(source_row_identifier="ER-2025-08-001|T-002")
        self.assertIn("cabin_class_upgrade", rec.flag_reasons)
        # Still uses the economy long_haul factor (we lack business-class factors)
        self.assertEqual(rec.activity_type.code, "flight_long_haul")
        self.assertIsNotNone(rec.co2e_kg)

    def test_airport_unknown_flagged_no_compute(self):
        TravelJsonIngester().ingest(file_bytes=TRAVEL_JSON, filename="tr.json", user=self.user)
        rec = EmissionRecord.objects.get(source_row_identifier="ER-2025-08-001|T-003")
        self.assertIn("airport_unknown", rec.flag_reasons)
        self.assertIsNone(rec.activity_type)
        self.assertIsNone(rec.co2e_kg)

    def test_hotel_record(self):
        TravelJsonIngester().ingest(file_bytes=TRAVEL_JSON, filename="tr.json", user=self.user)
        rec = EmissionRecord.objects.get(source_row_identifier="ER-2025-08-001|H-001")
        self.assertEqual(rec.activity_type.code, "hotel_night")
        self.assertEqual(int(rec.quantity), 5)
        # 5 nights * 10.4 kg/night
        self.assertAlmostEqual(float(rec.co2e_kg), 52.0, places=2)

    def test_ground_taxi_and_rental(self):
        TravelJsonIngester().ingest(file_bytes=TRAVEL_JSON, filename="tr.json", user=self.user)
        taxi = EmissionRecord.objects.get(source_row_identifier="ER-2025-08-001|G-001")
        rental = EmissionRecord.objects.get(source_row_identifier="ER-2025-08-001|G-002")
        self.assertEqual(taxi.activity_type.code, "taxi_ride")
        self.assertEqual(rental.activity_type.code, "rental_car")


class HaversineTest(TestCase):
    def test_known_distance(self):
        # FRA -> JFK published distance ~6190 km (great-circle, varies slightly by source)
        d = distance_between_iata("FRA", "JFK")
        self.assertIsNotNone(d)
        self.assertGreater(d, 6000)
        self.assertLess(d, 6400)

    def test_unknown_iata(self):
        self.assertIsNone(distance_between_iata("FRA", "ZZZ"))

    def test_zero_distance_same_point(self):
        self.assertAlmostEqual(haversine_km(0, 0, 0, 0), 0, places=4)

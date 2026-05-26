"""
Create a demo Organization + admin user + facilities + SAP mappings for local
testing. Idempotent; uses get_or_create. Names and codes match the included
sample files so an end-to-end demo works without extra setup.
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounts.models import Organization
from apps.emissions.models import (
    ActivityType,
    Facility,
    MaterialCodeMapping,
    PlantCodeMapping,
)

User = get_user_model()


class Command(BaseCommand):
    help = "Create a demo Organization, admin user, facilities, and SAP code mappings."

    def add_arguments(self, parser):
        parser.add_argument("--email", default="admin@demo.test")
        parser.add_argument("--password", default="demo12345")
        parser.add_argument("--org-name", default="Demo Industries")
        parser.add_argument("--org-slug", default="demo")

    @transaction.atomic
    def handle(self, **opts):
        org, _ = Organization.objects.get_or_create(
            slug=opts["org_slug"],
            defaults={"name": opts["org_name"]},
        )

        user, created = User.objects.get_or_create(
            email=opts["email"],
            defaults={
                "full_name": "Demo Admin",
                "organization": org,
                "role": User.ROLE_ADMIN,
                "is_staff": True,
                "is_superuser": True,
            },
        )
        if created or not user.has_usable_password():
            user.set_password(opts["password"])
            user.save()

        # Facilities — names match samples/utility_aug2025.csv
        facilities = {}
        for name, country, ftype in [
            ("Frankfurt Plant 3", "DE", Facility.PLANT),
            ("Munich Office", "DE", Facility.OFFICE),
            ("Dallas Distribution Center", "US", Facility.PLANT),
        ]:
            f, _ = Facility.objects.get_or_create(
                organization=org, name=name,
                defaults={"country": country, "facility_type": ftype},
            )
            facilities[name] = f

        # SAP plant code mappings — codes match samples/sap_aug2025.csv
        for code, facility_name in [
            ("DE03", "Frankfurt Plant 3"),
            ("US01", "Dallas Distribution Center"),
        ]:
            PlantCodeMapping.objects.update_or_create(
                organization=org, sap_plant_code=code,
                defaults={"facility": facilities[facility_name]},
            )

        # SAP material code mappings
        for code, activity_code in [
            ("FUEL-DIESEL-001", "diesel_mobile"),
            ("FUEL-GASOLINE-002", "gasoline_mobile"),
            ("FUEL-NATURAL-GAS-001", "natural_gas"),
        ]:
            MaterialCodeMapping.objects.update_or_create(
                organization=org, sap_material_code=code,
                defaults={"activity_type": ActivityType.objects.get(code=activity_code)},
            )

        self.stdout.write(self.style.SUCCESS(
            f"Demo org '{org.name}' ready. Login at /admin/ with {opts['email']} / {opts['password']}"
        ))

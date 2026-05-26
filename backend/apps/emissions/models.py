"""
Domain models for emissions data. Seven tables:

- Facility           — where activity happens (plant, office, fleet)
- PlantCodeMapping   — org-scoped lookup, SAP plant code -> Facility
- ActivityType       — the controlled vocabulary of "things that emit"
- EmissionFactor     — versioned multipliers (kg CO2e per canonical unit)
- IngestionBatch     — one upload event; carries the file/user/timestamps shared
                       across every record created from it
- EmissionRecord     — the row an analyst reviews; one per activity event
- EmissionRecordEdit — append-only audit log of every change to an EmissionRecord

See MODEL.md for the full design rationale and field-by-field justifications.
The order of class definitions here matches the FK dependency order, so reading
top-to-bottom gives a clean understanding of the graph.
"""
import uuid

from django.db import models
from django.utils import timezone

from apps.accounts.models import Organization, User

from .managers import TenantScopedManager


# ---------------------------------------------------------------------------
# Facility — where things happen
# ---------------------------------------------------------------------------
class Facility(models.Model):
    PLANT = "plant"
    OFFICE = "office"
    FLEET = "fleet"
    OTHER = "other"
    TYPE_CHOICES = [(PLANT, "Plant"), (OFFICE, "Office"), (FLEET, "Fleet"), (OTHER, "Other")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="facilities")
    name = models.CharField(max_length=200)
    country = models.CharField(max_length=2, help_text="ISO 3166-1 alpha-2 (e.g., DE, US)")
    facility_type = models.CharField(max_length=20, choices=TYPE_CHOICES, default=PLANT)
    created_at = models.DateTimeField(default=timezone.now)

    objects = TenantScopedManager()

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "name"], name="unique_facility_name_per_org"),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.country})"


class PlantCodeMapping(models.Model):
    """Org-scoped: maps an SAP plant code (Werk) to one of our Facility records.

    A row with facility=None means "we've seen this code in an upload but the
    admin hasn't told us which Facility it maps to yet." Ingestion creates the
    EmissionRecord with facility=None and a `facility_unknown` flag — the
    analyst can resolve from the review queue.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="plant_code_mappings")
    sap_plant_code = models.CharField(max_length=10)
    facility = models.ForeignKey(Facility, on_delete=models.PROTECT, null=True, blank=True, related_name="plant_codes")
    created_at = models.DateTimeField(default=timezone.now)

    objects = TenantScopedManager()

    class Meta:
        ordering = ["sap_plant_code"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "sap_plant_code"], name="unique_plant_code_per_org"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.sap_plant_code} -> {self.facility or '(unmapped)'}"


class MaterialCodeMapping(models.Model):
    """Org-scoped: maps an SAP material code (Material) to an ActivityType.

    SAP knows you posted material `FUEL-DIESEL-001` but doesn't know that's
    `diesel_mobile` for GHG accounting. Only the client org knows. Without a
    mapping, ingestion creates the EmissionRecord with activity_type=null,
    quantity=null, co2e_kg=null, and a `material_unknown` flag — the analyst
    fixes it from the review queue (or admin pre-fills via this table).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="material_code_mappings")
    sap_material_code = models.CharField(max_length=50)
    activity_type = models.ForeignKey(
        "ActivityType",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="material_codes",
        help_text="Null = seen but not yet classified by admin",
    )
    created_at = models.DateTimeField(default=timezone.now)

    objects = TenantScopedManager()

    class Meta:
        ordering = ["sap_material_code"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "sap_material_code"], name="unique_material_code_per_org"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.sap_material_code} -> {self.activity_type or '(unmapped)'}"


# ---------------------------------------------------------------------------
# ActivityType — controlled vocabulary, not tenant-scoped (managed by us, ~30 rows)
# ---------------------------------------------------------------------------
class ActivityType(models.Model):
    SCOPE_1 = 1
    SCOPE_2 = 2
    SCOPE_3 = 3
    SCOPE_CHOICES = [(SCOPE_1, "Scope 1"), (SCOPE_2, "Scope 2"), (SCOPE_3, "Scope 3")]

    code = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=200)
    scope = models.SmallIntegerField(choices=SCOPE_CHOICES)
    scope_category = models.CharField(
        max_length=50,
        blank=True,
        help_text="GHG Protocol sub-category, e.g. '3.6 Business travel'",
    )
    canonical_unit = models.CharField(max_length=20, help_text="Internal unit we normalize to")

    class Meta:
        ordering = ["scope", "code"]

    def __str__(self) -> str:
        return f"[S{self.scope}] {self.name}"


# ---------------------------------------------------------------------------
# EmissionFactor — versioned, not tenant-scoped (factors are public/standard data)
# ---------------------------------------------------------------------------
class EmissionFactor(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    activity_type = models.ForeignKey(ActivityType, on_delete=models.PROTECT, related_name="factors")
    region = models.CharField(max_length=10, default="*", help_text="Country code or '*' for global")
    factor_kg_co2e_per_unit = models.DecimalField(max_digits=12, decimal_places=6)
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True, help_text="Exclusive; null = currently active")
    source_citation = models.CharField(max_length=500)

    class Meta:
        ordering = ["activity_type", "region", "-effective_from"]
        indexes = [
            models.Index(fields=["activity_type", "region", "effective_from"]),
        ]

    def __str__(self) -> str:
        return f"{self.activity_type.code} @ {self.region} from {self.effective_from} = {self.factor_kg_co2e_per_unit}"

    @classmethod
    def lookup(cls, *, activity_type: "ActivityType", on_date, country: str | None = None) -> "EmissionFactor | None":
        """Pick the factor active on `on_date`, preferring exact country match over '*'."""
        candidates = cls.objects.filter(
            activity_type=activity_type,
            effective_from__lte=on_date,
        ).filter(
            models.Q(effective_to__isnull=True) | models.Q(effective_to__gt=on_date)
        )
        if country:
            exact = candidates.filter(region=country).order_by("-effective_from").first()
            if exact:
                return exact
        return candidates.filter(region="*").order_by("-effective_from").first()


# ---------------------------------------------------------------------------
# IngestionBatch — one upload event
# ---------------------------------------------------------------------------
class IngestionBatch(models.Model):
    SOURCE_SAP = "sap"
    SOURCE_UTILITY = "utility"
    SOURCE_TRAVEL = "travel"
    SOURCE_CHOICES = [
        (SOURCE_SAP, "SAP (fuel/procurement)"),
        (SOURCE_UTILITY, "Utility (electricity)"),
        (SOURCE_TRAVEL, "Corporate travel"),
    ]

    STATUS_PROCESSING = "processing"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_PROCESSING, "Processing"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_FAILED, "Failed"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="ingestion_batches")
    source_system = models.CharField(max_length=20, choices=SOURCE_CHOICES)
    uploaded_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="ingestion_batches")
    uploaded_at = models.DateTimeField(default=timezone.now)

    original_filename = models.CharField(max_length=500)
    file_size_bytes = models.IntegerField()
    file_sha256 = models.CharField(max_length=64, help_text="Dedup: same org cannot upload same file twice")

    row_count_total = models.IntegerField(default=0)
    row_count_succeeded = models.IntegerField(default=0)
    row_count_failed = models.IntegerField(default=0)
    row_count_flagged = models.IntegerField(default=0)
    parse_errors = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PROCESSING)

    objects = TenantScopedManager()

    class Meta:
        ordering = ["-uploaded_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "file_sha256"], name="unique_file_per_org"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.source_system} upload {self.original_filename} ({self.row_count_total} rows)"


# ---------------------------------------------------------------------------
# EmissionRecord — the row analysts review
# ---------------------------------------------------------------------------
class EmissionRecord(models.Model):
    STATUS_PENDING = "pending"
    STATUS_FLAGGED = "flagged"
    STATUS_APPROVED = "approved"
    STATUS_LOCKED = "locked"
    STATUS_REJECTED = "rejected"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_FLAGGED, "Flagged"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_LOCKED, "Locked"),
        (STATUS_REJECTED, "Rejected"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="emission_records")
    facility = models.ForeignKey(
        Facility, on_delete=models.PROTECT, null=True, blank=True, related_name="emission_records"
    )
    ingestion_batch = models.ForeignKey(IngestionBatch, on_delete=models.PROTECT, related_name="records")
    activity_type = models.ForeignKey(
        ActivityType,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="records",
        help_text="NULL when the source couldn't be classified (e.g., SAP material with no MaterialCodeMapping). Analyst must classify.",
    )

    period_start = models.DateField()
    period_end = models.DateField()

    original_quantity = models.DecimalField(max_digits=18, decimal_places=4)
    original_unit = models.CharField(max_length=20)
    quantity = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        null=True,
        blank=True,
        help_text="Normalized to activity_type.canonical_unit. NULL when unit normalization failed (analyst must fix).",
    )
    unit = models.CharField(
        max_length=20,
        blank=True,
        default="",
        help_text="== activity_type.canonical_unit when known; empty string when activity_type is null",
    )

    co2e_kg = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        null=True,
        blank=True,
        help_text="NULL when quantity could not be normalized or factor was missing. 0 would lie.",
    )
    emission_factor = models.ForeignKey(
        EmissionFactor,
        on_delete=models.PROTECT,
        related_name="records",
        null=True,
        blank=True,
        help_text="Frozen at compute time. Updating EmissionFactor does NOT recompute existing records. NULL when no matching factor existed at ingest time.",
    )

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    flag_reasons = models.JSONField(default=list, blank=True, help_text='List of strings like ["unit_unknown"]')

    source_payload = models.JSONField(
        help_text="Original row from the source system, untouched, for forensics"
    )
    source_row_identifier = models.CharField(
        max_length=200,
        help_text="Natural key from source (SAP doc#, bill ID, expense ID); used for dedup",
    )

    created_at = models.DateTimeField(default=timezone.now)
    created_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="records_created",
        help_text="Null = ingested by system; non-null = manually added",
    )
    locked_at = models.DateTimeField(null=True, blank=True)
    locked_by = models.ForeignKey(
        User, on_delete=models.PROTECT, null=True, blank=True, related_name="records_locked"
    )

    objects = TenantScopedManager()

    class Meta:
        ordering = ["-period_end", "-created_at"]
        indexes = [
            models.Index(fields=["organization", "status", "-period_end"], name="er_org_status_period_idx"),
            models.Index(fields=["organization", "ingestion_batch"], name="er_org_batch_idx"),
            models.Index(
                fields=["organization", "facility", "period_start", "period_end"],
                name="er_org_facility_period_idx",
            ),
        ]
        constraints = [
            # Dedup within a (source_system, source_row_identifier) per org. The source_system
            # lives on IngestionBatch but we can't compose constraints across FKs in vanilla
            # Django; enforced at the ingestion service layer instead. Documented in MODEL.md §5.
        ]

    def __str__(self) -> str:
        return f"{self.activity_type.code}: {self.quantity} {self.unit} ({self.status})"

    @property
    def is_locked(self) -> bool:
        return self.locked_at is not None


# ---------------------------------------------------------------------------
# EmissionRecordEdit — append-only audit log
# ---------------------------------------------------------------------------
class EmissionRecordEdit(models.Model):
    """One row per field change on an EmissionRecord. Application code never UPDATEs
    or DELETEs from this table; the only operation is INSERT. The audit trail is
    fully reconstructable by reading rows ordered by edited_at."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    emission_record = models.ForeignKey(EmissionRecord, on_delete=models.PROTECT, related_name="edits")
    edited_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="record_edits")
    edited_at = models.DateTimeField(default=timezone.now)
    field_name = models.CharField(max_length=100)
    old_value = models.TextField(blank=True)
    new_value = models.TextField(blank=True)
    reason = models.TextField(
        blank=True, help_text="Mandatory on edits; optional on status transitions"
    )

    class Meta:
        ordering = ["edited_at"]
        indexes = [
            models.Index(fields=["emission_record", "edited_at"], name="ere_record_time_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.emission_record_id} {self.field_name}: {self.old_value!r} -> {self.new_value!r}"

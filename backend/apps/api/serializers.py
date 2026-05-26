"""
DRF serializers. Two general patterns:

- *ListSerializer for compact rows in tables (list endpoints)
- *DetailSerializer for the side-panel/drawer view (single-record endpoints)

Compact serializers prefer a few denormalized strings (`activity_code`,
`facility_name`) over full nested objects so the React table can render
without N+1 lookups.
"""
from rest_framework import serializers

from apps.accounts.models import Organization, User
from apps.emissions.models import (
    ActivityType,
    EmissionFactor,
    EmissionRecord,
    EmissionRecordEdit,
    Facility,
    IngestionBatch,
)


# ---------------------------------------------------------------------------
# Auth / org / facility / activity-type — small lookup serializers
# ---------------------------------------------------------------------------
class OrganizationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Organization
        fields = ["id", "name", "slug"]


class UserMeSerializer(serializers.ModelSerializer):
    organization = OrganizationSerializer(read_only=True)

    class Meta:
        model = User
        fields = ["id", "email", "full_name", "role", "organization"]


class FacilitySerializer(serializers.ModelSerializer):
    class Meta:
        model = Facility
        fields = ["id", "name", "country", "facility_type"]


class ActivityTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = ActivityType
        fields = ["id", "code", "name", "scope", "scope_category", "canonical_unit"]


class EmissionFactorSerializer(serializers.ModelSerializer):
    activity_code = serializers.CharField(source="activity_type.code", read_only=True)

    class Meta:
        model = EmissionFactor
        fields = ["id", "activity_code", "region", "factor_kg_co2e_per_unit",
                  "effective_from", "effective_to", "source_citation"]


# ---------------------------------------------------------------------------
# Ingestion batch
# ---------------------------------------------------------------------------
class IngestionBatchSerializer(serializers.ModelSerializer):
    uploaded_by_email = serializers.EmailField(source="uploaded_by.email", read_only=True)

    class Meta:
        model = IngestionBatch
        fields = [
            "id", "source_system", "uploaded_by_email", "uploaded_at",
            "original_filename", "file_size_bytes", "file_sha256",
            "row_count_total", "row_count_succeeded", "row_count_flagged", "row_count_failed",
            "parse_errors", "status",
        ]
        read_only_fields = fields


# ---------------------------------------------------------------------------
# EmissionRecord — list (compact) and detail (full)
# ---------------------------------------------------------------------------
class EmissionRecordListSerializer(serializers.ModelSerializer):
    activity_code = serializers.CharField(source="activity_type.code", read_only=True, default=None)
    activity_scope = serializers.IntegerField(source="activity_type.scope", read_only=True, default=None)
    facility_name = serializers.CharField(source="facility.name", read_only=True, default=None)
    source_system = serializers.CharField(source="ingestion_batch.source_system", read_only=True)

    class Meta:
        model = EmissionRecord
        fields = [
            "id",
            "source_system",
            "facility_name",
            "activity_code",
            "activity_scope",
            "period_start",
            "period_end",
            "original_quantity",
            "original_unit",
            "quantity",
            "unit",
            "co2e_kg",
            "status",
            "flag_reasons",
        ]


class EmissionRecordEditSerializer(serializers.ModelSerializer):
    edited_by_email = serializers.EmailField(source="edited_by.email", read_only=True)

    class Meta:
        model = EmissionRecordEdit
        fields = ["id", "edited_by_email", "edited_at", "field_name", "old_value", "new_value", "reason"]


class EmissionRecordDetailSerializer(serializers.ModelSerializer):
    activity_type = ActivityTypeSerializer(read_only=True)
    facility = FacilitySerializer(read_only=True)
    emission_factor = EmissionFactorSerializer(read_only=True)
    ingestion_batch = IngestionBatchSerializer(read_only=True)
    edits = EmissionRecordEditSerializer(many=True, read_only=True)
    is_locked = serializers.BooleanField(read_only=True)
    locked_by_email = serializers.EmailField(source="locked_by.email", read_only=True, default=None)

    class Meta:
        model = EmissionRecord
        fields = [
            "id",
            "activity_type",
            "facility",
            "ingestion_batch",
            "emission_factor",
            "period_start",
            "period_end",
            "original_quantity",
            "original_unit",
            "quantity",
            "unit",
            "co2e_kg",
            "status",
            "flag_reasons",
            "source_payload",
            "source_row_identifier",
            "created_at",
            "is_locked",
            "locked_at",
            "locked_by_email",
            "edits",
        ]


# ---------------------------------------------------------------------------
# PATCH input — the React app POSTs {changes: {...}, reason: "..."}
# ---------------------------------------------------------------------------
class RecordEditInputSerializer(serializers.Serializer):
    """Validates the analyst's PATCH payload before handing to edit_record()."""

    activity_type_id = serializers.IntegerField(required=False, allow_null=True)
    facility_id = serializers.UUIDField(required=False, allow_null=True)
    original_quantity = serializers.DecimalField(max_digits=18, decimal_places=4, required=False)
    original_unit = serializers.CharField(max_length=20, required=False)
    period_start = serializers.DateField(required=False)
    period_end = serializers.DateField(required=False)
    reason = serializers.CharField(required=True, allow_blank=False, max_length=2000)

    def validate(self, data):
        # Must include at least one editable change
        change_keys = set(data.keys()) - {"reason"}
        if not change_keys:
            raise serializers.ValidationError("At least one editable field must be provided")
        return data


# ---------------------------------------------------------------------------
# Transition input — POST {action: "approve"|"reject"|"lock", reason: "..."}
# ---------------------------------------------------------------------------
class TransitionInputSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=["approve", "reject", "lock"])
    reason = serializers.CharField(required=False, allow_blank=True, max_length=2000)

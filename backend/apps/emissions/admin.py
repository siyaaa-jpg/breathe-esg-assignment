from django.contrib import admin

from .models import (
    ActivityType,
    EmissionFactor,
    EmissionRecord,
    EmissionRecordEdit,
    Facility,
    IngestionBatch,
    MaterialCodeMapping,
    PlantCodeMapping,
)


@admin.register(Facility)
class FacilityAdmin(admin.ModelAdmin):
    list_display = ("name", "organization", "country", "facility_type")
    list_filter = ("organization", "country", "facility_type")
    search_fields = ("name",)


@admin.register(PlantCodeMapping)
class PlantCodeMappingAdmin(admin.ModelAdmin):
    list_display = ("sap_plant_code", "organization", "facility")
    list_filter = ("organization",)
    search_fields = ("sap_plant_code",)
    autocomplete_fields = ("facility",)


@admin.register(MaterialCodeMapping)
class MaterialCodeMappingAdmin(admin.ModelAdmin):
    list_display = ("sap_material_code", "organization", "activity_type")
    list_filter = ("organization", "activity_type")
    search_fields = ("sap_material_code",)
    autocomplete_fields = ("activity_type",)


@admin.register(ActivityType)
class ActivityTypeAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "scope", "scope_category", "canonical_unit")
    list_filter = ("scope",)
    search_fields = ("code", "name")


@admin.register(EmissionFactor)
class EmissionFactorAdmin(admin.ModelAdmin):
    list_display = ("activity_type", "region", "factor_kg_co2e_per_unit", "effective_from", "effective_to")
    list_filter = ("activity_type", "region")


@admin.register(IngestionBatch)
class IngestionBatchAdmin(admin.ModelAdmin):
    list_display = (
        "original_filename",
        "organization",
        "source_system",
        "uploaded_by",
        "uploaded_at",
        "row_count_total",
        "row_count_failed",
        "status",
    )
    list_filter = ("organization", "source_system", "status")
    readonly_fields = ("file_sha256",)


@admin.register(EmissionRecord)
class EmissionRecordAdmin(admin.ModelAdmin):
    list_display = (
        "activity_type",
        "facility",
        "period_start",
        "period_end",
        "quantity",
        "unit",
        "co2e_kg",
        "status",
    )
    list_filter = ("organization", "status", "activity_type__scope", "activity_type")
    search_fields = ("source_row_identifier",)
    readonly_fields = ("source_payload",)


@admin.register(EmissionRecordEdit)
class EmissionRecordEditAdmin(admin.ModelAdmin):
    list_display = ("emission_record", "field_name", "edited_by", "edited_at")
    list_filter = ("field_name",)
    readonly_fields = ("emission_record", "edited_by", "edited_at", "field_name", "old_value", "new_value", "reason")

    def has_add_permission(self, request):
        # Edits are created programmatically only; the admin should never let you create one by hand.
        return False

    def has_delete_permission(self, request, obj=None):
        # Append-only audit log.
        return False

    def has_change_permission(self, request, obj=None):
        return False

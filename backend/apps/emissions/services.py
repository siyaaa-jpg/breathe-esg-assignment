"""
Domain services for EmissionRecord — the compute pipeline (unit normalization
+ factor lookup + co2e), record editing with audit trail, and status
transitions. Both the ingestion layer and the analyst PATCH endpoint use
these so the rules live in exactly one place.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User

from . import units
from .models import (
    ActivityType,
    EmissionFactor,
    EmissionRecord,
    EmissionRecordEdit,
    Facility,
)


# Flags whose presence/absence is determined purely by the compute pipeline.
# `edit_record` removes these and re-derives them from current field values;
# all other flags (facility_unknown, material_unknown, duplicate_candidate,
# cabin_class_upgrade, etc.) are preserved across edits.
COMPUTE_FLAGS = {"unit_unknown", "factor_missing"}


class InvalidTransition(Exception):
    """Status transition not allowed by the lifecycle state machine."""


class EditNotAllowed(Exception):
    """Record is locked/rejected, or the field isn't editable, or reason missing."""


@dataclass
class Computed:
    quantity: Decimal | None
    factor: EmissionFactor | None
    co2e_kg: Decimal | None
    flags: list[str]


def derive_co2e(
    *,
    activity: ActivityType | None,
    facility: Facility | None,
    original_quantity: Decimal,
    original_unit: str,
    period_start: date,
    region_override: str | None = None,
) -> Computed:
    """Pure-ish: given source inputs, compute normalized quantity + factor + co2e + flags.

    Doesn't touch the DB except for the factor lookup. Used by both BaseIngester
    (at record creation) and edit_record (after an analyst edits a field).
    """
    flags: list[str] = []

    if activity is None:
        # No activity = no normalization possible, no factor, no co2e.
        # The caller is responsible for the corresponding flag (e.g., material_unknown).
        return Computed(quantity=None, factor=None, co2e_kg=None, flags=flags)

    normalized_qty, _ = units.normalize(original_quantity, original_unit, activity.canonical_unit)
    if normalized_qty is None:
        flags.append("unit_unknown")
        return Computed(quantity=None, factor=None, co2e_kg=None, flags=flags)

    region = region_override or (facility.country if facility else None)
    factor = EmissionFactor.lookup(activity_type=activity, on_date=period_start, country=region)
    if factor is None:
        flags.append("factor_missing")
        return Computed(quantity=normalized_qty, factor=None, co2e_kg=None, flags=flags)

    return Computed(
        quantity=normalized_qty,
        factor=factor,
        co2e_kg=normalized_qty * factor.factor_kg_co2e_per_unit,
        flags=flags,
    )


# ---------------------------------------------------------------------------
# Edit
# ---------------------------------------------------------------------------
EDITABLE_FIELDS = {
    "activity_type",
    "facility",
    "original_quantity",
    "original_unit",
    "period_start",
    "period_end",
}


@transaction.atomic
def edit_record(
    *,
    record: EmissionRecord,
    edits: dict,
    reason: str,
    user: User,
) -> EmissionRecord:
    """
    Apply edits to a record, recompute derived fields, write audit rows.

    `edits` keys are field names from EDITABLE_FIELDS. Values are typed
    appropriately (ActivityType for activity_type, Facility for facility,
    Decimal/str/date primitives for the rest). Pre-validation belongs in
    the serializer.
    """
    if record.is_locked:
        raise EditNotAllowed("Cannot edit a locked record")
    if record.status == EmissionRecord.STATUS_REJECTED:
        raise EditNotAllowed("Cannot edit a rejected record")
    if not (reason or "").strip():
        raise EditNotAllowed("Reason is required for edits")

    # Apply changes, collect (field, old, new) for the audit log
    changes: list[tuple[str, object, object]] = []
    for field_name, new_value in edits.items():
        if field_name not in EDITABLE_FIELDS:
            raise EditNotAllowed(f"Field {field_name!r} is not editable")
        old_value = getattr(record, field_name)
        if old_value == new_value:
            continue
        changes.append((field_name, old_value, new_value))
        setattr(record, field_name, new_value)

    if not changes:
        return record

    # Recompute derived fields and rebuild flag list
    computed = derive_co2e(
        activity=record.activity_type,
        facility=record.facility,
        original_quantity=record.original_quantity,
        original_unit=record.original_unit,
        period_start=record.period_start,
    )
    record.quantity = computed.quantity
    record.co2e_kg = computed.co2e_kg
    record.emission_factor = computed.factor
    record.unit = record.activity_type.canonical_unit if record.activity_type else ""

    preserved_flags = [f for f in record.flag_reasons if f not in COMPUTE_FLAGS]
    record.flag_reasons = preserved_flags + computed.flags

    # If edits cleared all flags and the record was flagged, demote to pending.
    if not record.flag_reasons and record.status == EmissionRecord.STATUS_FLAGGED:
        changes.append(("status", record.status, EmissionRecord.STATUS_PENDING))
        record.status = EmissionRecord.STATUS_PENDING

    record.save()

    for field_name, old, new in changes:
        EmissionRecordEdit.objects.create(
            emission_record=record,
            edited_by=user,
            field_name=field_name,
            old_value="" if old is None else str(old),
            new_value="" if new is None else str(new),
            reason=reason,
        )
    return record


# ---------------------------------------------------------------------------
# Status transitions
# ---------------------------------------------------------------------------
# (current_status, action) -> new_status
TRANSITIONS = {
    (EmissionRecord.STATUS_PENDING, "approve"): EmissionRecord.STATUS_APPROVED,
    (EmissionRecord.STATUS_PENDING, "reject"): EmissionRecord.STATUS_REJECTED,
    (EmissionRecord.STATUS_FLAGGED, "approve"): EmissionRecord.STATUS_APPROVED,
    (EmissionRecord.STATUS_FLAGGED, "reject"): EmissionRecord.STATUS_REJECTED,
    (EmissionRecord.STATUS_APPROVED, "lock"): EmissionRecord.STATUS_LOCKED,
    (EmissionRecord.STATUS_APPROVED, "reject"): EmissionRecord.STATUS_REJECTED,
}


@transaction.atomic
def transition_record(
    *,
    record: EmissionRecord,
    action: str,
    user: User,
    reason: str = "",
) -> EmissionRecord:
    """Move a record through the lifecycle. Approve/reject/lock are all here."""
    new_status = TRANSITIONS.get((record.status, action))
    if new_status is None:
        raise InvalidTransition(
            f"Cannot {action!r} a record in status {record.status!r}"
        )

    old_status = record.status
    record.status = new_status
    if action == "lock":
        record.locked_at = timezone.now()
        record.locked_by = user
    record.save()

    EmissionRecordEdit.objects.create(
        emission_record=record,
        edited_by=user,
        field_name="status",
        old_value=old_status,
        new_value=new_status,
        reason=reason,
    )
    return record

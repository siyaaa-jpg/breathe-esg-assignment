"""
Shared ingestion machinery. Each per-source ingester (utility, sap, travel)
subclasses BaseIngester and implements `parse()` to yield ParsedRow or
ParseError. The base class handles everything universal: file deduplication,
batch creation, facility resolution, unit normalization, factor lookup,
EmissionRecord creation, and counter updates.

This abstraction exists because the universal logic is genuinely the same
across all three sources. Without it, the dedup/factor/normalization code
would be repeated three times and drift out of sync. The cost is that a
reader has to understand BaseIngester before reading a per-source ingester.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Iterator

from django.db import transaction

from apps.accounts.models import Organization, User
from apps.emissions.models import (
    ActivityType,
    EmissionRecord,
    Facility,
    IngestionBatch,
    MaterialCodeMapping,
    PlantCodeMapping,
)
from apps.emissions.services import derive_co2e

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Errors raised back to the API layer
# ---------------------------------------------------------------------------
class DuplicateUploadError(Exception):
    """Same file hash already uploaded by this org. The API maps this to 409."""


class ConfigurationError(Exception):
    """The ingester referenced an ActivityType code not in seed data. Programming bug."""


# ---------------------------------------------------------------------------
# Per-row outputs from parse()
# ---------------------------------------------------------------------------
@dataclass
class ParseError:
    """A row that couldn't even produce a structurally complete EmissionRecord.
    Lands in IngestionBatch.parse_errors; no record is created."""
    row_index: int
    message: str
    raw_row: Any


@dataclass
class ParsedRow:
    """A row that produced a structurally complete EmissionRecord candidate.
    The base class takes it from here: resolves facility, normalizes unit,
    looks up factor, computes co2e, persists.

    `activity_type_code` may be None when the parser knows it has a row but
    can't classify it (e.g., SAP material with no MaterialCodeMapping). When
    None, the parser MUST add a reason flag to `extra_flags` so the analyst
    knows why; the base class will then skip unit normalization, factor
    lookup, and co2e computation, leaving those fields null on the record.
    """
    activity_type_code: str | None
    period_start: date
    period_end: date
    original_quantity: Decimal
    original_unit: str
    source_row_identifier: str
    source_payload: dict
    # SAP-style indirect activity classification: the parser yields a raw material
    # code, the base class resolves it via MaterialCodeMapping (org-scoped). Set
    # `material_code` AND leave `activity_type_code=None`. The base will set
    # activity_type from the mapping, or add a `material_unknown` flag if no
    # mapping exists.
    material_code: str | None = None
    # Source-specific facility lookup. Exactly one of these should be set per row,
    # or neither (e.g., a flight has no facility). The base class picks the
    # right resolution path based on which is set.
    facility_name: str | None = None
    facility_plant_code: str | None = None
    # Override the country used for factor lookup. Useful when there's no facility
    # but we know the region (e.g., a domestic US flight -> region=US).
    region_override: str | None = None
    # Any flags the parser already wants to set (e.g., a parser-level anomaly check).
    extra_flags: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Base ingester
# ---------------------------------------------------------------------------
class BaseIngester:
    source_system: str = ""  # must be one of IngestionBatch.SOURCE_*, set by subclass

    def ingest(self, *, file_bytes: bytes, filename: str, user: User) -> IngestionBatch:
        if not user.organization:
            raise ValueError("User has no organization; cannot ingest.")
        org = user.organization

        sha = hashlib.sha256(file_bytes).hexdigest()
        if IngestionBatch.objects.filter(organization=org, file_sha256=sha).exists():
            raise DuplicateUploadError(
                f"File with SHA256 {sha[:12]}... already uploaded by {org}"
            )

        with transaction.atomic():
            batch = IngestionBatch.objects.create(
                organization=org,
                source_system=self.source_system,
                uploaded_by=user,
                original_filename=filename,
                file_size_bytes=len(file_bytes),
                file_sha256=sha,
            )

            for item in self.parse(file_bytes):
                batch.row_count_total += 1
                if isinstance(item, ParseError):
                    batch.parse_errors.append({
                        "row_index": item.row_index,
                        "message": item.message,
                        "raw_row": item.raw_row,
                    })
                    batch.row_count_failed += 1
                    continue

                record = self._create_record(batch=batch, parsed=item)
                if record.status == EmissionRecord.STATUS_FLAGGED:
                    batch.row_count_flagged += 1
                else:
                    batch.row_count_succeeded += 1

            batch.status = IngestionBatch.STATUS_COMPLETED
            batch.save()

        logger.info(
            "ingestion complete: source=%s file=%s total=%d ok=%d flagged=%d failed=%d",
            self.source_system, filename,
            batch.row_count_total, batch.row_count_succeeded,
            batch.row_count_flagged, batch.row_count_failed,
        )
        return batch

    def parse(self, file_bytes: bytes) -> Iterator[ParsedRow | ParseError]:
        """Subclass contract: read the file, yield one ParsedRow or ParseError per source row."""
        raise NotImplementedError

    # -----------------------------------------------------------------------
    # Internal: turn a ParsedRow into a persisted EmissionRecord
    # -----------------------------------------------------------------------
    def _create_record(self, *, batch: IngestionBatch, parsed: ParsedRow) -> EmissionRecord:
        flags: list[str] = list(parsed.extra_flags)

        # 1. activity type — three paths:
        #    (a) parser passed a code directly (utility, travel)
        #    (b) parser passed a material_code, we resolve via MaterialCodeMapping (SAP)
        #    (c) parser couldn't classify; activity stays None (parser must have flagged why)
        activity: ActivityType | None = None
        if parsed.activity_type_code is not None:
            try:
                activity = ActivityType.objects.get(code=parsed.activity_type_code)
            except ActivityType.DoesNotExist as e:
                raise ConfigurationError(
                    f"ActivityType {parsed.activity_type_code!r} not in seed data. "
                    f"Run `manage.py seed_reference_data` and check the activity codes in your ingester."
                ) from e
        elif parsed.material_code is not None:
            # Lazy-create the mapping row so admin sees unmapped codes in one place
            mapping, created = MaterialCodeMapping.objects.select_related("activity_type").get_or_create(
                organization=batch.organization,
                sap_material_code=parsed.material_code,
            )
            if mapping.activity_type is not None:
                activity = mapping.activity_type
            else:
                flags.append("material_unknown")

        # 2. facility resolution
        facility = self._resolve_facility(batch.organization, parsed)
        if facility is None and (parsed.facility_name or parsed.facility_plant_code):
            flags.append("facility_unknown")

        # 3. derive normalized quantity, factor, co2e — shared with the analyst PATCH path
        computed = derive_co2e(
            activity=activity,
            facility=facility,
            original_quantity=parsed.original_quantity,
            original_unit=parsed.original_unit,
            period_start=parsed.period_start,
            region_override=parsed.region_override,
        )
        flags.extend(computed.flags)

        # 4. duplicate candidate — same (source_system, source_row_identifier) elsewhere in this org
        if EmissionRecord.objects.filter(
            organization=batch.organization,
            source_row_identifier=parsed.source_row_identifier,
            ingestion_batch__source_system=batch.source_system,
        ).exists():
            flags.append("duplicate_candidate")

        status = EmissionRecord.STATUS_FLAGGED if flags else EmissionRecord.STATUS_PENDING

        return EmissionRecord.objects.create(
            organization=batch.organization,
            facility=facility,
            ingestion_batch=batch,
            activity_type=activity,
            period_start=parsed.period_start,
            period_end=parsed.period_end,
            original_quantity=parsed.original_quantity,
            original_unit=parsed.original_unit,
            quantity=computed.quantity,
            unit=activity.canonical_unit if activity else "",
            co2e_kg=computed.co2e_kg,
            emission_factor=computed.factor,
            status=status,
            flag_reasons=flags,
            source_payload=parsed.source_payload,
            source_row_identifier=parsed.source_row_identifier,
        )

    def _resolve_facility(self, org: Organization, parsed: ParsedRow) -> Facility | None:
        """Two paths: facility_plant_code via PlantCodeMapping (SAP), or
        facility_name via case-insensitive Facility.name match (utility, hotel).
        Lazy-creates an unmapped PlantCodeMapping row when first encountered so
        the admin sees it in one place."""
        if parsed.facility_plant_code:
            mapping, _ = PlantCodeMapping.objects.select_related("facility").get_or_create(
                organization=org, sap_plant_code=parsed.facility_plant_code,
            )
            return mapping.facility  # may be None (unmapped)
        if parsed.facility_name:
            return Facility.objects.filter(
                organization=org, name__iexact=parsed.facility_name.strip()
            ).first()
        return None

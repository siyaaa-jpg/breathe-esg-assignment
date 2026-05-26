"""
DRF views. Function-based with explicit pagination to keep the URL → handler
trace obvious. Every view assumes request.user.organization is set; that's
enforced by IsInOrganization in DEFAULT_PERMISSION_CLASSES.
"""
from django.shortcuts import get_object_or_404
from rest_framework import status as http_status
from rest_framework.decorators import api_view, parser_classes, permission_classes
from rest_framework.pagination import PageNumberPagination
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from apps.accounts.models import User
from apps.emissions.models import (
    ActivityType,
    EmissionRecord,
    Facility,
    IngestionBatch,
)
from apps.emissions.services import (
    EditNotAllowed,
    InvalidTransition,
    edit_record,
    transition_record,
)
from apps.ingestion.services.base import ConfigurationError, DuplicateUploadError
from apps.ingestion.services.sap import SapFlatFileIngester
from apps.ingestion.services.travel import TravelJsonIngester
from apps.ingestion.services.utility import UtilityCsvIngester

from .serializers import (
    ActivityTypeSerializer,
    EmissionRecordDetailSerializer,
    EmissionRecordListSerializer,
    FacilitySerializer,
    IngestionBatchSerializer,
    RecordEditInputSerializer,
    TransitionInputSerializer,
    UserMeSerializer,
)


INGESTERS = {
    IngestionBatch.SOURCE_UTILITY: UtilityCsvIngester,
    IngestionBatch.SOURCE_SAP: SapFlatFileIngester,
    IngestionBatch.SOURCE_TRAVEL: TravelJsonIngester,
}


# ---------------------------------------------------------------------------
# Health (no auth)
# ---------------------------------------------------------------------------
@api_view(["GET"])
@permission_classes([AllowAny])
def health(_request):
    return Response({"status": "ok"})


# ---------------------------------------------------------------------------
# Current user
# ---------------------------------------------------------------------------
@api_view(["GET"])
def me(request):
    return Response(UserMeSerializer(request.user).data)


# ---------------------------------------------------------------------------
# Lookups: facilities + activity types — for dropdowns in the record editor
# ---------------------------------------------------------------------------
@api_view(["GET"])
def facilities_list(request):
    qs = Facility.objects.for_org(request.user.organization)
    return Response(FacilitySerializer(qs, many=True).data)


@api_view(["GET"])
def activity_types_list(_request):
    # ActivityType is not tenant-scoped (it's reference data)
    return Response(ActivityTypeSerializer(ActivityType.objects.all(), many=True).data)


# ---------------------------------------------------------------------------
# Upload + batch list + batch detail
# ---------------------------------------------------------------------------
@api_view(["POST"])
@parser_classes([MultiPartParser])
def upload(request):
    source = request.data.get("source")
    file_obj = request.data.get("file")

    if not source:
        return Response({"error": "Missing 'source' field"}, status=http_status.HTTP_400_BAD_REQUEST)
    if source not in INGESTERS:
        return Response(
            {"error": f"Unknown source {source!r}. Expected one of {sorted(INGESTERS.keys())}."},
            status=http_status.HTTP_400_BAD_REQUEST,
        )
    if not file_obj:
        return Response({"error": "Missing 'file' upload"}, status=http_status.HTTP_400_BAD_REQUEST)

    try:
        batch = INGESTERS[source]().ingest(
            file_bytes=file_obj.read(),
            filename=file_obj.name,
            user=request.user,
        )
    except DuplicateUploadError as e:
        return Response({"error": str(e)}, status=http_status.HTTP_409_CONFLICT)
    except ConfigurationError as e:
        return Response({"error": str(e)}, status=http_status.HTTP_500_INTERNAL_SERVER_ERROR)

    return Response(IngestionBatchSerializer(batch).data, status=http_status.HTTP_201_CREATED)


@api_view(["GET"])
def batches_list(request):
    qs = IngestionBatch.objects.for_org(request.user.organization).select_related("uploaded_by")
    paginator = PageNumberPagination()
    page = paginator.paginate_queryset(qs, request)
    return paginator.get_paginated_response(IngestionBatchSerializer(page, many=True).data)


@api_view(["GET"])
def batch_detail(request, batch_id):
    batch = get_object_or_404(
        IngestionBatch.objects.for_org(request.user.organization).select_related("uploaded_by"),
        pk=batch_id,
    )
    return Response(IngestionBatchSerializer(batch).data)


# ---------------------------------------------------------------------------
# EmissionRecord list / detail / patch / transition
# ---------------------------------------------------------------------------
@api_view(["GET"])
def records_list(request):
    qs = EmissionRecord.objects.for_org(request.user.organization).select_related(
        "activity_type", "facility", "ingestion_batch"
    )

    # Filters
    if v := request.GET.get("status"):
        qs = qs.filter(status=v)
    if v := request.GET.get("source"):
        qs = qs.filter(ingestion_batch__source_system=v)
    if v := request.GET.get("batch"):
        qs = qs.filter(ingestion_batch_id=v)
    if v := request.GET.get("facility"):
        qs = qs.filter(facility_id=v)
    if v := request.GET.get("activity_type"):
        qs = qs.filter(activity_type_id=v)
    if v := request.GET.get("period_start_gte"):
        qs = qs.filter(period_start__gte=v)
    if v := request.GET.get("period_end_lte"):
        qs = qs.filter(period_end__lte=v)

    paginator = PageNumberPagination()
    page = paginator.paginate_queryset(qs, request)
    return paginator.get_paginated_response(EmissionRecordListSerializer(page, many=True).data)


def _get_record_or_404(request, record_id):
    return get_object_or_404(
        EmissionRecord.objects.for_org(request.user.organization).select_related(
            "activity_type", "facility", "ingestion_batch__uploaded_by",
            "emission_factor__activity_type", "locked_by",
        ).prefetch_related("edits__edited_by"),
        pk=record_id,
    )


@api_view(["GET"])
def record_detail(request, record_id):
    record = _get_record_or_404(request, record_id)
    return Response(EmissionRecordDetailSerializer(record).data)


@api_view(["PATCH"])
@parser_classes([JSONParser])
def record_patch(request, record_id):
    record = _get_record_or_404(request, record_id)

    serializer = RecordEditInputSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data

    # Translate ID fields into model instances; collect into the edits dict
    edits: dict = {}
    if "activity_type_id" in data:
        v = data["activity_type_id"]
        edits["activity_type"] = ActivityType.objects.get(pk=v) if v else None
    if "facility_id" in data:
        v = data["facility_id"]
        if v is None:
            edits["facility"] = None
        else:
            edits["facility"] = get_object_or_404(
                Facility.objects.for_org(request.user.organization), pk=v
            )
    for f in ("original_quantity", "original_unit", "period_start", "period_end"):
        if f in data:
            edits[f] = data[f]

    try:
        edit_record(record=record, edits=edits, reason=data["reason"], user=request.user)
    except EditNotAllowed as e:
        return Response({"error": str(e)}, status=http_status.HTTP_400_BAD_REQUEST)

    record.refresh_from_db()
    return Response(EmissionRecordDetailSerializer(_get_record_or_404(request, record.id)).data)


@api_view(["POST"])
@parser_classes([JSONParser])
def record_transition(request, record_id):
    record = _get_record_or_404(request, record_id)

    serializer = TransitionInputSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data

    try:
        transition_record(
            record=record,
            action=data["action"],
            user=request.user,
            reason=data.get("reason", ""),
        )
    except InvalidTransition as e:
        return Response({"error": str(e)}, status=http_status.HTTP_400_BAD_REQUEST)

    return Response(EmissionRecordDetailSerializer(_get_record_or_404(request, record.id)).data)

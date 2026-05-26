from django.urls import path

from . import views

urlpatterns = [
    path("health/", views.health, name="health"),
    path("me/", views.me, name="me"),

    # Uploads + batches
    path("uploads/", views.upload, name="upload"),
    path("batches/", views.batches_list, name="batches-list"),
    path("batches/<uuid:batch_id>/", views.batch_detail, name="batch-detail"),

    # Records
    path("records/", views.records_list, name="records-list"),
    path("records/<uuid:record_id>/", views.record_detail, name="record-detail"),
    path("records/<uuid:record_id>/edit/", views.record_patch, name="record-patch"),
    path("records/<uuid:record_id>/transition/", views.record_transition, name="record-transition"),

    # Lookups
    path("facilities/", views.facilities_list, name="facilities-list"),
    path("activity-types/", views.activity_types_list, name="activity-types-list"),
]

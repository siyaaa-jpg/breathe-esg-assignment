"""
Tenant-scoped manager — every emissions-app model uses this so the API layer
gets queryset.for_org(org) as a habitual filter, rather than relying on each
view to remember to add .filter(organization=request.user.organization).

This is not a hard isolation boundary (a buggy view can still call .objects.all()).
It's a guard rail. The MODEL.md tenancy section explains why we chose this over
schema-per-tenant.
"""
from django.db import models


class TenantScopedQuerySet(models.QuerySet):
    def for_org(self, organization):
        return self.filter(organization=organization)


class TenantScopedManager(models.Manager.from_queryset(TenantScopedQuerySet)):
    pass

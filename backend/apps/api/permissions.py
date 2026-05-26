from rest_framework.permissions import BasePermission


class IsInOrganization(BasePermission):
    """Ensures request.user.organization is set. Tenant-scoped queries
    can then safely use request.user.organization without None checks."""
    message = "User is not associated with an organization."

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.organization_id
        )

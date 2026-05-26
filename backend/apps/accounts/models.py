"""
Two models: Organization (the tenant) and User (which belongs to one Organization).

Organization is the tenant root. Every other tenant-scoped model has an
`organization` FK pointing here. There is no Django-level enforcement that
non-User models filter by org — that's done at the view layer through
serializers and querysets. The cost of that choice (and the alternative of
schema-per-tenant) is discussed in MODEL.md §1.
"""
import uuid

from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from django.utils import timezone

from .managers import UserManager


class Organization(models.Model):
    """The tenant. One row per client company."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=50, unique=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class User(AbstractBaseUser, PermissionsMixin):
    """Email-as-username. Belongs to exactly one Organization in v1."""

    ROLE_ANALYST = "analyst"
    ROLE_ADMIN = "admin"
    ROLE_CHOICES = [
        (ROLE_ANALYST, "Analyst"),
        (ROLE_ADMIN, "Admin"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(unique=True)
    full_name = models.CharField(max_length=200, blank=True)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default=ROLE_ANALYST)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        related_name="users",
        null=True,  # nullable so the very first superuser can be created before any Org exists
        blank=True,
    )
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(default=timezone.now)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    class Meta:
        ordering = ["email"]

    def __str__(self) -> str:
        return self.email

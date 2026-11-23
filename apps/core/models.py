"""Core Stage 3a models: User, PhotographerProfile, StudioMembership, StudioOwnedModel, AuditLog.

Design decisions enforced here:
  - core.User extends AbstractUser (AUTH_USER_MODEL = 'core.User' in settings).
  - One studio per user in v1: StudioMembership.user is a OneToOneField (PROTECT).
  - Owner-only membership: role has a single choice, 'owner'.
  - One currency per studio; GST is optional and only for registered INR studios.
  - GSTIN: blank becomes None; exactly 15 characters; format checked.
    The state code must match the first two GSTIN digits when both are present.
    The GSTIN checksum is NOT verified (deferred).
  - Money fields use DecimalField; never floats.
  - AuditLog is append-only at ORM level (no database triggers).
  - AuditLog.actor is nullable and uses PROTECT.
"""

import re
from decimal import Decimal

from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.core.validators import MaxLengthValidator, MinLengthValidator, RegexValidator
from django.db import models
from django.db.models import Q
from django.db.models.functions import Length
from django.db.models.lookups import Exact

from .validators import (
    validate_country_code,
    validate_currency_code,
    validate_gst_state_code,
    validate_timezone_name,
)

GSTIN_LENGTH = 15
# 2-digit state code, 5 letters, 4 digits, 1 letter, 1 alphanumeric entity code,
# literal 'Z', 1 alphanumeric check character. Format only; checksum not verified.
GSTIN_PATTERN = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")

gstin_format_validator = RegexValidator(
    regex=GSTIN_PATTERN,
    message="Enter a valid 15-character GSTIN.",
)


def _normalise_gstin(value):
    """Return None for blank input; otherwise an uppercase GSTIN without spaces."""
    if value is None:
        return None
    cleaned = "".join(str(value).split()).upper()
    return cleaned or None


class TimeStampedModel(models.Model):
    """Abstract base providing created_at and updated_at."""

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class User(AbstractUser):
    """Custom user model. Extend here only when a field is approved."""

    class Meta:
        db_table = "core_user"


class PhotographerProfile(TimeStampedModel):
    """The studio. One per owner in v1; owner-only membership."""

    class BusinessType(models.TextChoices):
        PHOTOGRAPHY = "photography", "Photography"
        RETAIL = "retail", "Retail"
        FREELANCE = "freelance", "Freelance"
        AGENCY = "agency", "Agency"
        CONSULTING = "consulting", "Consulting"
        OTHER = "other", "Other"

    studio_name = models.CharField(max_length=150)
    business_type = models.CharField(
        max_length=20,
        choices=BusinessType.choices,
        default=BusinessType.PHOTOGRAPHY,
    )
    business_email = models.EmailField()
    business_phone = models.CharField(max_length=20, blank=True, default="")
    website = models.URLField(blank=True, default="")
    address_line1 = models.CharField(max_length=200, blank=True, default="")
    address_line2 = models.CharField(max_length=200, blank=True, default="")
    city = models.CharField(max_length=100, blank=True, default="")
    state_region = models.CharField(max_length=100, blank=True, default="")
    postal_code = models.CharField(max_length=20, blank=True, default="")
    country = models.CharField(
        max_length=2,
        default="IN",
        validators=[validate_country_code],
    )
    currency = models.CharField(
        max_length=3,
        default="INR",
        validators=[validate_currency_code],
    )
    timezone = models.CharField(
        max_length=64,
        default="Asia/Kolkata",
        validators=[validate_timezone_name],
    )
    is_gst_registered = models.BooleanField(default=False)
    gstin = models.CharField(
        max_length=GSTIN_LENGTH,
        null=True,
        blank=True,
        unique=True,
        validators=[
            MinLengthValidator(GSTIN_LENGTH),
            MaxLengthValidator(GSTIN_LENGTH),
            gstin_format_validator,
        ],
    )
    state_code = models.CharField(
        max_length=2,
        blank=True,
        default="",
        validators=[validate_gst_state_code],
    )
    default_gst_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    default_payment_terms_days = models.PositiveSmallIntegerField(default=30)

    class Meta:
        db_table = "core_photographer_profile"
        constraints = [
            # Non-GST studios must have a zero default GST rate.
            models.CheckConstraint(
                condition=Q(is_gst_registered=True) | Q(default_gst_rate=Decimal("0.00")),
                name="core_studio_non_gst_zero_rate",
            ),
            # GST-registered studios must have a GSTIN.
            models.CheckConstraint(
                condition=Q(is_gst_registered=False) | Q(gstin__isnull=False),
                name="core_studio_gst_requires_gstin",
            ),
            # GST-registered studios must use INR.
            models.CheckConstraint(
                condition=Q(is_gst_registered=False) | Q(currency="INR"),
                name="core_studio_gst_requires_inr",
            ),
            # Default GST rate must be between 0 and 100 inclusive.
            models.CheckConstraint(
                condition=Q(default_gst_rate__gte=Decimal("0.00"))
                & Q(default_gst_rate__lte=Decimal("100.00")),
                name="core_studio_gst_rate_range",
            ),
            # When a GSTIN is present it must be exactly GSTIN_LENGTH characters.
            # NULL (no GSTIN) passes.
            # UNVERIFIED: this Q/Exact/Length form has not been tested with
            # Django 5.2 or MySQL 8.0.46. MySQL LENGTH() counts bytes, which
            # matches characters only for ASCII GSTINs.
            models.CheckConstraint(
                condition=Q(gstin__isnull=True)
                | Q(Exact(Length("gstin"), GSTIN_LENGTH)),
                name="core_studio_gstin_length_15",
            ),
        ]

    def __str__(self):
        return self.studio_name

    def clean_fields(self, exclude=None):
        # Normalise before field validators run, so lowercase or spaced input
        # is uppercased and trimmed before the format check.
        self.gstin = _normalise_gstin(self.gstin)
        super().clean_fields(exclude=exclude)

    def clean(self):
        super().clean()
        self.gstin = _normalise_gstin(self.gstin)

        if self.gstin and self.state_code and self.gstin[:2] != self.state_code:
            raise ValidationError(
                {"state_code": "State code must match the first two digits of the GSTIN."}
            )

        if self.is_gst_registered:
            errors = {}
            if not self.gstin:
                errors["gstin"] = "A GSTIN is required for a GST-registered studio."
            if not self.state_code:
                errors["state_code"] = "A state code is required for a GST-registered studio."
            if self.currency != "INR":
                errors["currency"] = "GST invoices are only supported for INR studios."
            if errors:
                raise ValidationError(errors)

    def save(self, *args, **kwargs):
        # Store blank GSTIN as NULL on every save.
        self.gstin = _normalise_gstin(self.gstin)
        super().save(*args, **kwargs)


class StudioMembership(TimeStampedModel):
    """Owner-only membership in v1. Each user belongs to at most one studio."""

    class Role(models.TextChoices):
        OWNER = "owner", "Owner"

    user = models.OneToOneField(
        "core.User",
        on_delete=models.PROTECT,
        related_name="studio_membership",
    )
    studio = models.ForeignKey(
        PhotographerProfile,
        on_delete=models.PROTECT,
        related_name="memberships",
    )
    role = models.CharField(
        max_length=20,
        choices=[("owner", "Owner")],
        default="owner",
    )

    class Meta:
        db_table = "core_studio_membership"

    def __str__(self):
        return f"{self.user} ({self.role}) @ {self.studio}"


class StudioOwnedModel(TimeStampedModel):
    """Reusable abstract base for studio-owned rows.

    Concrete subclasses get related_name '<app_label>_<model>_set'.
    """

    studio = models.ForeignKey(
        PhotographerProfile,
        on_delete=models.PROTECT,
        related_name="%(app_label)s_%(class)s_set",
    )

    class Meta:
        abstract = True


class AuditQuerySet(models.QuerySet):
    """Blocks bulk update and delete."""

    def update(self, **kwargs):
        raise PermissionError("AuditLog is append-only.")

    def delete(self):
        raise PermissionError("AuditLog is append-only.")


class AuditLog(models.Model):
    """Append-only audit record. Not a StudioOwnedModel subclass.

    record_audit in audit.py checks that the entity and actor belong to
    `studio`. Fields match the audit.py interface: studio, actor,
    entity_type, entity_id (CharField 64), action, changes (JSON), created_at.
    """

    studio = models.ForeignKey(
        PhotographerProfile,
        on_delete=models.PROTECT,
        related_name="audit_logs",
    )
    actor = models.ForeignKey(
        "core.User",
        on_delete=models.PROTECT,
        related_name="audit_logs",
        null=True,
        blank=True,
    )
    entity_type = models.CharField(max_length=100)
    entity_id = models.CharField(max_length=64)
    action = models.CharField(max_length=50)
    changes = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = AuditQuerySet.as_manager()

    class Meta:
        db_table = "core_audit_log"
        indexes = [
            models.Index(fields=["studio", "created_at"]),
            models.Index(fields=["entity_type", "entity_id"]),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise PermissionError("AuditLog is append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("AuditLog is append-only.")
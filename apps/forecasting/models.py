"""Forecasting models.

Forecast runs are snapshots; what-if runs are forecast rows only and never touch Finance records.
"""

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from apps.core.models import StudioOwnedModel, TimeStampedModel

ALLOWED_HORIZONS = (30, 60, 90)
ALLOWED_WHAT_IF_KEYS = frozenset({"income_delay_days", "income_haircut_percent", "extra_expense_amount"})


class ForecastRun(StudioOwnedModel):
    """An immutable forecast snapshot (baseline or what-if)."""

    class Kind(models.TextChoices):
        BASELINE = "baseline", "Baseline"
        WHAT_IF = "what_if", "What-if"

    name = models.CharField(max_length=120, blank=True, default="")
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.BASELINE)
    as_of = models.DateField()
    horizon_days = models.PositiveSmallIntegerField()
    opening_balance = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    assumptions = models.JSONField(default=dict, blank=True)
    confirmed_income = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    overdue_confirmed = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    expected_income = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    scheduled_expenses = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    lowest_balance = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    has_cash_gap = models.BooleanField(default=False)
    # Per-bucket summary; values stored as strings.
    buckets = models.JSONField(default=list, blank=True)
    source_run = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="what_if_runs",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="forecast_runs",
    )

    class Meta:
        db_table = "forecasting_forecastrun"
        ordering = ["-created_at", "-pk"]

    def __str__(self):
        return f"{self.get_kind_display()} forecast #{self.pk}"

    def clean(self):
        super().clean()
        if self.horizon_days not in ALLOWED_HORIZONS:
            raise ValidationError({"horizon_days": "Horizon must be 30, 60 or 90 days."})
        if self.kind == self.Kind.WHAT_IF and self.source_run_id is None:
            raise ValidationError({"source_run": "A what-if run needs a baseline run."})
        if self.kind == self.Kind.BASELINE and self.source_run_id is not None:
            raise ValidationError({"source_run": "Only what-if runs can reference a baseline run."})
        if self.source_run_id is not None and self.source_run.studio_id != self.studio_id:
            raise ValidationError({"source_run": "Baseline run must belong to this studio."})
        if self.kind == self.Kind.WHAT_IF:
            unknown = set(self.assumptions or {}) - ALLOWED_WHAT_IF_KEYS
            if unknown:
                raise ValidationError(
                    {"assumptions": f"Unknown what-if assumptions: {', '.join(sorted(unknown))}."}
                )

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Forecast runs are immutable snapshots.")
        if self.horizon_days not in ALLOWED_HORIZONS:
            raise ValidationError("Horizon must be 30, 60 or 90 days.")
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Forecast runs cannot be deleted.")


class ForecastItem(TimeStampedModel):
    """One dated income or expense line belonging to a forecast run."""

    class Source(models.TextChoices):
        CONFIRMED_INCOME = "confirmed_income", "Confirmed income"
        EXPECTED_INCOME = "expected_income", "Expected income"
        SCHEDULED_EXPENSE = "scheduled_expense", "Scheduled expense"

    run = models.ForeignKey(
        "forecasting.ForecastRun",
        on_delete=models.PROTECT,
        related_name="items",
    )
    kind = models.CharField(max_length=20, choices=Source.choices)
    bucket_index = models.PositiveSmallIntegerField()  # 1, 2 or 3
    # None for expected (draft) income: a draft invoice has no real due date.
    due_date = models.DateField(null=True, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    # Label only (e.g. "finance.invoice"); no foreign key.
    source_type = models.CharField(max_length=40, blank=True, default="")
    source_id = models.CharField(max_length=64, blank=True, default="")
    label = models.CharField(max_length=150, blank=True, default="")

    class Meta:
        db_table = "forecasting_forecastitem"
        ordering = ["bucket_index", "due_date", "pk"]

    def clean(self):
        super().clean()
        if self.amount is None or self.amount < 0:
            raise ValidationError({"amount": "Forecast item amount cannot be negative."})
        if self.bucket_index not in (1, 2, 3):
            raise ValidationError({"bucket_index": "Bucket index must be 1, 2 or 3."})
        if self.kind == self.Source.EXPECTED_INCOME:
            if self.due_date is not None:
                raise ValidationError({"due_date": "Expected (draft) income has no due date."})
        elif self.due_date is None:
            raise ValidationError({"due_date": "This forecast item needs a due date."})

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Forecast items are immutable.")
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Forecast items cannot be deleted.")

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q, Sum

from apps.core.models import StudioOwnedModel, TimeStampedModel
from apps.core.money import percent_of, quantize_money
from apps.core.scoping import ensure_same_studio


def _ensure_field_same_studio(obj, studio, field, message):
    try:
        ensure_same_studio(obj, studio)
    except ValidationError:
        raise ValidationError({field: message})


class Invoice(StudioOwnedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ISSUED = "issued", "Issued"
        PARTIALLY_PAID = "partially_paid", "Partially paid"
        PAID = "paid", "Paid"
        VOID = "void", "Void"

    client = models.ForeignKey(
        "clients.Client",
        on_delete=models.PROTECT,
        related_name="invoices",
    )
    project = models.ForeignKey(
        "clients.Project",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="invoices",
    )
    invoice_number = models.CharField(max_length=32, blank=True, default="")
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    currency = models.CharField(max_length=3, default="INR")
    issue_date = models.DateField(null=True, blank=True)
    due_date = models.DateField(null=True, blank=True)
    subtotal = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00")
    )
    gst_rate = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("0.00")
    )
    gst_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00")
    )
    total = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00")
    )
    issued_snapshot = models.JSONField(null=True, blank=True)

    class Meta:
        db_table = "finance_invoice"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["studio", "invoice_number"],
                condition=~Q(invoice_number=""),
                name="finance_invoice_number_unique_per_studio",
            ),
        ]

    def __str__(self):
        return self.invoice_number or f"Draft invoice #{self.pk}"

    def recalculate(self):
        subtotal = self.line_items.aggregate(total=Sum("line_total"))["total"] or Decimal("0")
        self.subtotal = quantize_money(subtotal)
        if self.studio.is_gst_registered:
            self.gst_amount = quantize_money(percent_of(self.subtotal, self.gst_rate))
        else:
            self.gst_rate = Decimal("0.00")
            self.gst_amount = Decimal("0.00")
        self.total = quantize_money(self.subtotal + self.gst_amount)
        self.save()

    def issue(self, invoice_number, issue_date, due_date):
        if self.status != self.Status.DRAFT:
            raise ValidationError("Only draft invoices can be issued.")
        if not self.line_items.exists():
            raise ValidationError("An invoice needs at least one line item to be issued.")
        if self.currency != self.studio.currency:
            raise ValidationError({"currency": "Invoice currency must match the studio currency."})
        self.invoice_number = invoice_number
        self.issue_date = issue_date
        self.due_date = due_date
        self.status = self.Status.ISSUED
        self.issued_snapshot = {
            "invoice_number": invoice_number,
            "issue_date": issue_date.isoformat(),
            "due_date": due_date.isoformat(),
            "client": self.client.name,
            "studio": self.studio.studio_name,
            "studio_gstin": self.studio.gstin,
            "currency": self.currency,
            "subtotal": str(self.subtotal),
            "gst_rate": str(self.gst_rate),
            "gst_amount": str(self.gst_amount),
            "total": str(self.total),
            "line_items": [
                {
                    "description": item.description,
                    "quantity": str(item.quantity),
                    "unit_price": str(item.unit_price),
                    "line_total": str(item.line_total),
                }
                for item in self.line_items.all()
            ],
        }
        self.save()

    def save(self, *args, **kwargs):
        if self.pk is None and self.studio.is_gst_registered and self.gst_rate == Decimal("0.00"):
            self.gst_rate = self.studio.default_gst_rate
        if self.pk is not None:
            stored = Invoice.objects.filter(pk=self.pk).values_list("status", flat=True).first()
            if stored is not None and stored != self.Status.DRAFT:
                update_fields = kwargs.get("update_fields")
                if update_fields is None or set(update_fields) != {"status", "updated_at"}:
                    raise ValidationError("Issued invoices are immutable.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.status != self.Status.DRAFT:
            raise ValidationError("Only draft invoices can be deleted.")
        with transaction.atomic():
            self.line_items.all().delete()
            return super().delete(*args, **kwargs)

    def clean(self):
        super().clean()
        if self.client_id:
            _ensure_field_same_studio(
                self.client, self.studio, "client", "Client must belong to this studio."
            )
        if self.project_id:
            _ensure_field_same_studio(
                self.project, self.studio, "project", "Project must belong to this studio."
            )
            if self.client_id and self.project.client_id != self.client_id:
                raise ValidationError({"project": "Project must belong to the selected client."})

    @property
    def balance_due(self):
        allocated = self.allocations.filter(
            payment__reversal__isnull=True
        ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
        return quantize_money(self.total - allocated)


class InvoiceLineItem(TimeStampedModel):
    invoice = models.ForeignKey(
        "finance.Invoice",
        on_delete=models.PROTECT,
        related_name="line_items",
    )
    description = models.CharField(max_length=255)
    quantity = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("1.00")
    )
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    line_total = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00")
    )
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = "finance_invoice_line_item"
        ordering = ["position", "pk"]

    def __str__(self):
        return self.description

    def _ensure_draft(self):
        if self.invoice.status != Invoice.Status.DRAFT:
            raise ValidationError("Line items can only change on draft invoices.")

    def save(self, *args, **kwargs):
        if self.quantity is None or self.quantity <= 0:
            raise ValidationError("Quantity must be greater than zero.")
        if self.unit_price is None or self.unit_price < 0:
            raise ValidationError("Unit price cannot be negative.")
        self._ensure_draft()
        self.line_total = quantize_money(self.quantity * self.unit_price)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        self._ensure_draft()
        return super().delete(*args, **kwargs)


class Payment(StudioOwnedModel):
    class Method(models.TextChoices):
        CASH = "cash", "Cash"
        BANK_TRANSFER = "bank_transfer", "Bank transfer"
        UPI = "upi", "UPI"
        CARD = "card", "Card"
        CHEQUE = "cheque", "Cheque"
        OTHER = "other", "Other"

    client = models.ForeignKey(
        "clients.Client",
        on_delete=models.PROTECT,
        related_name="payments",
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    received_on = models.DateField()
    method = models.CharField(
        max_length=20,
        choices=Method.choices,
        default=Method.OTHER,
    )
    reference = models.CharField(max_length=100, blank=True, default="")

    class Meta:
        db_table = "finance_payment"
        ordering = ["-received_on", "-pk"]

    def __str__(self):
        return f"Payment #{self.pk}"

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            raise ValidationError("Payments are immutable; reverse them instead.")
        if self.amount is None or self.amount <= 0:
            raise ValidationError("Payment amount must be greater than zero.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Payments cannot be deleted; reverse them instead.")

    @property
    def unallocated_amount(self):
        if PaymentReversal.objects.filter(payment=self).exists():
            return Decimal("0.00")
        allocated = self.allocations.aggregate(total=Sum("amount"))["total"] or Decimal("0")
        return quantize_money(self.amount - allocated)

    def clean(self):
        super().clean()
        if self.client_id:
            _ensure_field_same_studio(
                self.client, self.studio, "client", "Client must belong to this studio."
            )


class PaymentAllocation(StudioOwnedModel):
    payment = models.ForeignKey(
        "finance.Payment",
        on_delete=models.PROTECT,
        related_name="allocations",
    )
    invoice = models.ForeignKey(
        "finance.Invoice",
        on_delete=models.PROTECT,
        related_name="allocations",
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        db_table = "finance_payment_allocation"
        ordering = ["pk"]

    def __str__(self):
        return f"Allocation #{self.pk}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Allocations are immutable.")
        with transaction.atomic():
            payment = Payment.objects.select_for_update().get(pk=self.payment_id)
            invoice = Invoice.objects.select_for_update().get(pk=self.invoice_id)
            if not (payment.studio_id == self.studio_id == invoice.studio_id):
                raise ValidationError("Payment and invoice must belong to the same studio.")
            if invoice.client_id != payment.client_id:
                raise ValidationError("Payment and invoice must belong to the same client.")
            if invoice.status not in (Invoice.Status.ISSUED, Invoice.Status.PARTIALLY_PAID):
                raise ValidationError("Payments can only be allocated to issued invoices.")
            if self.amount is None or self.amount <= 0:
                raise ValidationError("Allocation amount must be greater than zero.")
            if self.amount > payment.unallocated_amount:
                raise ValidationError("Allocation exceeds the payment's unallocated amount.")
            if self.amount > invoice.balance_due:
                raise ValidationError("Allocation exceeds the invoice balance due.")
            super().save(*args, **kwargs)
            balance = invoice.balance_due
            invoice.status = (
                Invoice.Status.PAID if balance == 0 else Invoice.Status.PARTIALLY_PAID
            )
            invoice.save(update_fields=["status", "updated_at"])

    def delete(self, *args, **kwargs):
        raise ValidationError("Allocations are immutable.")


class PaymentReversal(StudioOwnedModel):
    payment = models.OneToOneField(
        "finance.Payment",
        on_delete=models.PROTECT,
        related_name="reversal",
    )
    reason = models.CharField(max_length=255)
    reversed_at = models.DateTimeField(auto_now_add=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="payment_reversals",
    )

    class Meta:
        db_table = "finance_payment_reversal"
        ordering = ["-reversed_at"]

    def __str__(self):
        return f"Reversal of payment #{self.payment_id}"

    def clean(self):
        super().clean()
        if self.payment_id and self.payment.studio_id != self.studio_id:
            raise ValidationError({"payment": "Payment must belong to this studio."})

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Reversals are immutable.")
        if not (self.reason or "").strip():
            raise ValidationError({"reason": "A reason is required to reverse a payment."})
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Reversals are immutable.")


class Expense(StudioOwnedModel):
    category = models.CharField(max_length=50)
    vendor = models.CharField(max_length=150, blank=True, default="")
    description = models.CharField(max_length=255, blank=True, default="")
    expense_date = models.DateField()
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    project = models.ForeignKey(
        "clients.Project",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="expenses",
    )

    class Meta:
        db_table = "finance_expense"
        ordering = ["-expense_date", "-pk"]

    def __str__(self):
        return f"{self.category} expense #{self.pk}"

    def save(self, *args, **kwargs):
        if self.amount is None or self.amount <= 0:
            raise ValidationError("Expense amount must be greater than zero.")
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        if self.project_id:
            _ensure_field_same_studio(
                self.project, self.studio, "project", "Project must belong to this studio."
            )

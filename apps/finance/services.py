from datetime import date, timedelta

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.core.audit import record_audit
from apps.core.models import PhotographerProfile, StudioMembership
from apps.core.money import to_decimal
from apps.core.scoping import ensure_same_studio

from .models import Expense, Invoice, InvoiceLineItem, Payment, PaymentAllocation, PaymentReversal


def _check_scope(studio, actor):
    if not isinstance(studio, PhotographerProfile) or studio.pk is None:
        raise ValidationError("A valid studio is required.")
    if actor is not None and not StudioMembership.objects.filter(user=actor, studio=studio).exists():
        raise ValidationError("Actor does not belong to this studio.")


def _expense_snapshot(e):
    return {
        "category": e.category,
        "vendor": e.vendor,
        "description": e.description,
        "expense_date": e.expense_date,
        "amount": str(e.amount),
        "project_id": e.project_id,
    }


def issue_invoice(*, studio, actor, invoice):
    _check_scope(studio, actor)
    with transaction.atomic():
        PhotographerProfile.objects.select_for_update().get(pk=studio.pk)
        last = (
            Invoice.objects.filter(studio=studio)
            .exclude(invoice_number="")
            .order_by("-invoice_number")
            .first()
        )
        seq = int(last.invoice_number.split("-")[1]) + 1 if last else 1
        number = f"INV-{seq:05d}"
        if invoice.studio_id != studio.pk:
            raise ValidationError("Invoice does not belong to this studio.")
        invoice.recalculate()
        today = date.today()
        due = today + timedelta(days=studio.default_payment_terms_days)
        invoice.issue(number, today, due)
        record_audit(
            studio=studio,
            actor=actor,
            action="invoice.issued",
            entity=invoice,
            before={"status": Invoice.Status.DRAFT.value},
            after={
                "status": invoice.status,
                "invoice_number": number,
                "total": str(invoice.total),
            },
        )
    return invoice


def record_payment(*, studio, actor, client, amount, received_on, method, reference=""):
    _check_scope(studio, actor)
    try:
        ensure_same_studio(client, studio)
    except ValidationError:
        raise ValidationError({"client": "Client must belong to this studio."})
    with transaction.atomic():
        payment = Payment(
            studio=studio,
            client=client,
            amount=to_decimal(amount),
            received_on=received_on,
            method=method,
            reference=reference,
        )
        payment.full_clean()
        payment.save()
        record_audit(
            studio=studio,
            actor=actor,
            action="payment.recorded",
            entity=payment,
            before=None,
            after={"amount": str(payment.amount), "method": payment.method},
        )
    return payment


def allocate_payment(*, studio, actor, payment, invoice, amount):
    _check_scope(studio, actor)
    with transaction.atomic():
        allocation = PaymentAllocation(
            studio=studio,
            payment=payment,
            invoice=invoice,
            amount=to_decimal(amount),
        )
        allocation.save()
        record_audit(
            studio=studio,
            actor=actor,
            action="payment.allocated",
            entity=allocation,
            before=None,
            after={
                "payment_id": payment.pk,
                "invoice_id": invoice.pk,
                "amount": str(allocation.amount),
            },
        )
    return allocation


def reverse_payment(*, studio, actor, payment, reason):
    _check_scope(studio, actor)
    with transaction.atomic():
        payment = Payment.objects.select_for_update().get(pk=payment.pk)
        if payment.studio_id != studio.pk:
            raise ValidationError("Payment does not belong to this studio.")
        if PaymentReversal.objects.filter(payment=payment).exists():
            raise ValidationError("Payment has already been reversed.")
        reversal = PaymentReversal(
            studio=studio,
            payment=payment,
            reason=reason,
            actor=actor,
        )
        reversal.full_clean()
        reversal.save()

        invoice_ids = list(
            Invoice.objects.filter(allocations__payment=payment)
            .values_list("pk", flat=True)
            .distinct()
            .order_by("pk")
        )
        for invoice_id in invoice_ids:
            invoice = Invoice.objects.select_for_update().get(pk=invoice_id)
            if invoice.status not in (
                Invoice.Status.ISSUED,
                Invoice.Status.PARTIALLY_PAID,
                Invoice.Status.PAID,
            ):
                continue
            if invoice.balance_due == 0:
                new_status = Invoice.Status.PAID
            elif invoice.allocations.filter(payment__reversal__isnull=True).exists():
                new_status = Invoice.Status.PARTIALLY_PAID
            else:
                new_status = Invoice.Status.ISSUED
            if invoice.status != new_status:
                invoice.status = new_status
                invoice.save(update_fields=["status", "updated_at"])

        record_audit(
            studio=studio,
            actor=actor,
            action="payment.reversed",
            entity=payment,
            before={"reversed": False},
            after={"reversed": True, "reason": reason},
        )
    return reversal


def create_expense(*, studio, actor, **fields):
    _check_scope(studio, actor)
    with transaction.atomic():
        expense = Expense(studio=studio, **fields)
        expense.full_clean()
        expense.save()
        record_audit(
            studio=studio,
            actor=actor,
            action="expense.created",
            entity=expense,
            before=None,
            after=_expense_snapshot(expense),
        )
    return expense


def update_expense(*, studio, actor, expense, **fields):
    _check_scope(studio, actor)
    if expense.studio_id != studio.pk:
        raise ValidationError("Expense does not belong to this studio.")
    with transaction.atomic():
        before = _expense_snapshot(expense)
        for key, value in fields.items():
            setattr(expense, key, value)
        expense.full_clean()
        expense.save()
        record_audit(
            studio=studio,
            actor=actor,
            action="expense.updated",
            entity=expense,
            before=before,
            after=_expense_snapshot(expense),
        )
    return expense



def create_draft_invoice(*, studio, actor, client, project=None, line_items):
    _check_scope(studio, actor)
    if not line_items:
        raise ValidationError("Add at least one line item.")
    with transaction.atomic():
        invoice = Invoice(
            studio=studio,
            client=client,
            project=project,
            currency=studio.currency,
        )
        invoice.full_clean()
        invoice.save()
        for index, item in enumerate(line_items):
            InvoiceLineItem(
                invoice=invoice,
                description=item["description"],
                quantity=item["quantity"],
                unit_price=item["unit_price"],
                position=index,
            ).save()
        invoice.recalculate()
    return invoice


def record_and_allocate_payment(*, studio, actor, invoice, amount, received_on, method, reference=""):
    _check_scope(studio, actor)
    amount = to_decimal(amount)
    with transaction.atomic():
        locked = Invoice.objects.select_for_update().get(pk=invoice.pk)
        if locked.studio_id != studio.pk:
            raise ValidationError("Invoice does not belong to this studio.")
        if locked.status not in (Invoice.Status.ISSUED, Invoice.Status.PARTIALLY_PAID):
            raise ValidationError("Payments can only be recorded against issued invoices.")
        if amount <= 0:
            raise ValidationError("Payment amount must be greater than zero.")
        if amount > locked.balance_due:
            raise ValidationError({"amount": "Payment cannot exceed the outstanding balance."})
        payment = record_payment(
            studio=studio,
            actor=actor,
            client=locked.client,
            amount=amount,
            received_on=received_on,
            method=method,
            reference=reference,
        )
        allocate_payment(
            studio=studio,
            actor=actor,
            payment=payment,
            invoice=locked,
            amount=amount,
        )
    return payment

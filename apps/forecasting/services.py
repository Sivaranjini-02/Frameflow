"""Cash-flow forecasting services.

Rules:
- Confirmed income = Invoice.balance_due for invoices with status in (ISSUED, PARTIALLY_PAID)
  whose due_date falls in (as_of, as_of + horizon]. Invoices already due on or before as_of
  and still outstanding are reported as overdue_confirmed and are not in the window.
- Expected income = total of DRAFT invoices, placed in the last bucket (simplifying assumption). These items carry no due date (None) and are labelled "Draft invoice #<pk>".
- Scheduled expenses = Expense.amount with expense_date in (as_of, as_of + horizon].
- Opening balance = the value passed in; there is no cash-balance model.
- What-if runs never write to Invoice, Payment, PaymentAllocation or Expense. They read stored
  baseline ForecastItems and write only new ForecastRun and ForecastItem rows.
"""

from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.core.audit import record_audit
from apps.core.models import PhotographerProfile, StudioMembership
from apps.core.money import quantize_money, to_decimal
from apps.finance.models import Expense, Invoice

from .models import ALLOWED_HORIZONS, ALLOWED_WHAT_IF_KEYS, ForecastItem, ForecastRun

BUCKET_DAYS = 30


def _check_scope(studio, actor):
    if not isinstance(studio, PhotographerProfile) or not studio.pk:
        raise ValidationError("A valid studio is required.")
    if actor is not None and not StudioMembership.objects.filter(user=actor, studio=studio).exists():
        raise ValidationError("Actor does not belong to this studio.")


def _check_horizon(horizon_days):
    if isinstance(horizon_days, bool) or horizon_days not in ALLOWED_HORIZONS:
        raise ValidationError({"horizon_days": "Horizon must be 30, 60 or 90 days."})


def _check_as_of(as_of):
    if type(as_of) is not date:
        raise TypeError("as_of must be a date.")


def _bucket_for(as_of, due_date, horizon):
    days = (due_date - as_of).days
    if days < 1 or days > horizon:
        return None
    return (days - 1) // BUCKET_DAYS + 1


def _bucket_bounds(as_of, index, horizon):
    start = as_of + timedelta(days=(index - 1) * BUCKET_DAYS + 1)
    end = as_of + timedelta(days=min(index * BUCKET_DAYS, horizon))
    return start, end


def _summarise(as_of, horizon, opening, entries):
    """Return (totals, buckets, alerts) for the given entries."""
    zero = Decimal("0.00")
    buckets = []
    alerts = []
    balances = []
    running = opening

    for index in range(1, horizon // BUCKET_DAYS + 1):
        in_bucket = [e for e in entries if e["bucket_index"] == index]
        confirmed_in = sum((e["amount"] for e in in_bucket if e["kind"] == "confirmed_income"), zero)
        expected_in = sum((e["amount"] for e in in_bucket if e["kind"] == "expected_income"), zero)
        expense_out = sum((e["amount"] for e in in_bucket if e["kind"] == "scheduled_expense"), zero)
        net = confirmed_in + expected_in - expense_out
        running = running + net
        balances.append(running)

        start, end = _bucket_bounds(as_of, index, horizon)
        bucket = {
            "index": index,
            "bucket_start": start.isoformat(),
            "bucket_end": end.isoformat(),
            "confirmed_in": str(quantize_money(confirmed_in)),
            "expected_in": str(quantize_money(expected_in)),
            "expense_out": str(quantize_money(expense_out)),
            "net": str(quantize_money(net)),
            "running_balance": str(quantize_money(running)),
        }
        buckets.append(bucket)
        if running < 0:
            alerts.append({
                "bucket_start": bucket["bucket_start"],
                "bucket_end": bucket["bucket_end"],
                "running_balance": bucket["running_balance"],
            })

    totals = {
        "confirmed_income": quantize_money(sum((e["amount"] for e in entries if e["kind"] == "confirmed_income"), zero)),
        "expected_income": quantize_money(sum((e["amount"] for e in entries if e["kind"] == "expected_income"), zero)),
        "scheduled_expenses": quantize_money(sum((e["amount"] for e in entries if e["kind"] == "scheduled_expense"), zero)),
        "lowest_balance": quantize_money(min(balances)) if balances else quantize_money(opening),
        "has_cash_gap": bool(alerts),
    }
    return totals, buckets, alerts


def _save_items(run, entries):
    for e in entries:
        if e["bucket_index"] is None:
            continue
        ForecastItem(
            run=run,
            kind=e["kind"],
            bucket_index=e["bucket_index"],
            due_date=e["due_date"],
            amount=e["amount"],
            source_type=e["source_type"],
            source_id=e["source_id"],
            label=e["label"],
        ).save()


def generate_forecast(*, studio, actor, as_of, horizon_days, opening_balance=Decimal("0.00"), name=""):
    _check_scope(studio, actor)
    _check_horizon(horizon_days)
    _check_as_of(as_of)
    opening = quantize_money(to_decimal(opening_balance))
    zero = Decimal("0.00")

    with transaction.atomic():
        window_end = as_of + timedelta(days=horizon_days)
        entries = []
        overdue = zero

        invoices = (
            Invoice.objects.filter(
                studio=studio,
                status__in=[Invoice.Status.ISSUED, Invoice.Status.PARTIALLY_PAID],
            ).exclude(due_date=None)
        )
        for invoice in invoices:
            balance = invoice.balance_due
            if balance <= 0:
                continue
            if invoice.due_date <= as_of:
                overdue += balance
            elif invoice.due_date <= window_end:
                entries.append({
                    "kind": "confirmed_income",
                    "bucket_index": _bucket_for(as_of, invoice.due_date, horizon_days),
                    "amount": balance,
                    "due_date": invoice.due_date,
                    "source_type": "finance.invoice",
                    "source_id": str(invoice.pk),
                    "label": invoice.invoice_number or "Invoice",
                })

        drafts = Invoice.objects.filter(studio=studio, status=Invoice.Status.DRAFT)
        for invoice in drafts:
            if invoice.total <= 0:
                continue
            entries.append({
                "kind": "expected_income",
                "bucket_index": horizon_days // BUCKET_DAYS,
                "amount": invoice.total,
                "due_date": None,
                "source_type": "finance.invoice",
                "source_id": str(invoice.pk),
                "label": f"Draft invoice #{invoice.pk}",
            })

        expenses = Expense.objects.filter(
            studio=studio,
            expense_date__gt=as_of,
            expense_date__lte=window_end,
        )
        for expense in expenses:
            entries.append({
                "kind": "scheduled_expense",
                "bucket_index": _bucket_for(as_of, expense.expense_date, horizon_days),
                "amount": expense.amount,
                "due_date": expense.expense_date,
                "source_type": "finance.expense",
                "source_id": str(expense.pk),
                "label": expense.category,
            })

        entries = [e for e in entries if e["bucket_index"] is not None]
        totals, buckets, _alerts = _summarise(as_of, horizon_days, opening, entries)

        run = ForecastRun(
            studio=studio,
            name=name,
            kind=ForecastRun.Kind.BASELINE,
            as_of=as_of,
            horizon_days=horizon_days,
            opening_balance=opening,
            confirmed_income=totals["confirmed_income"],
            overdue_confirmed=quantize_money(overdue),
            expected_income=totals["expected_income"],
            scheduled_expenses=totals["scheduled_expenses"],
            lowest_balance=totals["lowest_balance"],
            has_cash_gap=totals["has_cash_gap"],
            buckets=buckets,
            created_by=actor,
        )
        run.save()
        _save_items(run, entries)

        record_audit(
            studio=studio,
            actor=actor,
            action="forecast.generated",
            entity=run,
            before=None,
            after={
                "horizon_days": horizon_days,
                "as_of": as_of.isoformat(),
                "confirmed_income": str(totals["confirmed_income"]),
                "expected_income": str(totals["expected_income"]),
                "scheduled_expenses": str(totals["scheduled_expenses"]),
                "has_cash_gap": bool(totals["has_cash_gap"]),
            },
        )
    return run


def run_what_if(*, studio, actor, source_run, name, assumptions):
    _check_scope(studio, actor)
    if not isinstance(source_run, ForecastRun) or source_run.studio_id != studio.pk:
        raise ValidationError({"source_run": "Baseline run must belong to this studio."})
    if source_run.kind != ForecastRun.Kind.BASELINE:
        raise ValidationError({"source_run": "What-if runs must start from a baseline run."})
    if not isinstance(assumptions, dict):
        raise ValidationError({"assumptions": "Assumptions must be a mapping."})

    unknown = set(assumptions) - ALLOWED_WHAT_IF_KEYS
    if unknown:
        raise ValidationError({"assumptions": f"Unknown what-if assumptions: {', '.join(sorted(unknown))}"})

    delay = assumptions.get("income_delay_days", 0)
    if isinstance(delay, bool) or not isinstance(delay, int) or delay < 0:
        raise ValidationError({"assumptions": "income_delay_days must be a non-negative integer."})
    try:
        haircut = to_decimal(assumptions.get("income_haircut_percent", "0"))
        extra = to_decimal(assumptions.get("extra_expense_amount", "0"))
    except (TypeError, ValueError):
        raise ValidationError({"assumptions": "What-if amounts must be numeric."})
    if haircut < 0 or haircut > 100:
        raise ValidationError({"assumptions": "income_haircut_percent must be between 0 and 100."})
    if extra < 0:
        raise ValidationError({"assumptions": "extra_expense_amount must be zero or more."})

    as_of = source_run.as_of
    horizon = source_run.horizon_days
    keep_factor = (Decimal("100") - haircut) / Decimal("100")

    entries = []
    for item in source_run.items.all():
        if item.kind == "confirmed_income":
            new_due = item.due_date + timedelta(days=delay)
            bucket = _bucket_for(as_of, new_due, horizon)
            if bucket is None:
                continue
            amount = quantize_money(item.amount * keep_factor)
            due_date = new_due
        elif item.kind == "expected_income":
            bucket = horizon // BUCKET_DAYS
            amount = quantize_money(item.amount * keep_factor)
            due_date = item.due_date
        elif item.kind == "scheduled_expense":
            bucket = item.bucket_index
            amount = item.amount
            due_date = item.due_date
        else:
            continue
        entries.append({
            "kind": item.kind,
            "bucket_index": bucket,
            "amount": amount,
            "due_date": due_date,
            "source_type": item.source_type,
            "source_id": item.source_id,
            "label": item.label,
        })

    if extra > 0:
        entries.append({
            "kind": "scheduled_expense",
            "bucket_index": 1,
            "amount": quantize_money(extra),
            "due_date": as_of + timedelta(days=1),
            "source_type": "what_if.extra_expense",
            "source_id": "",
            "label": "What-if extra expense",
        })

    totals, buckets, _alerts = _summarise(as_of, horizon, source_run.opening_balance, entries)

    with transaction.atomic():
        new_run = ForecastRun(
            studio=studio,
            name=name,
            kind=ForecastRun.Kind.WHAT_IF,
            source_run=source_run,
            as_of=as_of,
            horizon_days=horizon,
            opening_balance=source_run.opening_balance,
            assumptions={
                "income_delay_days": str(delay),
                "income_haircut_percent": str(haircut),
                "extra_expense_amount": str(extra),
            },
            overdue_confirmed=source_run.overdue_confirmed,
            confirmed_income=totals["confirmed_income"],
            expected_income=totals["expected_income"],
            scheduled_expenses=totals["scheduled_expenses"],
            lowest_balance=totals["lowest_balance"],
            has_cash_gap=totals["has_cash_gap"],
            buckets=buckets,
            created_by=actor,
        )
        new_run.save()
        _save_items(new_run, entries)

        record_audit(
            studio=studio,
            actor=actor,
            action="forecast.what_if_run",
            entity=new_run,
            before=None,
            after={
                "source_run_id": source_run.pk,
                "income_delay_days": str(delay),
                "income_haircut_percent": str(haircut),
                "extra_expense_amount": str(extra),
                "lowest_balance": str(totals["lowest_balance"]),
                "has_cash_gap": bool(totals["has_cash_gap"]),
            },
        )
    return new_run

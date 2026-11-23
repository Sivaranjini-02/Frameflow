
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.forms import formset_factory
from django.shortcuts import get_object_or_404, redirect, render

from apps.core.scoping import get_studio_for_user
from apps.clients.models import Client

from .forms import (
    ExpenseForm,
    InvoiceCreateForm,
    LineItemForm,
    PaymentForm,
)
from .models import Expense, Invoice, InvoiceLineItem, PaymentAllocation
from .services import (
    create_draft_invoice,
    create_expense,
    issue_invoice,
    record_and_allocate_payment,
    update_expense,
)


def _studio_or_redirect(request):
    studio = get_studio_for_user(request.user)
    if studio is None:
        return None
    return studio


def _add_validation_error(form, exc):
    if hasattr(exc, "message_dict"):
        for field, errors in exc.message_dict.items():
            if field in form.fields:
                for error in errors:
                    form.add_error(field, error)
            else:
                form.add_error(None, " ".join(errors))
    else:
        form.add_error(None, " ".join(exc.messages))


@login_required
def invoice_list(request):
    studio = _studio_or_redirect(request)
    if studio is None:
        return redirect("core:create_studio")

    invoices = (
        Invoice.objects
        .filter(studio=studio)
        .select_related("client", "project")
        .order_by("-created_at", "-pk")
    )

    return render(
        request,
        "finance/invoice_list.html",
        {"studio": studio, "invoices": invoices},
    )


@login_required
def invoice_create(request):
    studio = _studio_or_redirect(request)
    if studio is None:
        return redirect("core:create_studio")

    InvoiceFormSet = formset_factory(LineItemForm, extra=3)

    if request.method == "POST":
        form = InvoiceCreateForm(request.POST, studio=studio)
        formset = InvoiceFormSet(request.POST)

        if form.is_valid() and formset.is_valid():
            line_items = [
                {
                    "description": item["description"],
                    "quantity": item["quantity"],
                    "unit_price": item["unit_price"],
                }
                for item in formset.cleaned_data
                if item and not item.get("empty")
            ]

            try:
                invoice = create_draft_invoice(
                    studio=studio,
                    actor=request.user,
                    client=form.cleaned_data["client"],
                    project=form.cleaned_data.get("project"),
                    line_items=line_items,
                )
            except ValidationError as exc:
                _add_validation_error(form, exc)
            else:
                messages.success(request, "Invoice draft created.")
                return redirect("finance:invoice_detail", pk=invoice.pk)
    else:
        form = InvoiceCreateForm(studio=studio)
        formset = InvoiceFormSet()

    return render(
        request,
        "finance/invoice_form.html",
        {
            "studio": studio,
            "form": form,
            "formset": formset,
            "title": "Create invoice",
            "cancel_url": "/finance/",
        },
    )


@login_required
def invoice_detail(request, pk):
    studio = _studio_or_redirect(request)
    if studio is None:
        return redirect("core:create_studio")

    invoice = get_object_or_404(
        Invoice.objects.select_related("client", "project"),
        pk=pk,
        studio=studio,
    )

    line_items = invoice.line_items.all().order_by("position", "pk")

    allocations = (
        PaymentAllocation.objects
        .filter(studio=studio, invoice=invoice)
        .select_related("payment", "payment__reversal")
        .order_by("-payment__received_on", "-pk")
    )

    payment_form = PaymentForm()
    balance_due = invoice.balance_due

    return render(
        request,
        "finance/invoice_detail.html",
        {
            "studio": studio,
            "invoice": invoice,
            "line_items": line_items,
            "allocations": allocations,
            "payment_form": payment_form,
            "balance_due": balance_due,
        },
    )


@login_required
def invoice_issue(request, pk):
    if request.method != "POST":
        return redirect("finance:invoice_detail", pk=pk)

    studio = _studio_or_redirect(request)
    if studio is None:
        return redirect("core:create_studio")

    invoice = get_object_or_404(
        Invoice,
        pk=pk,
        studio=studio,
    )

    try:
        issue_invoice(
            studio=studio,
            actor=request.user,
            invoice=invoice,
        )
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        messages.success(request, "Invoice issued successfully.")

    return redirect("finance:invoice_detail", pk=pk)


@login_required
def invoice_record_payment(request, pk):
    studio = _studio_or_redirect(request)
    if studio is None:
        return redirect("core:create_studio")

    invoice = get_object_or_404(
        Invoice.objects.select_related("client"),
        pk=pk,
        studio=studio,
    )

    if request.method != "POST":
        return redirect("finance:invoice_detail", pk=pk)

    form = PaymentForm(request.POST)

    if form.is_valid():
        try:
            record_and_allocate_payment(
                studio=studio,
                actor=request.user,
                invoice=invoice,
                amount=form.cleaned_data["amount"],
                received_on=form.cleaned_data["received_on"],
                method=form.cleaned_data["method"],
                reference=form.cleaned_data["reference"],
            )
        except ValidationError as exc:
            _add_validation_error(form, exc)
        else:
            messages.success(request, "Payment recorded successfully.")
            return redirect("finance:invoice_detail", pk=pk)

    line_items = invoice.line_items.all().order_by("position", "pk")
    allocations = (
        PaymentAllocation.objects
        .filter(studio=studio, invoice=invoice)
        .select_related("payment", "payment__reversal")
        .order_by("-payment__received_on", "-pk")
    )

    return render(
        request,
        "finance/invoice_detail.html",
        {
            "studio": studio,
            "invoice": invoice,
            "line_items": line_items,
            "allocations": allocations,
            "payment_form": form,
            "balance_due": invoice.balance_due,
        },
    )


@login_required
def expense_list(request):
    studio = _studio_or_redirect(request)
    if studio is None:
        return redirect("core:create_studio")

    expenses = (
        Expense.objects
        .filter(studio=studio)
        .select_related("project")
        .order_by("-expense_date", "-pk")
    )

    return render(
        request,
        "finance/expense_list.html",
        {"studio": studio, "expenses": expenses},
    )


@login_required
def expense_create(request):
    studio = _studio_or_redirect(request)
    if studio is None:
        return redirect("core:create_studio")

    if request.method == "POST":
        form = ExpenseForm(request.POST, studio=studio)

        if form.is_valid():
            try:
                expense = create_expense(
                    studio=studio,
                    actor=request.user,
                    **form.cleaned_data,
                )
            except ValidationError as exc:
                _add_validation_error(form, exc)
            else:
                messages.success(request, "Expense created successfully.")
                return redirect("finance:expense_list")
    else:
        form = ExpenseForm(studio=studio)

    return render(
        request,
        "finance/expense_form.html",
        {
            "studio": studio,
            "form": form,
            "title": "Add expense",
            "cancel_url": "/finance/expenses/",
        },
    )


@login_required
def expense_edit(request, pk):
    studio = _studio_or_redirect(request)
    if studio is None:
        return redirect("core:create_studio")

    expense = get_object_or_404(
        Expense,
        pk=pk,
        studio=studio,
    )

    if request.method == "POST":
        form = ExpenseForm(request.POST, studio=studio)

        if form.is_valid():
            try:
                update_expense(
                    studio=studio,
                    actor=request.user,
                    expense=expense,
                    **form.cleaned_data,
                )
            except ValidationError as exc:
                _add_validation_error(form, exc)
            else:
                messages.success(request, "Expense updated successfully.")
                return redirect("finance:expense_list")
    else:
        form = ExpenseForm(
            studio=studio,
            initial={
                "category": expense.category,
                "vendor": expense.vendor,
                "description": expense.description,
                "expense_date": expense.expense_date,
                "amount": expense.amount,
                "project": expense.project,
            },
        )

    return render(
        request,
        "finance/expense_form.html",
        {
            "studio": studio,
            "form": form,
            "title": "Edit expense",
            "cancel_url": "/finance/expenses/",
        },
    )

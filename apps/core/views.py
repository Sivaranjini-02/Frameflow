
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.shortcuts import redirect, render

from apps.finance.models import Expense, Invoice, PaymentAllocation

from .forms import SignUpForm, StudioForm
from .scoping import get_studio_for_user, scope_queryset
from .services import create_studio_with_owner


@login_required
def dashboard(request):
    """Show studio dashboard with studio-scoped financial analytics."""
    studio = get_studio_for_user(request.user)
    if studio is None:
        return redirect('core:create_studio')

    zero = Decimal('0.00')

    valid_allocations = scope_queryset(
        PaymentAllocation.objects.filter(
            payment__reversal__isnull=True
        ),
        studio,
    )

    revenue_received = (
        valid_allocations.aggregate(total=Sum('amount'))['total'] or zero
    )

    open_invoices = scope_queryset(
        Invoice.objects.filter(
            status__in=[
                Invoice.Status.ISSUED,
                Invoice.Status.PARTIALLY_PAID,
            ]
        ),
        studio,
    )

    invoice_total = open_invoices.aggregate(
        total=Sum('total')
    )['total'] or zero

    allocated_to_open_invoices = valid_allocations.filter(
        invoice__status__in=[
            Invoice.Status.ISSUED,
            Invoice.Status.PARTIALLY_PAID,
        ]
    ).aggregate(total=Sum('amount'))['total'] or zero

    outstanding_invoices = max(
        zero, invoice_total - allocated_to_open_invoices
    )

    total_expenses = (
        scope_queryset(
            Expense.objects.all(), studio
        ).aggregate(total=Sum('amount'))['total'] or zero
    )

    net_position = revenue_received - total_expenses

    analytics = {
        'revenue_received': revenue_received,
        'outstanding_invoices': outstanding_invoices,
        'total_expenses': total_expenses,
        'net_position': net_position,
    }

    return render(
        request,
        'core/dashboard.html',
        {
            'studio': studio,
            'analytics': analytics,
        },
    )


@login_required
def create_studio(request):
    """Create a studio owned by the authenticated user."""
    if get_studio_for_user(request.user) is not None:
        return redirect('core:dashboard')

    if request.method == 'POST':
        form = StudioForm(request.POST)
        if form.is_valid():
            try:
                create_studio_with_owner(request.user, **form.cleaned_data)
            except ValidationError as exc:
                form.add_error(None, ' '.join(exc.messages))
            else:
                messages.success(request, 'Business created.')
                return redirect('core:dashboard')
    else:
        form = StudioForm()

    return render(request, 'core/create_studio.html', {'form': form})


def signup(request):
    """Register a new account, log the user in, then continue to studio creation."""
    if request.user.is_authenticated:
        return redirect('core:dashboard')

    if request.method == 'POST':
        form = SignUpForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            return redirect('core:create_studio')
    else:
        form = SignUpForm()

    return render(request, 'core/signup.html', {'form': form})


import json
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from apps.core.scoping import get_studio_for_user

from .forms import CashflowForm
from .models import ALLOWED_HORIZONS, ForecastRun
from .services import generate_forecast


def _studio_today(studio):
    """Get today's date using the studio's timezone."""
    return datetime.now(ZoneInfo(studio.timezone)).date()


def _latest_baselines(studio):
    """Get the latest baseline snapshot for each horizon."""
    return {
        horizon: ForecastRun.objects.filter(
            studio=studio,
            kind=ForecastRun.Kind.BASELINE,
            horizon_days=horizon,
        ).first()
        for horizon in ALLOWED_HORIZONS
    }


def _first_negative(runs):
    """Find the first negative running balance from stored snapshots."""
    for horizon in ALLOWED_HORIZONS:
        run = runs[horizon]

        if run is None:
            continue

        for bucket in run.buckets:
            if Decimal(bucket["running_balance"]) < 0:
                return {
                    "horizon": horizon,
                    "bucket_start": bucket["bucket_start"],
                    "bucket_end": bucket["bucket_end"],
                    "running_balance": bucket["running_balance"],
                }

    return None


def _value(run, field):
    return str(getattr(run, field)) if run is not None else "-"


def _closing(run):
    if run is None or not run.buckets:
        return "-"

    return run.buckets[-1]["running_balance"]


def _render(request, studio, form):
    runs = _latest_baselines(studio)
    horizons = list(ALLOWED_HORIZONS)

    rows = [
        {
            "label": "Opening balance",
            "values": [
                _value(runs[h], "opening_balance") for h in horizons
            ],
        },
        {
            "label": "Confirmed income",
            "values": [
                _value(runs[h], "confirmed_income") for h in horizons
            ],
        },
        {
            "label": "Expected income",
            "values": [
                _value(runs[h], "expected_income") for h in horizons
            ],
        },
        {
            "label": "Scheduled expenses",
            "values": [
                _value(runs[h], "scheduled_expenses") for h in horizons
            ],
        },
        {
            "label": "Projected closing balance",
            "values": [_closing(runs[h]) for h in horizons],
        },
    ]

    overdue = [
        {
            "days": horizon,
            "value": _value(runs[horizon], "overdue_confirmed"),
        }
        for horizon in horizons
    ]

    # Prepare real forecast data for the cash-flow chart.
    chart_labels = ["Days 1–30", "Days 31–60", "Days 61–90"]

    chart_datasets = []
    for horizon in horizons:
        run = runs[horizon]
        balances = (
            [float(bucket["running_balance"]) for bucket in run.buckets]
            if run is not None
            else []
        )

        chart_datasets.append({
            "label": f"{horizon}-day forecast",
            "data": balances,
        })

    context = {
        "studio": studio,
        "form": form,
        "has_runs": any(run is not None for run in runs.values()),
        "first_negative": _first_negative(runs),
        "horizons": [{"days": horizon} for horizon in horizons],
        "rows": rows,
        "overdue": overdue,
        "runs": [
            runs[horizon]
            for horizon in horizons
            if runs[horizon] is not None
        ],
        "chart_labels": json.dumps(chart_labels),
        "chart_datasets": json.dumps(chart_datasets),
    }

    return render(
        request,
        "forecasting/cashflow.html",
        context,
    )


def _form_for_studio(studio):
    runs = _latest_baselines(studio)
    latest = runs[ALLOWED_HORIZONS[0]]

    opening = (
        latest.opening_balance
        if latest is not None
        else Decimal("0.00")
    )

    return CashflowForm(
        initial={"opening_balance": opening}
    )


@login_required
def cashflow_dashboard(request):
    studio = get_studio_for_user(request.user)

    if studio is None:
        return redirect("core:create_studio")

    return _render(
        request,
        studio,
        _form_for_studio(studio),
    )


@login_required
@require_POST
def cashflow_generate(request):
    studio = get_studio_for_user(request.user)

    if studio is None:
        return redirect("core:create_studio")

    form = CashflowForm(request.POST)

    if not form.is_valid():
        messages.error(
            request,
            "Please enter a valid opening balance.",
        )
        return _render(request, studio, form)

    opening = form.cleaned_data["opening_balance"]
    as_of = _studio_today(studio)

    try:
        with transaction.atomic():
            for horizon in ALLOWED_HORIZONS:
                generate_forecast(
                    studio=studio,
                    actor=request.user,
                    as_of=as_of,
                    horizon_days=horizon,
                    opening_balance=opening,
                    name=f"{horizon}-day baseline",
                )

    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return _render(request, studio, form)

    messages.success(
        request,
        "Forecasts generated for 30, 60 and 90 days.",
    )

    return redirect("forecasting:cashflow")

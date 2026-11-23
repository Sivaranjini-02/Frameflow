from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.core.models import AuditLog, PhotographerProfile, StudioMembership, User
from apps.clients.models import Client
from apps.finance import services as finance_services
from apps.finance.models import Expense, Invoice, Payment
from apps.forecasting import services
from apps.forecasting.models import ForecastItem, ForecastRun


def make_studio(name):
    slug = name.lower().replace(" ", "-")
    return PhotographerProfile.objects.create(
        studio_name=name,
        business_email=f"{slug}@example.com",
        currency="INR",
        is_gst_registered=False,
        default_gst_rate=Decimal("0.00"),
        default_payment_terms_days=30,
    )


def make_member(studio, username):
    user = User.objects.create_user(username=username, password="x")
    StudioMembership.objects.create(user=user, studio=studio)
    return user


def make_invoice(studio, client, total, due, status=Invoice.Status.ISSUED, number=""):
    if status != Invoice.Status.DRAFT and not number:
        # Invoice count only grows in these tests, so this stays unique.
        number = f"INV-{Invoice.objects.count() + 1:05d}"
    return Invoice.objects.create(
        studio=studio,
        client=client,
        status=status,
        invoice_number=number,
        currency="INR",
        issue_date=date(2025, 1, 1) if status != Invoice.Status.DRAFT else None,
        due_date=due,
        subtotal=total,
        total=total,
    )


def make_expense(studio, on, amount):
    return Expense.objects.create(
        studio=studio,
        category="travel",
        expense_date=on,
        amount=Decimal(amount),
    )


class ForecastingTestBase(TestCase):
    def setUp(self):
        self.as_of = date(2025, 6, 1)
        self.studio_a = make_studio("Studio A")
        self.studio_b = make_studio("Studio B")
        self.actor_a = make_member(self.studio_a, "owner_a")
        self.actor_b = make_member(self.studio_b, "owner_b")
        self.client_a = Client.objects.create(studio=self.studio_a, name="Acme")
        self.client_b = Client.objects.create(studio=self.studio_b, name="Other")

    def forecast(self, studio=None, actor=None, horizon=30, opening="0.00"):
        return services.generate_forecast(
            studio=studio or self.studio_a,
            actor=actor or self.actor_a,
            as_of=self.as_of,
            horizon_days=horizon,
            opening_balance=Decimal(opening),
        )

    def what_if(self, source, assumptions, studio=None, actor=None):
        return services.run_what_if(
            studio=studio or self.studio_a,
            actor=actor or self.actor_a,
            source_run=source,
            name="stress",
            assumptions=assumptions,
        )


class BaselineForecastTests(ForecastingTestBase):
    def test_confirmed_income_in_window(self):
        make_invoice(self.studio_a, self.client_a, Decimal("1000.00"), self.as_of + timedelta(days=10))
        run = self.forecast(horizon=30)
        self.assertEqual(run.confirmed_income, Decimal("1000.00"))
        self.assertEqual(run.buckets[0]["confirmed_in"], "1000.00")
        self.assertEqual(run.items.filter(kind=ForecastItem.Source.CONFIRMED_INCOME).count(), 1)

    def test_invoice_due_beyond_horizon_excluded(self):
        make_invoice(self.studio_a, self.client_a, Decimal("1000.00"), self.as_of + timedelta(days=40))
        run = self.forecast(horizon=30)
        self.assertEqual(run.confirmed_income, Decimal("0.00"))

    def test_overdue_reported_separately(self):
        make_invoice(self.studio_a, self.client_a, Decimal("500.00"), self.as_of - timedelta(days=5))
        run = self.forecast(horizon=30)
        self.assertEqual(run.overdue_confirmed, Decimal("500.00"))
        self.assertEqual(run.confirmed_income, Decimal("0.00"))

    def test_partial_payment_reduces_confirmed_income(self):
        invoice = make_invoice(self.studio_a, self.client_a, Decimal("1000.00"), self.as_of + timedelta(days=10))
        payment = finance_services.record_payment(
            studio=self.studio_a,
            actor=self.actor_a,
            client=self.client_a,
            amount="300.00",
            received_on=self.as_of,
            method="upi",
        )
        finance_services.allocate_payment(
            studio=self.studio_a,
            actor=self.actor_a,
            payment=payment,
            invoice=invoice,
            amount="300.00",
        )
        run = self.forecast(horizon=30)
        self.assertEqual(run.confirmed_income, Decimal("700.00"))

    def test_draft_invoice_is_expected_income_in_last_bucket(self):
        make_invoice(self.studio_a, self.client_a, Decimal("250.00"), None, status=Invoice.Status.DRAFT)
        run = self.forecast(horizon=90)
        self.assertEqual(run.expected_income, Decimal("250.00"))
        self.assertEqual(run.buckets[2]["expected_in"], "250.00")
        self.assertEqual(run.buckets[0]["expected_in"], "0.00")
        self.assertEqual(run.confirmed_income, Decimal("0.00"))

    def test_scheduled_expenses_window_only(self):
        make_expense(self.studio_a, self.as_of + timedelta(days=5), "80.00")
        make_expense(self.studio_a, self.as_of - timedelta(days=5), "999.00")
        run = self.forecast(horizon=30)
        self.assertEqual(run.scheduled_expenses, Decimal("80.00"))

    def test_cash_gap_alert(self):
        make_expense(self.studio_a, self.as_of + timedelta(days=5), "200.00")
        run = self.forecast(horizon=30, opening="0.00")
        self.assertTrue(run.has_cash_gap)
        self.assertEqual(run.lowest_balance, Decimal("-200.00"))
        self.assertEqual(run.buckets[0]["running_balance"], "-200.00")
        self.assertEqual(run.buckets[0]["bucket_start"], (self.as_of + timedelta(days=1)).isoformat())

    def test_no_gap_when_income_covers_expense(self):
        make_invoice(self.studio_a, self.client_a, Decimal("1000.00"), self.as_of + timedelta(days=5))
        make_expense(self.studio_a, self.as_of + timedelta(days=6), "200.00")
        run = self.forecast(horizon=30, opening="0.00")
        self.assertFalse(run.has_cash_gap)
        self.assertGreaterEqual(run.lowest_balance, Decimal("0.00"))

    def test_forecast_is_studio_isolated(self):
        make_invoice(self.studio_b, self.client_b, Decimal("1000.00"), self.as_of + timedelta(days=10))
        make_expense(self.studio_b, self.as_of + timedelta(days=5), "200.00")
        run = self.forecast(studio=self.studio_a, actor=self.actor_a, horizon=30)
        self.assertEqual(run.confirmed_income, Decimal("0.00"))
        self.assertEqual(run.scheduled_expenses, Decimal("0.00"))

    def test_horizon_must_be_30_60_or_90(self):
        with self.assertRaises(ValidationError):
            self.forecast(horizon=45)

    def test_float_opening_balance_rejected(self):
        with self.assertRaises(TypeError):
            services.generate_forecast(
                studio=self.studio_a,
                actor=self.actor_a,
                as_of=self.as_of,
                horizon_days=30,
                opening_balance=100.0,
            )

    def test_baseline_run_is_immutable(self):
        make_invoice(self.studio_a, self.client_a, Decimal("1000.00"), self.as_of + timedelta(days=5))
        run = self.forecast(horizon=30)
        run.name = "changed"
        with self.assertRaises(ValidationError):
            run.save()
        with self.assertRaises(ValidationError):
            run.delete()
        item = run.items.first()
        with self.assertRaises(ValidationError):
            item.save()


class WhatIfTests(ForecastingTestBase):
    def test_what_if_does_not_modify_finance_records(self):
        invoice = make_invoice(self.studio_a, self.client_a, Decimal("1000.00"), self.as_of + timedelta(days=5))
        expense = make_expense(self.studio_a, self.as_of + timedelta(days=6), "200.00")
        finance_services.record_payment(
            studio=self.studio_a,
            actor=self.actor_a,
            client=self.client_a,
            amount="100.00",
            received_on=self.as_of,
            method="upi",
        )
        baseline = self.forecast(opening="100.00")
        invoices_before = Invoice.objects.count()
        expenses_before = Expense.objects.count()
        payments_before = Payment.objects.count()

        self.what_if(
            baseline,
            {"income_haircut_percent": "10", "extra_expense_amount": "50.00", "income_delay_days": 0},
        )

        invoice.refresh_from_db()
        expense.refresh_from_db()
        self.assertEqual(invoice.total, Decimal("1000.00"))
        self.assertEqual(invoice.status, Invoice.Status.ISSUED)
        self.assertEqual(expense.amount, Decimal("200.00"))
        self.assertEqual(Expense.objects.count(), expenses_before)
        self.assertEqual(Payment.objects.count(), payments_before)
        self.assertEqual(Invoice.objects.count(), invoices_before)

    def test_what_if_numbers(self):
        make_invoice(self.studio_a, self.client_a, Decimal("1000.00"), self.as_of + timedelta(days=5))
        baseline = self.forecast(opening="100.00")
        what_if = self.what_if(
            baseline,
            {"income_haircut_percent": "10", "extra_expense_amount": "50.00"},
        )
        self.assertEqual(what_if.confirmed_income, Decimal("900.00"))
        self.assertEqual(what_if.buckets[0]["expense_out"], "50.00")
        self.assertEqual(what_if.buckets[0]["running_balance"], "950.00")
        self.assertEqual(what_if.kind, ForecastRun.Kind.WHAT_IF)
        self.assertEqual(what_if.source_run_id, baseline.pk)

    def test_what_if_delay_moves_income_out_of_window(self):
        make_invoice(self.studio_a, self.client_a, Decimal("1000.00"), self.as_of + timedelta(days=20))
        baseline = self.forecast(horizon=30)
        what_if = self.what_if(baseline, {"income_delay_days": 15})
        self.assertEqual(what_if.confirmed_income, Decimal("0.00"))

    def test_what_if_rejects_unknown_assumption(self):
        baseline = self.forecast()
        with self.assertRaises(ValidationError):
            self.what_if(baseline, {"magic": "1"})

    def test_what_if_rejects_negative_delay(self):
        baseline = self.forecast()
        with self.assertRaises(ValidationError):
            self.what_if(baseline, {"income_delay_days": -1})

    def test_what_if_rejects_haircut_over_100(self):
        baseline = self.forecast()
        with self.assertRaises(ValidationError):
            self.what_if(baseline, {"income_haircut_percent": "101"})

    def test_what_if_cannot_start_from_other_studio_run(self):
        baseline_a = self.forecast(studio=self.studio_a, actor=self.actor_a)
        with self.assertRaises(ValidationError):
            self.what_if(baseline_a, {}, studio=self.studio_b, actor=self.actor_b)

    def test_what_if_cannot_start_from_what_if_run(self):
        baseline = self.forecast()
        what_if = self.what_if(baseline, {"extra_expense_amount": "10.00"})
        with self.assertRaises(ValidationError):
            self.what_if(what_if, {})

    def test_actor_from_other_studio_rejected(self):
        with self.assertRaises(ValidationError):
            services.generate_forecast(
                studio=self.studio_a,
                actor=self.actor_b,
                as_of=self.as_of,
                horizon_days=30,
            )


class AuditTests(ForecastingTestBase):
    def test_audit_entries_written(self):
        run = self.forecast()
        self.assertTrue(
            AuditLog.objects.filter(
                studio=self.studio_a,
                action="forecast.generated",
                entity_type="forecasting.forecastrun",
                entity_id=str(run.pk),
            ).exists()
        )
        what_if = self.what_if(run, {"extra_expense_amount": "10.00"})
        self.assertTrue(
            AuditLog.objects.filter(
                studio=self.studio_a,
                action="forecast.what_if_run",
                entity_type="forecasting.forecastrun",
                entity_id=str(what_if.pk),
            ).exists()
        )


class ForecastItemIntegrityTests(TestCase):
    """Draft items carry no due date; validation runs on normal save()."""

    def setUp(self):
        self.as_of = date(2025, 6, 1)
        self.studio = make_studio("Integrity Studio")
        self.actor = make_member(self.studio, "integrity_owner")
        self.client_obj = Client.objects.create(studio=self.studio, name="Acme")

    def _forecast(self, horizon=30):
        return services.generate_forecast(
            studio=self.studio,
            actor=self.actor,
            as_of=self.as_of,
            horizon_days=horizon,
        )

    def test_draft_item_has_no_due_date_and_invoice_reference(self):
        draft = make_invoice(self.studio, self.client_obj, Decimal("250.00"), None, status=Invoice.Status.DRAFT)
        run = self._forecast(horizon=90)
        item = run.items.get(kind=ForecastItem.Source.EXPECTED_INCOME)
        self.assertIsNone(item.due_date)
        self.assertEqual(item.label, f"Draft invoice #{draft.pk}")
        self.assertEqual(item.source_type, "finance.invoice")
        self.assertEqual(item.source_id, str(draft.pk))
        self.assertEqual(item.amount, Decimal("250.00"))

    def test_confirmed_item_keeps_invoice_due_date(self):
        due = self.as_of + timedelta(days=10)
        make_invoice(self.studio, self.client_obj, Decimal("1000.00"), due)
        run = self._forecast(horizon=30)
        item = run.items.get(kind=ForecastItem.Source.CONFIRMED_INCOME)
        self.assertEqual(item.due_date, due)

    def test_what_if_expected_item_keeps_no_due_date(self):
        draft = make_invoice(self.studio, self.client_obj, Decimal("250.00"), None, status=Invoice.Status.DRAFT)
        baseline = self._forecast(horizon=90)
        what_if = services.run_what_if(
            studio=self.studio,
            actor=self.actor,
            source_run=baseline,
            name="check",
            assumptions={},
        )
        item = what_if.items.get(kind=ForecastItem.Source.EXPECTED_INCOME)
        self.assertIsNone(item.due_date)
        self.assertEqual(item.label, f"Draft invoice #{draft.pk}")

    def test_confirmed_item_requires_due_date(self):
        baseline = self._forecast(horizon=30)
        with self.assertRaises(ValidationError):
            ForecastItem(
                run=baseline,
                kind=ForecastItem.Source.CONFIRMED_INCOME,
                bucket_index=1,
                due_date=None,
                amount=Decimal("10.00"),
            ).save()

    def test_expected_item_rejects_due_date(self):
        baseline = self._forecast(horizon=30)
        with self.assertRaises(ValidationError):
            ForecastItem(
                run=baseline,
                kind=ForecastItem.Source.EXPECTED_INCOME,
                bucket_index=3,
                due_date=self.as_of + timedelta(days=80),
                amount=Decimal("10.00"),
            ).save()

    def test_item_save_runs_full_clean(self):
        baseline = self._forecast(horizon=30)
        with self.assertRaises(ValidationError):
            ForecastItem(
                run=baseline,
                kind="not_a_kind",
                bucket_index=1,
                due_date=self.as_of + timedelta(days=5),
                amount=Decimal("10.00"),
            ).save()

    def test_item_rejects_bucket_out_of_range(self):
        baseline = self._forecast(horizon=30)
        with self.assertRaises(ValidationError):
            ForecastItem(
                run=baseline,
                kind=ForecastItem.Source.SCHEDULED_EXPENSE,
                bucket_index=4,
                due_date=self.as_of + timedelta(days=5),
                amount=Decimal("10.00"),
            ).save()

    def test_run_save_runs_full_clean(self):
        with self.assertRaises(ValidationError):
            ForecastRun(
                studio=self.studio,
                kind=ForecastRun.Kind.WHAT_IF,
                as_of=self.as_of,
                horizon_days=30,
            ).save()

    def test_run_save_rejects_unknown_kind(self):
        with self.assertRaises(ValidationError):
            ForecastRun(
                studio=self.studio,
                kind="bogus",
                as_of=self.as_of,
                horizon_days=30,
            ).save()

    def test_existing_items_are_still_immutable(self):
        make_invoice(self.studio, self.client_obj, Decimal("100.00"), self.as_of + timedelta(days=5))
        baseline = self._forecast(horizon=30)
        item = baseline.items.first()
        item.amount = Decimal("1.00")
        with self.assertRaises(ValidationError):
            item.save()

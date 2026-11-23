from datetime import date, datetime
from decimal import Decimal
from unittest import mock
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from apps.core.models import AuditLog
from apps.core.services import create_studio_with_owner
from apps.finance.models import Invoice, InvoiceLineItem
from apps.finance.services import issue_invoice
from apps.forecasting import views as forecasting_views
from apps.forecasting.models import ForecastRun

User = get_user_model()


class CashflowViewTestBase(TestCase):
    def setUp(self):
        self.user_a = User.objects.create_user(username="cf_a", password="x-Pass-123")
        self.studio_a = create_studio_with_owner(
            self.user_a, studio_name="CF A", business_email="cfa@example.com",
        )
        self.user_b = User.objects.create_user(username="cf_b", password="x-Pass-123")
        self.studio_b = create_studio_with_owner(
            self.user_b, studio_name="CF B", business_email="cfb@example.com",
        )
        self.client_a = self.client_class()
        self.client_a.force_login(self.user_a)
        self.client_b = self.client_class()
        self.client_b.force_login(self.user_b)

    def generate(self, opening="0.00", client=None):
        return (client or self.client_a).post(
            reverse("forecasting:cashflow_generate"), {"opening_balance": opening},
        )

    def latest_runs(self, studio):
        return {
            r.horizon_days: r
            for r in ForecastRun.objects.filter(
                studio=studio, kind=ForecastRun.Kind.BASELINE,
            ).order_by("pk")
        }


class CashflowAccessTests(CashflowViewTestBase):
    def test_anonymous_user_is_redirected_to_login(self):
        response = self.client_class().get(reverse("forecasting:cashflow"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])

    def test_user_without_studio_is_redirected_to_create_studio(self):
        loner = User.objects.create_user(username="cf_loner", password="x-Pass-123")
        c = self.client_class()
        c.force_login(loner)
        response = c.get(reverse("forecasting:cashflow"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("core:create_studio"))

    def test_generate_rejects_get(self):
        response = self.client_a.get(reverse("forecasting:cashflow_generate"))
        self.assertEqual(response.status_code, 405)
        self.assertEqual(ForecastRun.objects.count(), 0)


class CashflowDashboardTests(CashflowViewTestBase):
    def test_dashboard_without_runs_shows_empty_state(self):
        response = self.client_a.get(reverse("forecasting:cashflow"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No forecast yet")
        self.assertFalse(response.context["has_runs"])

    def test_core_dashboard_links_to_cashflow(self):
        response = self.client_a.get(reverse("core:dashboard"))
        self.assertContains(response, 'href="%s"' % reverse("forecasting:cashflow"))

    def test_isolation_user_b_sees_no_runs_of_studio_a(self):
        self.generate(client=self.client_a)
        response = self.client_b.get(reverse("forecasting:cashflow"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["runs"]), [])
        self.assertFalse(response.context["has_runs"])


class CashflowGenerateTests(CashflowViewTestBase):
    def test_generate_creates_three_baseline_runs_in_one_request(self):
        response = self.generate(opening="1000.00")
        self.assertRedirects(response, reverse("forecasting:cashflow"))
        runs = ForecastRun.objects.filter(studio=self.studio_a, kind=ForecastRun.Kind.BASELINE)
        self.assertEqual(sorted(r.horizon_days for r in runs), [30, 60, 90])
        self.assertEqual({r.as_of for r in runs}, {forecasting_views._studio_today(self.studio_a)})
        self.assertEqual({r.opening_balance for r in runs}, {Decimal("1000.00")})

    def test_generate_writes_one_audit_entry_per_horizon(self):
        self.generate()
        count = AuditLog.objects.filter(studio=self.studio_a, action="forecast.generated").count()
        self.assertEqual(count, 3)

    def test_invalid_opening_balance_creates_nothing(self):
        response = self.generate(opening="not-a-number")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ForecastRun.objects.count(), 0)

    def test_failure_on_one_horizon_rolls_back_all_three(self):
        real = forecasting_views.generate_forecast

        def fail_on_60(**kwargs):
            if kwargs["horizon_days"] == 60:
                raise ValidationError("boom at 60 days")
            return real(**kwargs)

        with mock.patch.object(forecasting_views, "generate_forecast", side_effect=fail_on_60):
            response = self.generate()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "boom at 60 days")
        self.assertEqual(ForecastRun.objects.count(), 0)
        self.assertEqual(
            AuditLog.objects.filter(studio=self.studio_a, action="forecast.generated").count(), 0,
        )

    def test_as_of_uses_studio_timezone_date(self):
        with mock.patch.object(forecasting_views, "_studio_today", return_value=date(2026, 3, 1)):
            self.generate()
        runs = ForecastRun.objects.filter(studio=self.studio_a)
        self.assertEqual({r.as_of for r in runs}, {date(2026, 3, 1)})

    def test_studio_today_follows_studio_timezone(self):
        self.studio_a.timezone = "Pacific/Kiritimati"
        self.studio_a.save()
        expected = datetime.now(ZoneInfo("Pacific/Kiritimati")).date()
        self.assertEqual(forecasting_views._studio_today(self.studio_a), expected)


class CashflowWarningTests(CashflowViewTestBase):
    def test_negative_opening_shows_first_negative_warning(self):
        self.generate(opening="-500.00")
        response = self.client_a.get(reverse("forecasting:cashflow"))
        self.assertContains(response, "Projected cash goes negative")
        first = response.context["first_negative"]
        self.assertIsNotNone(first)
        self.assertEqual(first["horizon"], 30)
        self.assertEqual(Decimal(first["running_balance"]), Decimal("-500.00"))

    def test_positive_opening_shows_no_negative_warning(self):
        self.generate(opening="1000.00")
        response = self.client_a.get(reverse("forecasting:cashflow"))
        self.assertNotContains(response, "Projected cash goes negative")
        self.assertContains(response, "No negative projected balance")
        self.assertIsNone(response.context["first_negative"])


class CashflowOverdueTests(CashflowViewTestBase):
    def test_overdue_invoice_is_shown_separately(self):
        from apps.clients.models import Client

        client_obj = Client.objects.create(studio=self.studio_a, name="Late Payer")
        invoice = Invoice(studio=self.studio_a, client=client_obj, currency=self.studio_a.currency)
        invoice.save()
        InvoiceLineItem(
            invoice=invoice, description="Shoot", quantity=Decimal("1"),
            unit_price=Decimal("300.00"),
        ).save()
        invoice.recalculate()
        issue_invoice(studio=self.studio_a, actor=self.user_a, invoice=invoice)
        Invoice.objects.filter(pk=invoice.pk).update(due_date=date(2020, 1, 1))

        self.generate()
        response = self.client_a.get(reverse("forecasting:cashflow"))
        self.assertContains(response, "Overdue invoices")
        overdue = {item["days"]: item["value"] for item in response.context["overdue"]}
        self.assertEqual(overdue[30], "300.00")
        self.assertContains(response, "Overdue (not in forecast window)")

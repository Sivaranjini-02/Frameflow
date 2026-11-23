from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.clients.models import Client, Project
from apps.core.models import AuditLog, PhotographerProfile, StudioMembership, User
from apps.finance import services
from apps.finance.models import (
    Expense,
    Invoice,
    InvoiceLineItem,
    PaymentReversal,
)


def make_studio(name, gst=False):
    slug = name.lower().replace(" ", "")
    return PhotographerProfile.objects.create(
        studio_name=name,
        business_email=f"{slug}@example.com",
        currency="INR",
        is_gst_registered=gst,
        gstin="27ABCDE1234F1Z5" if gst else None,
        state_code="27" if gst else "",
        default_gst_rate=Decimal("18.00") if gst else Decimal("0.00"),
        default_payment_terms_days=30,
    )


def make_member(studio, username):
    user = User.objects.create_user(username=username, password="x")
    StudioMembership.objects.create(user=user, studio=studio)
    return user


class FinanceRulesTests(TestCase):
    def setUp(self):
        self.studio_gst = make_studio("Gst Studio", gst=True)
        self.studio_plain = make_studio("Plain Studio")
        self.actor = make_member(self.studio_gst, "owner_gst")
        self.other_actor = make_member(self.studio_plain, "owner_plain")
        # Not named "client": Django's test case already uses self.client.
        self.customer = Client.objects.create(studio=self.studio_gst, name="Acme")
        self.customer2 = Client.objects.create(studio=self.studio_gst, name="Beta")
        self.project = Project.objects.create(
            studio=self.studio_gst, client=self.customer, title="Wedding"
        )
        self.other_customer = Client.objects.create(studio=self.studio_plain, name="Other")

    # Helpers

    def new_draft(self, studio, client, project=None, gst_rate=None):
        invoice = Invoice(studio=studio, client=client, project=project)
        if gst_rate is not None:
            invoice.gst_rate = gst_rate
        invoice.save()
        return invoice

    def add_line(self, invoice, description, quantity, unit_price):
        return InvoiceLineItem.objects.create(
            invoice=invoice,
            description=description,
            quantity=Decimal(quantity),
            unit_price=Decimal(unit_price),
        )

    def issued_gst_invoice(self, client=None):
        inv = self.new_draft(self.studio_gst, client or self.customer)
        self.add_line(inv, "Shoot", "1", "100.00")
        services.issue_invoice(studio=self.studio_gst, actor=self.actor, invoice=inv)
        return inv

    def pay(self, amount, client=None):
        return services.record_payment(
            studio=self.studio_gst,
            actor=self.actor,
            client=client or self.customer,
            amount=amount,
            received_on=date(2025, 1, 1),
            method="upi",
        )

    def allocate(self, payment, invoice, amount):
        return services.allocate_payment(
            studio=self.studio_gst,
            actor=self.actor,
            payment=payment,
            invoice=invoice,
            amount=amount,
        )

    # Totals and GST

    def test_non_gst_totals(self):
        inv = self.new_draft(self.studio_plain, self.other_customer)
        self.add_line(inv, "Prints", "2", "100.00")
        self.add_line(inv, "Card", "1", "0.10")
        inv.recalculate()
        inv.refresh_from_db()
        self.assertEqual(inv.subtotal, Decimal("200.10"))
        self.assertEqual(inv.gst_amount, Decimal("0.00"))
        self.assertEqual(inv.total, Decimal("200.10"))

    def test_gst_total(self):
        inv = self.new_draft(self.studio_gst, self.customer)
        self.add_line(inv, "Shoot", "1", "100.00")
        inv.recalculate()
        inv.refresh_from_db()
        self.assertEqual(inv.gst_rate, Decimal("18.00"))
        self.assertEqual(inv.gst_amount, Decimal("18.00"))
        self.assertEqual(inv.total, Decimal("118.00"))

    def test_gst_rounding(self):
        inv = self.new_draft(self.studio_gst, self.customer)
        self.add_line(inv, "Tiny", "1", "0.05")
        inv.recalculate()
        inv.refresh_from_db()
        self.assertEqual(inv.subtotal, Decimal("0.05"))
        self.assertEqual(inv.gst_amount, Decimal("0.01"))
        self.assertEqual(inv.total, Decimal("0.06"))

    def test_line_total_rounding(self):
        inv = self.new_draft(self.studio_gst, self.customer)
        line = self.add_line(inv, "Hours", "1.50", "33.33")
        self.assertEqual(line.line_total, Decimal("50.00"))

    def test_non_gst_studio_ignores_supplied_rate(self):
        inv = self.new_draft(self.studio_plain, self.other_customer, gst_rate=Decimal("18.00"))
        self.add_line(inv, "Shoot", "1", "100.00")
        inv.recalculate()
        inv.refresh_from_db()
        self.assertEqual(inv.gst_rate, Decimal("0.00"))
        self.assertEqual(inv.total, Decimal("100.00"))

    def test_gst_studio_default_rate_applied_on_create(self):
        inv = self.new_draft(self.studio_gst, self.customer)
        inv.refresh_from_db()
        self.assertEqual(inv.gst_rate, Decimal("18.00"))

    # Issuing

    def test_sequential_numbers_and_snapshot(self):
        first = self.issued_gst_invoice()
        second = self.issued_gst_invoice()
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.invoice_number, "INV-00001")
        self.assertEqual(second.invoice_number, "INV-00002")
        self.assertEqual(second.status, "issued")
        self.assertEqual(second.issued_snapshot["total"], "118.00")
        self.assertEqual(second.issued_snapshot["studio"], self.studio_gst.studio_name)

    def test_issue_requires_a_line(self):
        inv = self.new_draft(self.studio_gst, self.customer)
        with self.assertRaises(ValidationError):
            services.issue_invoice(studio=self.studio_gst, actor=self.actor, invoice=inv)

    def test_issue_rejects_currency_mismatch(self):
        inv = self.new_draft(self.studio_gst, self.customer)
        self.add_line(inv, "Shoot", "1", "100.00")
        inv.currency = "USD"
        with self.assertRaises(ValidationError):
            services.issue_invoice(studio=self.studio_gst, actor=self.actor, invoice=inv)

    def test_issued_invoice_is_immutable(self):
        inv = self.issued_gst_invoice()
        line = inv.line_items.get()
        line.unit_price = Decimal("500.00")
        with self.assertRaises(ValidationError):
            line.save()
        inv.total = Decimal("1.00")
        with self.assertRaises(ValidationError):
            inv.save()

    # Deleting

    def test_draft_with_lines_can_be_deleted(self):
        inv = self.new_draft(self.studio_gst, self.customer)
        self.add_line(inv, "Shoot", "1", "100.00")
        pk = inv.pk
        inv.delete()
        self.assertFalse(Invoice.objects.filter(pk=pk).exists())
        self.assertEqual(InvoiceLineItem.objects.filter(invoice_id=pk).count(), 0)

    def test_issued_invoice_cannot_be_deleted(self):
        inv = self.issued_gst_invoice()
        with self.assertRaises(ValidationError):
            inv.delete()

    # Payments and allocations

    def test_partial_then_full_payment(self):
        inv = self.issued_gst_invoice()
        payment1 = self.pay("60.00")
        self.allocate(payment1, inv, "60.00")
        inv.refresh_from_db()
        self.assertEqual(inv.status, "partially_paid")
        self.assertEqual(inv.balance_due, Decimal("58.00"))
        payment2 = self.pay("58.00")
        self.allocate(payment2, inv, "58.00")
        inv.refresh_from_db()
        self.assertEqual(inv.status, "paid")
        self.assertEqual(inv.balance_due, Decimal("0.00"))

    def test_payment_over_allocation_rejected(self):
        inv = self.issued_gst_invoice()
        payment = self.pay("50.00")
        with self.assertRaises(ValidationError):
            self.allocate(payment, inv, "60.00")

    def test_invoice_balance_over_allocation_rejected(self):
        inv = self.issued_gst_invoice()
        payment = self.pay("200.00")
        with self.assertRaises(ValidationError):
            self.allocate(payment, inv, "150.00")

    def test_draft_invoice_allocation_rejected(self):
        inv = self.new_draft(self.studio_gst, self.customer)
        self.add_line(inv, "Shoot", "1", "100.00")
        payment = self.pay("50.00")
        with self.assertRaises(ValidationError):
            self.allocate(payment, inv, "10.00")

    def test_cross_client_allocation_rejected(self):
        inv = self.issued_gst_invoice(client=self.customer2)
        payment = self.pay("50.00", client=self.customer)
        with self.assertRaises(ValidationError):
            self.allocate(payment, inv, "10.00")

    def test_payments_are_immutable(self):
        payment = self.pay("50.00")
        payment.amount = Decimal("1.00")
        with self.assertRaises(ValidationError):
            payment.save()
        with self.assertRaises(ValidationError):
            payment.delete()

    def test_allocations_are_immutable(self):
        inv = self.issued_gst_invoice()
        allocation = self.allocate(self.pay("50.00"), inv, "10.00")
        allocation.amount = Decimal("1.00")
        with self.assertRaises(ValidationError):
            allocation.save()
        with self.assertRaises(ValidationError):
            allocation.delete()

    # Reversals

    def test_full_reversal_of_partial_payment(self):
        inv = self.issued_gst_invoice()
        payment = self.pay("60.00")
        self.allocate(payment, inv, "60.00")
        services.reverse_payment(
            studio=self.studio_gst, actor=self.actor, payment=payment, reason="Bounced"
        )
        inv.refresh_from_db()
        self.assertEqual(inv.status, "issued")
        self.assertEqual(inv.balance_due, Decimal("118.00"))
        self.assertTrue(PaymentReversal.objects.filter(payment=payment).exists())
        self.assertEqual(payment.unallocated_amount, Decimal("0.00"))

    def test_reversal_of_paid_invoice(self):
        inv = self.issued_gst_invoice()
        payment = self.pay("118.00")
        self.allocate(payment, inv, "118.00")
        inv.refresh_from_db()
        self.assertEqual(inv.status, "paid")
        services.reverse_payment(
            studio=self.studio_gst, actor=self.actor, payment=payment, reason="Refund"
        )
        inv.refresh_from_db()
        self.assertEqual(inv.status, "issued")

    def test_double_reversal_rejected(self):
        payment = self.pay("50.00")
        services.reverse_payment(
            studio=self.studio_gst, actor=self.actor, payment=payment, reason="First"
        )
        with self.assertRaises(ValidationError):
            services.reverse_payment(
                studio=self.studio_gst, actor=self.actor, payment=payment, reason="Second"
            )

    def test_blank_reversal_reason_rejected(self):
        payment = self.pay("50.00")
        with self.assertRaises(ValidationError):
            services.reverse_payment(
                studio=self.studio_gst, actor=self.actor, payment=payment, reason="   "
            )

    # Expenses

    def test_expense_create(self):
        expense = services.create_expense(
            studio=self.studio_gst,
            actor=self.actor,
            category="travel",
            vendor="Uber",
            expense_date=date(2025, 1, 2),
            amount=Decimal("250.00"),
            project=self.project,
        )
        self.assertIsInstance(expense, Expense)
        self.assertEqual(expense.amount, Decimal("250.00"))

    def test_expense_amount_must_be_positive(self):
        with self.assertRaises(ValidationError):
            services.create_expense(
                studio=self.studio_gst,
                actor=self.actor,
                category="travel",
                expense_date=date(2025, 1, 2),
                amount=Decimal("0"),
            )

    def test_expense_project_from_other_studio_rejected(self):
        plain_project = Project.objects.create(
            studio=self.studio_plain, client=self.other_customer, title="Plain"
        )
        with self.assertRaises(ValidationError):
            services.create_expense(
                studio=self.studio_gst,
                actor=self.actor,
                category="travel",
                expense_date=date(2025, 1, 2),
                amount=Decimal("10.00"),
                project=plain_project,
            )

    def test_update_expense_audits_before_and_after(self):
        expense = services.create_expense(
            studio=self.studio_gst,
            actor=self.actor,
            category="travel",
            expense_date=date(2025, 1, 2),
            amount=Decimal("250.00"),
        )
        services.update_expense(
            studio=self.studio_gst, actor=self.actor, expense=expense, amount=Decimal("300.00")
        )
        log = AuditLog.objects.filter(action="expense.updated").order_by("-pk").first()
        self.assertEqual(log.changes["before"]["amount"], "250.00")
        self.assertEqual(log.changes["after"]["amount"], "300.00")

    # Audit trail

    def test_audit_entries_for_invoice_payment_flow(self):
        inv = self.issued_gst_invoice()
        payment = self.pay("60.00")
        self.allocate(payment, inv, "60.00")
        services.reverse_payment(
            studio=self.studio_gst, actor=self.actor, payment=payment, reason="Bounced"
        )
        expected = [
            ("invoice.issued", "finance.invoice"),
            ("payment.recorded", "finance.payment"),
            ("payment.allocated", "finance.paymentallocation"),
            ("payment.reversed", "finance.payment"),
        ]
        for action, entity_type in expected:
            self.assertTrue(
                AuditLog.objects.filter(
                    action=action, entity_type=entity_type, studio=self.studio_gst
                ).exists(),
                f"missing audit entry {action}",
            )

    # Input validation and studio isolation

    def test_float_amount_rejected(self):
        with self.assertRaises(TypeError):
            services.record_payment(
                studio=self.studio_gst,
                actor=self.actor,
                client=self.customer,
                amount=100.0,
                received_on=date(2025, 1, 1),
                method="upi",
            )

    def test_payment_client_from_other_studio_rejected(self):
        with self.assertRaises(ValidationError):
            services.record_payment(
                studio=self.studio_gst,
                actor=self.actor,
                client=self.other_customer,
                amount="50.00",
                received_on=date(2025, 1, 1),
                method="upi",
            )

    def test_payment_by_actor_outside_studio_rejected(self):
        with self.assertRaises(ValidationError):
            services.record_payment(
                studio=self.studio_gst,
                actor=self.other_actor,
                client=self.customer,
                amount="50.00",
                received_on=date(2025, 1, 1),
                method="upi",
            )

    def test_allocation_to_invoice_in_other_studio_rejected(self):
        plain_inv = self.new_draft(self.studio_plain, self.other_customer)
        self.add_line(plain_inv, "Shoot", "1", "100.00")
        services.issue_invoice(studio=self.studio_plain, actor=self.other_actor, invoice=plain_inv)
        gst_payment = self.pay("50.00")
        with self.assertRaises(ValidationError):
            self.allocate(gst_payment, plain_inv, "10.00")

    def test_reversal_by_other_studio_rejected(self):
        gst_payment = self.pay("50.00")
        with self.assertRaises(ValidationError):
            services.reverse_payment(
                studio=self.studio_plain,
                actor=self.other_actor,
                payment=gst_payment,
                reason="x",
            )

    def test_issue_by_other_studio_rejected(self):
        gst_draft = self.new_draft(self.studio_gst, self.customer)
        self.add_line(gst_draft, "Shoot", "1", "100.00")
        with self.assertRaises(ValidationError):
            services.issue_invoice(
                studio=self.studio_plain, actor=self.other_actor, invoice=gst_draft
            )

    def test_expense_update_by_other_studio_rejected(self):
        gst_expense = services.create_expense(
            studio=self.studio_gst,
            actor=self.actor,
            category="travel",
            expense_date=date(2025, 1, 2),
            amount=Decimal("250.00"),
        )
        with self.assertRaises(ValidationError):
            services.update_expense(
                studio=self.studio_plain,
                actor=self.other_actor,
                expense=gst_expense,
                amount=Decimal("1.00"),
            )

    def test_invoice_project_from_other_studio_rejected(self):
        plain_project = Project.objects.create(
            studio=self.studio_plain, client=self.other_customer, title="Plain"
        )
        invoice = Invoice(studio=self.studio_gst, client=self.customer, project=plain_project)
        with self.assertRaises(ValidationError):
            invoice.full_clean()

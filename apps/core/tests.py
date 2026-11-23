from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.clients.models import Client
from apps.core.audit import record_audit
from apps.core.models import AuditLog, PhotographerProfile, StudioMembership
from apps.core.money import percent_of, quantize_money
from apps.core.scoping import ensure_same_studio, get_studio_for_user

FAKE_PASSWORD = 'not-a-real-password-test-only'


def make_studio(name='Studio', email='studio@example.com', **kwargs):
    return PhotographerProfile.objects.create(
        studio_name=name, business_email=email, **kwargs
    )


def make_user(username):
    return get_user_model().objects.create_user(username=username, password=FAKE_PASSWORD)


class MoneyTests(TestCase):
    def test_quantize_rounds_half_up(self):
        self.assertEqual(quantize_money(Decimal('0.005')), Decimal('0.01'))
        self.assertEqual(quantize_money(Decimal('0.0049')), Decimal('0.00'))

    def test_percent_of(self):
        self.assertEqual(percent_of(Decimal('1000.00'), Decimal('9')), Decimal('90.00'))

    def test_float_rejected(self):
        with self.assertRaises(TypeError):
            quantize_money(1.5)


class UserTests(TestCase):
    def test_create_user(self):
        user = make_user('alice')
        self.assertIsInstance(user, get_user_model())

    def test_user_model_label(self):
        self.assertEqual(get_user_model()._meta.label, 'core.User')


class ScopingTests(TestCase):
    def test_get_studio_for_user(self):
        studio_a = make_studio('A', 'a@example.com')
        studio_b = make_studio('B', 'b@example.com')
        owner_a = make_user('owner_a')
        owner_b = make_user('owner_b')
        StudioMembership.objects.create(user=owner_a, studio=studio_a)
        StudioMembership.objects.create(user=owner_b, studio=studio_b)

        self.assertEqual(get_studio_for_user(owner_a), studio_a)
        self.assertEqual(get_studio_for_user(owner_b), studio_b)

    def test_no_studio_returns_none(self):
        self.assertIsNone(get_studio_for_user(AnonymousUser()))
        self.assertIsNone(get_studio_for_user(None))
        self.assertIsNone(get_studio_for_user(make_user('loner')))

    def test_ensure_same_studio(self):
        studio_a = make_studio('A', 'a@example.com')
        studio_b = make_studio('B', 'b@example.com')
        client = Client.objects.create(studio=studio_a, name='Acme')
        ensure_same_studio(client, studio_a)
        with self.assertRaises(ValidationError):
            ensure_same_studio(client, studio_b)


class StudioMembershipTests(TestCase):
    def test_second_membership_for_same_user_fails(self):
        studio_a = make_studio('A', 'a@example.com')
        studio_b = make_studio('B', 'b@example.com')
        user = make_user('member')
        StudioMembership.objects.create(user=user, studio=studio_a)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                StudioMembership.objects.create(user=user, studio=studio_b)


class PhotographerProfileConstraintTests(TestCase):
    def test_gst_registered_requires_gstin(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                make_studio(is_gst_registered=True, gstin=None)

    def test_non_gst_requires_zero_rate(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                make_studio(is_gst_registered=False, default_gst_rate=Decimal('18'))

    def test_gst_registered_requires_inr(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                make_studio(
                    is_gst_registered=True,
                    gstin='29ABCDE1234F1Z5',
                    currency='USD',
                )

    def test_clean_rejects_lowercase_currency(self):
        studio = make_studio()
        studio.currency = 'inr'
        with self.assertRaises(ValidationError) as ctx:
            studio.full_clean()
        self.assertIn('currency', ctx.exception.message_dict)


class AuditLogTests(TestCase):
    def setUp(self):
        self.studio = make_studio('Audit', 'audit@example.com')
        self.user = make_user('auditor')
        StudioMembership.objects.create(user=self.user, studio=self.studio)
        self.log = record_audit(
            studio=self.studio,
            actor=self.user,
            action='update',
            entity=self.studio,
            before={'amount': Decimal('1.50')},
            after={'amount': Decimal('2.00')},
        )

    def test_decimal_stored_as_string(self):
        self.assertEqual(
            self.log.changes,
            {'before': {'amount': '1.50'}, 'after': {'amount': '2.00'}},
        )

    def test_resave_raises(self):
        with self.assertRaises(PermissionError):
            self.log.save()

    def test_instance_delete_raises(self):
        with self.assertRaises(PermissionError):
            self.log.delete()

    def test_queryset_update_raises(self):
        with self.assertRaises(PermissionError):
            AuditLog.objects.filter(pk=self.log.pk).update(action='x')

    def test_queryset_delete_raises(self):
        with self.assertRaises(PermissionError):
            AuditLog.objects.filter(pk=self.log.pk).delete()

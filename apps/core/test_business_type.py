from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.clients.forms import ProjectForm
from apps.core.forms import StudioForm
from apps.core.models import PhotographerProfile
from apps.core.services import create_studio_with_owner

User = get_user_model()
PASSWORD = "Sturdy-Pass-42!"


def studio_data(**overrides):
    data = {
        "studio_name": "Type Studio",
        "business_email": "type@example.com",
        "business_phone": "",
        "currency": "INR",
        "timezone": "Asia/Kolkata",
        "country": "IN",
        "business_type": "photography",
    }
    data.update(overrides)
    return data


class BusinessTypeModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="bt_model", password=PASSWORD)

    def test_default_business_type_is_photography(self):
        studio = create_studio_with_owner(
            self.user, studio_name="Default Biz", business_email="d@example.com",
        )
        self.assertEqual(studio.business_type, PhotographerProfile.BusinessType.PHOTOGRAPHY)
        self.assertEqual(studio.business_type, "photography")

    def test_chosen_business_type_is_stored(self):
        studio = create_studio_with_owner(
            self.user, studio_name="Shop", business_email="s@example.com", business_type="retail",
        )
        studio.refresh_from_db()
        self.assertEqual(studio.business_type, "retail")
        self.assertEqual(studio.get_business_type_display(), "Retail")

    def test_all_six_business_types_are_offered(self):
        values = [value for value, _label in PhotographerProfile.BusinessType.choices]
        self.assertEqual(
            values, ["photography", "retail", "freelance", "agency", "consulting", "other"],
        )


class BusinessTypeFormTests(TestCase):
    def test_form_offers_all_business_types(self):
        form = StudioForm()
        choices = [value for value, _label in form.fields["business_type"].choices]
        self.assertIn("photography", choices)
        self.assertIn("retail", choices)
        self.assertIn("other", choices)

    def test_form_accepts_a_valid_business_type(self):
        form = StudioForm(data=studio_data(business_type="agency"))
        self.assertTrue(form.is_valid(), form.errors)

    def test_form_rejects_an_unknown_business_type(self):
        form = StudioForm(data=studio_data(business_type="bakery"))
        self.assertFalse(form.is_valid())
        self.assertIn("business_type", form.errors)

    def test_business_name_label_replaces_studio_name(self):
        form = StudioForm()
        self.assertEqual(form.fields["studio_name"].label, "Business name")


class BusinessTypeViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="bt_view", password=PASSWORD)
        self.client.force_login(self.user)

    def test_create_page_uses_business_wording(self):
        response = self.client.get(reverse("core:create_studio"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Set up your business")
        self.assertContains(response, "Create business")
        self.assertContains(response, 'name="business_type"')

    def test_posting_business_type_persists_it(self):
        response = self.client.post(
            reverse("core:create_studio"), studio_data(business_type="freelance"),
        )
        self.assertRedirects(response, reverse("core:dashboard"))
        studio = PhotographerProfile.objects.get(studio_name="Type Studio")
        self.assertEqual(studio.business_type, "freelance")

    def test_dashboard_shows_selected_business_type(self):
        create_studio_with_owner(
            self.user, studio_name="Dash Biz", business_email="dash@example.com",
            business_type="consulting",
        )
        response = self.client.get(reverse("core:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Business type")
        self.assertContains(response, "Consulting")

    def test_dashboard_heading_says_business_not_studio(self):
        create_studio_with_owner(
            self.user, studio_name="Words Biz", business_email="w@example.com",
        )
        response = self.client.get(reverse("core:dashboard"))
        self.assertContains(response, '<h2 class="h5 card-title">Business</h2>')


class DueDateWordingTests(TestCase):
    def test_project_form_labels_event_date_as_due_date(self):
        from apps.clients.models import Project
        self.assertEqual(Project._meta.get_field("event_date").verbose_name, "event date")
        self.assertEqual(ProjectForm.Meta.labels["event_date"], "Due date")

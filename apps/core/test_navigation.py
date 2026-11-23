from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.core.services import create_studio_with_owner

User = get_user_model()
PASSWORD = "Sturdy-Pass-42!"


class NavigationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="nav_user", password=PASSWORD)
        create_studio_with_owner(
            self.user, studio_name="Nav Business", business_email="nav@example.com",
        )
        self.client.force_login(self.user)

    def test_navbar_links_to_every_main_area(self):
        response = self.client.get(reverse("core:dashboard"))
        self.assertEqual(response.status_code, 200)
        for name in (
            "clients:client_list",
            "clients:project_list",
            "finance:invoice_list",
            "finance:expense_list",
            "forecasting:cashflow",
        ):
            self.assertContains(response, 'href="%s"' % reverse(name))

    def test_navbar_has_responsive_toggler(self):
        response = self.client.get(reverse("core:dashboard"))
        self.assertContains(response, 'data-bs-toggle="collapse"')
        self.assertContains(response, 'id="ffNavMenu"')

    def test_navbar_menu_is_not_shown_to_anonymous_users(self):
        self.client.logout()
        response = self.client.get(reverse("core:login"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'id="ffNavMenu"')


class DashboardQuickActionsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="qa_user", password=PASSWORD)
        create_studio_with_owner(
            self.user, studio_name="QA Business", business_email="qa@example.com",
        )
        self.client.force_login(self.user)

    def test_quick_actions_link_to_all_features(self):
        response = self.client.get(reverse("core:dashboard"))
        self.assertContains(response, 'href="%s"' % reverse("finance:invoice_list"))
        self.assertContains(response, 'href="%s"' % reverse("finance:expense_list"))
        self.assertContains(response, 'href="%s"' % reverse("forecasting:cashflow"))

    def test_coming_soon_message_is_removed(self):
        response = self.client.get(reverse("core:dashboard"))
        self.assertNotContains(response, "Coming soon")

    def test_no_quick_action_button_is_disabled(self):
        response = self.client.get(reverse("core:dashboard"))
        self.assertNotContains(response, "aria-disabled")
        self.assertNotContains(response, "disabled")

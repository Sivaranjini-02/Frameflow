from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.core.models import StudioMembership

User = get_user_model()
PASSWORD = "Sturdy-Pass-42!"


def signup_data(**overrides):
    data = {
        "username": "newphotog",
        "email": "new@example.com",
        "password1": PASSWORD,
        "password2": PASSWORD,
    }
    data.update(overrides)
    return data


def studio_data(**overrides):
    data = {
        "studio_name": "New Studio",
        "business_email": "studio@example.com",
        "business_phone": "",
        "currency": "INR",
        "timezone": "Asia/Kolkata",
        "country": "IN",
    }
    data.update(overrides)
    return data


class SignupTests(TestCase):
    def test_signup_page_renders_for_anonymous_user(self):
        response = self.client.get(reverse("core:signup"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="username"')
        self.assertContains(response, 'name="password1"')

    def test_successful_signup_creates_user_logs_in_and_redirects(self):
        response = self.client.post(reverse("core:signup"), signup_data())
        self.assertRedirects(response, reverse("core:create_studio"))
        user = User.objects.get(username="newphotog")
        self.assertEqual(user.email, "new@example.com")
        self.assertTrue(user.check_password(PASSWORD))
        self.assertNotEqual(user.password, PASSWORD)
        self.assertEqual(self.client.session["_auth_user_id"], str(user.pk))

    def test_signup_does_not_create_a_studio(self):
        self.client.post(reverse("core:signup"), signup_data())
        user = User.objects.get(username="newphotog")
        self.assertFalse(StudioMembership.objects.filter(user=user).exists())

    def test_new_user_can_then_create_a_studio(self):
        self.client.post(reverse("core:signup"), signup_data())
        response = self.client.post(reverse("core:create_studio"), studio_data())
        self.assertRedirects(response, reverse("core:dashboard"))
        user = User.objects.get(username="newphotog")
        self.assertTrue(
            StudioMembership.objects.filter(user=user, studio__studio_name="New Studio").exists()
        )

    def test_duplicate_username_is_rejected(self):
        User.objects.create_user(username="taken", password=PASSWORD, email="t@example.com")
        response = self.client.post(
            reverse("core:signup"), signup_data(username="taken", email="x@example.com"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("username", response.context["form"].errors)
        self.assertEqual(User.objects.filter(username="taken").count(), 1)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_mismatched_passwords_are_rejected(self):
        response = self.client.post(
            reverse("core:signup"), signup_data(password2="Different-Pass-99!"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("password2", response.context["form"].errors)
        self.assertFalse(User.objects.filter(username="newphotog").exists())

    def test_password_validators_are_enforced(self):
        response = self.client.post(
            reverse("core:signup"), signup_data(password1="12345678", password2="12345678"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("password2", response.context["form"].errors)
        self.assertFalse(User.objects.filter(username="newphotog").exists())

    def test_email_is_required(self):
        response = self.client.post(reverse("core:signup"), signup_data(email=""))
        self.assertEqual(response.status_code, 200)
        self.assertIn("email", response.context["form"].errors)
        self.assertFalse(User.objects.filter(username="newphotog").exists())


class SignupAccessTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="existing", password=PASSWORD)

    def test_logged_in_user_is_redirected_away_from_signup(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("core:signup"))
        self.assertRedirects(response, reverse("core:dashboard"), fetch_redirect_response=False)

    def test_logged_in_user_cannot_create_an_account(self):
        self.client.force_login(self.user)
        before = User.objects.count()
        self.client.post(reverse("core:signup"), signup_data())
        self.assertEqual(User.objects.count(), before)
        self.assertFalse(User.objects.filter(username="newphotog").exists())


class LoginLogoutPreservedTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="old", password=PASSWORD)

    def test_login_page_links_to_signup(self):
        response = self.client.get(reverse("core:login"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'href="%s"' % reverse("core:signup"))

    def test_existing_login_still_works(self):
        response = self.client.post(reverse("core:login"), {"username": "old", "password": PASSWORD})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.session["_auth_user_id"], str(self.user.pk))

    def test_logout_still_works(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse("core:logout"))
        self.assertRedirects(response, reverse("core:login"), fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)

from pathlib import Path

from django.test import SimpleTestCase


class BrandingTemplateTests(SimpleTestCase):
    def test_shared_theme_is_maroon_and_gray(self):
        base = Path("templates/base.html").read_text()
        self.assertIn("--sms-maroon-900", base)
        self.assertIn("--sms-gray-900", base)
        self.assertNotIn("--sms-jungle-", base)
        self.assertNotIn("--sms-charcoal-", base)

    def test_sidebar_uses_school_logo_with_icon_fallback(self):
        base = Path("templates/base.html").read_text()
        self.assertIn("dashboard_school.logo.url", base)
        self.assertIn("dashboard_school.short_name", base)
        self.assertIn("bi-mortarboard-fill", base)

    def test_login_is_school_branded(self):
        login = Path("templates/registration/login.html").read_text()
        self.assertIn("login_school.logo.url", login)
        self.assertIn("--maroon-900", login)
        self.assertIn("Welcome back", login)

from pathlib import Path

from django.core.files.storage import storages
from django.test import SimpleTestCase


class BrandingTemplateTests(SimpleTestCase):
    def test_default_media_storage_can_build_urls(self):
        self.assertTrue(storages["default"].url("school/logos/example.png"))

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
        self.assertIn("rel=\"icon\" href=\"{{ dashboard_school.logo.url }}\"", base)

    def test_school_logo_upload_is_available_in_both_school_admin_forms(self):
        config = Path("templates/dashboard/super_admin/school_config.html").read_text()
        from smsApp.admin import SchoolAdmin

        self.assertIn('enctype="multipart/form-data"', config)
        self.assertIn('name="logo" type="file"', config)
        self.assertIn("Upload/replace school logo", config)
        self.assertIn("logo", SchoolAdmin.fields)
        self.assertIn("logo_preview", SchoolAdmin.readonly_fields)

    def test_login_is_school_branded(self):
        login = Path("templates/registration/login.html").read_text()
        self.assertIn("login_school.logo.url", login)
        self.assertIn("rel=\"icon\" href=\"{{ login_school.logo.url }}\"", login)
        self.assertIn("--maroon-900", login)
        self.assertIn("Welcome back", login)

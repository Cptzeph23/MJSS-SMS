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
        self.assertIn("background-color: var(--sms-school-primary, var(--sms-maroon-500))", base)
        self.assertIn("border-left: 4px solid var(--sms-school-primary, var(--sms-maroon-500))", base)
        self.assertIn("--sms-school-primary: {{ dashboard_school.primary_color", base)
        self.assertIn("--sms-school-secondary: {{ dashboard_school.secondary_color", base)
        self.assertNotIn("--sms-jungle-", base)
        self.assertNotIn("--sms-charcoal-", base)

    def test_sidebar_uses_school_logo_with_icon_fallback(self):
        base = Path("templates/base.html").read_text()
        self.assertIn("dashboard:school_logo", base)
        self.assertIn("dashboard_school.short_name", base)
        self.assertIn("bi-mortarboard-fill", base)
        self.assertIn("dashboard_school.logo.name|urlencode", base)

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
        self.assertIn("dashboard:school_logo", login)
        self.assertIn("login_school.logo.name|urlencode", login)
        self.assertIn("--maroon-900", login)
        self.assertIn("Welcome back", login)

    def test_dashboard_charts_use_school_theme_palette(self):
        academic = Path("templates/dashboard/academic_admin/overview.html").read_text()
        super_admin = Path("templates/dashboard/super_admin.html").read_text()
        self.assertIn("schoolPalette(schoolPrimary", academic)
        self.assertIn("backgroundColor:schools.map(x=>x.primary_color)", super_admin)
        self.assertIn("backgroundColor:schools.map(x=>x.secondary_color)", super_admin)

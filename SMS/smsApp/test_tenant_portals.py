from datetime import date

from django.test import TestCase, override_settings

from .models import School, Staff, User


@override_settings(
    TENANT_ROOT_DOMAIN="localhost",
    ALLOWED_HOSTS=["localhost", ".localhost", "testserver"],
)
class TenantPortalTests(TestCase):
    def setUp(self):
        self.springfield = School.objects.create(
            name="Springfield High",
            short_name="Springfield-High",
            code="SPR",
            subdomain="springfield",
            motto="Learn and lead",
            primary_color="#7a1f37",
            secondary_color="#555555",
        )
        self.oakridge = School.objects.create(
            name="Oakridge School",
            code="OAK",
            subdomain="oakridge",
        )
        self.user = User.objects.create_user(
            username="springteacher",
            password="pass12345",
            role=User.Role.TEACHER,
        )
        Staff.objects.create(
            user=self.user,
            school=self.springfield,
            staff_id="SPR-001",
            job_title="Teacher",
            date_hired=date(2024, 1, 1),
        )

    def test_each_host_loads_its_own_login_branding(self):
        response = self.client.get(
            "/login/", HTTP_HOST="springfield.localhost:8000"
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Springfield High")
        self.assertContains(response, "#7a1f37")

        response = self.client.get(
            "/login/", HTTP_HOST="oakridge.localhost:8000"
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Oakridge School")
        self.assertNotContains(response, "Springfield High")

    def test_unknown_inactive_and_reserved_hosts_are_not_tenants(self):
        self.assertEqual(
            self.client.get("/login/", HTTP_HOST="unknown.localhost").status_code,
            404,
        )
        self.oakridge.is_active = False
        self.oakridge.save(update_fields=["is_active"])
        self.assertEqual(
            self.client.get("/login/", HTTP_HOST="oakridge.localhost").status_code,
            404,
        )
        self.assertEqual(
            self.client.get("/login/", HTTP_HOST="admin.localhost").status_code,
            404,
        )

    def test_wrong_school_login_is_rejected_without_session(self):
        response = self.client.post(
            "/login/",
            {"username": "springteacher", "password": "pass12345"},
            HTTP_HOST="oakridge.localhost",
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Unable to sign in with these credentials.")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_authorized_login_and_cross_tenant_request(self):
        response = self.client.post(
            "/login/",
            {"username": "springteacher", "password": "pass12345"},
            HTTP_HOST="springfield.localhost",
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("_auth_user_id", self.client.session)

        response = self.client.get("/teacher/", HTTP_HOST="springfield.localhost")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Springfield-High")

        response = self.client.get("/", HTTP_HOST="oakridge.localhost")
        self.assertEqual(response.status_code, 404)

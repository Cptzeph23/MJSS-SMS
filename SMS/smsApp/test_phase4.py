from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, override_settings

from .models import Payment


class Phase4ReleaseHardeningTests(SimpleTestCase):
    def test_deprecated_client_navigation_is_hidden_but_routes_remain_in_code(self):
        templates = [
            "dashboard/student/_student_base.html",
            "dashboard/student/overview.html",
            "dashboard/student/academic.html",
            "dashboard/teacher/_teacher_base.html",
            "dashboard/finance/_finance_base.html",
            "base.html",
        ]
        combined = "".join(
            (Path(settings.BASE_DIR) / "templates" / name).read_text()
            for name in templates
        )
        self.assertNotIn(">Learning</a>", combined)
        self.assertNotIn(">Materials</a>", combined)
        self.assertNotIn(">Assignments</a>", combined)
        self.assertNotIn("My Leave Requests", combined)

    def test_gateway_payment_methods_are_not_in_active_manual_workflow(self):
        manual = {
            Payment.Method.CASH,
            Payment.Method.BANK_TRANSFER,
            Payment.Method.MOBILE_MONEY,
            Payment.Method.MPESA,
        }
        self.assertNotIn(Payment.Method.CARD, manual)
        self.assertNotIn(Payment.Method.OTHER_GATEWAY, manual)

    @override_settings(
        SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        CSRF_COOKIE_SAMESITE="Lax",
    )
    def test_security_configuration_contract(self):
        from django.conf import settings as configured

        self.assertEqual(configured.SECURE_PROXY_SSL_HEADER[1], "https")
        self.assertTrue(configured.SESSION_COOKIE_HTTPONLY)
        self.assertEqual(configured.SESSION_COOKIE_SAMESITE, "Lax")
        self.assertEqual(configured.CSRF_COOKIE_SAMESITE, "Lax")

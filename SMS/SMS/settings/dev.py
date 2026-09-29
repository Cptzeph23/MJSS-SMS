"""
Development settings.
Absolute path: SMS/SMS/settings/dev.py
"""
from .base import *  # noqa: F401,F403
from .base import env, BASE_DIR, supabase_storage_options

DEBUG = True

ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

# ---------------------------------------------------------------------------
# Database — Supabase PostgreSQL (falls back to local sqlite if DATABASE_URL
# is not set, so a fresh clone can still run `migrate` without Supabase creds)
# ---------------------------------------------------------------------------
DATABASES = {
    "default": env.db(
        "DATABASE_URL",
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
    )
}
DATABASES["default"]["CONN_MAX_AGE"] = env.int("DB_CONN_MAX_AGE", default=60)
DATABASES["default"]["CONN_HEALTH_CHECKS"] = True

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# ---------------------------------------------------------------------------
# Media storage (Phase 23, spec §41/§42). Local filesystem by default — a
# fresh clone should be able to run `python manage.py runserver` and upload
# a test file without needing Supabase Storage credentials at all (spec §42
# 'do not make production settings dependent on local development
# assumptions' applies in reverse too: dev shouldn't require prod infra).
#
# To test the real Supabase Storage path locally (e.g. verifying an upload
# actually lands in your bucket before deploying), set
# USE_SUPABASE_STORAGE_IN_DEV=True in your .env alongside the
# SUPABASE_STORAGE_* variables from base.py.
# ---------------------------------------------------------------------------
if env.bool("USE_SUPABASE_STORAGE_IN_DEV", default=False):
    STORAGES = {
        "default": {
            "BACKEND": "storages.backends.s3.S3Storage",
            "OPTIONS": supabase_storage_options(),
        },
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
        },
    }


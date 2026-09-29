"""
Production settings.
Absolute path: SMS/SMS/settings/prod.py
"""
from .base import *  # noqa: F401,F403
from .base import (
    env,
    supabase_storage_options,
    SUPABASE_STORAGE_ENDPOINT_URL,
    SUPABASE_STORAGE_ACCESS_KEY_ID,
    SUPABASE_STORAGE_SECRET_ACCESS_KEY,
)
from django.core.exceptions import ImproperlyConfigured

DEBUG = False

ALLOWED_HOSTS = env.list("ALLOWED_HOSTS")  # required, no default in prod

# ---------------------------------------------------------------------------
# Database — Supabase PostgreSQL (required; no fallback in production)
# ---------------------------------------------------------------------------
DATABASES = {
    "default": env.db("DATABASE_URL")
}
DATABASES["default"]["CONN_MAX_AGE"] = env.int("DB_CONN_MAX_AGE", default=60)
DATABASES["default"]["CONN_HEALTH_CHECKS"] = True

# ---------------------------------------------------------------------------
# Security hardening (§27 of spec)
# ---------------------------------------------------------------------------
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = env("EMAIL_HOST", default="")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")

# ---------------------------------------------------------------------------
# Media storage — Supabase Storage, required in production (Phase 23,
# spec §41). Deliberately NO fallback to local filesystem storage here:
# Render's (and most PaaS) filesystem is ephemeral — anything written to
# local disk is gone on the next deploy or restart. Silently falling back
# would mean uploaded student photos, report cards, and transcripts
# randomly disappearing in production. Fail loudly at settings-load time
# instead, with an actionable message, rather than a cryptic boto3
# connection error the first time someone uploads a file.
# ---------------------------------------------------------------------------
if not (
    SUPABASE_STORAGE_ENDPOINT_URL
    and SUPABASE_STORAGE_ACCESS_KEY_ID
    and SUPABASE_STORAGE_SECRET_ACCESS_KEY
):
    raise ImproperlyConfigured(
        "Production requires Supabase Storage to be configured: set "
        "SUPABASE_STORAGE_ENDPOINT_URL, SUPABASE_STORAGE_ACCESS_KEY_ID, "
        "and SUPABASE_STORAGE_SECRET_ACCESS_KEY (see .env.example). These "
        "are dedicated S3-protocol keys from the Supabase dashboard's "
        "Storage -> S3 Connection page, not SUPABASE_ANON_KEY/"
        "SUPABASE_SERVICE_ROLE_KEY."
    )

STORAGES = {
    "default": {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": supabase_storage_options(),
    },
    "staticfiles": {
        # Whitenoise's compressed+hashed storage — enables long-lived
        # browser caching (filename changes when content changes) plus
        # gzip/brotli compression, without needing a separate CDN.
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}
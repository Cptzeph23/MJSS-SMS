"""
Base settings shared by all environments.
Absolute path: SMS/SMS/settings/base.py
"""
from datetime import timedelta
from pathlib import Path

import environ

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent  # -> SMS/

# ---------------------------------------------------------------------------
# Environment variables
# ---------------------------------------------------------------------------
env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env")  # SMS/.env (never commit this file)

SECRET_KEY = env("SECRET_KEY")

# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------
DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

LOCAL_APPS = [
    "smsApp",  # accounts / users / RBAC core (Phase 1)
]

THIRD_PARTY_APPS = [
    "rest_framework",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
    "drf_spectacular",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # WhiteNoise serves static files directly from the app process (no
    # separate nginx/CDN needed to start on Render) — must sit
    # immediately after SecurityMiddleware per whitenoise's own docs.
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "SMS.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "smsApp.context_processors.dashboard_notifications",
            ],
        },
    },
]

WSGI_APPLICATION = "SMS.wsgi.application"
ASGI_APPLICATION = "SMS.asgi.application"

# ---------------------------------------------------------------------------
# Custom user model (RBAC foundation — Phase 1B)
# ---------------------------------------------------------------------------
AUTH_USER_MODEL = "smsApp.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Auth redirect targets (spec §4-§19: role-based dashboard routing).
LOGIN_URL = "dashboard:login"
LOGIN_REDIRECT_URL = "dashboard:home"
LOGOUT_REDIRECT_URL = "dashboard:login"

# ---------------------------------------------------------------------------
# Cache (Phase 24, spec §41 lists REDIS_URL as an expected env var). Used
# for rate limiting (spec §27) — login lockout and DRF throttling both
# need a cache shared across worker processes to actually be effective;
# Django's default LocMemCache is per-process and would let an attacker
# bypass a lockout just by hitting a different gunicorn worker.
#
# REDIS_URL unset -> LocMemCache, so a fresh clone still runs without
# Redis installed (dev convenience, same principle as Phase 1's sqlite
# fallback for DATABASE_URL). REDIS_URL set -> Redis, required in
# practice for prod to have real cross-worker rate limiting.
# ---------------------------------------------------------------------------
REDIS_URL = env("REDIS_URL", default="")
if REDIS_URL:
    CACHES = {
        "default": {
            "BACKEND": "django_redis.cache.RedisCache",
            "LOCATION": REDIS_URL,
            "OPTIONS": {"CLIENT_CLASS": "django_redis.client.DefaultClient"},
        }
    }
else:
    CACHES = {
        "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
    }

# ---------------------------------------------------------------------------
# Internationalization
# ---------------------------------------------------------------------------
LANGUAGE_CODE = "en-us"
TIME_ZONE = env("TIME_ZONE", default="Africa/Nairobi")
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------------------
# Static / Media (local defaults — overridden by Supabase Storage in Phase 17)
# ---------------------------------------------------------------------------
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# =============================================================================
# Phase 22 — REST API (spec §25/§26). API-first: this exposes the same
# service-layer functions the Django template views already call (Phases
# 4-21), never duplicating business logic — see smsApp/api/views.py.
# =============================================================================

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
    ),
    "DEFAULT_VERSIONING_CLASS": "rest_framework.versioning.URLPathVersioning",
    "DEFAULT_VERSION": "v1",
    "ALLOWED_VERSIONS": ["v1"],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 25,
    # Spec §27 'Rate limiting where appropriate'. Anonymous throttling
    # matters most on /api/v1/auth/token/ (credential-stuffing target);
    # authenticated throttling is a general abuse backstop, generous
    # enough that no legitimate dashboard/app usage should ever hit it.
    "DEFAULT_THROTTLE_CLASSES": (
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ),
    "DEFAULT_THROTTLE_RATES": {
        "anon": "20/minute",
        "user": "300/minute",
    },
}

SIMPLE_JWT = {
    # Short-lived access token + longer refresh token is the standard
    # mobile-client pattern (spec §26 'Flutter readiness') — access
    # tokens expiring quickly limits the blast radius of a leaked token
    # on a phone, while the refresh token keeps the user logged in.
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=30),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
}

SPECTACULAR_SETTINGS = {
    "TITLE": "School Management System API",
    "DESCRIPTION": "REST API for the SMS platform — powers the Flutter "
                    "mobile clients (spec §26) alongside the Django "
                    "template dashboards.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "SCHEMA_PATH_PREFIX": r"/api/v1",
}

# =============================================================================
# Phase 23 — Supabase Storage (spec §41 'Deployment', §42 'Environment
# Configuration'). Read here; actually wired to django-storages'
# S3Boto3Storage in dev.py/prod.py, not here, since dev and prod make
# different decisions about whether to use it (see those files).
#
# IMPORTANT: these are dedicated S3-protocol access keys generated in
# the Supabase dashboard under Storage -> S3 Connection — NOT the same
# as SUPABASE_ANON_KEY / SUPABASE_SERVICE_ROLE_KEY (those authenticate
# against Supabase's REST/GraphQL API, not the S3-compatible Storage
# API this settings block talks to).
# =============================================================================

SUPABASE_STORAGE_ENDPOINT_URL = env(
    "SUPABASE_STORAGE_ENDPOINT_URL", default=""
)  # e.g. https://<project-ref>.supabase.co/storage/v1/s3
SUPABASE_STORAGE_ACCESS_KEY_ID = env("SUPABASE_STORAGE_ACCESS_KEY_ID", default="")
SUPABASE_STORAGE_SECRET_ACCESS_KEY = env("SUPABASE_STORAGE_SECRET_ACCESS_KEY", default="")
SUPABASE_STORAGE_BUCKET_NAME = env("SUPABASE_STORAGE_BUCKET_NAME", default="sms-media")
SUPABASE_STORAGE_REGION = env("SUPABASE_STORAGE_REGION", default="us-east-1")
# Private bucket + short-lived signed URLs is the safe default here: this
# bucket holds student/staff photographs, report cards, transcripts, and
# assignment submissions — none of that should ever be a permanently
# public, guessable URL. AWS_QUERYSTRING_EXPIRE controls how long a
# generated URL stays valid (seconds) before it must be re-signed.
SUPABASE_STORAGE_URL_EXPIRE_SECONDS = env.int(
    "SUPABASE_STORAGE_URL_EXPIRE_SECONDS", default=3600
)


def supabase_storage_options() -> dict:
    """Shared S3Boto3Storage kwargs for both dev (opt-in) and prod
    (required) use — kept in one place so the two settings files can't
    drift apart on the security-relevant options (private ACL, signed
    URLs, path-style addressing which Supabase's S3 gateway requires)."""
    return {
        "access_key": SUPABASE_STORAGE_ACCESS_KEY_ID,
        "secret_key": SUPABASE_STORAGE_SECRET_ACCESS_KEY,
        "bucket_name": SUPABASE_STORAGE_BUCKET_NAME,
        "endpoint_url": SUPABASE_STORAGE_ENDPOINT_URL,
        "region_name": SUPABASE_STORAGE_REGION,
        "default_acl": None,  # Supabase's S3 gateway rejects ACL headers entirely
        "querystring_auth": True,  # private bucket -> every .url is a signed URL
        "querystring_expire": SUPABASE_STORAGE_URL_EXPIRE_SECONDS,
        "addressing_style": "path",  # required by Supabase's S3-compatible endpoint
        "signature_version": "s3v4",
        "file_overwrite": False,  # never silently clobber a same-named upload
    }

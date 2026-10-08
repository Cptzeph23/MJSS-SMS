"""Isolated settings for pytest; never connect tests to external services."""
import atexit
import os
import tempfile

# Ensure base/dev settings can load when CI has no application .env file.
os.environ.setdefault("SECRET_KEY", "pytest-only-not-for-deployment")
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["REDIS_URL"] = ""
os.environ["USE_SUPABASE_STORAGE_IN_DEV"] = "False"

from .dev import *  # noqa: F401,F403,E402

# Override all persistence/configuration backends after importing development
# defaults: tests must not reach a developer's Supabase/Redis from .env.
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
CACHES = {
    "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
}
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}

_test_media = tempfile.TemporaryDirectory(prefix="sms-pytest-media-")
atexit.register(_test_media.cleanup)
MEDIA_ROOT = _test_media.name

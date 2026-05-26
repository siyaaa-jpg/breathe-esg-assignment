"""Development settings — SQLite, debug on, CORS open."""
from .base import *  # noqa: F401, F403
from .base import BASE_DIR, env

DEBUG = True

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

# Loosen for local React dev server if we ever run it separately. Default config
# (frontend built and served by Django) doesn't need this.
CORS_ALLOW_ALL_ORIGINS = True

ALLOWED_HOSTS = ["*"]

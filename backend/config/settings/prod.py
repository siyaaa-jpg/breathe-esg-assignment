"""Production settings — Postgres via DATABASE_URL (Render-style), security hardening."""
import dj_database_url

from .base import *  # noqa: F401, F403
from .base import env

DEBUG = False

# Render injects DATABASE_URL automatically when a Postgres service is attached.
DATABASES = {
    "default": dj_database_url.config(
        default=env("DATABASE_URL", required=True),
        conn_max_age=600,
        ssl_require=True,
    )
}

# Render provides the hostname at deploy time as RENDER_EXTERNAL_HOSTNAME.
ALLOWED_HOSTS = [h for h in env("DJANGO_ALLOWED_HOSTS", default="").split(",") if h]
_render_host = env("RENDER_EXTERNAL_HOSTNAME", default=None)
if _render_host:
    ALLOWED_HOSTS.append(_render_host)

# CSRF requires concrete origins (or wildcards with explicit "*"). RENDER_EXTERNAL_HOSTNAME
# is the actual deploy URL; the wildcard '.onrender.com' in ALLOWED_HOSTS does the
# host-validation job but isn't a valid CSRF origin string.
CSRF_TRUSTED_ORIGINS = []
if _render_host:
    CSRF_TRUSTED_ORIGINS.append(f"https://{_render_host}")
for h in ALLOWED_HOSTS:
    if h.startswith(".") and h not in CSRF_TRUSTED_ORIGINS:
        CSRF_TRUSTED_ORIGINS.append(f"https://*{h}")
    elif h and not h.startswith(".") and h != _render_host:
        CSRF_TRUSTED_ORIGINS.append(f"https://{h}")

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True

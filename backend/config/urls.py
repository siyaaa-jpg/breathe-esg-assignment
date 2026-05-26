from django.contrib import admin
from django.urls import include, path, re_path
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.generic import TemplateView

# The catch-all renders the React build's index.html for any path /api/ and
# /admin/ don't claim, so React Router handles in-app routing. Wrapped in
# ensure_csrf_cookie so the CSRF cookie is set on first SPA page load —
# subsequent fetch() POSTs/PATCHes can read it from document.cookie.
spa_view = ensure_csrf_cookie(TemplateView.as_view(template_name="index.html"))

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("apps.api.urls")),
    re_path(r"^.*$", spa_view, name="spa"),
]

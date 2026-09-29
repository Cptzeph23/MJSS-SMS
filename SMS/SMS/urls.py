"""Absolute path: SMS/SMS/urls.py"""
from django.contrib import admin
from django.conf import settings
from django.urls import include, path
from django.conf.urls.static import static
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("smsApp.urls")),
    # Spec §25: versioned API + OpenAPI/Swagger-compatible documentation.
    path("api/v1/", include("smsApp.api.urls")),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="api_docs"),
]

# Uploaded files use local MEDIA_ROOT during development. Static files are
# handled by WhiteNoise, but WhiteNoise deliberately does not serve user
# uploads, so Django needs this explicit debug-only route.
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

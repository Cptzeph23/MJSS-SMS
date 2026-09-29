# Absolute path: SMS/smsApp/api/urls.py
from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView, TokenVerifyView

from . import views

app_name = "api_v1"

router = DefaultRouter()
router.register("students", views.StudentViewSet, basename="student")
router.register("attendance", views.AttendanceViewSet, basename="attendance")
router.register("notifications", views.NotificationViewSet, basename="notification")

urlpatterns = [
    # Spec §25 example layout: /api/v1/auth/, /api/v1/users/, ...
    path("auth/token/", views.SchoolTokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("auth/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("auth/token/verify/", TokenVerifyView.as_view(), name="token_verify"),
    path("users/me/", views.MeView.as_view(), name="user_me"),
    path("courses/mine/", views.MyClassSubjectsView.as_view(), name="my_class_subjects"),
    path("", include(router.urls)),
]
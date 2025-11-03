"""
URL configuration for BasicWFMbb project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path
from django.contrib.auth import views as auth_views
from django.conf import settings
from django.conf.urls.static import static

from core.forms import EmailAuthenticationForm
from core.views import (
    schedule_week,
    exchange_create,
    get_agent_shifts_for_month,
    signup,
    logout_view,
    tools,
    dashboard,
    requests_view,
    request_sick_leave,
    upload_sick_leave_proof,
)


urlpatterns = [
    path("admin/", admin.site.urls),
    path(
        "accounts/login/",
        auth_views.LoginView.as_view(
            template_name="registration/login.html",
            redirect_authenticated_user=True,
            authentication_form=EmailAuthenticationForm,
        ),
        name="login",
    ),
    path("accounts/logout/", logout_view, name="logout"),
    path("accounts/signup/", signup, name="signup"),

    path("", schedule_week, name="home"),
    path("schedule/", schedule_week, name="schedule_week"),
    path("requests/", requests_view, name="requests"),
    path("requests/sick-leave/", request_sick_leave, name="requests_sick_leave"),
    path(
        "requests/sick-leave/<int:proof_id>/upload/",
        upload_sick_leave_proof,
        name="upload_sick_leave_proof",
    ),
    path("dashboard/", dashboard, name="dashboard"),
    path("tools/", tools, name="tools"),
    path("exchange/", exchange_create, name="exchange_create"),
    path("ajax/get-agent-shifts/", get_agent_shifts_for_month, name="ajax_get_agent_shifts"),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

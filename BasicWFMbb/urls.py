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
from core.views import schedule_week, exchange_create, get_agent_shifts_for_month


urlpatterns = [
    path('admin/', admin.site.urls),
    path('schedule/', schedule_week, name='schedule_week'),
    path('exchange/', exchange_create, name='exchange_create'),
    path('ajax/get-agent-shifts/', get_agent_shifts_for_month, name='ajax_get_agent_shifts'),
]

# core/admin.py
from django.contrib import admin
from import_export.admin import ImportExportModelAdmin
from .models import Agent, Shift, ShiftExchange, AuditLog
from .resources import ShiftResource  # <--- 1. ДОДАЙТЕ ЦЕЙ ІМПОРТ


@admin.register(Agent)
class AgentAdmin(admin.ModelAdmin):
    list_display = ("user", "team_lead", "active")
    list_filter = ("active", "team_lead")
    search_fields = (
        "user__username",
        "user__first_name",
        "user__last_name",
        "user__email",
    )


@admin.register(Shift)
class ShiftAdmin(ImportExportModelAdmin):
    resource_class = ShiftResource  # <--- 2. ДОДАЙТЕ ЦЕЙ РЯДОК

    list_display = ("agent", "start", "end", "direction", "status")
    list_filter = ("direction", "status", "agent__team_lead")

    search_fields = (
        "agent__user__username",
        "agent__user__first_name",
        "agent__user__last_name",
    )
    date_hierarchy = "start"


@admin.register(ShiftExchange)
class ShiftExchangeAdmin(admin.ModelAdmin):
    list_display = ("from_shift", "to_shift", "approved", "created_at", "requested_by")
    list_filter = ("approved", "created_at")
    search_fields = (
        "from_shift__agent__user__username",
        "to_shift__agent__user__username",
    )


def _is_in(user, group_name):
    return user.is_superuser or user.groups.filter(name=group_name).exists()


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = (
        "timestamp",
        "user",
        "action",
        "app_label",
        "model",
        "object_pk",
        "object_repr",
    )
    list_filter = ("action", "app_label", "model", "user")
    search_fields = ("object_pk", "object_repr", "changes")
    readonly_fields = (
        "timestamp",
        "user",
        "action",
        "app_label",
        "model",
        "object_pk",
        "object_repr",
        "changes",
        "ip_address",
        "user_agent",
    )
    fieldsets = (
        (None, {
            "fields": ("timestamp", "user", "action"),
        }),
        ("Об'єкт", {
            "fields": ("app_label", "model", "object_pk", "object_repr"),
        }),
        ("Зміни", {
            "fields": ("changes",),
        }),
        ("Запит", {
            "fields": ("ip_address", "user_agent"),
        }),
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

from __future__ import annotations

from django.db.models.signals import pre_save, post_save, post_delete
from django.dispatch import receiver
from django.utils import timezone
from django.apps import apps

from .models import AuditLog, AuditAction, Agent, Shift, ShiftExchange, SickLeaveProof
from .middleware import get_current_user, get_current_request


TRACKED_MODELS = (Agent, Shift, ShiftExchange, SickLeaveProof)


def _field_value_map(instance):
    data = {}
    for field in instance._meta.local_fields:
        name = field.name
        try:
            val = getattr(instance, name)
        except Exception:
            continue
        # Normalize related objects and datetimes to JSON-safe strings
        if hasattr(field, "remote_field") and getattr(field.remote_field, "model", None):
            # ForeignKey: keep id and string
            try:
                data[name] = str(val) if val is not None else None
            except Exception:
                data[name] = None
            try:
                data[f"{name}_id"] = getattr(instance, f"{name}_id")
            except Exception:
                pass
        else:
            if hasattr(val, "isoformat"):
                try:
                    data[name] = val.isoformat()
                except Exception:
                    data[name] = str(val)
            else:
                data[name] = val if isinstance(val, (int, float, bool, type(None))) else str(val)
    return data


def _object_identity(instance):
    meta = instance._meta
    return meta.app_label, meta.model_name, str(getattr(instance, instance._meta.pk.attname, ""))


def _log(action: str, instance, changes: dict | None):
    app_label, model_name, pk = _object_identity(instance)
    req = get_current_request()
    user = get_current_user()
    ip = ""
    ua = ""
    if req is not None:
        ip = req.META.get("REMOTE_ADDR", "") or req.META.get("HTTP_X_FORWARDED_FOR", "")
        ua = req.META.get("HTTP_USER_AGENT", "")
    try:
        obj_repr = str(instance)
    except Exception:
        obj_repr = f"{model_name}#{pk}"

    AuditLog.objects.create(
        user=user if getattr(user, "is_authenticated", False) else None,
        app_label=app_label,
        model=model_name,
        object_pk=pk,
        object_repr=obj_repr[:255],
        action=action,
        changes=changes or None,
        ip_address=ip,
        user_agent=ua,
    )


@receiver(pre_save)
def _capture_before(sender, instance, **kwargs):
    if not isinstance(instance, TRACKED_MODELS):
        return
    if getattr(instance, instance._meta.pk.attname, None) is None:
        return
    try:
        before = sender.objects.get(pk=getattr(instance, instance._meta.pk.attname))
    except sender.DoesNotExist:
        return
    instance.__audit_before = _field_value_map(before)


@receiver(post_save)
def _log_create_update(sender, instance, created, **kwargs):
    if not isinstance(instance, TRACKED_MODELS):
        return
    if created:
        after = _field_value_map(instance)
        changes = {k: {"old": None, "new": v} for k, v in after.items()}
        _log(AuditAction.CREATE, instance, changes)
    else:
        before = getattr(instance, "__audit_before", None)
        after = _field_value_map(instance)
        diff = {}
        if before is None:
            # Fallback: compare with DB now
            try:
                persisted = sender.objects.get(pk=getattr(instance, instance._meta.pk.attname))
                before = _field_value_map(persisted)
            except Exception:
                before = {}
        for k, newv in after.items():
            oldv = before.get(k)
            if oldv != newv:
                diff[k] = {"old": oldv, "new": newv}
        if diff:
            _log(AuditAction.UPDATE, instance, diff)


@receiver(post_delete)
def _log_delete(sender, instance, **kwargs):
    if not isinstance(instance, TRACKED_MODELS):
        return
    snapshot = _field_value_map(instance)
    changes = {"__all__": {"old": snapshot, "new": None}}
    _log(AuditAction.DELETE, instance, changes)


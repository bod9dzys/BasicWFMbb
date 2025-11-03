# core/models.py
from pathlib import Path
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from django.utils.text import slugify
from django.contrib.auth import get_user_model
from simple_history.models import HistoricalRecords
from django.core.serializers.json import DjangoJSONEncoder

try:
    from django.db.models import JSONField  # Django 3.1+
except Exception:  # pragma: no cover
    from django.contrib.postgres.fields import JSONField  # fallback


class Skill(models.TextChoices):
    CALLS = "calls", "Дзвінки"
    TICKETS = "tickets", "Тікети"
    CHATS = "chats", "Чати"


class ShiftStatus(models.TextChoices):
    WORK = "work", "Робоча зміна"
    DAY_OFF = "day_off", "Вихідний"
    VACATION = "vacation", "Відпустка"
    SICK = "sick", "Лікарняний"
    TRAINING = "training", "Тренінг"
    MEETING = "meeting", "Мітинг"
    ONBOARD = "onboard", "Онборд"
    MENTOR = "mentor", "Менторство"


class Direction(models.TextChoices):
    CALLS = "calls", "Дзвінки"
    TICKETS = "tickets", "Тікети"
    CHATS = "chats", "Чати"


class Agent(models.Model):
    # Зв'язуємо з користувачем адмінки
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    team_lead = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="tl_agents"
    )
    # Швидко і дешево: список рядків зі скілами
    skills = models.JSONField(default=list, blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["user__username"]
        permissions = (
            ("export_schedule", "Може експортувати розклад"),
        )

    def __str__(self):
        return self.user.get_full_name() or self.user.username


class Shift(models.Model):
    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name="shifts")
    start = models.DateTimeField(db_index=True)
    end = models.DateTimeField(db_index=True)

    direction = models.CharField(
        max_length=20, choices=Direction.choices, default=Direction.CALLS
    )
    status = models.CharField(
        max_length=20, choices=ShiftStatus.choices, default=ShiftStatus.WORK
    )
    activity = models.CharField(max_length=100, blank=True)
    comment = models.TextField(blank=True, null=True)

    history = HistoricalRecords()

    class Meta:
        ordering = ["-start"]
        indexes = [
            models.Index(fields=["agent", "start", "end"]),
        ]
        permissions = (
            ("import_schedule", "Може імпортувати базовий розклад"),
            ("export_schedule", "Може експортувати розклад"),
        )
    def __str__(self):
        tz = timezone.get_default_timezone()
        start_local = timezone.localtime(self.start, tz)
        end_local = timezone.localtime(self.end, tz)
        return f"{self.agent} | {start_local:%Y-%m-%d %H:%M} → {end_local:%H:%M}"

    @property
    def duration_hours(self) -> float:
        return round((self.end - self.start).total_seconds() / 3600, 2)


class ShiftExchange(models.Model):
    # Обмін між двома конкретними змінами
    from_shift = models.ForeignKey(
        Shift, on_delete=models.CASCADE, related_name="ex_from"
    )
    to_shift = models.ForeignKey(Shift, on_delete=models.CASCADE, related_name="ex_to")
    requested_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True
    )
    approved = models.BooleanField(
        null=True, blank=True
    )  # None=в очікуванні, True/False=рішення
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    history = HistoricalRecords()

    class Meta:
        ordering = ["-created_at"]
        permissions = (
            ("request_exchange", "Може створювати запити на обмін"),
            ("approve_exchange", "Може погоджувати/відхиляти обміни"),
            ("view_exchange_history", "Може бачити історію обмінів"),
        )

    def __str__(self):
        state = "очікує" if self.approved is None else ("схвалено" if self.approved else "відхилено")
        return f"Обмін: {self.from_shift} ⇄ {self.to_shift} [{state}]"


def sick_leave_proof_upload_to(instance, filename):
    agent_name = instance.agent.user.get_full_name() or instance.agent.user.username
    agent_slug = slugify(agent_name) or f"agent-{instance.agent_id}"
    stamp_source = getattr(instance, "upload_timestamp", None) or timezone.now()
    timestamp = stamp_source.strftime("%Y%m%d_%H%M%S")
    original_name = Path(filename).name or "proof"
    return f"sick_leave_proofs/{agent_slug}/{timestamp}/{original_name}"


class SickLeaveProof(models.Model):
    PROOF_CHOICES = [
        ("sick_leave", "Запит на лікарняний"),
    ]

    agent = models.ForeignKey(
        Agent,
        on_delete=models.CASCADE,
        related_name="sick_leave_proofs",
    )
    start_date = models.DateField()
    end_date = models.DateField()
    attachment = models.FileField(
        upload_to=sick_leave_proof_upload_to,
        blank=True,
        null=True,
    )
    attach_later = models.BooleanField(default=False)
    submitted_by = models.ForeignKey(
        get_user_model(),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="submitted_sick_leave_proofs",
    )
    proof_type = models.CharField(
        max_length=32,
        choices=PROOF_CHOICES,
        default="sick_leave",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        period = f"{self.start_date:%d.%m.%Y}–{self.end_date:%d.%m.%Y}"
        status = "очікує підтвердження" if self.is_pending else "підтверджено"
        return f"Підтвердження лікарняного для {self.agent} ({period}) – {status}"

    @property
    def is_pending(self) -> bool:
        return self.attachment is None


class AuditAction(models.TextChoices):
    CREATE = "create", "Створено"
    UPDATE = "update", "Оновлено"
    DELETE = "delete", "Видалено"


class AuditLog(models.Model):
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    user = models.ForeignKey(get_user_model(), null=True, blank=True, on_delete=models.SET_NULL, related_name="audit_logs")

    app_label = models.CharField(max_length=64)
    model = models.CharField(max_length=64)
    object_pk = models.CharField(max_length=64)
    object_repr = models.CharField(max_length=255)

    action = models.CharField(max_length=16, choices=AuditAction.choices)
    changes = JSONField(null=True, blank=True, encoder=DjangoJSONEncoder)

    ip_address = models.CharField(max_length=45, blank=True)
    user_agent = models.TextField(blank=True)

    class Meta:
        ordering = ["-timestamp"]
        indexes = [
            models.Index(fields=["app_label", "model", "object_pk"]),
            models.Index(fields=["user", "timestamp"]),
        ]

    def __str__(self):
        return f"{self.timestamp:%Y-%m-%d %H:%M:%S} {self.user or 'system'} {self.action} {self.app_label}.{self.model}#{self.object_pk}"

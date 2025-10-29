# core/models.py
from django.db import models
from django.contrib.auth.models import User
from simple_history.models import HistoricalRecords


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
        return f"{self.agent} | {self.start:%Y-%m-%d %H:%M} → {self.end:%H:%M}"

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

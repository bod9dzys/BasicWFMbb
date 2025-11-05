# core/templatetags/schedule_filters.py
from django import template
from django.utils import timezone

register = template.Library()


@register.filter
def fmt_time(value):
    """Форматує datetime або time об'єкт у формат HH:MM."""
    if value is None:
        return ""
    if isinstance(value, str):
        # Якщо вже рядок, повертаємо як є
        return value
    # Якщо datetime, конвертуємо в локальний час
    if hasattr(value, "tzinfo") and value.tzinfo is not None:
        tz = timezone.get_current_timezone()
        value = timezone.localtime(value, tz)
    return value.strftime("%H:%M")


@register.filter
def fmt_time_range(start, end):
    """Форматує діапазон часу у формат HH:MM–HH:MM."""
    start_str = fmt_time(start)
    end_str = fmt_time(end)
    if start_str and end_str:
        return f"{start_str}–{end_str}"
    return ""


@register.filter
def shift_time_label(shift_card):
    """Форматує мітку часу для ShiftCard з datetime об'єктів."""
    if hasattr(shift_card, "start") and hasattr(shift_card, "end"):
        return fmt_time_range(shift_card.start, shift_card.end)
    # Fallback для старого формату
    if hasattr(shift_card, "time_label"):
        return shift_card.time_label
    return ""


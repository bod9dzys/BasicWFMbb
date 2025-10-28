# core/filters.py
import django_filters
from django import forms
from django.utils import timezone  # якщо реально використаєш
from django.contrib.auth.models import User  # ← додано
from .models import Shift, Direction, ShiftStatus, Agent


class ShiftFilter(django_filters.FilterSet):
    # зміна: прибрав лямбду, даю порожній queryset, який наповню в __init__
    team_lead = django_filters.ModelChoiceFilter(
        field_name="agent__team_lead",
        queryset=User.objects.none(),  # ← змінено
        label="Тімлід",
        empty_label="Всі",
    )

    agent = django_filters.ModelChoiceFilter(
        field_name="agent",
        queryset=Agent.objects.all(),
        label="Агент",
        empty_label="Всі",
    )

    direction = django_filters.ChoiceFilter(
        field_name="direction",
        choices=Direction.choices,
        label="Напрямок",
    )

    status = django_filters.ChoiceFilter(
        field_name="status",
        choices=ShiftStatus.choices,
        label="Статус",
    )

    start__gte = django_filters.DateTimeFilter(
        field_name="start",
        lookup_expr="gte",
        label="Початок з",
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
    )

    end__lte = django_filters.DateTimeFilter(
        field_name="end",
        lookup_expr="lte",
        label="Кінець до",
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
    )

    class Meta:
        model = Shift
        fields = ["team_lead", "agent", "direction", "status"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # нормальний queryset для TL: тільки ті юзери, які реально є тімлідами у Agent
        tls_ids = (
            Agent.objects
            .exclude(team_lead__isnull=True)
            .values_list("team_lead", flat=True)
            .distinct()
        )
        self.filters["team_lead"].field.queryset = User.objects.filter(id__in=list(tls_ids))

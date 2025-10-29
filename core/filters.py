# core/filters.py
import django_filters
from django import forms
from django.utils import timezone  # якщо реально використаєш
from django.contrib.auth.models import User  # ← додано
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Row, Column
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
        input_formats=("%Y-%m-%d %H:%M",),
        widget=forms.DateTimeInput(
            format="%Y-%m-%d %H:%M",
            attrs={
                "class": "form-control datetime-picker",
                "placeholder": "Оберіть дату й час",
                "autocomplete": "off",
            },
        ),
    )

    end__lte = django_filters.DateTimeFilter(
        field_name="end",
        lookup_expr="lte",
        label="Кінець до",
        input_formats=("%Y-%m-%d %H:%M",),
        widget=forms.DateTimeInput(
            format="%Y-%m-%d %H:%M",
            attrs={
                "class": "form-control datetime-picker",
                "placeholder": "Оберіть дату й час",
                "autocomplete": "off",
            },
        ),
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
        tl_field = self.filters["team_lead"].field
        tl_field.label_from_instance = (
            lambda user: (user.get_full_name() or "").strip() or user.username
        )

        helper = getattr(self.form, "helper", None) or FormHelper(self.form)
        helper.form_tag = False  # форму-обгортку малюємо вручну в шаблоні
        helper.disable_csrf = True
        helper.form_method = "get"
        helper.layout = Layout(
            Row(
                Column("team_lead", css_class="col-md-3 col-sm-6"),
                Column("agent", css_class="col-md-3 col-sm-6"),
                Column("direction", css_class="col-md-3 col-sm-6"),
                Column("status", css_class="col-md-3 col-sm-6"),
            ),
            Row(
                Column("start__gte", css_class="col-md-3 col-sm-6"),
                Column("end__lte", css_class="col-md-3 col-sm-6"),
            ),
        )
        self.form.helper = helper

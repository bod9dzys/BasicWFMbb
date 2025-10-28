# core/forms.py
from django import forms
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Submit, Field, Layout
from .models import Shift, Agent


class ExchangeCreateForm(forms.Form):
    from_agent = forms.ModelChoiceField(
        queryset=Agent.objects.select_related("user").filter(active=True),
        label="Агент 1 (чия зміна)",
        required=True,
    )
    to_agent = forms.ModelChoiceField(
        queryset=Agent.objects.select_related("user").filter(active=True),
        label="Агент 2 (на чию зміну)",
        required=True,
    )

    from_shift = forms.ModelChoiceField(
        queryset=Shift.objects.none(),
        label="Зміна Агента 1",
        required=True,
    )
    to_shift = forms.ModelChoiceField(
        queryset=Shift.objects.none(),
        label="Зміна Агента 2",
        required=True,
    )

    comment = forms.CharField(label="Коментар", widget=forms.Textarea, required=False)

    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

        base_agent_qs = Agent.objects.select_related("user").filter(active=True).order_by(
            "user__last_name", "user__first_name"
        )
        self.fields["from_agent"].queryset = base_agent_qs
        self.fields["to_agent"].queryset = base_agent_qs

        # Початкові значення з POST / initial
        from_agent_pk = self._resolve_agent_for_from_field(user)
        to_agent_value = self.data.get("to_agent") if self.is_bound else self.initial.get("to_agent")
        to_agent_pk = self._normalize_agent_value(to_agent_value)

        from_shift_value = self.data.get("from_shift") if self.is_bound else self.initial.get("from_shift")
        to_shift_value = self.data.get("to_shift") if self.is_bound else self.initial.get("to_shift")
        from_shift_pk = self._normalize_shift_value(from_shift_value)
        to_shift_pk = self._normalize_shift_value(to_shift_value)

        shift_base_qs = Shift.objects.select_related("agent", "agent__user").order_by("start")
        self.fields["from_shift"].queryset = self._build_shift_queryset(shift_base_qs, from_agent_pk, from_shift_pk)
        self.fields["to_shift"].queryset = self._build_shift_queryset(shift_base_qs, to_agent_pk, to_shift_pk)

        self._set_shift_widget_attrs("from_shift", from_shift_pk)
        self._set_shift_widget_attrs("to_shift", to_shift_pk)
        self.fields["from_agent"].widget.attrs.setdefault("class", "form-select")
        self.fields["to_agent"].widget.attrs.setdefault("class", "form-select")

        self.helper = FormHelper()
        self.helper.form_method = "post"
        self.helper.layout = Layout(
            Field("from_agent", id="id_from_agent"),
            Field("from_shift", id="id_from_shift"),
            Field("to_agent", id="id_to_agent"),
            Field("to_shift", id="id_to_shift"),
            "comment",
            Submit("submit", "Запросити обмін"),
        )

    def clean(self):
        cleaned = super().clean()
        from_agent = cleaned.get("from_agent")
        to_agent = cleaned.get("to_agent")
        from_shift_id = self.data.get("from_shift")
        to_shift_id = self.data.get("to_shift")

        if from_agent and to_agent and from_agent == to_agent:
            raise forms.ValidationError("Оберіть двох різних агентів.")

        cleaned["from_shift"] = self._ensure_shift_matches_agent(
            field_name="from_shift",
            agent=from_agent,
            shift_id=from_shift_id,
            mismatch_message="Обрана 'Зміна Агента 1' не належить Агенту 1.",
        )
        cleaned["to_shift"] = self._ensure_shift_matches_agent(
            field_name="to_shift",
            agent=to_agent,
            shift_id=to_shift_id,
            mismatch_message="Обрана 'Зміна Агента 2' не належить Агенту 2.",
        )

        f_shift = cleaned.get("from_shift")
        t_shift = cleaned.get("to_shift")
        if f_shift and t_shift and f_shift.pk == t_shift.pk:
            raise forms.ValidationError("Виберіть дві різні зміни для обміну.")

        return cleaned

    def _resolve_agent_for_from_field(self, user):
        """
        Обмежуємо відправника, якщо він звичайний агент. Повертає PK.
        """
        if not user.is_staff and hasattr(user, "agent"):
            self.fields["from_agent"].queryset = Agent.objects.filter(pk=user.agent.pk)
            self.fields["from_agent"].initial = user.agent
            self.fields["from_agent"].disabled = True
            return user.agent.pk

        value = self.data.get("from_agent") if self.is_bound else self.initial.get("from_agent")
        return self._normalize_agent_value(value)

    def _ensure_shift_matches_agent(self, field_name, agent, shift_id, mismatch_message):
        if not agent:
            self.add_error(field_name, "Оберіть агента.")
            raise forms.ValidationError("Заповніть усі обов'язкові поля.")
        if not shift_id:
            self.add_error(field_name, "Оберіть зміну.")
            raise forms.ValidationError("Заповніть усі обов'язкові поля.")

        try:
            shift = Shift.objects.select_related("agent").get(pk=int(shift_id))
        except (Shift.DoesNotExist, ValueError):
            message = f"Обрана '{self.fields[field_name].label}' не існує."
            self.add_error(field_name, message)
            raise forms.ValidationError(message)

        if shift.agent_id != agent.pk:
            self.add_error(field_name, mismatch_message)
            raise forms.ValidationError(mismatch_message)

        return shift

    def _set_shift_widget_attrs(self, field_name, selected_pk):
        field = self.fields[field_name]
        placeholder = field.empty_label or "Оберіть зміну"
        attrs = field.widget.attrs
        attrs.setdefault("class", "form-select")
        attrs.setdefault("data-placeholder", placeholder)
        attrs["data-selected"] = str(selected_pk) if selected_pk else ""

    @staticmethod
    def _normalize_agent_value(value):
        if isinstance(value, Agent):
            return value.pk
        try:
            return int(value) if value else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _normalize_shift_value(value):
        if isinstance(value, Shift):
            return value.pk
        try:
            return int(value) if value else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _build_shift_queryset(base_queryset, agent_pk, shift_pk):
        qs = base_queryset.none()
        if agent_pk:
            qs = base_queryset.filter(agent_id=agent_pk)
        if shift_pk:
            qs = qs | base_queryset.filter(pk=shift_pk)
        return qs.distinct()

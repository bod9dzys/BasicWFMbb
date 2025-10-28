# core/forms.py
from django import forms
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Submit, Layout, Row, Column
from .models import Shift, ShiftExchange, Agent

class DummyForm(forms.Form):
    name = forms.CharField(label="Ім'я")
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_method = "post"
        self.helper.add_input(Submit("submit", "Зберегти"))

class ExchangeCreateForm(forms.Form):
    from_agent = forms.ModelChoiceField(
        queryset=Agent.objects.none(),
        label="Агент (віддає зміну)",
        empty_label="Оберіть агента",
    )
    from_shift = forms.ModelChoiceField(
        queryset=Shift.objects.none(),
        label="Зміна агента",
        empty_label="Оберіть зміну",
    )
    to_agent = forms.ModelChoiceField(
        queryset=Agent.objects.none(),
        label="Агент (отримує зміну)",
        empty_label="Оберіть агента",
    )
    to_shift = forms.ModelChoiceField(
        queryset=Shift.objects.none(),
        label="Зміна на обмін",
        empty_label="Оберіть зміну",
    )
    comment = forms.CharField(label="Коментар", widget=forms.Textarea, required=False)

    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

        agents_qs = Agent.objects.select_related("user").filter(active=True).order_by(
            "user__last_name", "user__first_name", "user__username"
        )
        shifts_qs = Shift.objects.select_related("agent", "agent__user").order_by("-start")

        self.fields["from_agent"].queryset = agents_qs
        self.fields["to_agent"].queryset = agents_qs

        # Автовибір поточного агента якщо існує
        user_agent = agents_qs.filter(user=user).first()
        if user_agent and not self.is_bound:
            self.initial.setdefault("from_agent", user_agent.pk)

        from_agent = self._resolve_agent(self.data.get("from_agent") if self.is_bound else self.initial.get("from_agent"), agents_qs)
        to_agent = self._resolve_agent(self.data.get("to_agent") if self.is_bound else self.initial.get("to_agent"), agents_qs)

        if from_agent:
            self.fields["from_shift"].queryset = shifts_qs.filter(agent=from_agent)
        else:
            self.fields["from_shift"].queryset = Shift.objects.none()

        if to_agent:
            self.fields["to_shift"].queryset = shifts_qs.filter(agent=to_agent)
        else:
            self.fields["to_shift"].queryset = Shift.objects.none()

        self.helper = FormHelper()
        self.helper.form_method = "post"
        self.helper.layout = Layout(
            Row(
                Column("from_agent", css_class="col-md-6"),
                Column("from_shift", css_class="col-md-6"),
            ),
            Row(
                Column("to_agent", css_class="col-md-6"),
                Column("to_shift", css_class="col-md-6"),
            ),
            "comment",
        )
        self.helper.add_input(Submit("submit", "Запросити обмін"))

    @staticmethod
    def _resolve_agent(value, queryset):
        try:
            if value:
                return queryset.get(pk=value)
        except (ValueError, queryset.model.DoesNotExist):
            return None
        return None

    def clean(self):
        cleaned = super().clean()
        from_agent = cleaned.get("from_agent")
        to_agent = cleaned.get("to_agent")
        from_shift = cleaned.get("from_shift")
        to_shift = cleaned.get("to_shift")

        if not all([from_agent, to_agent, from_shift, to_shift]):
            return cleaned

        if from_shift.agent_id != from_agent.id:
            self.add_error("from_shift", "Ця зміна не належить обраному агенту.")
        if to_shift.agent_id != to_agent.id:
            self.add_error("to_shift", "Ця зміна не належить обраному агенту.")

        if from_shift.id == to_shift.id:
            raise forms.ValidationError("Вибери дві різні зміни.")

        if from_agent.id == to_agent.id:
            raise forms.ValidationError("Оберіть різних агентів для обміну.")

        return cleaned

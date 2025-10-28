# core/forms.py
from datetime import timedelta

from django import forms
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm
from django.contrib.auth.models import User
from django.utils import timezone
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Submit, Field, Layout
from .models import Shift, Agent


class ExchangeCreateForm(forms.Form):
    from_agent = forms.ModelChoiceField(
        queryset=Agent.objects.select_related("user").filter(active=True),
        label="Агент 1 (чия зміна)",
        required=True,
        empty_label="Оберіть агента",
    )
    to_agent = forms.ModelChoiceField(
        queryset=Agent.objects.select_related("user").filter(active=True),
        label="Агент 2 (на чию зміну)",
        required=True,
        empty_label="Оберіть агента",
    )

    from_shift = forms.ModelChoiceField(
        queryset=Shift.objects.none(),
        label="Зміна Агента 1",
        required=True,
        empty_label="Оберіть зміну",
    )
    to_shift = forms.ModelChoiceField(
        queryset=Shift.objects.none(),
        label="Зміна Агента 2",
        required=True,
        empty_label="Оберіть зміну",
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


class SignUpForm(UserCreationForm):
    first_name = forms.CharField(label="Ім'я", max_length=150, required=False)
    last_name = forms.CharField(label="Прізвище", max_length=150, required=False)
    email = forms.EmailField(label="Email", required=True)

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "email", "first_name", "last_name", "password1", "password2")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        username_field = self.fields["username"]
        username_field.widget = forms.HiddenInput()
        username_field.required = False
        username_field.label = ""
        field_configs = {
            "email": {"placeholder": "name@example.com"},
            "first_name": {"placeholder": "Ім'я"},
            "last_name": {"placeholder": "Прізвище"},
            "password1": {"placeholder": "Пароль"},
            "password2": {"placeholder": "Повторіть пароль"},
        }
        for name, attrs in field_configs.items():
            self.fields[name].widget.attrs.update(attrs)
        self.fields["password1"].help_text = "Створіть пароль щонайменше з 8 символів."
        self.fields["password2"].help_text = "Повторіть пароль для підтвердження."

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("Користувач з таким email уже існує.")
        return email

    def clean_username(self):
        email = self.cleaned_data.get("email")
        if not email:
            return ""
        email = email.lower()
        if User.objects.filter(username__iexact=email).exists():
            raise forms.ValidationError("Користувач з таким email уже існує.")
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        email = self.cleaned_data["email"].lower()
        user.username = email
        user.email = email
        user.first_name = self.cleaned_data.get("first_name", "")
        user.last_name = self.cleaned_data.get("last_name", "")
        if commit:
            user.save()
            Agent.objects.get_or_create(user=user)
        return user


class EmailAuthenticationForm(AuthenticationForm):
    def __init__(self, request=None, *args, **kwargs):
        super().__init__(request, *args, **kwargs)
        self.fields["username"].label = "Email"
        self.fields["username"].widget.attrs.update({"placeholder": "name@example.com"})
        self.fields["password"].widget.attrs.update({"placeholder": "Пароль"})

    def clean(self):
        username = self.cleaned_data.get("username")
        if username:
            self.cleaned_data["username"] = username.lower()
        return super().clean()


class ToolsHoursForm(forms.Form):
    agent = forms.ModelChoiceField(
        queryset=Agent.objects.select_related("user").filter(active=True),
        label="Агент",
        empty_label="Оберіть агента",
    )
    start = forms.DateTimeField(
        label="Початок періоду",
        widget=forms.DateTimeInput(
            format="%Y-%m-%d %H:%M",
            attrs={
                "class": "form-control datetime-picker",
                "placeholder": "Оберіть дату й час",
                "autocomplete": "off",
            },
        ),
    )
    end = forms.DateTimeField(
        label="Кінець періоду",
        widget=forms.DateTimeInput(
            format="%Y-%m-%d %H:%M",
            attrs={
                "class": "form-control datetime-picker",
                "placeholder": "Оберіть дату й час",
                "autocomplete": "off",
            },
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        now = timezone.now()
        start_default = (now - timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)
        end_default = now.replace(second=0, microsecond=0)
        self.fields["start"].initial = start_default
        self.fields["end"].initial = end_default

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("start")
        end = cleaned.get("end")
        if start and end and start > end:
            raise forms.ValidationError("Початок періоду не може бути пізніше завершення.")
        return cleaned

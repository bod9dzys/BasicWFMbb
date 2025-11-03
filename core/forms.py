# core/forms.py
from datetime import datetime, timedelta
import zipfile
from pathlib import Path
import tempfile
import shutil

from django import forms
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files import File
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Submit, Field, Layout
from .models import Shift, Agent, Direction, SickLeaveProof


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
    team_lead = forms.ModelChoiceField(
        queryset=User.objects.none(),
        label="Тімлід",
        required=False,
        empty_label="Усі тімліди",
    )
    agent = forms.ModelChoiceField(
        queryset=Agent.objects.select_related("user").filter(active=True),
        label="Агент",
        required=False,
        empty_label="Усі агенти",
    )
    direction = forms.MultipleChoiceField(
        label="Напрямки",
        required=False,
        choices=[],
        widget=forms.SelectMultiple(
            attrs={
                "class": "form-select",
                "data-placeholder": "Усі напрямки",
            }
        ),
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

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        # Base querysets
        tl_ids_qs = (
            Agent.objects.exclude(team_lead__isnull=True)
            .values_list("team_lead", flat=True)
            .distinct()
        )
        tl_ids = list(tl_ids_qs)
        team_lead_qs = User.objects.filter(id__in=tl_ids).order_by("last_name", "first_name")

        if user and not user.is_staff and user.id in tl_ids:
            team_lead_qs = team_lead_qs.filter(pk=user.pk)
            self.fields["team_lead"].empty_label = None
            if not self.is_bound:
                self.fields["team_lead"].initial = user.pk
        self.fields["team_lead"].queryset = team_lead_qs
        self.fields["team_lead"].label_from_instance = (
            lambda obj: (obj.get_full_name() or "").strip() or obj.username
        )

        agent_qs = Agent.objects.select_related("user").filter(active=True).order_by(
            "user__last_name", "user__first_name"
        )

        team_lead_value = self.data.get("team_lead") if self.is_bound else self.initial.get("team_lead")
        try:
            team_lead_pk = int(team_lead_value) if team_lead_value else None
        except (TypeError, ValueError):
            team_lead_pk = None

        if team_lead_pk:
            agent_qs = agent_qs.filter(team_lead_id=team_lead_pk)
        elif user and not user.is_staff and user.id in tl_ids:
            agent_qs = agent_qs.filter(team_lead_id=user.pk)

        self.fields["agent"].queryset = agent_qs

        self.fields["direction"].choices = Direction.choices

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

        team_lead = cleaned.get("team_lead")
        agent = cleaned.get("agent")
        if agent and team_lead and agent.team_lead_id and agent.team_lead_id != team_lead.id:
            raise forms.ValidationError("Обраний агент не належить зазначеному тімліду.")

        return cleaned


class DashboardFilterForm(forms.Form):
    day = forms.DateField(
        label="Дата",
        widget=forms.DateInput(
            format="%Y-%m-%d",
            attrs={
                "class": "form-control date-picker",
                "placeholder": "Оберіть дату",
            },
        ),
    )
    _TIME_CHOICES = [
        (f"{hour:02d}:{minute:02d}", f"{hour:02d}:{minute:02d}")
        for hour in range(0, 24)
        for minute in (0, 30)
    ]

    time_start = forms.TypedChoiceField(
        label="Початок",
        choices=_TIME_CHOICES,
        coerce=lambda value: datetime.strptime(value, "%H:%M").time(),
        widget=forms.Select(
            attrs={
                "class": "form-select",
                "data-placeholder": "Оберіть час початку",
            },
        ),
    )
    direction = forms.ChoiceField(
        label="",
        required=False,
        choices=[],
        widget=forms.HiddenInput(),
    )
    window_direction = forms.ChoiceField(
        label="",
        required=False,
        choices=[],
        widget=forms.HiddenInput(),
    )
    time_end = forms.TypedChoiceField(
        label="Кінець",
        choices=_TIME_CHOICES,
        coerce=lambda value: datetime.strptime(value, "%H:%M").time(),
        widget=forms.Select(
            attrs={
                "class": "form-select",
                "data-placeholder": "Оберіть час завершення",
            },
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        tz = timezone.get_current_timezone()
        now = timezone.localtime(timezone.now(), tz)
        start_default = now.replace(minute=0, second=0, microsecond=0)
        end_default = (start_default + timedelta(hours=1))

        if not self.is_bound:
            self.fields["day"].initial = start_default.date()
            self.fields["time_start"].initial = start_default.strftime("%H:%M")
            self.fields["time_end"].initial = end_default.strftime("%H:%M")

        choices = [("", "Усі напрямки")] + list(Direction.choices)
        self.fields["direction"].choices = choices
        self.fields["window_direction"].choices = choices

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("time_start")
        end = cleaned.get("time_end")
        if start and end and start >= end:
            raise forms.ValidationError("Час початку має бути раніше за час завершення.")
        return cleaned


class SickLeaveRequestForm(forms.Form):
    agent = forms.ModelChoiceField(
        queryset=Agent.objects.none(),
        label="Агент",
        required=True,
        empty_label=None,
    )
    start = forms.DateField(
        label="Початок лікарняного (включно)",
        widget=forms.DateInput(
            attrs={
                "type": "text",
                "class": "form-control date-picker",
                "placeholder": "Оберіть дату",
                "autocomplete": "off",
            }
        ),
        input_formats=["%Y-%m-%d"],
        required=True,
    )
    end = forms.DateField(
        label="Кінець лікарняного (включно)",
        widget=forms.DateInput(
            attrs={
                "type": "text",
                "class": "form-control date-picker",
                "placeholder": "Оберіть дату",
                "autocomplete": "off",
            }
        ),
        input_formats=["%Y-%m-%d"],
        required=True,
    )
    attachment = forms.FileField(
        label="Підтвердження лікарняного",
        required=False,
        widget=forms.ClearableFileInput(
            attrs={
                "class": "form-control",
            }
        ),
        help_text="Додайте файл підтвердження",
    )
    attach_later = forms.BooleanField(
        label="Прикріпити пізніше",
        required=False,
        widget=forms.CheckboxInput(
            attrs={
                "class": "form-check-input",
            }
        ),
    )

    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        tz = timezone.get_current_timezone()
        now_local = timezone.localtime(timezone.now(), tz)

        base_qs = Agent.objects.select_related("user").filter(active=True)
        if user.is_superuser or user.is_staff:
            allowed_qs = base_qs
        else:
            filters = Q()
            if hasattr(user, "agent"):
                filters |= Q(pk=user.agent.pk)
            filters |= Q(team_lead=user)
            if filters:
                allowed_qs = base_qs.filter(filters)
            else:
                allowed_qs = base_qs.none()

        allowed_qs = allowed_qs.order_by("user__last_name", "user__first_name")
        self.allowed_agents = allowed_qs
        self.fields["agent"].queryset = allowed_qs
        self.fields["agent"].widget.attrs.setdefault("class", "form-select")

        if allowed_qs.count() == 1:
            single_agent = allowed_qs.first()
            self.fields["agent"].initial = single_agent
            if not (user.is_staff or user.is_superuser):
                self.fields["agent"].disabled = True

        if not self.is_bound:
            start_default = now_local.date()
            end_default = start_default + timedelta(days=1)
            self.initial["start"] = start_default.strftime("%Y-%m-%d")
            self.initial["end"] = end_default.strftime("%Y-%m-%d")

    def clean_agent(self):
        agent = self.cleaned_data.get("agent")
        if not self.allowed_agents.exists():
            raise forms.ValidationError("У вас немає доступних агентів для запиту.")
        if agent not in self.allowed_agents:
            raise forms.ValidationError("Ви не можете створити запит для цього агента.")
        return agent

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("start")
        end = cleaned.get("end")
        if start and end and start > end:
            raise forms.ValidationError("Дата початку має бути раніше або дорівнювати даті завершення.")

        attachment = cleaned.get("attachment")
        attach_later = cleaned.get("attach_later")

        if not attachment and not attach_later:
            raise forms.ValidationError(
                _("Додайте підтвердження або оберіть «Прикріпити пізніше».")
            )

        if attachment:
            cleaned["attachment"] = self._prepare_attachment(attachment)

        return cleaned

    def _prepare_attachment(self, attachment):
        max_size_mb = 10
        if attachment.size > max_size_mb * 1024 * 1024:
            raise ValidationError(
                _("Розмір файлу не може перевищувати %(size)d МБ."),
                params={"size": max_size_mb},
            )
        return _compress_uploaded_file(attachment)


class SickLeaveProofUploadForm(forms.ModelForm):
    class Meta:
        model = SickLeaveProof
        fields = ["attachment"]
        widgets = {
            "attachment": forms.ClearableFileInput(
                attrs={
                    "class": "form-control",
                }
            )
        }
        labels = {
            "attachment": _("Підтвердження лікарняного"),
        }

    def clean_attachment(self):
        attachment = self.cleaned_data.get("attachment")
        if attachment:
            max_size_mb = 10
            if attachment.size > max_size_mb * 1024 * 1024:
                raise ValidationError(
                    _("Розмір файлу не може перевищувати %(size)d МБ."),
                    params={"size": max_size_mb},
                )
            return _compress_uploaded_file(attachment)
        raise ValidationError(_("Додайте файл підтвердження."))


def _compress_uploaded_file(uploaded_file):
    original_name = Path(getattr(uploaded_file, "name", "") or "proof").name
    base_name = Path(original_name).stem or "proof"
    compressed_name = f"{base_name}.zip"

    if hasattr(uploaded_file, "seek"):
        uploaded_file.seek(0)

    # Читаємо вміст файлу як байти
    try:
        file_content = uploaded_file.read()
    except Exception as e:
        # Спробуємо ще раз, якщо файл вже був прочитаний
        if hasattr(uploaded_file, "seek"):
            uploaded_file.seek(0)
            file_content = uploaded_file.read()
        else:
            raise e  # Якщо нічого не допомагає, прокидаємо помилку

    temp_file = tempfile.SpooledTemporaryFile(max_size=5 * 1024 * 1024)
    with zipfile.ZipFile(
        temp_file,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=5,
        allowZip64=True,
    ) as archive:
        # Використовуємо .writestr() для запису байтів напряму
        archive.writestr(original_name, file_content)

    temp_file.seek(0)
    return File(temp_file, name=compressed_name)
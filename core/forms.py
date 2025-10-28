# core/forms.py
from django import forms
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Submit
from .models import Shift, ShiftExchange

class DummyForm(forms.Form):
    name = forms.CharField(label="Ім'я")
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_method = "post"
        self.helper.add_input(Submit("submit", "Зберегти"))

class ExchangeCreateForm(forms.Form):
    # Було: from_shift = forms.ModelChoiceFilter = forms.ModelChoiceField(...)
    from_shift = forms.ModelChoiceField(
        queryset=Shift.objects.select_related("agent", "agent__user").order_by("-start"),
        label="Моя/перша зміна"
    )

    to_shift = forms.ModelChoiceField(
        queryset=Shift.objects.select_related("agent", "agent__user").order_by("-start"),
        label="Чужа/друга зміна"
    )
    comment = forms.CharField(label="Коментар", widget=forms.Textarea, required=False)

    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.helper = FormHelper()
        self.helper.form_method = "post"
        self.helper.add_input(Submit("submit", "Запросити обмін"))

    def clean(self):
        cleaned = super().clean()
        f = cleaned.get("from_shift")
        t = cleaned.get("to_shift")
        if not f or not t:
            return cleaned
        if f.id == t.id:
            raise forms.ValidationError("Вибери дві різні зміни.")
        return cleaned

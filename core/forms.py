# core/forms.py
from django import forms
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Submit, Layout, Field
from .models import Shift, Agent # Додано Agent
from django.contrib.auth.models import User # Якщо потрібно фільтрувати за User

class ExchangeCreateForm(forms.Form):
    # Поля для вибору агентів
    from_agent = forms.ModelChoiceField(
        queryset=Agent.objects.select_related('user').filter(active=True).order_by('user__last_name', 'user__first_name'),
        label="Агент 1 (чия зміна)",
        required=True
    )
    to_agent = forms.ModelChoiceField(
        queryset=Agent.objects.select_related('user').filter(active=True).order_by('user__last_name', 'user__first_name'),
        label="Агент 2 (на чию зміну)",
        required=True
    )

    # Поля змін тепер починаються порожніми
    from_shift = forms.ModelChoiceField(
        queryset=Shift.objects.none(), # Починаємо з порожнього queryset
        label="Зміна Агента 1",
        required=True
        # Додаємо id для JavaScript
        # widget=forms.Select(attrs={'id': 'id_from_shift'}) # Або зробимо це в шаблоні/crispy forms
    )
    to_shift = forms.ModelChoiceField(
        queryset=Shift.objects.none(), # Починаємо з порожнього queryset
        label="Зміна Агента 2",
        required=True
        # widget=forms.Select(attrs={'id': 'id_to_shift'}) # Або зробимо це в шаблоні/crispy forms
    )

    comment = forms.CharField(label="Коментар", widget=forms.Textarea, required=False)

    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user # Зберігаємо користувача для можливих перевірок

        # Обмеження вибору першого агента (якщо це не адмін/менеджер)
        # Припускаємо, що звичайний агент може ініціювати обмін лише своєю зміною
        if not user.is_staff and hasattr(user, 'agent'): # Перевіряємо чи це агент
             self.fields['from_agent'].queryset = Agent.objects.filter(user=user)
             self.fields['from_agent'].initial = user.agent
             self.fields['from_agent'].disabled = True # Не даємо змінювати себе

        self.helper = FormHelper()
        self.helper.form_method = "post"
        # Оновлюємо Layout для нових полів та додаємо ID для JS
        self.helper.layout = Layout(
            Field('from_agent', id='id_from_agent'),
            Field('from_shift', id='id_from_shift'),
            Field('to_agent', id='id_to_agent'),
            Field('to_shift', id='id_to_shift'),
            'comment',
            Submit("submit", "Запросити обмін")
        )

    # Важливо: Додати clean метод для перевірки, що вибрані зміни належать вибраним агентам
    def clean(self):
        cleaned_data = super().clean()
        from_agent = cleaned_data.get("from_agent")
        to_agent = cleaned_data.get("to_agent")
        from_shift_id = self.data.get("from_shift") # Отримуємо ID з POST даних
        to_shift_id = self.data.get("to_shift")       # Отримуємо ID з POST даних

        if from_agent and to_agent and from_agent == to_agent:
             raise forms.ValidationError("Оберіть двох різних агентів.")

        # Перевіряємо належність змін після того, як JS заповнить поля
        if from_agent and from_shift_id:
            try:
                from_shift = Shift.objects.get(pk=from_shift_id)
                if from_shift.agent != from_agent:
                     raise forms.ValidationError("Обрана 'Зміна Агента 1' не належить Агенту 1.")
                cleaned_data['from_shift'] = from_shift # Додаємо об'єкт Shift до cleaned_data
            except Shift.DoesNotExist:
                 raise forms.ValidationError("Обрана 'Зміна Агента 1' не існує.")

        if to_agent and to_shift_id:
            try:
                to_shift = Shift.objects.get(pk=to_shift_id)
                if to_shift.agent != to_agent:
                     raise forms.ValidationError("Обрана 'Зміна Агента 2' не належить Агенту 2.")
                cleaned_data['to_shift'] = to_shift # Додаємо об'єкт Shift до cleaned_data
            except Shift.DoesNotExist:
                 raise forms.ValidationError("Обрана 'Зміна Агента 2' не існує.")

        # Додаткова перевірка, якщо from_shift та to_shift були обрані
        f_shift = cleaned_data.get("from_shift")
        t_shift = cleaned_data.get("to_shift")
        if f_shift and t_shift and f_shift.id == t_shift.id:
             raise forms.ValidationError("Виберіть дві різні зміни для обміну.")

        return cleaned_data
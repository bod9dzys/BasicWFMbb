# core/resources.py
from import_export import resources, fields
from import_export.widgets import ForeignKeyWidget
from .models import Shift, Agent
from django.contrib.auth.models import User


class ShiftResource(resources.ModelResource):
    # Це "магічний" віджет, який шукає об'єкт за текстовим полем
    agent = fields.Field(
        column_name='agent',  # Назва колонки у вашому Excel/CSV файлі
        attribute='agent',  # Назва поля в моделі Shift

        # Головна частина:
        # Ми кажемо: "Візьми значення з колонки 'agent',
        # знайди об'єкт Agent, у якого поле 'user__username'
        # дорівнює цьому значенню".
        widget=ForeignKeyWidget(Agent, 'user__username')
    )

    class Meta:
        model = Shift

        # Вкажіть, які поля ви очікуєте з файлу
        # Переконайтеся, що 'agent' тут є.
        fields = ('id', 'agent', 'start', 'end', 'direction', 'status', 'activity', 'comment')

        # Якщо ви хочете оновлювати існуючі зміни за 'id', залиште це.
        # Якщо ви завжди імпортуєте тільки нові, закоментуйте або видаліть:
        import_id_fields = ('id',)

        # Якщо ви хочете, щоб неіснуючі об'єкти створювались:
        skip_unchanged = True
        report_skipped = True
# core/resources.py
from import_export import resources, fields
from import_export.widgets import ForeignKeyWidget
from .models import Shift, Agent
from django.contrib.auth.models import User
from django.contrib.auth.hashers import make_password # <-- Додано для пароля

class ShiftResource(resources.ModelResource):
    agent = fields.Field(
        column_name='agent',
        attribute='agent',
        widget=ForeignKeyWidget(Agent, 'user__username')
    )

    class Meta:
        model = Shift
        fields = ('id', 'agent', 'start', 'end', 'direction', 'status', 'activity', 'comment')
        import_id_fields = ('id',)
        skip_unchanged = True
        report_skipped = True

    # --- ДОДАНО ЦЕЙ МЕТОД ---
    def before_import_row(self, row, **kwargs):
        """
        Створює User та Agent, якщо їх немає, перед імпортом рядка Shift.
        """
        username = row.get('agent')
        if username:
            # Спробувати знайти або створити користувача (User)
            user, created = User.objects.get_or_create(
                username=username,
                defaults={
                    # Встановлюємо тимчасовий пароль для нового користувача
                    # Ви можете змінити 'temp_password123' на щось інше
                    # Або розробити іншу логіку генерації/встановлення паролів
                    'password': make_password('temp_password123'),
                    'is_staff': False, # За бажанням, можна одразу не давати доступ до адмінки
                    'is_active': True,
                }
            )
            if created:
                print(f"Створено нового користувача: {username}")

            # Спробувати знайти або створити агента (Agent), пов'язаного з користувачем
            agent, agent_created = Agent.objects.get_or_create(
                user=user,
                defaults={} # Можна додати значення за замовчуванням для полів Agent, якщо потрібно
            )
            if agent_created:
                print(f"Створено нового агента для користувача: {username}")
    # --- КІНЕЦЬ ДОДАНОГО МЕТОДУ ---
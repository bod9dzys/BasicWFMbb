# core/resources.py
from import_export import resources, fields
from import_export.widgets import ForeignKeyWidget, Widget
from .models import Shift, Agent
from django.contrib.auth.models import User
from django.contrib.auth.hashers import make_password

# Допоміжний віджет, щоб просто читати значення без перетворень
class SimpleReadWidget(Widget):
    def clean(self, value, row=None, *args, **kwargs):
        return value

class ShiftResource(resources.ModelResource):
    # Поле для зв'язку Shift -> Agent.
    # Воно читатиме колонку 'agent' з CSV, але очікуватиме ID після обробки в before_import_row.
    agent = fields.Field(
        column_name='agent', # Назва колонки в CSV
        attribute='agent',   # Атрибут моделі Shift
        widget=ForeignKeyWidget(Agent, 'pk') # Шукаємо Agent за ID (pk)
    )
    # Тимчасове поле, щоб прочитати оригінальний username з CSV ДО того,
    # як before_import_row замінить значення 'agent' на ID.
    # Ми не хочемо, щоб це поле записувалось у модель Shift.
    original_agent_username = fields.Field(
        column_name='agent',
        attribute='original_agent_username', # Просто тимчасовий атрибут на рівні ресурсу
        widget=SimpleReadWidget(), # Просто читаємо рядок як є
        readonly=True # Не намагаємося записати це в модель
    )

    # Словник для кешування агентів {username: agent_id}
    _agent_cache = {}

    class Meta:
        model = Shift
        # Поля, які імпортуються/експортуються. 'agent' тепер працює з ID.
        fields = ('id', 'agent', 'start', 'end', 'direction', 'status', 'activity', 'comment', 'original_agent_username')
        # Вказуємо порядок для експорту (без тимчасового поля)
        export_order = ('id', 'agent', 'start', 'end', 'direction', 'status', 'activity', 'comment')
        import_id_fields = ('id',)
        skip_unchanged = True
        report_skipped = False # Показувати пропущені рядки може бути корисно для відладки
        # Важливо: Не намагатися створити об'єкти Agent через віджет ForeignKeyWidget
        # Ми робимо це вручну в before_import
        clean_model_instances = True # Дозволяє before_import_row модифікувати дані рядка

    def before_import(self, dataset, using_transactions, dry_run, **kwargs):
        """
        Знаходить або створює всіх User/Agent ОДИН РАЗ перед імпортом.
        Заповнює кеш _agent_cache = {username: agent_id}.
        """
        self._agent_cache.clear()

        # Використовуємо 'agent' як назву колонки для отримання імен
        if 'agent' not in dataset.headers:
             raise ValueError("Колонка 'agent' відсутня у CSV файлі.")

        usernames = set(username for username in dataset['agent'] if username) # Ігноруємо порожні
        print(f"Знайдено {len(usernames)} унікальних імен агентів у файлі.")

        # Оптимізований запит: отримуємо username та ID агента одним запитом
        existing_agents_data = Agent.objects.filter(user__username__in=usernames).values_list('user__username', 'pk')
        self._agent_cache = dict(existing_agents_data)
        existing_usernames = set(self._agent_cache.keys())

        new_usernames = usernames - existing_usernames
        print(f"З них {len(new_usernames)} нових.")

        # Створюємо нових користувачів
        new_users_to_create = []
        for username in new_usernames:
            new_users_to_create.append(
                User(
                    username=username,
                    password=make_password('temp_password123'),
                    is_staff=False,
                    is_active=True
                )
            )

        created_users = []
        if new_users_to_create:
            # batch_size допомагає уникнути проблем з обмеженнями БД на кількість параметрів
            created_users = User.objects.bulk_create(new_users_to_create, batch_size=500)
            print(f"Створено {len(created_users)} нових користувачів.")

        # Словник нових користувачів {username: user_object}
        new_users_map = {user.username: user for user in created_users}

        # Створюємо нових агентів для нових користувачів
        agents_to_create = []
        for username, user_obj in new_users_map.items():
             agents_to_create.append(Agent(user=user_obj))

        created_agents = []
        if agents_to_create:
             # batch_size також важливий тут
            created_agents = Agent.objects.bulk_create(agents_to_create, batch_size=500)
            print(f"Створено {len(created_agents)} нових агентів.")

            # Оновлюємо кеш для нових агентів
            # Важливо: після bulk_create об'єкти в created_agents мають user_id,
            # але може не бути повного об'єкта user. Використовуємо new_users_map.
            # Якщо Django версії < 4.1, pk може не бути встановлений після bulk_create без refresh.
            # Якщо у вас стара версія Django, може знадобитися додатковий запит для отримання ID.
            # Припускаємо, що PK встановлюється коректно (сучасні версії Django).
            for agent in created_agents:
                 # Шукаємо username відповідного user в new_users_map за user_id
                 username = next((uname for uname, u in new_users_map.items() if u.pk == agent.user_id), None)
                 if username and agent.pk:
                      self._agent_cache[username] = agent.pk
                 else:
                      print(f"Помилка: Не вдалося додати створеного агента для user_id {agent.user_id} до кешу.")


        print("Кеш агентів підготовлено.")


    def before_import_row(self, row, row_number=None, **kwargs):
        """
        Підставляє ID агента з кешу в колонку 'agent' для ForeignKeyWidget.
        """
        # Читаємо оригінальний username, який зберегло поле 'original_agent_username'
        username = row.get('agent') # Доступ до оригінального значення колонки 'agent'
        if username and username in self._agent_cache:
            # Замінюємо значення в колонці 'agent' на ID
            row['agent'] = self._agent_cache[username]
        elif username:
            # Якщо username є, але його немає в кеші - це помилка
            print(f"ПОМИЛКА в рядку {row_number}: Не знайдено ID для агента '{username}' у кеші!")
            # Можна пропустити рядок, додавши skip_row=True, або викликати виключення
            # raise ValueError(f"Agent ID for {username} not found in cache.")
            kwargs['skip_row'] = True # Пропускаємо цей рядок
            # Або додати помилку до рядка
            # self.add_instance_error(None, row_number, row, ValidationError(f"Агента '{username}' не знайдено."))

        else:
            # Якщо username порожній, пропускаємо рядок
             kwargs['skip_row'] = True


    # Можна видалити цей метод, якщо він був у попередній версії
    # def after_import_row(self, row, row_result, **kwargs):
    #     pass
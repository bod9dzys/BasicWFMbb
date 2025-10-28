# core/resources.py
from import_export import resources, fields
from import_export.widgets import ForeignKeyWidget
from .models import Shift, Agent
from django.contrib.auth.models import User
from django.contrib.auth.hashers import make_password

class ShiftResource(resources.ModelResource):
    # Використовуємо ID для ForeignKeyWidget для швидшого пошуку під час імпорту рядків
    agent = fields.Field(
        column_name='agent_username', # Тимчасова назва колонки для username
        attribute='agent',
        # Ми будемо підставляти ID агента, тому віджет тепер шукає за ID
        widget=ForeignKeyWidget(Agent, 'pk')
    )
    # Зберігаємо оригінальне поле для доступу в before_import
    agent_username = fields.Field(column_name='agent', attribute='agent_username')

    # Словник для кешування агентів {username: agent_id}
    _agent_cache = {}

    class Meta:
        model = Shift
        # Використовуємо тимчасову колонку 'agent_username', а не 'agent' напряму
        fields = ('id', 'agent_username', 'start', 'end', 'direction', 'status', 'activity', 'comment')
        # Не імпортуємо поле agent_username напряму в модель
        export_order = ('id', 'agent', 'start', 'end', 'direction', 'status', 'activity', 'comment')
        import_id_fields = ('id',)
        skip_unchanged = True
        report_skipped = True

    def before_import(self, dataset, using_transactions, dry_run, **kwargs):
        """
        Знаходить або створює всіх User/Agent ОДИН РАЗ перед імпортом.
        Заповнює кеш _agent_cache = {username: agent_id}.
        """
        self._agent_cache.clear() # Очищаємо кеш на випадок повторних імпортів

        # 1. Збираємо всі унікальні username з колонки 'agent' датасету
        usernames = set(dataset['agent'])
        print(f"Знайдено {len(usernames)} унікальних імен агентів у файлі.")

        # 2. Знаходимо існуючих користувачів та їх агентів
        existing_users = User.objects.filter(username__in=usernames).select_related('agent')
        existing_usernames = set()
        for user in existing_users:
            try:
                 # Додаємо існуючих агентів до кешу
                self._agent_cache[user.username] = user.agent.pk
                existing_usernames.add(user.username)
            except Agent.DoesNotExist:
                 # Якщо User є, а Agent чомусь ні - створимо Agent нижче
                print(f"Попередження: Користувач {user.username} існує, але пов'язаний Agent - ні. Спробую створити.")


        # 3. Визначаємо, які username нові
        new_usernames = usernames - existing_usernames
        print(f"З них {len(new_usernames)} нових.")

        # 4. Створюємо нових користувачів (якщо є)
        new_users_to_create = []
        for username in new_usernames:
            new_users_to_create.append(
                User(
                    username=username,
                    password=make_password('temp_password123'), # Тимчасовий пароль
                    is_staff=False,
                    is_active=True
                )
            )
        # Створюємо всіх нових користувачів одним запитом!
        if new_users_to_create:
            created_users = User.objects.bulk_create(new_users_to_create)
            print(f"Створено {len(created_users)} нових користувачів.")
            # Словник нових користувачів для легкого доступу {username: user_object}
            new_users_map = {user.username: user for user in created_users}
        else:
             new_users_map = {}

        # 5. Створюємо нових агентів для нових користувачів І для тих існуючих, у кого не було Agent
        agents_to_create = []
        # Спочатку для нових
        for username, user_obj in new_users_map.items():
             agents_to_create.append(Agent(user=user_obj))
        # Потім для існуючих User без Agent
        users_needing_agent = existing_users.filter(agent__isnull=True)
        for user_obj in users_needing_agent:
             agents_to_create.append(Agent(user=user_obj))

        # Створюємо всіх нових агентів одним запитом!
        if agents_to_create:
            created_agents = Agent.objects.bulk_create(agents_to_create)
            print(f"Створено {len(created_agents)} нових агентів.")
            # Оновлюємо кеш для нових агентів
            for agent in created_agents:
                 # user_id тут вже встановлено завдяки bulk_create
                self._agent_cache[agent.user.username] = agent.pk
        # Дозаповнюємо кеш для існуючих User, яким щойно створили Agent
        if users_needing_agent and created_agents:
             agents_map = {agent.user_id: agent.pk for agent in created_agents if agent.user_id in users_needing_agent.values_list('id', flat=True)}
             for user_obj in users_needing_agent:
                  if user_obj.id in agents_map:
                       self._agent_cache[user_obj.username] = agents_map[user_obj.id]


        print("Кеш агентів підготовлено.")


    def before_import_row(self, row, **kwargs):
        """
        Тепер цей метод просто підставляє ID агента з кешу.
        """
        username = row.get('agent') # Беремо username з оригінальної колонки
        if username in self._agent_cache:
            # Замінюємо username на ID агента для ForeignKeyWidget
            row['agent_username'] = self._agent_cache[username]
        else:
            # Це не повинно статись, якщо before_import спрацював правильно,
            # але про всяк випадок залишаємо перевірку
            print(f"ПОМИЛКА: Не знайдено ID для агента {username} у кеші!")
            # Можна тут викликати виключення або пропустити рядок
            raise ValueError(f"Agent ID for {username} not found in cache.")

    # Видаляємо старий before_import_row, якщо він ще є
# core/resources.py
from import_export import resources, fields
from import_export.widgets import ForeignKeyWidget, Widget
from .models import Shift, ShiftExchange, Agent, ShiftStatus
from django.contrib.auth.models import User, Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.contrib.auth.hashers import make_password
from django.utils.text import slugify

UKRAINIAN_DIRECTION_MAP = {
    "дзвінки": "calls",
    "дзінки": "calls",
    "дзвонки": "calls",
    "кіл-центр": "calls",
    "тікети": "tickets",
    "тикети": "tickets",
    "tickets": "tickets",
    "чати": "chats",
    "чат": "chats",
    "соцмережі": "chats",
    "чати/соцмережі": "chats",
}
DEFAULT_DIRECTION = "calls"

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
    team_lead = fields.Field(
        column_name='team_lead',
        attribute='team_lead_display',
        widget=SimpleReadWidget(),
        readonly=True
    )
    # Тимчасове поле, щоб прочитати оригінальний username з CSV ДО того,
    # як before_import_row замінить значення 'agent' на ID.
    # Ми не хочемо, щоб це поле записувалось у модель Shift.
    original_agent_name = fields.Field(
        column_name='agent',
        attribute='original_agent_name', # Просто тимчасовий атрибут на рівні ресурсу
        widget=SimpleReadWidget(), # Просто читаємо рядок як є
        readonly=True # Не намагаємося записати це в модель
    )

    # Словник для кешування агентів {normalized_name: agent_id}
    _agent_cache = {}
    _team_lead_cache = {}
    _agent_team_lead_map = {}

    class Meta:
        model = Shift
        # Поля, які імпортуються/експортуються. 'agent' тепер працює з ID.
        fields = ('id', 'agent', 'team_lead', 'start', 'end', 'direction', 'status', 'activity', 'comment', 'original_agent_name')
        # Вказуємо порядок для експорту (без тимчасового поля)
        export_order = ('id', 'agent', 'team_lead', 'start', 'end', 'direction', 'status', 'activity', 'comment')
        import_id_fields = ('id',)
        skip_unchanged = True
        report_skipped = False # Показувати пропущені рядки може бути корисно для відладки
        # Важливо: Не намагатися створити об'єкти Agent через віджет ForeignKeyWidget
        # Ми робимо це вручну в before_import
        clean_model_instances = True # Дозволяє before_import_row модифікувати дані рядка

    def before_import(self, dataset, using_transactions=None, dry_run=False, **kwargs):
        """
        Знаходить або створює всіх User/Agent ОДИН РАЗ перед імпортом.
        Заповнює кеш _agent_cache = {normalized_name: agent_id}.
        """
        self._agent_cache.clear()
        self._team_lead_cache.clear()
        self._agent_team_lead_map = {}

        # Використовуємо 'agent' як назву колонки для отримання імен
        if 'agent' not in dataset.headers:
            raise ValueError("Колонка 'agent' відсутня у CSV файлі.")
        if 'team_lead' not in dataset.headers:
            raise ValueError("Колонка 'team_lead' відсутня у CSV файлі.")

        # Збираємо унікальні імена та пам'ятаємо оригінальний запис
        normalized_to_original = {}
        normalized_agent_to_tl = {}
        normalized_team_leads = {}
        for row in dataset.dict:
            raw_name = row.get('agent')
            if not raw_name:
                continue
            normalized = self._normalize_name(raw_name)
            if normalized:
                normalized_to_original.setdefault(normalized, self._clean_display_name(raw_name))
                raw_tl = row.get('team_lead')
                tl_normalized = self._normalize_name(raw_tl)
                if tl_normalized:
                    normalized_agent_to_tl[normalized] = tl_normalized
                    normalized_team_leads.setdefault(tl_normalized, self._clean_display_name(raw_tl))

        print(f"Знайдено {len(normalized_to_original)} унікальних імен агентів у файлі.")
        print(f"Знайдено {len(normalized_team_leads)} унікальних тімлідів у файлі.")

        normalized_names = set(normalized_to_original.keys())

        # Забезпечуємо наявність тімлідів
        taken_usernames = set(User.objects.values_list('username', flat=True))
        tl_group, _ = Group.objects.get_or_create(name="TL")
        self._ensure_tl_group_permissions(tl_group)

        if normalized_team_leads:
            for user in User.objects.all():
                display_name = self._clean_display_name(user.get_full_name() or user.username)
                normalized = self._normalize_name(display_name)
                if normalized in normalized_team_leads and normalized not in self._team_lead_cache:
                    self._team_lead_cache[normalized] = user.pk

            new_tl_names = set(normalized_team_leads.keys()) - set(self._team_lead_cache.keys())
            for normalized in new_tl_names:
                original_name = normalized_team_leads[normalized]
                first_name, last_name = self._split_name(original_name)
                username = self._generate_username(original_name, taken_usernames)
                user = User.objects.create(
                    username=username,
                    first_name=first_name,
                    last_name=last_name,
                    password=make_password('temp_password123'),
                    is_staff=True,
                    is_active=True,
                )
                user.groups.add(tl_group)
                self._team_lead_cache[normalized] = user.pk

            print(f"Підготовлено {len(self._team_lead_cache)} тімлідів для призначення.")

        # Підтягнемо вже існуючих агентів за іменами
        matched_existing = 0
        for agent in Agent.objects.select_related('user').all():
            display_name = self._clean_display_name(agent.user.get_full_name() or agent.user.username)
            normalized = self._normalize_name(display_name)
            if normalized in normalized_names:
                self._agent_cache[normalized] = agent.pk
                matched_existing += 1

        print(f"Знайдено {matched_existing} агентів у базі.")

        # Визначаємо нові імена, для яких потрібно створити користувача та агента
        new_normalized_names = normalized_names - set(self._agent_cache.keys())
        print(f"Створюємо {len(new_normalized_names)} нових агентів.")

        for normalized in new_normalized_names:
            original_name = normalized_to_original[normalized]
            first_name, last_name = self._split_name(original_name)
            username = self._generate_username(original_name, taken_usernames)
            user = User.objects.create(
                username=username,
                first_name=first_name,
                last_name=last_name,
                password=make_password('temp_password123'),
                is_staff=False,
                is_active=True,
            )
            agent = Agent.objects.create(user=user)
            self._agent_cache[normalized] = agent.pk

        print("Кеш агентів підготовлено.")
        self._agent_team_lead_map = normalized_agent_to_tl

        # Призначаємо тімлідів агентам (існуючим та новим)
        assignments_done = 0
        for agent_normalized, tl_normalized in self._agent_team_lead_map.items():
            agent_id = self._agent_cache.get(agent_normalized)
            tl_id = self._team_lead_cache.get(tl_normalized)
            if agent_id and tl_id:
                updated = Agent.objects.filter(pk=agent_id).exclude(team_lead_id=tl_id).update(team_lead_id=tl_id)
                if updated:
                    assignments_done += 1
            elif agent_id and not tl_id:
                print(f"ПОПЕРЕДЖЕННЯ: не знайдено тімліда '{normalized_team_leads.get(tl_normalized, tl_normalized)}' для агента з ім'ям '{normalized_to_original.get(agent_normalized, agent_normalized)}'.")

        if assignments_done:
            print(f"Оновлено тімліда для {assignments_done} агентів.")

    def before_import_row(self, row, row_number=None, **kwargs):
        """
        Підставляє ID агента з кешу в колонку 'agent' для ForeignKeyWidget.
        """
        # Читаємо оригінальне значення імені агента, що прийшло із CSV
        name = row.get('agent') # Доступ до оригінального значення колонки 'agent'
        normalized = self._normalize_name(name)
        if normalized and normalized in self._agent_cache:
            # Замінюємо значення в колонці 'agent' на ID
            row['agent'] = self._agent_cache[normalized]
        elif normalized:
            # Якщо ім'я є, але його немає в кеші - це помилка
            print(f"ПОМИЛКА в рядку {row_number}: Не знайдено ID для агента '{name}' у кеші!")
            # Можна пропустити рядок, додавши skip_row=True, або викликати виключення
            # raise ValueError(f"Agent ID for {username} not found in cache.")
            kwargs['skip_row'] = True # Пропускаємо цей рядок
            # Або додати помилку до рядка
            # self.add_instance_error(None, row_number, row, ValidationError(f"Агента '{username}' не знайдено."))
            return

        else:
            # Якщо ім'я агента порожнє, пропускаємо рядок
            kwargs['skip_row'] = True
            return

        # Нормалізуємо напрямок (direction) з урахуванням активності
        current_direction = row.get('direction')
        activity_hint = row.get('activity')
        row['direction'] = self._normalize_direction(current_direction, activity_hint)

        # Гармонізуємо статуси, щоб приймати різні варіанти написання
        row['status'] = self._normalize_status(row.get('status'))

        # Переконуємось, що зазначений тімлід існує
        tl_display = row.get('team_lead')
        tl_normalized = self._normalize_name(tl_display)
        if tl_normalized and tl_normalized not in self._team_lead_cache:
            print(f"ПОПЕРЕДЖЕННЯ: тімліда '{tl_display}' не вдалося знайти або створити. Перевірте дані файлу.")


    # Можна видалити цей метод, якщо він був у попередній версії
    # def after_import_row(self, row, row_result, **kwargs):
    #     pass

    @staticmethod
    def _normalize_name(name):
        if not name:
            return ""
        cleaned = ShiftResource._clean_display_name(name)
        return " ".join(cleaned.split()).casefold()

    @staticmethod
    def _clean_display_name(name):
        if not name:
            return ""
        return " ".join(str(name).strip().split())

    @staticmethod
    def _split_name(full_name):
        cleaned = ShiftResource._clean_display_name(full_name)
        if not cleaned:
            return "", ""
        parts = cleaned.split(" ", 1)
        first_name = parts[0]
        last_name = parts[1] if len(parts) > 1 else ""
        return first_name, last_name

    @staticmethod
    def _generate_username(full_name, taken_usernames):
        base = slugify(full_name, allow_unicode=True).replace('-', '_')
        if not base:
            base = "agent"
        candidate = base
        counter = 1
        while candidate in taken_usernames:
            candidate = f"{base}_{counter}"
            counter += 1
        taken_usernames.add(candidate)
        return candidate

    @staticmethod
    def _normalize_direction(direction_value, activity_value=None):
        def _clean(value):
            return " ".join(str(value).strip().split()) if value else ""

        label = _clean(direction_value)
        fallback = _clean(activity_value)
        normalized = label.casefold()

        if normalized in {"calls", "tickets", "chats"}:
            return normalized

        if normalized:
            mapped = UKRAINIAN_DIRECTION_MAP.get(normalized)
            if mapped:
                return mapped
            print(f"ПОПЕРЕДЖЕННЯ: невідомий напрям '{label}'. Використовую значення за замовчуванням '{DEFAULT_DIRECTION}'.")
            return DEFAULT_DIRECTION

        # Якщо direction порожній - пробуємо activity
        fallback_normalized = fallback.casefold()
        if fallback_normalized in {"calls", "tickets", "chats"}:
            return fallback_normalized

        mapped = UKRAINIAN_DIRECTION_MAP.get(fallback_normalized)
        if mapped:
            return mapped

        if fallback:
            print(f"ПОПЕРЕДЖЕННЯ: невідома активність '{fallback}'. Використовую значення за замовчуванням '{DEFAULT_DIRECTION}'.")

        return DEFAULT_DIRECTION

    @staticmethod
    def _normalize_status(status_value):
        """
        Приводить значення статусу до одного з дозволених ShiftStatus.
        Приймає різні варіанти написання, у тому числі новий статус 'mentor'.
        """
        if not status_value:
            return ShiftStatus.WORK

        raw = str(status_value).strip()
        if not raw:
            return ShiftStatus.WORK

        lowered = raw.casefold()
        alias_map = {
            "off": ShiftStatus.DAY_OFF,
            "day off": ShiftStatus.DAY_OFF,
            "day_off": ShiftStatus.DAY_OFF,
            "vacation": ShiftStatus.VACATION,
            "відпустка": ShiftStatus.VACATION,
            "sick": ShiftStatus.SICK,
            "лікарняний": ShiftStatus.SICK,
            "training": ShiftStatus.TRAINING,
            "тренінг": ShiftStatus.TRAINING,
            "meeting": ShiftStatus.MEETING,
            "мітинг": ShiftStatus.MEETING,
            "onboard": ShiftStatus.ONBOARD,
            "онборд": ShiftStatus.ONBOARD,
            "mentor": ShiftStatus.MENTOR,
            "ментор": ShiftStatus.MENTOR,
            "менторство": ShiftStatus.MENTOR,
        }

        if lowered in alias_map:
            return alias_map[lowered]

        # Дозволяємо пряме використання кодів Статусів (work, day_off тощо)
        valid_values = {choice: choice for choice, _ in ShiftStatus.choices}
        if raw in valid_values:
            return raw

        lowered_map = {choice.casefold(): choice for choice in valid_values}
        if lowered in lowered_map:
            return lowered_map[lowered]

        print(f"ПОПЕРЕДЖЕННЯ: невідомий статус '{raw}'. Використовую значення за замовчуванням '{ShiftStatus.WORK}'.")
        return ShiftStatus.WORK

    def dehydrate_team_lead(self, shift):
        tl = getattr(shift.agent, "team_lead", None)
        if not tl:
            return ""
        display = tl.get_full_name() or tl.username
        return self._clean_display_name(display)

    @staticmethod
    def _ensure_tl_group_permissions(group: Group):
        """
        Додає обов'язкові права для групи тімлідів, якщо вони ще не призначені.
        """
        required = {
            Shift: ["view_shift", "export_schedule"],
            ShiftExchange: ["add_shiftexchange", "view_shiftexchange", "request_exchange", "view_exchange_history"],
        }
        existing = set(group.permissions.values_list("codename", flat=True))
        to_add = []

        for model, codes in required.items():
            content_type = ContentType.objects.get_for_model(model)
            perms = Permission.objects.filter(content_type=content_type, codename__in=codes)
            for perm in perms:
                if perm.codename not in existing:
                    to_add.append(perm)

        if to_add:
            group.permissions.add(*to_add)

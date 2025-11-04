# core/resources.py
from import_export import resources, fields
from import_export.widgets import ForeignKeyWidget, Widget
from .models import Shift, ShiftExchange, Agent, ShiftStatus
from django.contrib.auth.models import User, Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.contrib.auth.hashers import make_password
from django.utils.text import slugify
from django.db import transaction
from import_export.exceptions import SkipRow
from import_export.instance_loaders import CachedInstanceLoader
from import_export.widgets import DateTimeWidget


UKRAINIAN_DIRECTION_MAP = {
    "дзвінки": "calls",
    "тікети": "tickets",
    "чати": "chats",
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



    # Словник для кешування агентів {normalized_name: agent_id}
    _agent_cache = {}
    _team_lead_cache = {}
    _agent_team_lead_map = {}

    class Meta:
        model = Shift
        fields = ('id', 'agent', 'team_lead', 'start', 'end', 'direction', 'status')
        export_order = ('id', 'agent', 'team_lead', 'start', 'end', 'direction', 'status')
        import_id_fields = ('id',)  # ДИВ. пункт 4 нижче щодо альтернативи
        skip_unchanged = True
        report_skipped = False
        use_bulk = True
        batch_size = 1000
        skip_diff = True
        clean_model_instances = False
        use_transactions = True
        instance_loader_class = CachedInstanceLoader

    def before_import(self, dataset, using_transactions=None, dry_run=False, **kwargs):
        """
        ПІСЛЯ РОЗДІЛЕННЯ ПРОЦЕСІВ: імпорт розкладу НЕ створює користувачів/тімлідів.
        Тут лише будуємо кеш агентів, що ВЖЕ існують, і попереджаємо про відсутніх.
        """
        self._agent_cache.clear()
        self._team_lead_cache.clear()
        self._agent_team_lead_map = {}

        # Перевіряємо, що файл має потрібні колонки
        if 'agent' not in dataset.headers:
            raise ValueError("Колонка 'agent' відсутня у файлі.")
        if 'team_lead' not in dataset.headers:
            # не обов'язково для імпорту змін, але тримаємо перевірку для сумісності
            print("ПОПЕРЕДЖЕННЯ: колонка 'team_lead' відсутня. Пропускаю перевірку TL.")

        # Швидке визначення індексів колонок і збір унікальних імен агентів
        headers = list(getattr(dataset, 'headers', []) or [])
        if not headers:
            raise ValueError("Порожні заголовки файлу імпорту.")
        try:
            idx_agent = headers.index('agent')
        except ValueError:
            raise ValueError("Колонка 'agent' відсутня у файлі.")

        normalized_needed = set()
        for row in dataset:
            try:
                raw_name = row[idx_agent]
            except Exception:
                continue
            if not raw_name:
                continue
            normalized = self._normalize_name(raw_name)
            if normalized:
                normalized_needed.add(normalized)

        # Синхронізуємо з існуючими агентами
        matched_existing = 0
        qs = (Agent.objects.select_related('user')
              .only('id', 'user__first_name', 'user__last_name', 'user__username')
              .iterator(chunk_size=2000))
        for agent in qs:
            display_name = self._clean_display_name(agent.user.get_full_name() or agent.user.username)
            normalized = self._normalize_name(display_name)
            if normalized in normalized_needed and normalized not in self._agent_cache:
                self._agent_cache[normalized] = agent.pk
                matched_existing += 1

        if matched_existing:
            print(f"Знайдено {matched_existing} агентів у базі для імпорту змін.")

        missing = normalized_needed - set(self._agent_cache.keys())
        if missing:
            print(f"ПОПЕРЕДЖЕННЯ: {len(missing)} агентів з файлу відсутні у системі. Ці рядки буде пропущено при імпорті розкладу.")

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
        elif normalized and normalized not in self._agent_cache:
            print(f"ПОМИЛКА в рядку {row_number}: Не знайдено ID для агента '{name}' у кеші!")
            raise SkipRow(f"Agent '{name}' не існує")
        elif not normalized:
            raise SkipRow("Порожнє ім'я агента")

        # Нормалізуємо напрямок (direction) з урахуванням активності
        current_direction = row.get('direction')
        row['direction'] = self._normalize_direction(current_direction)

        # Гармонізуємо статуси, щоб приймати різні варіанти написання
        row['status'] = self._normalize_status(row.get('status'))

        # Перевіряємо тімліда лише для попередження (без автостворення)
        tl_display = row.get('team_lead')
        tl_normalized = self._normalize_name(tl_display)
        if tl_normalized:
            # Ледачо наповнюємо кеш існуючих користувачів без тримання всього QuerySet у пам'яті
            if not self._team_lead_cache:
                for user in User.objects.only('first_name', 'last_name', 'username').iterator(chunk_size=2000):
                    display_name = self._clean_display_name(user.get_full_name() or user.username)
                    self._team_lead_cache[self._normalize_name(display_name)] = user.pk
            if tl_normalized not in self._team_lead_cache:
                print(f"ПОПЕРЕДЖЕННЯ: тімліда '{tl_display}' не знайдено в системі. Імпорт розкладу не створює користувачів.")


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
    def _normalize_direction(direction_value):
        def _clean(value):
            return " ".join(str(value).strip().split()) if value else ""

        label = _clean(direction_value)
        normalized = label.casefold()

        if normalized in {"calls", "tickets", "chats"}:
            return normalized

        if normalized:
            mapped = UKRAINIAN_DIRECTION_MAP.get(normalized)
            if mapped:
                return mapped
            print(f"ПОПЕРЕДЖЕННЯ: невідомий напрям '{label}'. Використовую значення за замовчуванням '{DEFAULT_DIRECTION}'.")
            return DEFAULT_DIRECTION

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

    @staticmethod
    def _allocate_username(base: str, per_base_cache: dict) -> str:
        """Повертає вільний username з префіксом base, мінімізуючи запити і пам'ять.
        - per_base_cache: дикт base -> set усіх зайнятих імен (тільки для цього base)
        - запит у БД робиться лише перший раз для кожного base
        """
        if not base:
            base = 'user'
        base = base.strip()
        used = per_base_cache.get(base)
        if used is None:
            # отримуємо наявні юзернейми лише для цього префіксу
            existing = set(User.objects.filter(username__startswith=base)
                           .values_list('username', flat=True))
            used = existing
            per_base_cache[base] = used
        # підбір кандидата
        candidate = base
        suffix = 1
        while candidate in used:
            candidate = f"{base}_{suffix}"
            suffix += 1
        used.add(candidate)
        return candidate


class UsersFromScheduleResource(resources.ModelResource):
    """
    ОКРЕМИЙ імпорт користувачів (агенти та тімліди) з того ж файлу, що і розклад.
    - Створює відсутніх User/Agent
    - Створює відсутніх тімлідів (User з групою TL) і призначає їх агентам
    - НЕ створює жодних Shift
    """

    # Читаємо сирі значення з колонок, але не записуємо їх у модель напряму
    agent_display = fields.Field(column_name='agent', attribute='agent_display', widget=SimpleReadWidget(), readonly=True)
    team_lead_display = fields.Field(column_name='team_lead', attribute='team_lead_display', widget=SimpleReadWidget(), readonly=True)

    class Meta:
        model = Agent
        fields = ('agent_display', 'team_lead_display',)
        import_id_fields = ()
        skip_unchanged = True
        report_skipped = False

    def before_import(self, dataset, using_transactions=None, dry_run=False, **kwargs):
        headers = list(getattr(dataset, 'headers', []) or [])
        if not headers:
            raise ValueError("Порожні заголовки файлу імпорту.")
        if 'agent' not in headers:
            raise ValueError("Колонка 'agent' відсутня у файлі.")
        if 'team_lead' not in headers:
            raise ValueError("Колонка 'team_lead' відсутня у файлі.")

        idx_agent = headers.index('agent')
        idx_tl = headers.index('team_lead')

        # Підготовка нормалізованих імен без дублювання пам'яті
        normalized_to_original = {}
        normalized_agent_to_tl = {}
        normalized_team_leads = {}
        for row in dataset:
            try:
                raw_agent = row[idx_agent]
            except Exception:
                continue
            if not raw_agent:
                continue
            agent_norm = ShiftResource._normalize_name(raw_agent)
            if not agent_norm:
                continue
            normalized_to_original.setdefault(agent_norm, ShiftResource._clean_display_name(raw_agent))
            raw_tl = row[idx_tl] if idx_tl is not None else None
            tl_norm = ShiftResource._normalize_name(raw_tl)
            if tl_norm:
                normalized_agent_to_tl[agent_norm] = tl_norm
                normalized_team_leads.setdefault(tl_norm, ShiftResource._clean_display_name(raw_tl))

        print(f"[Імпорт користувачів] Агенти у файлі: {len(normalized_to_original)} | Тімліди: {len(normalized_team_leads)}")

        # Створюємо структури лише для потрібних баз юзернеймів
        per_base_cache = {}

        with transaction.atomic():
            # Підтверджуємо/створюємо тім-лідів
            tl_cache = {}
            if normalized_team_leads:
                # вже існуючі — ітеруємо без кешу пам'яті
                for user in User.objects.only('first_name', 'last_name', 'username').iterator(chunk_size=2000):
                    display = ShiftResource._clean_display_name(user.get_full_name() or user.username)
                    norm = ShiftResource._normalize_name(display)
                    if norm in normalized_team_leads and norm not in tl_cache:
                        tl_cache[norm] = user.pk

                # створюємо нових
                new_tl = set(normalized_team_leads.keys()) - set(tl_cache.keys())
                if new_tl:
                    tl_group, _ = Group.objects.get_or_create(name="TL")
                    ShiftResource._ensure_tl_group_permissions(tl_group)
                    for norm in new_tl:
                        original = normalized_team_leads[norm]
                        first, last = ShiftResource._split_name(original)
                        base = slugify(original, allow_unicode=True).replace('-', '_') or 'tl'
                        username = ShiftResource._allocate_username(base, per_base_cache)
                        user = User.objects.create(
                            username=username,
                            first_name=first,
                            last_name=last,
                            password=make_password('temp_password123'),
                            is_staff=True,
                            is_active=True,
                        )
                        user.groups.add(tl_group)
                        tl_cache[norm] = user.pk
                print(f"[Імпорт користувачів] Готово тімлідів: {len(tl_cache)}")

            # Підтверджуємо/створюємо агентів
            agent_cache = {}
            for agent in Agent.objects.select_related('user').only('id', 'user__first_name', 'user__last_name', 'user__username').iterator(chunk_size=2000):
                display = ShiftResource._clean_display_name(agent.user.get_full_name() or agent.user.username)
                agent_cache[ShiftResource._normalize_name(display)] = agent.pk

            new_agents = set(normalized_to_original.keys()) - set(agent_cache.keys())
            for norm in new_agents:
                original = normalized_to_original[norm]
                first, last = ShiftResource._split_name(original)
                base = slugify(original, allow_unicode=True).replace('-', '_') or 'agent'
                username = ShiftResource._allocate_username(base, per_base_cache)
                user = User.objects.create(
                    username=username,
                    first_name=first,
                    last_name=last,
                    password=make_password('temp_password123'),
                    is_staff=False,
                    is_active=True,
                )
                agent = Agent.objects.create(user=user)
                agent_cache[norm] = agent.pk

            if new_agents:
                print(f"[Імпорт користувачів] Створено нових агентів: {len(new_agents)}")

            # Призначаємо тім-лідів
            assigned = 0
            for agent_norm, tl_norm in normalized_agent_to_tl.items():
                agent_id = agent_cache.get(agent_norm)
                tl_id = tl_cache.get(tl_norm)
                if agent_id and tl_id:
                    updated = Agent.objects.filter(pk=agent_id).exclude(team_lead_id=tl_id).update(team_lead_id=tl_id)
                    if updated:
                        assigned += 1
            if assigned:
                print(f"[Імпорт користувачів] Оновлено тім-ліда для {assigned} агентів")

    def before_import_row(self, row, row_number=None, **kwargs):
        # Увесь запис зроблено в before_import. Рядки не записуємо принципово.
        raise SkipRow("UsersFromScheduleResource: пропуск рядка, запис не виконується")

    # Підтримка експорту (не обов'язково, але корисно):
    def dehydrate_agent_display(self, obj):
        return ShiftResource._clean_display_name(obj.user.get_full_name() or obj.user.username)

    def dehydrate_team_lead_display(self, obj):
        tl = getattr(obj, 'team_lead', None)
        if not tl:
            return ""
        return ShiftResource._clean_display_name(tl.get_full_name() or tl.username)

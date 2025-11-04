# core/resources.py
from import_export import resources, fields
from import_export.widgets import ForeignKeyWidget, Widget
from .models import Shift, ShiftExchange, Agent, ShiftStatus
from django.contrib.auth.models import User, Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.utils.text import slugify
from django.db import transaction
from import_export.instance_loaders import CachedInstanceLoader
# Видалено імпорти сигналів та аудиту - вони більше не потрібні
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
        column_name='agent',  # Назва колонки в CSV
        attribute='agent',  # Атрибут моделі Shift
        widget=ForeignKeyWidget(Agent, 'pk')  # Шукаємо Agent за ID (pk)
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

        # --- ОПТИМІЗАЦІЯ ---

        # 1. Повністю вимикає сигнали (pre_save, post_save).
        # Це автоматично вимкне і simple-history, і ваш core.audit.
        skip_signals = True

        # 2. Вимикає перевірку "чи змінився рядок".
        # Це прибирає один SELECT-запит НА КОЖЕН рядок у файлі.
        skip_unchanged = False

        # --- ---------------- ---

        report_skipped = False
        use_bulk = True
        batch_size = 1000
        skip_diff = True
        clean_model_instances = False
        use_transactions = True
        instance_loader_class = CachedInstanceLoader

    def before_import(self, dataset, using_transactions=None, dry_run=False, **kwargs):
        """
        (ЦЕ ВАШ ОРИГІНАЛЬНИЙ МЕТОД, ВІН ПРАЦЮВАТИМЕ)
        ПІСЛЯ РОЗДІЛЕННЯ ПРОЦЕСІВ: імпорт розкладу НЕ створює користувачів/тімлідів.
        Тут лише будуємо кеш агентів, що ВЖЕ існують, і попереджаємо про відсутніх.
        """
        self._agent_cache.clear()
        self._team_lead_cache.clear()
        self._agent_team_lead_map = {}
        # Яку колонку використовуємо як ідентифікатор агента
        self._agent_header = 'agent'
        self._agent_header_is_id_alias = False
        # Набори для швидких перевірок існування агентів
        self._agent_ids_needed = set()
        self._agent_ids_existing = set()
        self._reported_missing_agent_ids = set()

        # Перевіряємо, що файл має колонку агента: 'agent' або допускаємо 'id' як Agent ID alias
        headers = list(getattr(dataset, 'headers', []) or [])
        if not headers:
            raise ValueError("Порожні заголовки файлу імпорту.")
        if 'agent' in headers:
            self._agent_header = 'agent'
        elif 'id' in headers:
            # Трактуємо 'id' як ідентифікатор агента у файлі, а id зміни буде автогенерований
            self._agent_header = 'id'
            self._agent_header_is_id_alias = True
        else:
            raise ValueError("Колонка 'agent' відсутня у файлі (або використайте колонку 'id' з ID агента).")
        if 'team_lead' not in headers:
            # не обов'язково для імпорту змін, але тримаємо перевірку для сумісності
            print("ПОПЕРЕДЖЕННЯ: колонка 'team_lead' відсутня. Пропускаю перевірку TL.")
        # Якщо колонка агента — це ім'я ('agent'), будуємо кеш імен → ID
        if self._agent_header == 'agent':
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
                print(
                    f"ПОПЕРЕДЖЕННЯ: {len(missing)} агентів з файлу відсутні у системі. Ці рядки буде пропущено при імпорті розкладу.")
        else:
            # Колонка агента — це alias 'id' з Agent ID: зберемо унікальні ID та підтвердимо їх існування одним запитом
            try:
                idx_id = headers.index('id')
            except ValueError:
                idx_id = None
            if idx_id is not None:
                needed = set()
                for row in dataset:
                    try:
                        raw = row[idx_id]
                    except Exception:
                        continue
                    if raw is None or raw == "":
                        continue
                    try:
                        val = int(str(raw).strip())
                        if val > 0:
                            needed.add(val)
                    except Exception:
                        # некоректний ID — ігноруємо тут, відфільтруємо в before_import_row
                        continue
                self._agent_ids_needed = needed
                if needed:
                    existing = set(Agent.objects.filter(id__in=needed).values_list('id', flat=True))
                    self._agent_ids_existing = existing
                    missing_ids = needed - existing
                    if missing_ids:
                        print(f"ПОПЕРЕДЖЕННЯ: {len(missing_ids)} Agent ID відсутні у системі (приклад: {sorted(list(missing_ids))[:5]}). Такі рядки буде пропущено.")

    def before_import_row(self, row, row_number=None, **kwargs):
        """
        Підставляє ID агента з кешу в колонку 'agent' для ForeignKeyWidget.
        """
        # Читаємо значення агента (ім'я або ID). Допускаємо alias 'id' як Agent ID.
        # Використовуємо self._agent_header, який тепер коректно встановлюється у before_import
        raw_agent = row.get('agent') if self._agent_header == 'agent' else row.get('id')

        # 1) Якщо файл уже містить числовий ID агента — використовуємо його як є
        if raw_agent is not None:
            try:
                # Дозволяємо як int, так і рядок з числом
                agent_id = int(str(raw_agent).strip())
                if agent_id > 0:
                    row['agent'] = agent_id
                    # Продовжуємо нормалізацію інших полів нижче
                else:
                    raise ValueError
            except (ValueError, TypeError):
                # Не число — обробляємо як ім'я і мапимо на кешований ID
                name = raw_agent
                normalized = self._normalize_name(name)
                if normalized and normalized in self._agent_cache:
                    row['agent'] = self._agent_cache[normalized]
                elif normalized and normalized not in self._agent_cache:
                    print(f"ПОМИЛКА в рядку {row_number}: Не знайдено ID для агента '{name}' у кеші!")
                    row['_skip_row_reason'] = f"Agent '{name}' не існує"
                    return
                else:
                    row['_skip_row_reason'] = "Порожнє ім'я агента"
                    return
        else:
            row['_skip_row_reason'] = "Відсутня колонка 'agent'"
            return

        # Якщо використовували alias 'id' як агент — приберемо 'id', щоб не трактувати як ID зміни
        if self._agent_header_is_id_alias and 'id' in row:
            try:
                del row['id']
            except Exception:
                pass

        # Якщо у рядку agent — числовий ID, перевіримо, що Agent існує без запиту на рядок
        try:
            candidate_id = int(row.get('agent'))
            if candidate_id > 0 and self._agent_ids_existing:
                if candidate_id not in self._agent_ids_existing:
                    if candidate_id not in self._reported_missing_agent_ids:
                        print(f"ПОМИЛКА: Agent ID='{candidate_id}' не існує у системі (рядок {row_number}). Рядок буде пропущено.")
                        self._reported_missing_agent_ids.add(candidate_id)
                    row['_skip_row_reason'] = f"Agent ID '{candidate_id}' не існує"
                    return
        except Exception:
            pass

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
                print(
                    f"ПОПЕРЕДЖЕННЯ: тімліда '{tl_display}' не знайдено в системі. Імпорт розкладу не створює користувачів.")

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
            print(
                f"ПОПЕРЕДЖЕННЯ: невідомий напрям '{label}'. Використовую значення за замовчуванням '{DEFAULT_DIRECTION}'.")
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

    def skip_row(self, instance, original, row, import_validation_errors=None):
        """Пропускає рядок, якщо у before_import_row він був позначений як такий, що підлягає пропуску."""
        reason = row.get('_skip_row_reason')
        if reason:
            # За потреби можна зберегти reason у лог
            return True
        # Повертаємо стандартну логіку пропуску (наприклад, skip_unchanged=False тепер обробляється тут)
        return super().skip_row(instance, original, row, import_validation_errors=import_validation_errors)

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
    # Читаємо сирі значення з колонок, але не записуємо їх у модель напряму
    agent_display = fields.Field(column_name='agent', attribute='agent_display', widget=SimpleReadWidget(),
                                 readonly=True)
    team_lead_display = fields.Field(column_name='team_lead', attribute='team_lead_display', widget=SimpleReadWidget(),
                                     readonly=True)

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

        # Необов'язкові ідентифікатори з файлу
        # Підтримуємо кілька alias-ів для ідентифікаторів
        def _idx(*names):
            for n in names:
                try:
                    return headers.index(n)
                except ValueError:
                    continue
            return None

        idx_agent_id = _idx('agent_id', 'id')  # 'id' як alias для Agent.id
        idx_agent_user_id = _idx('agent_user_id', 'user_id')
        idx_tl_user_id = _idx('team_lead_id', 'team_lead_user_id', 'tl_user_id', 'tl_id')

        # Підготовка нормалізованих імен без дублювання пам'яті
        normalized_to_original = {}
        normalized_agent_to_tl = {}
        normalized_team_leads = {}
        # Мапи бажаних ідентифікаторів з файлу (за нормалізованим ім'ям)
        desired_agent_id_by_norm = {}
        desired_agent_user_id_by_norm = {}
        desired_tl_user_id_by_norm = {}
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

            # Зчитуємо бажані ID, якщо є відповідні колонки
            def _to_int(val):
                try:
                    v = int(str(val).strip())
                    return v if v > 0 else None
                except Exception:
                    return None

            if idx_agent_id is not None:
                desired = _to_int(row[idx_agent_id])
                if desired:
                    desired_agent_id_by_norm.setdefault(agent_norm, desired)
            if idx_agent_user_id is not None:
                desired = _to_int(row[idx_agent_user_id])
                if desired:
                    desired_agent_user_id_by_norm.setdefault(agent_norm, desired)
            if tl_norm and idx_tl_user_id is not None:
                desired = _to_int(row[idx_tl_user_id])
                if desired:
                    desired_tl_user_id_by_norm.setdefault(tl_norm, desired)

        print(
            f"[Імпорт користувачів] Агенти у файлі: {len(normalized_to_original)} | Тімліди: {len(normalized_team_leads)}")

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
                        desired_uid = desired_tl_user_id_by_norm.get(norm)
                        # Якщо у файлі задано бажаний ID користувача — спробуємо його використати
                        create_kwargs = dict(
                            username=username,
                            first_name=first,
                            last_name=last,
                            is_staff=True,
                            is_active=True,
                        )
                        if desired_uid and not User.objects.filter(pk=desired_uid).exists():
                            create_kwargs['id'] = desired_uid
                        user = User.objects.create(**create_kwargs)
                        user.set_unusable_password()
                        user.save(update_fields=['password'])

                        user.groups.add(tl_group)
                        tl_cache[norm] = user.pk
                print(f"[Імпорт користувачів] Готово тімлідів: {len(tl_cache)}")

            # Підтверджуємо/створюємо агентів
            agent_cache = {}
            for agent in Agent.objects.select_related('user').only('id', 'user__first_name', 'user__last_name',
                                                                   'user__username').iterator(chunk_size=2000):
                display = ShiftResource._clean_display_name(agent.user.get_full_name() or agent.user.username)
                agent_cache[ShiftResource._normalize_name(display)] = agent.pk

            new_agents = set(normalized_to_original.keys()) - set(agent_cache.keys())
            for norm in new_agents:
                original = normalized_to_original[norm]
                first, last = ShiftResource._split_name(original)
                base = slugify(original, allow_unicode=True).replace('-', '_') or 'agent'
                username = ShiftResource._allocate_username(base, per_base_cache)
                desired_user_id = desired_agent_user_id_by_norm.get(norm)
                user_create_kwargs = dict(
                    username=username,
                    first_name=first,
                    last_name=last,
                    is_staff=False,
                    is_active=True,
                )
                if desired_user_id and not User.objects.filter(pk=desired_user_id).exists():
                    user_create_kwargs['id'] = desired_user_id
                user = User.objects.create(**user_create_kwargs)
                user.set_unusable_password()
                user.save(update_fields=['password'])
                # Можемо також встановити бажаний ID агента, якщо задано
                desired_agent_id = desired_agent_id_by_norm.get(norm)
                if desired_agent_id and not Agent.objects.filter(pk=desired_agent_id).exists():
                    agent = Agent.objects.create(id=desired_agent_id, user=user)
                else:
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
        # Позначаємо рядок як такий, що треба пропустити.
        row['_skip_row_reason'] = "UsersFromScheduleResource: пропуск рядка, запис не виконується"

    def skip_row(self, instance, original, row, import_validation_errors=None):
        # Пропускаємо усі рядки для цього ресурсу
        return True

    # Підтримка експорту (не обов'язково, але корисно):
    def dehydrate_agent_display(self, obj):
        return ShiftResource._clean_display_name(obj.user.get_full_name() or obj.user.username)

    def dehydrate_team_lead_display(self, obj):
        tl = getattr(obj, 'team_lead', None)
        if not tl:
            return ""
        return ShiftResource._clean_display_name(tl.get_full_name() or tl.username)

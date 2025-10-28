# core/services.py
from typing import Tuple
from .models import Shift

def can_swap(sh1: Shift, sh2: Shift, user) -> Tuple[bool, str]:
    # Агент може міняти лише якщо володіє хоча б однією із змін
    if user.groups.filter(name="Agent").exists():
        owns = (sh1.agent.user_id == user.id) or (sh2.agent.user_id == user.id)
        if not owns:
            return False, "Можна міняти лише власні зміни."

    # Скіли (перетин) або однаковий напрям
    s1 = set(sh1.agent.skills or [])
    s2 = set(sh2.agent.skills or [])
    if s1.isdisjoint(s2) and sh1.direction != sh2.direction:
        return False, "Скіли не збігаються та різні напрямки."

    # Заборонені статуси для обміну
    non_work = {"vacation", "sick", "day_off"}
    if sh1.status in non_work or sh2.status in non_work:
        return False, "Не можна міняти на відпустку/лікарняний/вихідний."

    # Перетин часу? Дозволено, але попереджай у майбутньому
    return True, ""

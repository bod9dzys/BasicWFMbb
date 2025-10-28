# core/views.py
import json
from datetime import timedelta, datetime
from django.utils import timezone
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required, permission_required

from .models import Shift, ShiftExchange, Agent, ShiftStatus
from .filters import ShiftFilter
from .forms import ExchangeCreateForm
from django.contrib import messages
from .services import can_swap
from calendar import monthrange


def _monday(dt):
    return dt - timedelta(days=dt.weekday())

def _weeks_of_year(year: int, tz):
    # Знаходимо перший понеділок року
    d = datetime(year, 1, 1, tzinfo=tz)
    start = _monday(d)
    # Формуємо всі понеділки до першого понеділка наступного року
    end = _monday(datetime(year + 1, 1, 1, tzinfo=tz))
    weeks = []
    i = 1
    while start < end:
        weeks.append({
            "index": i,
            "start": start,
            "end": start + timedelta(days=6),
            "param": start.date().isoformat(),  # для ?week=
            "label": f"{i:02d} тиждень · {start.date().strftime('%d.%m')}–{(start + timedelta(days=6)).date().strftime('%d.%m')}",
        })
        start += timedelta(days=7)
        i += 1
    return weeks

def _build_agent_shift_map():
    tz = timezone.get_current_timezone()
    shifts = (
        Shift.objects
        .select_related("agent", "agent__user")
        .order_by("-start")
    )
    data = {}
    for shift in shifts:
        start_local = timezone.localtime(shift.start, tz)
        end_local = timezone.localtime(shift.end, tz)
        direction = shift.get_direction_display()
        status = shift.get_status_display()
        label_parts = [
            f"{start_local:%d.%m %H:%M}–{end_local:%H:%M}",
            direction,
        ]
        if shift.status != ShiftStatus.WORK:
            label_parts.append(status)
        data.setdefault(str(shift.agent_id), []).append({
            "id": shift.pk,
            "label": " · ".join(label_parts),
        })
    return data

@login_required
def schedule_week(request):
    # 1) Визначаємо базову дату тижня з ?week=YYYY-MM-DD або беремо сьогодні
    try:
        q_week = request.GET.get("week")
        base = datetime.fromisoformat(q_week).date() if q_week else timezone.localdate()
    except Exception:
        base = timezone.localdate()

    # 2) Рахуємо межі тижня [понеділок; понеділок+7)
    tz = timezone.get_current_timezone()
    week_start = _monday(datetime.combine(base, datetime.min.time(), tzinfo=tz))
    week_end = week_start + timedelta(days=7)

    # 3) Базовий queryset змін за тиждень
    qs = (
        Shift.objects
        .select_related("agent", "agent__user")
        .filter(start__gte=week_start, end__lt=week_end)

    )

    # 4) Підключаємо фільтри (TL, агент, статуси тощо)
    f = ShiftFilter(request.GET, queryset=qs)

    # 5) Список дат тижня для заголовків колонок
    days = [week_start + timedelta(days=i) for i in range(7)]

    # 6) Півот: рядок = агент, колонки 0..6 = список змін у той день
    #    Спочатку зберемо впорядкований список агентів, яких торкається вибірка
    agents_order = []
    seen = set()
    for s in f.qs.order_by("agent__user__last_name", "agent__user__first_name", "start"):
        if s.agent_id not in seen:
            seen.add(s.agent_id)
            agents_order.append(s.agent)

    # 7) Табличні дані: [{ "agent": Agent, "cells": [list[Shift], ... x7] }, ...]
    table = []
    # Готуємо порожні клітинки
    empty_row = {i: [] for i in range(7)}
    # Індекс зміни в межах тижня
    def day_idx(dttm):
        return (dttm.date() - week_start.date()).days

    # Заповнюємо клітинки
    grid = {a.id: {i: [] for i in range(7)} for a in agents_order}
    for s in f.qs.order_by("start"):
        idx = day_idx(s.start)
        if 0 <= idx < 7:
            grid[s.agent_id][idx].append(s)

    for a in agents_order:
        cells = [grid[a.id][i] for i in range(7)]
        table.append({"agent": a, "cells": cells})

    # 8) Посилання “попередній/наступний тиждень”
    prev_week = (week_start - timedelta(days=7)).date().isoformat()
    next_week = (week_start + timedelta(days=7)).date().isoformat()

    year = week_start.year
    weeks = _weeks_of_year(year, tz)

    # знайдемо активний тиждень (щоб підсвітити у списку)
    active_param = week_start.date().isoformat()
    active_idx = 0
    for idx, w in enumerate(weeks):
        if w["param"] == active_param:
            active_idx = idx
            break

    ctx = {
        "filter": f,
        "week_start": week_start,
        "days": days,
        "table": table,
        "prev_week": prev_week,
        "next_week": next_week,
        "weeks": weeks,  # ← нове
        "active_week_idx": active_idx  # ← нове
    }
    return render(request, "schedule_week.html", ctx)



@login_required
@permission_required("core.add_shiftexchange", raise_exception=True)
def exchange_create(request):
    form = ExchangeCreateForm(request.user, data=request.POST or None)
    if request.method == "POST" and form.is_valid():
        sh1 = form.cleaned_data["from_shift"]
        sh2 = form.cleaned_data["to_shift"]
        ok, msg = can_swap(sh1, sh2, request.user)
        if not ok:
            messages.error(request, msg)
        else:
            ex = ShiftExchange.objects.create(
                from_shift=sh1, to_shift=sh2, requested_by=request.user, comment=form.cleaned_data.get("comment", "")
            )
            messages.success(request, "Запит на обмін створено і очікує рішення.")
            return redirect("exchange_create")

    agent_shifts = _build_agent_shift_map()
    return render(request, "exchange_form.html", {"form": form, "agent_shifts_json": json.dumps(agent_shifts, ensure_ascii=False)})


@login_required
@permission_required("core.approve_exchange", raise_exception=True)
def exchange_approve(request, pk: int):
    ex = get_object_or_404(ShiftExchange, pk=pk)
    ex.approved = True
    ex.save()
    messages.success(request, "Обмін схвалено.")
    # тут пізніше зробимо реальну заміну агентів/змін, зараз лише статус
    return redirect("schedule_week")


@login_required
@permission_required("core.approve_exchange", raise_exception=True)
def exchange_reject(request, pk: int):
    ex = get_object_or_404(ShiftExchange, pk=pk)
    ex.approved = False
    ex.save()
    messages.warning(request, "Обмін відхилено.")
    return redirect("schedule_week")

@login_required # Або інша перевірка доступу
def get_agent_shifts_for_month(request):
    agent_id = request.GET.get('agent_id')
    shifts_data = []
    error_message = None # Для збереження тексту помилки

    if not agent_id:
        error_message = "Не надано ID агента."
    else:
        try:
            # Перевіряємо, чи agent_id є числом
            agent_id_int = int(agent_id)
            agent = Agent.objects.get(pk=agent_id_int)
            now = timezone.localdate()
            first_day = now.replace(day=1)
            last_day_num = monthrange(now.year, now.month)[1]
            last_day = now.replace(day=last_day_num)

            start_dt = timezone.make_aware(datetime.combine(first_day, datetime.min.time()))
            # Кінець місяця - це початок наступного дня після останнього дня місяця
            end_dt_exclusive = timezone.make_aware(datetime.combine(last_day + timedelta(days=1), datetime.min.time()))

            shifts = Shift.objects.filter(
                agent=agent,
                # Зміни, що починаються В межах місяця
                start__gte=start_dt,
                start__lt=end_dt_exclusive, # Використовуємо < для кінця місяця
                status__in=['work', 'training', 'meeting', 'onboard']
            ).select_related('agent__user').order_by('start')

            # Переконуємось, що str(shift) працює
            shifts_data = [{'id': shift.id, 'text': str(shift) if shift else 'Невдалося відобразити зміну'} for shift in shifts]

        except Agent.DoesNotExist:
            error_message = f"Агент з ID {agent_id} не знайдений."
        except ValueError:
             error_message = f"Невірний ID агента: {agent_id}."
        except Exception as e:
             # Логуємо непередбачену помилку на сервері
             print(f"Помилка у get_agent_shifts_for_month: {e}")
             error_message = "Внутрішня помилка сервера при отриманні змін."

    # Завжди повертаємо JSON, включаючи помилку, якщо вона є
    return JsonResponse({'shifts': shifts_data, 'error': error_message})
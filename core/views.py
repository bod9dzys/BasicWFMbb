# core/views.py
from datetime import timedelta, datetime
from django.utils import timezone
from django.shortcuts import render, redirect
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required, permission_required
from django.db import transaction

from django.http import JsonResponse
from .models import Shift, ShiftExchange, Agent, ShiftStatus
from .filters import ShiftFilter
from .forms import ExchangeCreateForm, SignUpForm
from django.contrib import messages
from .services import can_swap

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


def _format_shift_label(shift: Shift, agent) -> str:
    """Build human-readable label with start date/time and end time."""
    tz = timezone.get_current_timezone()
    start_local = timezone.localtime(shift.start, tz)
    end_local = timezone.localtime(shift.end, tz)

    end_part = f"{end_local:%H:%M}"
    return f"{agent} · {start_local:%d.%m %H:%M}–{end_part}"


def signup(request):
    if request.user.is_authenticated:
        return redirect("schedule_week")

    form = SignUpForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user)
        messages.success(request, "Обліковий запис створено. Ласкаво просимо!")
        return redirect("schedule_week")

    return render(request, "registration/signup.html", {"form": form})


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
            comment = form.cleaned_data.get("comment", "")
            agent_a_before = sh1.agent
            agent_b_before = sh2.agent
            shift_a_label = _format_shift_label(sh1, agent_a_before)
            shift_b_label = _format_shift_label(sh2, agent_b_before)
            try:
                with transaction.atomic():
                    ShiftExchange.objects.create(
                        from_shift=sh1,
                        to_shift=sh2,
                        requested_by=request.user,
                        comment=comment,
                        approved=True,
                    )
                    sh1.agent, sh2.agent = agent_b_before, agent_a_before
                    sh1.save(update_fields=["agent"])
                    sh2.save(update_fields=["agent"])
            except Exception as exc:
                messages.error(
                    request,
                    f"Не вдалося виконати обмін. Помилка: {exc}",
                )
            else:
                messages.success(
                    request,
                    f"Обмін виконано: {shift_a_label} ⇄ {shift_b_label}.",
                )
                form = ExchangeCreateForm(request.user)

    return render(request, "exchange_form.html", {"form": form})

@login_required  # Або інша перевірка доступу
def get_agent_shifts_for_month(request):
    agent_id = request.GET.get("agent_id")
    shifts_data = []
    error_message = None

    if not agent_id:
        error_message = "Не надано ID агента."
    else:
        try:
            agent_id_int = int(agent_id)
            Agent.objects.get(pk=agent_id_int)  # Переконуємось, що агент існує

            tz = timezone.get_current_timezone()
            now = timezone.now()
            horizon = now + timedelta(days=60)

            shifts = (
                Shift.objects
                .select_related("agent", "agent__user")
                .filter(agent_id=agent_id_int, end__gte=now - timedelta(days=1), start__lte=horizon)
                .order_by("start")
            )

            for shift in shifts:
                start_local = timezone.localtime(shift.start, tz)
                end_local = timezone.localtime(shift.end, tz)
                label_parts = [
                    f"{start_local:%d.%m %H:%M}–{end_local:%H:%M}",
                    shift.get_direction_display(),
                ]
                if shift.status != ShiftStatus.WORK:
                    label_parts.append(shift.get_status_display())
                if shift.activity:
                    label_parts.append(shift.activity)

                shifts_data.append({
                    "id": shift.id,
                    "text": " · ".join(label_parts),
                })

        except Agent.DoesNotExist:
            error_message = f"Агент з ID {agent_id} не знайдений."
        except ValueError:
            error_message = f"Невірний ID агента: {agent_id}."
        except Exception as e:
            print(f"Помилка у get_agent_shifts_for_month: {e}")
            error_message = "Внутрішня помилка сервера при отриманні змін."

    return JsonResponse({"shifts": shifts_data, "error": error_message})

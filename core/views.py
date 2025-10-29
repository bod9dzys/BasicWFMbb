# core/views.py
from datetime import timedelta, datetime
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional
from io import BytesIO
from django.utils import timezone
from django.shortcuts import render, redirect
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required, permission_required
from django.db import transaction

from django.http import JsonResponse, HttpResponse
from .models import Shift, ShiftExchange, Agent, ShiftStatus, Direction
from .filters import ShiftFilter
from .forms import ExchangeCreateForm, SignUpForm, ToolsHoursForm, DashboardFilterForm
from django.contrib import messages
from .services import can_swap

NON_WORKING_STATUSES = {
    ShiftStatus.VACATION,
    ShiftStatus.SICK,
    ShiftStatus.DAY_OFF,
}
DIRECTION_LABELS = dict(Direction.choices)
VALID_DIRECTIONS = set(DIRECTION_LABELS.keys())


@dataclass(slots=True)
class ShiftCard:
    status: str
    status_label: str
    direction: str
    direction_label: str
    time_label: str
    activity: Optional[str]
    comment: Optional[str]


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
def logout_view(request):
    logout(request)
    messages.info(request, "Ви вийшли з акаунта.")
    return redirect("login")


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
    qs = Shift.objects.filter(start__gte=week_start, start__lt=week_end)

    # 4) Підключаємо фільтри (TL, агент, статуси тощо)
    f = ShiftFilter(request.GET, queryset=qs)

    # 5) Список дат тижня для заголовків колонок
    days = [week_start + timedelta(days=i) for i in range(7)]

    # 6) Готуємо компактне подання змін у розрізі агентів та днів тижня
    filtered_qs = f.qs
    raw_shifts = list(
        filtered_qs.values(
            "agent_id",
            "start",
            "end",
            "status",
            "direction",
            "activity",
            "comment",
        ).order_by("agent_id", "start")
    )

    agent_ids = {row["agent_id"] for row in raw_shifts}
    agents = list(
        Agent.objects.filter(id__in=agent_ids)
        .select_related("user", "team_lead")
        .order_by("user__last_name", "user__first_name", "user__username")
    )

    shifts_by_agent = defaultdict(list)
    for row in raw_shifts:
        shifts_by_agent[row["agent_id"]].append(row)

    status_labels = dict(ShiftStatus.choices)
    direction_labels = dict(Direction.choices)

    table = []
    for agent in agents:
        cells = [[] for _ in range(7)]
        for entry in shifts_by_agent.get(agent.id, ()):
            local_start = timezone.localtime(entry["start"], tz)
            idx = (local_start.date() - week_start.date()).days
            if 0 <= idx < 7:
                local_end = timezone.localtime(entry["end"], tz)
                cells[idx].append(
                    ShiftCard(
                        status=entry["status"],
                        status_label=status_labels.get(entry["status"], entry["status"]),
                        direction=entry["direction"],
                        direction_label=direction_labels.get(
                            entry["direction"], entry["direction"]
                        ),
                        time_label=f"{local_start:%H:%M}–{local_end:%H:%M}",
                        activity=entry["activity"] or None,
                        comment=entry["comment"] or None,
                    )
                )
        table.append({"agent": agent, "cells": cells})

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
        "weeks": weeks,
        "active_week_idx": active_idx,
        "week_param": active_param,
    }
    return render(request, "schedule_week.html", ctx)


def _prepare_agent_entries(shifts_qs, tz, window=None):
    entries = {}
    for shift in shifts_qs:
        agent_id = shift.agent_id
        info = entries.setdefault(
            agent_id,
            {
                "agent": shift.agent,
                "display_name": shift.agent.user.get_full_name() or shift.agent.user.username,
                "shifts": [],
            },
        )
        if window:
            overlap_start = max(shift.start, window[0])
            overlap_end = min(shift.end, window[1])
        else:
            overlap_start = shift.start
            overlap_end = shift.end

        info["shifts"].append(
            {
                "start": timezone.localtime(shift.start, tz),
                "end": timezone.localtime(shift.end, tz),
                "overlap_start": timezone.localtime(overlap_start, tz),
                "overlap_end": timezone.localtime(overlap_end, tz),
                "status_key": shift.status,
                "status_label": shift.get_status_display(),
                "direction": shift.get_direction_display(),
            }
        )

    ordered = list(entries.values())
    ordered.sort(
        key=lambda item: (
            item["agent"].user.last_name or "",
            item["agent"].user.first_name or "",
            item["agent"].user.username,
        )
    )
    for item in ordered:
        item["shifts"].sort(key=lambda sh: sh["overlap_start"])
    return ordered


def _direction_counts(qs):
    buckets = {key: set() for key in VALID_DIRECTIONS}
    for direction, agent_id in qs.values_list("direction", "agent_id"):
        if direction not in buckets:
            buckets[direction] = set()
        buckets[direction].add(agent_id)
    summary = [
        {
            "direction": key,
            "label": DIRECTION_LABELS.get(key, key),
            "count": len(agent_ids),
        }
        for key, agent_ids in buckets.items()
    ]
    summary.sort(key=lambda item: item["label"])
    return summary


@login_required
def dashboard(request):
    tz = timezone.get_current_timezone()
    now = timezone.now()

    form = DashboardFilterForm(request.GET or None)
    direction_filter = None
    if form.is_valid():
        direction_filter = form.cleaned_data.get("direction") or None
    elif form.is_bound:
        raw_direction = form.data.get("direction") or ""
        if raw_direction in VALID_DIRECTIONS:
            direction_filter = raw_direction

    current_base_qs = (
        Shift.objects.select_related("agent", "agent__user")
        .filter(start__lte=now, end__gt=now)
        .exclude(status__in=NON_WORKING_STATUSES)
        .order_by("agent__user__last_name", "agent__user__first_name", "start")
    )
    current_direction_counts = _direction_counts(current_base_qs)
    if direction_filter:
        current_qs = current_base_qs.filter(direction=direction_filter)
    else:
        current_qs = current_base_qs

    current_agents = _prepare_agent_entries(current_qs, tz)
    current_count = len(current_agents)
    current_direction_total = current_count

    window_summary = None
    window_agents = []
    window_direction_counts = []
    window_direction_total = 0

    if form.is_valid():
        day = form.cleaned_data["day"]
        time_start = form.cleaned_data["time_start"]
        time_end = form.cleaned_data["time_end"]

        window_start = timezone.make_aware(
            datetime.combine(day, time_start),
            tz,
        )
        window_end = timezone.make_aware(
            datetime.combine(day, time_end),
            tz,
        )

        window_base_qs = (
            Shift.objects.select_related("agent", "agent__user")
            .filter(start__lt=window_end, end__gt=window_start)
            .exclude(status__in=NON_WORKING_STATUSES)
            .order_by("agent__user__last_name", "agent__user__first_name", "start")
        )

        window_direction_counts = _direction_counts(window_base_qs)

        if direction_filter:
            window_qs = window_base_qs.filter(direction=direction_filter)
        else:
            window_qs = window_base_qs

        window_agents = _prepare_agent_entries(window_qs, tz, window=(window_start, window_end))
        window_summary = {
            "start": timezone.localtime(window_start, tz),
            "end": timezone.localtime(window_end, tz),
            "count": len(window_agents),
        }
        window_direction_total = window_summary["count"]

    return render(
        request,
        "dashboard.html",
        {
            "form": form,
            "current_count": current_count,
            "current_agents": current_agents,
            "now_local": timezone.localtime(now, tz),
            "window_summary": window_summary,
            "window_agents": window_agents,
            "current_direction_counts": current_direction_counts,
            "window_direction_counts": window_direction_counts,
            "current_direction_total": current_direction_total,
            "window_direction_total": window_direction_total,
            "selected_direction": direction_filter,
            "selected_direction_label": DIRECTION_LABELS.get(direction_filter) if direction_filter else None,
        },
    )



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


@login_required
def tools(request):
    form = ToolsHoursForm(request.GET or None, user=request.user)
    summary = None
    shift_rows = []
    agent_summaries = []

    if form.is_valid():
        agent = form.cleaned_data.get("agent")
        team_lead = form.cleaned_data.get("team_lead")
        start = form.cleaned_data["start"]
        end = form.cleaned_data["end"]
        selected_directions = form.cleaned_data.get("direction") or []
        tz = timezone.get_current_timezone()

        if timezone.is_naive(start):
            start = timezone.make_aware(start, tz)
        if timezone.is_naive(end):
            end = timezone.make_aware(end, tz)

        agent_queryset = Agent.objects.select_related("user").filter(active=True)
        if team_lead:
            agent_queryset = agent_queryset.filter(team_lead=team_lead)
        if agent:
            agent_queryset = agent_queryset.filter(pk=agent.pk)

        agent_list = list(agent_queryset.order_by("user__last_name", "user__first_name"))
        total_seconds_all = 0
        total_shifts_all = 0
        processed_agents = []

        EXCLUDED_STATUSES = {
            ShiftStatus.VACATION,
            ShiftStatus.SICK,
            ShiftStatus.DAY_OFF,
        }

        for ag in agent_list:
            shifts_qs = (
                Shift.objects.select_related("agent", "agent__user")
                .filter(agent=ag, start__lt=end, end__gt=start)
            )
            if selected_directions:
                shifts_qs = shifts_qs.filter(direction__in=selected_directions)
            shifts = list(shifts_qs.order_by("start"))

            if selected_directions and not shifts:
                continue

            agent_seconds = 0
            agent_shift_rows = []
            counted_shifts = 0

            for shift in shifts:
                overlap_start = max(shift.start, start)
                overlap_end = min(shift.end, end)
                if overlap_start >= overlap_end:
                    continue

                seconds = (overlap_end - overlap_start).total_seconds()
                counted = shift.status not in EXCLUDED_STATUSES

                if counted:
                    agent_seconds += seconds
                    counted_shifts += 1

                agent_shift_rows.append({
                    "id": shift.id,
                    "direction": shift.get_direction_display(),
                    "status": shift.get_status_display(),
                    "activity": shift.activity,
                    "start": timezone.localtime(overlap_start, tz),
                    "end": timezone.localtime(overlap_end, tz),
                    "full_start": timezone.localtime(shift.start, tz),
                    "full_end": timezone.localtime(shift.end, tz),
                    "duration_hours": round(seconds / 3600, 2),
                    "counted": counted,
                })

            total_seconds_all += agent_seconds
            total_shifts_all += counted_shifts

            processed_agents.append(ag)
            agent_summaries.append({
                "agent": ag,
                "total_hours": round(agent_seconds / 3600, 2),
                "total_shifts": counted_shifts,
                "display_name": ag.user.get_full_name() or ag.user.username,
            })

            if len(processed_agents) == 1:
                shift_rows = agent_shift_rows

        single_agent = processed_agents[0] if len(processed_agents) == 1 else None

        summary = {
            "team_lead": team_lead,
            "agent": single_agent,
            "start": timezone.localtime(start, tz),
            "end": timezone.localtime(end, tz),
            "total_hours": round(total_seconds_all / 3600, 2),
            "total_shifts": total_shifts_all,
            "total_agents": len(processed_agents),
            "directions": [DIRECTION_LABELS.get(code, code) for code in selected_directions],
        }

        shift_rows.sort(key=lambda row: row["start"])

        if summary["total_agents"] > 1 and agent_summaries:
            agent_summaries.sort(key=lambda item: (-item["total_hours"], item["display_name"]))

        if request.GET.get("export") == "1":
            try:
                from openpyxl import Workbook
            except ImportError:
                messages.error(request, "Експорт неможливий: пакет openpyxl не встановлено.")
            else:
                workbook = Workbook()
                sheet = workbook.active
                sheet.title = "Години"
                sheet.append(["Агент", "Відпрацьовані години", "Період"])
                period_label = f"{summary['start']:%d.%m.%Y %H:%M} – {summary['end']:%d.%m.%Y %H:%M}"
                for item in agent_summaries:
                    sheet.append([item["display_name"], item["total_hours"], period_label])

                buffer = BytesIO()
                workbook.save(buffer)
                buffer.seek(0)
                filename = f"hours_{summary['start']:%Y%m%d_%H%M}-{summary['end']:%Y%m%d_%H%M}.xlsx"
                response = HttpResponse(
                    buffer.getvalue(),
                    content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
                response["Content-Disposition"] = f'attachment; filename=\"{filename}\"'
                return response

    return render(
        request,
        "tools.html",
        {
            "form": form,
            "summary": summary,
            "shift_rows": shift_rows,
            "agent_summaries": agent_summaries if summary else [],
        },
    )

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

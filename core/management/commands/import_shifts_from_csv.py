import csv
from pathlib import Path
from typing import Dict, List

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.db import transaction, connection
from zoneinfo import ZoneInfo

from core.models import Shift, Agent
from core.resources import ShiftResource


def parse_dt(value: str, tz):
    if not value:
        return None
    s = str(value).strip()
    # Try a few common formats quickly (no heavy parsers)
    fmts = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%d.%m.%Y %H:%M:%S",
        "%d.%m.%Y %H:%M",
    ]
    for fmt in fmts:
        try:
            dt = timezone.datetime.strptime(s, fmt)
            if timezone.is_naive(dt):
                return timezone.make_aware(dt, tz)
            return timezone.localtime(dt, tz)
        except ValueError:
            continue
    # As last resort, try fromisoformat (Python 3.11 tolerant)
    try:
        dt = timezone.datetime.fromisoformat(s)
        if timezone.is_naive(dt):
            return timezone.make_aware(dt, tz)
        return timezone.localtime(dt, tz)
    except Exception:
        return None


class Command(BaseCommand):
    help = "Fast, streaming import of shifts from CSV file. Expects headers: agent,start,end,direction,status,activity,comment"

    def add_arguments(self, parser):
        parser.add_argument("csv_path", type=str, help="Path to CSV file (UTF-8)")
        parser.add_argument("--delimiter", default=",", help="CSV delimiter, default ','")
        parser.add_argument("--batch-size", type=int, default=5000, help="Bulk insert batch size (default: 5000)")
        parser.add_argument("--tz", default=None, help="Timezone name (defaults to Django current)")
        parser.add_argument("--dry-run", action="store_true", help="Parse only, do not write shifts")

    def handle(self, *args, **opts):
        csv_path = Path(opts["csv_path"]).expanduser()
        if not csv_path.exists():
            raise CommandError(f"File not found: {csv_path}")

        delimiter = opts["delimiter"]
        batch_size = int(opts["batch_size"]) or 5000
        dry_run = bool(opts["dry_run"]) 
        tz = timezone.get_current_timezone() if not opts["tz"] else ZoneInfo(opts["tz"])

        # Build agent cache (normalized display -> id) using streaming to keep memory low
        agent_map: Dict[str, int] = {}
        for a in Agent.objects.select_related("user").only("id", "user__first_name", "user__last_name", "user__username").iterator(chunk_size=5000):
            disp = ShiftResource._clean_display_name(a.user.get_full_name() or a.user.username)
            agent_map[ShiftResource._normalize_name(disp)] = a.pk

        created = 0
        processed = 0
        skipped_no_agent = 0
        skipped_bad_time = 0

        # Stream rows and build Shifts in chunks
        def flush_batch(batch: List[Shift]):
            nonlocal created
            if not batch or dry_run:
                return
            # bulk_create avoids signals/history overhead => much faster and less memory
            Shift.objects.bulk_create(batch, batch_size=batch_size)
            created += len(batch)

        batch: List[Shift] = []

        with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f, delimiter=delimiter)
            headers = [h.strip() for h in (reader.fieldnames or [])]
            required = {"agent", "start", "end"}
            missing = required - set(headers)
            if missing:
                raise CommandError(f"CSV missing required headers: {', '.join(sorted(missing))}")

            # Suggest optional columns but not required
            for row in reader:
                processed += 1
                a_raw = (row.get("agent") or "").strip()
                a_norm = ShiftResource._normalize_name(a_raw)
                agent_id = agent_map.get(a_norm)
                if not agent_id:
                    skipped_no_agent += 1
                    continue

                start_dt = parse_dt(row.get("start"), tz)
                end_dt = parse_dt(row.get("end"), tz)
                if not start_dt or not end_dt:
                    skipped_bad_time += 1
                    continue
                if end_dt <= start_dt:
                    # Assume overnight shift => add day
                    end_dt = end_dt + timezone.timedelta(days=1)

                direction = ShiftResource._normalize_direction(row.get("direction"), row.get("activity"))
                status = ShiftResource._normalize_status(row.get("status"))
                activity = (row.get("activity") or "").strip()
                comment = (row.get("comment") or None)

                batch.append(Shift(
                    agent_id=agent_id,
                    start=start_dt,
                    end=end_dt,
                    direction=direction,
                    status=status,
                    activity=activity,
                    comment=comment,
                ))

                if len(batch) >= batch_size:
                    flush_batch(batch)
                    batch.clear()

        # final flush
        with transaction.atomic():
            with connection.cursor() as cur:
                cur.execute("SET LOCAL synchronous_commit = OFF")
            flush_batch(batch)

        self.stdout.write(self.style.SUCCESS(
            f"[shifts] processed={processed} created={created} skipped_no_agent={skipped_no_agent} skipped_bad_time={skipped_bad_time}"
        ))

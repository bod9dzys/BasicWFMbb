import csv
from pathlib import Path
from typing import Dict, Set, Tuple

from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth.models import User, Group
from django.contrib.auth.hashers import make_password
from django.db import transaction, connection
from django.utils.text import slugify

from core.models import Agent
from core.resources import ShiftResource


class Command(BaseCommand):
    help = "Fast, streaming import of users (agents + team leads) from CSV file with columns: agent, team_lead."

    def add_arguments(self, parser):
        parser.add_argument("csv_path", type=str, help="Path to CSV file (UTF-8)")
        parser.add_argument("--delimiter", default=",", help="CSV delimiter, default ','")
        parser.add_argument("--batch-size", type=int, default=2000, help="Insert batch size (default: 2000)")
        parser.add_argument("--dry-run", action="store_true", help="Parse only, do not write to DB")

    def handle(self, *args, **opts):
        csv_path = Path(opts["csv_path"]).expanduser()
        if not csv_path.exists():
            raise CommandError(f"File not found: {csv_path}")

        delimiter = opts["delimiter"]
        batch_size = int(opts["batch_size"]) or 2000
        dry_run = bool(opts["dry_run"])

        # First pass: collect unique names and mapping agent -> tl
        agents_needed: Set[str] = set()
        tls_needed: Set[str] = set()
        agent_original_by_norm: Dict[str, str] = {}
        tl_original_by_norm: Dict[str, str] = {}
        agent_to_tl: Dict[str, str] = {}

        with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f, delimiter=delimiter)
            headers = [h.strip() for h in (reader.fieldnames or [])]
            if "agent" not in headers:
                raise CommandError("CSV must contain 'agent' header")
            if "team_lead" not in headers:
                raise CommandError("CSV must contain 'team_lead' header")
            for row in reader:
                a_raw = (row.get("agent") or "").strip()
                if not a_raw:
                    continue
                a_norm = ShiftResource._normalize_name(a_raw)
                if not a_norm:
                    continue
                agents_needed.add(a_norm)
                agent_original_by_norm.setdefault(a_norm, ShiftResource._clean_display_name(a_raw))
                tl_raw = (row.get("team_lead") or "").strip()
                tl_norm = ShiftResource._normalize_name(tl_raw)
                if tl_norm:
                    tls_needed.add(tl_norm)
                    tl_original_by_norm.setdefault(tl_norm, ShiftResource._clean_display_name(tl_raw))
                    agent_to_tl[a_norm] = tl_norm

        self.stdout.write(self.style.NOTICE(f"[users] unique agents in file: {len(agents_needed)}; TLs: {len(tls_needed)}"))

        if dry_run:
            self.stdout.write(self.style.SUCCESS("Dry run complete."))
            return

        # Build caches from DB using streaming to keep memory low
        user_display_to_id: Dict[str, int] = {}
        for u in User.objects.only("first_name", "last_name", "username").iterator(chunk_size=5000):
            disp = ShiftResource._clean_display_name(u.get_full_name() or u.username)
            user_display_to_id[ShiftResource._normalize_name(disp)] = u.pk

        agent_display_to_id: Dict[str, int] = {}
        for a in Agent.objects.select_related("user").only("id", "user__first_name", "user__last_name", "user__username").iterator(chunk_size=5000):
            disp = ShiftResource._clean_display_name(a.user.get_full_name() or a.user.username)
            agent_display_to_id[ShiftResource._normalize_name(disp)] = a.pk

        # Prepare TL group
        tl_group, _ = Group.objects.get_or_create(name="TL")
        ShiftResource._ensure_tl_group_permissions(tl_group)

        # Create missing TL users
        per_base_cache: Dict[str, Set[str]] = {}
        new_tl_norms = [norm for norm in tls_needed if norm not in user_display_to_id]
        created_tl_users: Dict[str, int] = {}

        with transaction.atomic():
            # Speed up commit in Postgres
            with connection.cursor() as cur:
                cur.execute("SET LOCAL synchronous_commit = OFF")

            for norm in new_tl_norms:
                original = tl_original_by_norm.get(norm, norm)
                first, last = ShiftResource._split_name(original)
                base = slugify(original, allow_unicode=True).replace('-', '_') or 'tl'
                username = ShiftResource._allocate_username(base, per_base_cache)
                u = User.objects.create(
                    username=username,
                    first_name=first,
                    last_name=last,
                    password=make_password('temp_password123'),
                    is_staff=True,
                    is_active=True,
                )
                u.groups.add(tl_group)
                user_display_to_id[norm] = u.pk
                created_tl_users[norm] = u.pk

        self.stdout.write(self.style.SUCCESS(f"[users] created TLs: {len(created_tl_users)}"))

        # Create missing agents (User + Agent)
        new_agent_norms = [norm for norm in agents_needed if norm not in agent_display_to_id]
        created_agents = 0

        with transaction.atomic():
            with connection.cursor() as cur:
                cur.execute("SET LOCAL synchronous_commit = OFF")

            user_objs = []
            created_usernames = []
            for norm in new_agent_norms:
                original = agent_original_by_norm.get(norm, norm)
                first, last = ShiftResource._split_name(original)
                base = slugify(original, allow_unicode=True).replace('-', '_') or 'agent'
                username = ShiftResource._allocate_username(base, per_base_cache)
                user_objs.append(User(
                    username=username,
                    first_name=first,
                    last_name=last,
                    password=make_password('temp_password123'),
                    is_active=True,
                ))
                created_usernames.append(username)
                if len(user_objs) >= batch_size:
                    created_users = User.objects.bulk_create(user_objs, batch_size=batch_size)
                    for u in created_users:
                        disp = ShiftResource._clean_display_name(u.get_full_name() or u.username)
                        user_display_to_id[ShiftResource._normalize_name(disp)] = u.pk
                    user_objs.clear()
            if user_objs:
                created_users = User.objects.bulk_create(user_objs, batch_size=batch_size)
                for u in created_users:
                    disp = ShiftResource._clean_display_name(u.get_full_name() or u.username)
                    user_display_to_id[ShiftResource._normalize_name(disp)] = u.pk

            agent_objs = []
            for norm in new_agent_norms:
                uid = user_display_to_id.get(norm)
                if uid:
                    agent_objs.append(Agent(user_id=uid))
                if len(agent_objs) >= batch_size:
                    Agent.objects.bulk_create(agent_objs, batch_size=batch_size)
                    agent_objs.clear()
                    created_agents += batch_size
            if agent_objs:
                created = Agent.objects.bulk_create(agent_objs, batch_size=batch_size)
                created_agents += len(created)

            # Reload agent cache for assignments
            agent_display_to_id.clear()
            for a in Agent.objects.select_related("user").only("id", "user__first_name", "user__last_name", "user__username").iterator(chunk_size=5000):
                disp = ShiftResource._clean_display_name(a.user.get_full_name() or a.user.username)
                agent_display_to_id[ShiftResource._normalize_name(disp)] = a.pk

        self.stdout.write(self.style.SUCCESS(f"[users] created agents: {created_agents}"))

        # Assign team leads
        assigned = 0
        with transaction.atomic():
            with connection.cursor() as cur:
                cur.execute("SET LOCAL synchronous_commit = OFF")
            # group by TL to reduce updates
            tl_to_agent_ids: Dict[int, list] = {}
            for a_norm, tl_norm in agent_to_tl.items():
                aid = agent_display_to_id.get(a_norm)
                tlid = user_display_to_id.get(tl_norm)
                if aid and tlid:
                    tl_to_agent_ids.setdefault(tlid, []).append(aid)
            for tlid, agent_ids in tl_to_agent_ids.items():
                updated = Agent.objects.filter(id__in=agent_ids).exclude(team_lead_id=tlid).update(team_lead_id=tlid)
                assigned += updated

        self.stdout.write(self.style.SUCCESS(f"[users] assigned team leads to {assigned} agents"))

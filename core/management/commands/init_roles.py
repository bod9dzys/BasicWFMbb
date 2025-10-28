# core/management/commands/init_roles.py
from django.core.management.base import BaseCommand
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from core.models import Agent, Shift, ShiftExchange


class Command(BaseCommand):
    help = "Створює групи ролей і призначає права"

    def handle(self, *args, **options):
        def perms_for(model, codes):
            ct = ContentType.objects.get_for_model(model)
            return list(Permission.objects.filter(content_type=ct, codename__in=codes))

        # Базові CRUD для зручності (не обов'язково)
        def crud(model):
            m = model._meta.model_name
            return perms_for(model, [f"add_{m}", f"view_{m}", f"change_{m}", f"delete_{m}"])

        # Права по моделях
        shift_crud = crud(Shift)
        shift_extra = perms_for(Shift, ["import_schedule", "export_schedule"])

        agent_view = perms_for(Agent, ["view_agent"])
        agent_extra = perms_for(Agent, ["export_schedule"])  # якщо тримаєш export на Agent

        ex_crud = crud(ShiftExchange)
        ex_extra = perms_for(ShiftExchange, ["request_exchange", "approve_exchange", "view_exchange_history"])

        # Групи
        agent_g, _ = Group.objects.get_or_create(name="Agent")
        tl_g, _ = Group.objects.get_or_create(name="TL")
        mon_g, _ = Group.objects.get_or_create(name="Monitoring")
        plan_g, _ = Group.objects.get_or_create(name="Planning")

        # Роздача прав
        agent_perms = []
        agent_perms += perms_for(Shift, ["view_shift"])
        agent_perms += perms_for(ShiftExchange, ["add_shiftexchange", "view_shiftexchange", "request_exchange"])
        agent_g.permissions.set(agent_perms)

        tl_perms = list(agent_perms)
        tl_perms += perms_for(Shift, ["export_schedule"])
        tl_perms += perms_for(ShiftExchange, ["view_exchange_history"])
        tl_g.permissions.set(tl_perms)

        mon_perms = []
        mon_perms += shift_crud
        mon_perms += ex_crud
        mon_perms += ex_extra
        mon_perms += perms_for(Shift, ["export_schedule"])
        mon_g.permissions.set(mon_perms)

        plan_perms = list(mon_perms)
        plan_perms += perms_for(Shift, ["import_schedule"])
        plan_g.permissions.set(plan_perms)

        self.stdout.write(self.style.SUCCESS("Групи та права ініціалізовано"))

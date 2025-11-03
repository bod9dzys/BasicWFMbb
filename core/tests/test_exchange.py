from datetime import datetime, timedelta, time

from django.contrib.auth.models import Permission, User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.forms import ExchangeCreateForm
from core.models import Agent, Shift, ShiftExchange, ShiftStatus


class ExchangeTests(TestCase):
    def setUp(self):
        tz = timezone.get_current_timezone()
        base_start = timezone.make_aware(
            datetime.combine(timezone.localdate(), time(9, 0)), tz
        )

        self.user_with_perm = User.objects.create_user(
            username="exchanger", password="pass1234"
        )
        perm = Permission.objects.get(codename="add_shiftexchange")
        self.user_with_perm.user_permissions.add(perm)

        self.user_without_perm = User.objects.create_user(
            username="noperm", password="pass1234"
        )

        self.agent_a = Agent.objects.create(
            user=self.user_with_perm,
            active=True,
            skills=["calls"],
        )
        self.agent_b = Agent.objects.create(
            user=User.objects.create_user(username="agent_b"),
            active=True,
            skills=["calls"],
        )

        self.shift_a = Shift.objects.create(
            agent=self.agent_a,
            start=base_start,
            end=base_start + timedelta(hours=8),
            status=ShiftStatus.WORK,
            direction="calls",
        )
        self.shift_b = Shift.objects.create(
            agent=self.agent_b,
            start=base_start + timedelta(days=1),
            end=base_start + timedelta(days=1, hours=8),
            status=ShiftStatus.WORK,
            direction="calls",
        )

    def test_form_rejects_same_agent(self):
        form = ExchangeCreateForm(
            self.user_with_perm,
            data={
                "from_agent": self.agent_a.pk,
                "from_shift": self.shift_a.pk,
                "to_agent": self.agent_a.pk,
                "to_shift": self.shift_b.pk,
            },
        )
        self.assertFalse(form.is_valid())
        self.assertIn("Оберіть двох різних агентів.", form.errors.get("__all__", [""])[0])

    def test_form_requires_matching_shift_agent(self):
        form = ExchangeCreateForm(
            self.user_with_perm,
            data={
                "from_agent": self.agent_a.pk,
                "from_shift": self.shift_b.pk,
                "to_agent": self.agent_b.pk,
                "to_shift": self.shift_a.pk,
            },
        )
        self.assertFalse(form.is_valid())
        self.assertIn("не належить", form.errors.get("from_shift", [""])[0])

    def test_view_exchanges_shifts_successfully(self):
        self.client.login(username="exchanger", password="pass1234")
        response = self.client.post(
            reverse("exchange_create"),
            data={
                "from_agent": self.agent_a.pk,
                "from_shift": self.shift_a.pk,
                "to_agent": self.agent_b.pk,
                "to_shift": self.shift_b.pk,
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.shift_a.refresh_from_db()
        self.shift_b.refresh_from_db()
        self.assertEqual(self.shift_a.agent, self.agent_b)
        self.assertEqual(self.shift_b.agent, self.agent_a)

        exchange = ShiftExchange.objects.latest("created_at")
        self.assertTrue(exchange.approved)
        self.assertEqual(exchange.from_shift, self.shift_a)
        self.assertEqual(exchange.to_shift, self.shift_b)

    def test_view_prevents_invalid_swap(self):
        self.client.login(username="exchanger", password="pass1234")
        self.shift_b.status = ShiftStatus.SICK
        self.shift_b.save(update_fields=["status"])

        response = self.client.post(
            reverse("exchange_create"),
            data={
                "from_agent": self.agent_a.pk,
                "from_shift": self.shift_a.pk,
                "to_agent": self.agent_b.pk,
                "to_shift": self.shift_b.pk,
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.shift_a.refresh_from_db()
        self.shift_b.refresh_from_db()
        self.assertEqual(self.shift_a.agent, self.agent_a)
        self.assertEqual(self.shift_b.agent, self.agent_b)
        self.assertFalse(ShiftExchange.objects.exists())
        self.assertContains(response, "Не можна міняти на відпустку", status_code=200)

    def test_view_requires_permission(self):
        self.client.login(username="noperm", password="pass1234")
        response = self.client.post(
            reverse("exchange_create"),
            data={
                "from_agent": self.agent_a.pk,
                "from_shift": self.shift_a.pk,
                "to_agent": self.agent_b.pk,
                "to_shift": self.shift_b.pk,
            },
        )
        self.assertEqual(response.status_code, 403)


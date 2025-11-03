from datetime import datetime, timedelta, time

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify

from core.models import Agent, Shift, ShiftStatus, SickLeaveProof


class SickLeaveViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="agent_view",
            password="pass1234",
            first_name="Alan",
            last_name="Turing",
        )
        self.agent = Agent.objects.create(user=self.user, active=True)
        self.client.login(username="agent_view", password="pass1234")

        tz = timezone.get_current_timezone()
        start = timezone.make_aware(datetime.combine(timezone.localdate(), time(9)), tz)
        end = start + timedelta(hours=8)
        self.shift = Shift.objects.create(
            agent=self.agent,
            start=start,
            end=end,
            status=ShiftStatus.WORK,
            activity="лікарняний",
            comment="[Лікарняний] первинний запис\nДодаткова інформація",
        )

    def _make_file(self, name="proof.txt", content=b"proof-data"):
        return SimpleUploadedFile(name, content)

    def test_submit_with_attachment_marks_shift_and_stores_proof(self):
        today = timezone.localdate()
        response = self.client.post(
            reverse("requests_sick_leave"),
            data={
                "agent": self.agent.pk,
                "start": today.strftime("%Y-%m-%d"),
                "end": today.strftime("%Y-%m-%d"),
                "attach_later": False,
                # файл має бути тут, а не в files=
                "attachment": self._make_file("proof.txt", b"proof-data"),
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.shift.refresh_from_db()
        self.assertEqual(self.shift.status, ShiftStatus.SICK)
        comment_text = (self.shift.comment or "").strip()
        self.assertNotIn("[Лікарняний", comment_text)
        self.assertFalse(self.shift.activity)

        proof = SickLeaveProof.objects.get(agent=self.agent)
        self.assertIsNotNone(proof.attachment)
        self.assertFalse(proof.attach_later)
        self.assertIsNotNone(proof.resolved_at)
        self.assertTrue(proof.attachment.name.endswith("proof.txt"))
        agent_slug = slugify(self.agent.user.get_full_name() or self.agent.user.username) or f"agent-{self.agent.pk}"
        self.assertIn(f"sick_leave_proofs/{agent_slug}/", proof.attachment.name)

    def test_submit_attach_later_creates_pending_proof(self):
        today = timezone.localdate()
        response = self.client.post(
            reverse("requests_sick_leave"),
            data={
                "agent": self.agent.pk,
                "start": today.strftime("%Y-%m-%d"),
                "end": today.strftime("%Y-%m-%d"),
                "attach_later": True,
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)

        proof = SickLeaveProof.objects.get(agent=self.agent)
        self.assertTrue(proof.attach_later)
        self.assertFalse(proof.attachment)

        response = self.client.get(reverse("requests_sick_leave"))
        self.assertContains(response, "Неприкріплені підтвердження")
        self.assertContains(response, proof.get_proof_type_display())

    def test_upload_proof_requires_file(self):
        proof = SickLeaveProof.objects.create(
            agent=self.agent,
            start_date=timezone.localdate(),
            end_date=timezone.localdate(),
            submitted_by=self.user,
            attach_later=True,
        )

        url = reverse("upload_sick_leave_proof", args=[proof.pk])
        response = self.client.post(url, data={"next": reverse("requests_sick_leave")}, follow=True)

        proof.refresh_from_db()
        self.assertFalse(proof.attachment)
        self.assertTrue(proof.attach_later)
        self.assertIsNone(proof.resolved_at)
        self.assertContains(response, "Додайте файл підтвердження.", status_code=200)

    def test_upload_proof_success(self):
        proof = SickLeaveProof.objects.create(
            agent=self.agent,
            start_date=timezone.localdate(),
            end_date=timezone.localdate(),
            submitted_by=self.user,
            attach_later=True,
        )

        url = reverse("upload_sick_leave_proof", args=[proof.pk])
        response = self.client.post(
            url,
            data={
                "next": reverse("requests_sick_leave"),
                # файл тут
                "attachment": self._make_file("evidence.png", b"img-bytes"),
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        proof.refresh_from_db()
        self.assertFalse(proof.attach_later)
        self.assertIsNotNone(proof.attachment)
        self.assertIsNotNone(proof.resolved_at)
        self.assertTrue(proof.attachment.name.endswith("evidence.png"))



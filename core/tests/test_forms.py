from datetime import timedelta
import zipfile

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone

from core.forms import SickLeaveRequestForm, SickLeaveProofUploadForm
from core.models import Agent


class SickLeaveFormTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="agent1",
            password="pass1234",
            first_name="Ada",
            last_name="Lovelace",
        )
        self.agent = Agent.objects.create(user=self.user, active=True)

    def _make_file(self, name="proof.txt", content=b"example data"):
        return SimpleUploadedFile(name, content)

    def test_request_form_with_attachment_zips_file(self):
        today = timezone.localdate()
        file_obj = self._make_file()
        form = SickLeaveRequestForm(
            self.user,
            data={
                "agent": self.agent.pk,
                "start": today.strftime("%Y-%m-%d"),
                "end": (today + timedelta(days=1)).strftime("%Y-%m-%d"),
                "attach_later": False,
            },
            files={"attachment": file_obj},
        )

        self.assertTrue(form.is_valid(), form.errors.as_data())

        attachment = form.cleaned_data["attachment"]
        self.assertTrue(attachment.name.endswith(".zip"))

        attachment.open("rb")
        with zipfile.ZipFile(attachment, "r") as archive:
            names = archive.namelist()
            self.assertEqual(names, ["proof.txt"])
            self.assertEqual(archive.read("proof.txt"), b"example data")

    def test_request_form_without_attachment_requires_attach_later(self):
        today = timezone.localdate()
        form = SickLeaveRequestForm(
            self.user,
            data={
                "agent": self.agent.pk,
                "start": today.strftime("%Y-%m-%d"),
                "end": today.strftime("%Y-%m-%d"),
                "attach_later": False,
            },
        )

        self.assertFalse(form.is_valid())
        self.assertIn("Додайте підтвердження", form.errors["__all__"][0])

    def test_request_form_attach_later_is_valid_without_file(self):
        today = timezone.localdate()
        form = SickLeaveRequestForm(
            self.user,
            data={
                "agent": self.agent.pk,
                "start": today.strftime("%Y-%m-%d"),
                "end": (today + timedelta(days=3)).strftime("%Y-%m-%d"),
                "attach_later": True,
            },
        )

        self.assertTrue(form.is_valid(), form.errors.as_data())
        self.assertIsNone(form.cleaned_data.get("attachment"))

    def test_upload_form_requires_file(self):
        form = SickLeaveProofUploadForm(data={}, files={})

        self.assertFalse(form.is_valid())
        self.assertIn("Додайте файл", form.errors["attachment"][0])

    def test_upload_form_compresses_file(self):
        form = SickLeaveProofUploadForm(
            data={},
            files={"attachment": self._make_file("evidence.pdf", b"binary data")},
        )

        self.assertTrue(form.is_valid(), form.errors)
        attachment = form.cleaned_data["attachment"]

        self.assertTrue(attachment.name.endswith(".zip"))
        attachment.open("rb")
        with zipfile.ZipFile(attachment, "r") as archive:
            self.assertEqual(archive.read("evidence.pdf"), b"binary data")

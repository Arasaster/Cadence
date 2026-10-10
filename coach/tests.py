import json
from datetime import time, timedelta
from unittest.mock import patch

from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from coach import ai
from coach.insights import parse_archive
from coach.models import Plan, Profile, Task

PROFILE = {
    "x_username": "@excel", "niche": "design for founders", "goal": "followers", "audience": "early-stage founders",
    "offer": "", "tone": "casual", "admired": "", "voice_samples": "", "posts_per_day": 2, "reply_target": 20,
    "daily_minutes": 45, "timezone": "Africa/Lagos", "email_reminders": "on",
}

ARCHIVE = "window.YTD.tweets.part0 = " + json.dumps([
    {"tweet": {"full_text": "Design is thinking made visible", "created_at": "Wed Oct 01 10:00:00 +0000 2025",
               "favorite_count": "40", "retweet_count": "5", "entities": {"user_mentions": []}}},
    {"tweet": {"full_text": "@sam agreed", "created_at": "Thu Oct 02 11:00:00 +0000 2025", "favorite_count": "1",
               "retweet_count": "0", "in_reply_to_screen_name": "sam", "entities": {"user_mentions": []}}},
    {"tweet": {"full_text": "RT @x: ignore", "created_at": "Thu Oct 02 12:00:00 +0000 2025", "favorite_count": "0", "retweet_count": "0"}},
])


class FlowTests(TestCase):
    def signup(self):
        r = self.client.post(reverse("signup"), {"email": "A@b.com", "password": "longenough1"})
        self.assertRedirects(r, reverse("onboarding"))

    def test_full_flow_demo_mode(self):
        self.signup()
        r = self.client.post(reverse("onboarding"), PROFILE)
        self.assertRedirects(r, reverse("today"))
        self.assertEqual(Profile.objects.get().x_username, "excel")
        r = self.client.post(reverse("generate"))
        self.assertRedirects(r, reverse("today"))
        plan = Plan.objects.get()
        self.assertEqual(plan.source, "demo")
        self.assertTrue(plan.tasks.filter(kind="reply_session").exists())
        page = self.client.get(reverse("today"))
        self.assertContains(page, "Open in X")
        task = plan.tasks.first()
        r = self.client.post(reverse("toggle_task", args=[task.pk]))
        self.assertTrue(r.json()["done"])
        self.assertEqual(r.json()["done_count"], 1)

    def test_other_user_cannot_toggle(self):
        self.signup()
        self.client.post(reverse("onboarding"), PROFILE)
        self.client.post(reverse("generate"))
        task = Task.objects.first()
        self.client.logout()
        self.client.post(reverse("signup"), {"email": "c@d.com", "password": "longenough1"})
        self.assertEqual(self.client.post(reverse("toggle_task", args=[task.pk])).status_code, 404)

    def test_reply_helper_demo(self):
        self.signup()
        self.client.post(reverse("onboarding"), PROFILE)
        r = self.client.post(reverse("replies"), {"post_text": "Shipping beats planning.", "author": "sam"})
        self.assertContains(r, "Adds value")

    def test_bad_reply_target_rejected(self):
        self.signup()
        r = self.client.post(reverse("onboarding"), {**PROFILE, "reply_target": 500})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Profile.objects.exists())

    def test_insights_upload(self):
        self.signup()
        self.client.post(reverse("onboarding"), PROFILE)
        f = SimpleUploadedFile("tweets.js", ARCHIVE.encode())
        r = self.client.post(reverse("insights"), {"file": f}, follow=True)
        self.assertContains(r, "Original posts")
        self.assertContains(r, "@sam")

    def test_insights_rejects_garbage(self):
        self.signup()
        self.client.post(reverse("onboarding"), PROFILE)
        f = SimpleUploadedFile("x.js", b"not an archive")
        r = self.client.post(reverse("insights"), {"file": f}, follow=True)
        self.assertContains(r, "does not look like")


class AiTests(TestCase):
    def test_normalize_drops_bad_rows(self):
        out = ai.normalize_plan({"summary": "s", "tasks": [
            {"time": "25:00", "kind": "post", "title": "bad"},
            {"time": "9:05", "kind": "weird", "title": "ok", "body": "b", "count": 3},
        ]})
        self.assertEqual(len(out["tasks"]), 1)
        self.assertEqual(out["tasks"][0]["kind"], "post")
        self.assertEqual(out["tasks"][0]["time"], time(9, 5))

    def test_extract_json_with_noise(self):
        self.assertEqual(ai._extract_json('Here: ```json\n{"a": 1}\n```'), {"a": 1})

    @override_settings(OPENROUTER_API_KEY="k")
    def test_ai_path_used_when_key_present(self):
        good = {"summary": "s", "tasks": [{"time": "09:00", "kind": "post", "title": "t", "body": "hello"}]}
        with patch("coach.ai._ask", return_value=good):
            from django.contrib.auth import get_user_model
            u = get_user_model().objects.create_user("u@x.com", password="x")
            p = Profile.objects.create(user=u, niche="n", audience="a")
            data, source = ai.generate_plan(p, [], None, "Monday")
        self.assertEqual(source, "ai")
        self.assertEqual(data["tasks"][0]["title"], "t")


class ParserTests(TestCase):
    def test_parse(self):
        d = parse_archive(ARCHIVE, "Africa/Lagos", "excel")
        self.assertEqual(d["total_posts"], 1)
        self.assertEqual(d["total_replies"], 1)
        self.assertEqual(d["circle"][0]["name"], "sam")
        self.assertEqual(d["top_posts"][0]["likes"], 40)


class ReminderTests(TestCase):
    def test_due_tasks_emailed_once(self):
        from django.contrib.auth import get_user_model
        u = get_user_model().objects.create_user("r@x.com", email="r@x.com", password="x")
        p = Profile.objects.create(user=u, niche="n", audience="a", timezone="UTC")
        now = timezone.now()
        plan = Plan.objects.create(user=u, date=p.today())
        past = (now - timedelta(minutes=10)).time().replace(microsecond=0)
        Task.objects.create(plan=plan, time=past, kind="post", title="Due now", body="Hello world")
        Task.objects.create(plan=plan, time=time(23, 59), kind="post", title="Later", body="x")
        if past > now.time():  # edge case right after midnight
            return
        call_command("send_reminders")
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Due now", mail.outbox[0].subject)
        call_command("send_reminders")
        self.assertEqual(len(mail.outbox), 1)


class CronEndpointTests(TestCase):
    @override_settings(CRON_SECRET="s3cret")
    def test_requires_secret(self):
        self.assertEqual(self.client.get("/cron/reminders/").status_code, 403)
        self.assertEqual(self.client.get("/cron/reminders/", HTTP_AUTHORIZATION="Bearer nope").status_code, 403)
        ok = self.client.get("/cron/reminders/", HTTP_AUTHORIZATION="Bearer s3cret")
        self.assertEqual(ok.status_code, 200)
        self.assertIn("reminder", ok.content.decode())
        self.assertEqual(self.client.get("/cron/plans/", HTTP_AUTHORIZATION="Bearer s3cret").status_code, 200)
        self.assertEqual(self.client.get("/cron/nope/", HTTP_AUTHORIZATION="Bearer s3cret").status_code, 404)

    @override_settings(CRON_SECRET="")
    def test_disabled_without_secret(self):
        self.assertEqual(self.client.get("/cron/reminders/", HTTP_AUTHORIZATION="Bearer ").status_code, 403)

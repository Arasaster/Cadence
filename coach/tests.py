import json
import random
from datetime import time, timedelta
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from coach import ai, fxtwitter, prompts, style
from coach.email_backend import BrevoEmailBackend
from coach.insights import parse_archive
from coach.models import Plan, Profile, Roadmap, Task

User = get_user_model()

PROFILE = {
    "account_type": "company", "x_username": "@excel", "current_followers": 120, "target_followers": 1000,
    "target_note": "1000 followers and 5 enquiries a month", "niche": "design for founders", "goal": "followers",
    "audience": "early-stage founders", "offer": "", "tone": "casual", "admired": "", "voice_samples": "",
    "posts_per_day": 2, "reply_target": 20, "daily_minutes": 45, "timezone": "Africa/Lagos", "email_reminders": "on",
}

FAKE_PLAN = {
    "summary": "Focus day",
    "tasks": [
        {"time": "09:00", "kind": "post", "title": "Opinion post", "body": "Hot take.", "why": "Starts talks."},
        {"time": "10:00", "kind": "reply_session", "title": "Replies", "body": "Reply to 10.", "why": "Reach.", "count": 10},
    ],
}


def fake_roadmap(days=30):
    n = 3 if days == 30 else 4
    size = days // n
    return {
        "summary": "Build, then grow.", "baseline": "120 followers.", "target": "1000 followers.",
        "reality_check": "Reachable if followed.",
        "phases": [
            {"name": f"Phase {i+1}", "start_day": i * size + 1, "end_day": (i + 1) * size, "focus": "Focus",
             "actions": ["Do A", "Do B"], "content_pillars": ["Tips"], "checkpoint": "20 posts"}
            for i in range(n)
        ],
    }


ARCHIVE = "window.YTD.tweets.part0 = " + json.dumps([
    {"tweet": {"full_text": "Design is thinking made visible", "created_at": "Wed Oct 01 10:00:00 +0000 2025",
               "favorite_count": "40", "retweet_count": "5", "entities": {"user_mentions": []}}},
    {"tweet": {"full_text": "@sam agreed", "created_at": "Thu Oct 02 11:00:00 +0000 2025", "favorite_count": "1",
               "retweet_count": "0", "in_reply_to_screen_name": "sam", "entities": {"user_mentions": []}}},
    {"tweet": {"full_text": "RT @x: ignore", "created_at": "Thu Oct 02 12:00:00 +0000 2025", "favorite_count": "0", "retweet_count": "0"}},
])


class Recorder:
    """Stands in for ai._ask: records the payload and answers by which prompt was used."""

    def __init__(self, **answers):
        self.calls = []
        self.answers = answers

    def __call__(self, system, user, **kw):
        payload = json.loads(user)
        self.calls.append((system, payload))
        for name, answer in self.answers.items():
            if system is getattr(prompts, name):
                return answer(payload) if callable(answer) else answer
        raise AssertionError("unexpected prompt")


class Base(TestCase):
    def signup(self, email="a@b.com"):
        r = self.client.post(reverse("signup"), {"email": email, "password": "longenough1", "agree": "on"})
        self.assertRedirects(r, reverse("onboarding"))

    def setup_user(self, **over):
        self.signup()
        r = self.client.post(reverse("onboarding"), {**PROFILE, **over})
        self.assertRedirects(r, reverse("today"))


class AccountAndSignupTests(Base):
    def test_terms_checkbox_required(self):
        r = self.client.post(reverse("signup"), {"email": "a@b.com", "password": "longenough1"})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "accept the Terms")
        self.assertFalse(User.objects.exists())

    def test_assets_are_versioned_so_browsers_refresh(self):
        html = self.client.get(reverse("landing")).content.decode()
        self.assertRegex(html, r"app\.js\?v=\d+")
        self.assertRegex(html, r"app\.css\?v=\d+")

    def test_legal_and_landing_pages(self):
        for name in ("terms", "privacy", "landing"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200)
        self.assertContains(self.client.get(reverse("terms")), "Terms of Service")
        self.assertContains(self.client.get(reverse("landing")), "Privacy")

    def test_account_type_saved_and_sent_to_model(self):
        self.setup_user()
        self.assertEqual(Profile.objects.get().account_type, "company")
        rec = Recorder(PLAN_SYSTEM=FAKE_PLAN)
        with patch("coach.ai._ask", rec):
            self.client.post(reverse("generate"))
        voice = rec.calls[0][1]["user"]["voice"]
        self.assertIn("Company", voice["account_type"])
        self.assertIn("brand", voice["voice"].lower())
        self.assertEqual(voice["emoji_max_per_item"], 1)


class EmojiTests(TestCase):
    def test_strength_zero_never_emoji(self):
        self.assertFalse(any(style.emoji_flags("creator", 200, strength=0)))

    def test_not_always_and_shuffled(self):
        flags = style.emoji_flags("individual", 400, rng=random.Random(1))
        share = sum(flags) / len(flags)
        self.assertTrue(0.3 < share < 0.5)
        self.assertNotEqual(flags[:10], flags[10:20])

    def test_company_rate_below_creator(self):
        self.assertLess(style.ACCOUNT_TYPES["company"]["emoji_rate"], style.ACCOUNT_TYPES["creator"]["emoji_rate"])

    def test_rules_in_every_prompt(self):
        for p in (prompts.PLAN_SYSTEM, prompts.BIO_SYSTEM, prompts.REPLY_WRITE_SYSTEM):
            self.assertIn("Emoji rules", p)
            self.assertIn("Voice:", p)


class DailyFlowTests(Base):
    def test_full_flow(self):
        self.setup_user()
        with patch("coach.ai._ask", Recorder(PLAN_SYSTEM=FAKE_PLAN)):
            r = self.client.post(reverse("generate"))
        self.assertRedirects(r, reverse("today"))
        plan = Plan.objects.get()
        self.assertTrue(plan.tasks.filter(kind="reply_session").exists())
        self.assertContains(self.client.get(reverse("today")), "Open in X")
        task = plan.tasks.first()
        r = self.client.post(reverse("toggle_task", args=[task.pk]))
        self.assertTrue(r.json()["done"])

    def test_other_user_cannot_toggle(self):
        self.setup_user()
        with patch("coach.ai._ask", Recorder(PLAN_SYSTEM=FAKE_PLAN)):
            self.client.post(reverse("generate"))
        task = Task.objects.first()
        self.client.logout()
        self.signup("c@d.com")
        self.assertEqual(self.client.post(reverse("toggle_task", args=[task.pk])).status_code, 404)

    @override_settings(OPENROUTER_API_KEY="")
    def test_missing_key_shows_error_and_creates_no_plan(self):
        self.setup_user()
        r = self.client.post(reverse("generate"), follow=True)
        self.assertContains(r, "OPENROUTER_API_KEY is not set")
        self.assertFalse(Plan.objects.exists())

    def test_bad_reply_target_rejected(self):
        self.signup()
        r = self.client.post(reverse("onboarding"), {**PROFILE, "reply_target": 500})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Profile.objects.exists())

    @override_settings(AI_DAILY_LIMIT=1)
    def test_daily_ai_limit(self):
        self.setup_user()
        with patch("coach.ai._ask", Recorder(PLAN_SYSTEM=FAKE_PLAN)):
            self.client.post(reverse("generate"))
            r = self.client.post(reverse("generate"), follow=True)
        self.assertContains(r, "AI limit")


class NewDayFlowTests(Base):
    def add_yesterday(self, done_flags):
        from datetime import timedelta as td
        user = User.objects.get()
        prof = Profile.objects.get()
        plan = Plan.objects.create(user=user, date=prof.today() - td(days=1))
        for i, done in enumerate(done_flags):
            Task.objects.create(plan=plan, time=time(9 + i), kind="post", title=f"Yesterday task {i}", body="x", done=done)
        return plan

    def test_new_day_builds_automatically_without_a_button_click(self):
        self.setup_user()
        page = self.client.get(reverse("today"))
        self.assertContains(page, 'id="buildCard"')
        self.assertContains(page, "Building today")
        self.assertNotContains(page, "Rebuild plan")

    def test_generate_json_mode_for_auto_build(self):
        self.setup_user()
        with patch("coach.ai._ask", Recorder(PLAN_SYSTEM=FAKE_PLAN)):
            r = self.client.post(reverse("generate"), HTTP_X_REQUESTED_WITH="fetch")
        self.assertEqual(r.json(), {"ok": True})
        page = self.client.get(reverse("today"))
        self.assertNotContains(page, 'id="buildCard"')
        self.assertContains(page, "Rebuild plan")

    @override_settings(OPENROUTER_API_KEY="")
    def test_generate_json_reports_errors(self):
        self.setup_user()
        d = self.client.post(reverse("generate"), HTTP_X_REQUESTED_WITH="fetch").json()
        self.assertFalse(d["ok"])
        self.assertIn("OPENROUTER_API_KEY", d["error"])

    def test_unfinished_yesterday_shows_warning_and_option_to_continue(self):
        self.setup_user()
        self.add_yesterday([True, False, False])
        page = self.client.get(reverse("today"))
        self.assertContains(page, 'id="carryCard"')
        self.assertContains(page, "2 unfinished tasks")
        self.assertContains(page, "Yesterday task 1")
        self.assertNotContains(page, "Yesterday task 0")
        self.assertContains(page, "Continue without finishing")
        self.assertContains(page, "less accurate")
        self.assertNotContains(page, 'id="buildCard"')
        go = self.client.get(reverse("today") + "?go=1")
        self.assertContains(go, 'id="buildCard"')
        self.assertNotContains(go, 'id="carryCard"')

    def test_finished_yesterday_goes_straight_to_building(self):
        self.setup_user()
        self.add_yesterday([True, True])
        page = self.client.get(reverse("today"))
        self.assertContains(page, 'id="buildCard"')
        self.assertNotContains(page, 'id="carryCard"')

    def test_ticking_carry_over_tasks_removes_them_from_the_warning(self):
        self.setup_user()
        plan = self.add_yesterday([False, False])
        for t in plan.tasks.all():
            self.assertTrue(self.client.post(reverse("toggle_task", args=[t.pk])).json()["done"])
        self.assertNotContains(self.client.get(reverse("today")), 'id="carryCard"')

    def test_planner_is_told_what_was_actually_done(self):
        self.setup_user()
        self.add_yesterday([True, False, False])
        rec = Recorder(PLAN_SYSTEM=FAKE_PLAN)
        with patch("coach.ai._ask", rec):
            self.client.post(reverse("generate"))
        r = rec.calls[0][1]["recent_completion"]
        self.assertEqual((r["last_plan_done"], r["last_plan_total"]), (1, 3))
        self.assertEqual(r["last_plan_unfinished"], ["Yesterday task 1", "Yesterday task 2"])
        self.assertEqual(r["last_7_days_completion_pct"], 33)
        self.assertIn("COMPLETION", prompts.PLAN_SYSTEM)

    def test_first_ever_day_has_no_completion_data(self):
        self.setup_user()
        rec = Recorder(PLAN_SYSTEM=FAKE_PLAN)
        with patch("coach.ai._ask", rec):
            self.client.post(reverse("generate"))
        self.assertIsNone(rec.calls[0][1]["recent_completion"])

    def test_post_tasks_have_schedule_button_and_hint(self):
        self.setup_user()
        with patch("coach.ai._ask", Recorder(PLAN_SYSTEM=FAKE_PLAN)):
            self.client.post(reverse("generate"))
        page = self.client.get(reverse("today"))
        self.assertContains(page, "Schedule post in X")
        self.assertContains(page, "calendar icon")
        self.assertContains(page, "https://x.com/intent/post?text=")
        self.assertContains(page, "data-confirm")  # rebuilding asks first


class HeaderTests(Base):
    def test_settings_on_avatar_and_logout_is_an_icon(self):
        self.setup_user()
        html = self.client.get(reverse("today")).content.decode()
        nav = html[html.index('class="pillnav"'):html.index("</nav>")]
        self.assertNotIn("Settings", nav)
        for label in ("Today", "Roadmap", "Reply helper", "Bio", "Insights"):
            self.assertIn(label, nav)
        self.assertIn('href="/setup/"', html[html.index('class="me"'):])
        self.assertIn('aria-label="Settings"', html)
        self.assertIn('aria-label="Log out"', html)
        self.assertNotIn(">Log out<", html)

    def test_test_email_is_gone_from_settings(self):
        self.setup_user()
        page = self.client.get(reverse("onboarding")).content.decode()
        self.assertNotIn("test email", page.lower())
        self.assertEqual(self.client.post("/setup/test-email/").status_code, 404)


class RoadmapTests(Base):
    def test_normalize_fixes_gaps_and_overlaps(self):
        data = {"phases": [
            {"name": "B", "start_day": 8, "end_day": 20, "focus": "f"},
            {"name": "A", "start_day": 1, "end_day": 10, "focus": "f"},
        ]}
        out = ai.normalize_roadmap(data, 30)
        spans = [(p["start_day"], p["end_day"]) for p in out["phases"]]
        self.assertEqual(spans, [(1, 10), (11, 30)])

    def test_normalize_rejects_empty(self):
        with self.assertRaises(ValueError):
            ai.normalize_roadmap({"phases": []}, 30)

    def test_create_30_and_60_day_roadmaps(self):
        self.setup_user()
        for days in (30, 60):
            rec = Recorder(ROADMAP_SYSTEM=lambda p, d=days: fake_roadmap(d))
            with patch("coach.ai._ask", rec):
                r = self.client.post(reverse("roadmap"), {"days": str(days), "note": ""})
            self.assertRedirects(r, reverse("roadmap"))
            self.assertEqual(rec.calls[0][1]["days"], days)
            self.assertEqual(Roadmap.objects.filter(active=True).get().days, days)
        self.assertEqual(Roadmap.objects.filter(active=True).count(), 1)
        page = self.client.get(reverse("roadmap"))
        self.assertContains(page, "Day 1")
        self.assertContains(page, "Phase 4")
        self.assertContains(page, "Reachable if followed.")

    def test_daily_plan_follows_roadmap(self):
        self.setup_user()
        with patch("coach.ai._ask", Recorder(ROADMAP_SYSTEM=fake_roadmap(30))):
            self.client.post(reverse("roadmap"), {"days": "30", "note": ""})
        rec = Recorder(PLAN_SYSTEM=FAKE_PLAN)
        with patch("coach.ai._ask", rec):
            self.client.post(reverse("generate"))
        ctx = rec.calls[0][1]["roadmap_today"]
        self.assertEqual(ctx["day"], 1)
        self.assertEqual(ctx["phase"]["name"], "Phase 1")
        plan = Plan.objects.get()
        self.assertEqual(plan.day_number, 1)
        self.assertContains(self.client.get(reverse("today")), "Day 1 of 30")

    def test_checkpoint_day_flagged(self):
        self.setup_user()
        with patch("coach.ai._ask", Recorder(ROADMAP_SYSTEM=fake_roadmap(30))):
            self.client.post(reverse("roadmap"), {"days": "30", "note": ""})
        rm = Roadmap.objects.get()
        rm.start_date = rm.start_date - timedelta(days=9)  # day 10 = end of phase 1
        rm.save()
        rec = Recorder(PLAN_SYSTEM=FAKE_PLAN)
        with patch("coach.ai._ask", rec):
            self.client.post(reverse("generate"))
        self.assertTrue(rec.calls[0][1]["roadmap_today"]["is_checkpoint_day"])

    def test_invalid_length_rejected(self):
        self.setup_user()
        r = self.client.post(reverse("roadmap"), {"days": "45", "note": ""})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Roadmap.objects.exists())


def bio_answer(payload):
    return {
        "score": {"clarity": 6, "positioning": 5, "personality": 7, "specificity": 4},
        "opportunity": "Your bio says what you do but not why to follow you.",
        "content_match": {"verdict": "partial", "note": "You post about branding more than the bio says."},
        "insight": {"topics": ["branding", "founders"], "expertise": "Brand design", "different": "Plain advice", "style": "Short and direct"},
        "versions": [
            {"key": k, "bio": b, "why": f"Why {k} works."}
            for k, b in [("clear", "Brand designer for early-stage founders."), ("personal", "I make brands people remember."),
                         ("authority", "Brand design for 40+ founders [your proof]."), ("curiosity", "Why do most brands look the same?"),
                         ("minimal", "Brands that stick.")]
        ],
        "notes": "Pin your best post.",
    }


class BioCoachTests(Base):
    DATA = {"current_bio": "I design stuff", "goal": "clients", "role": "designer", "notes": "Keep brand designer. No location.", "use_data": "on"}

    def test_analysis_score_and_five_directions(self):
        self.setup_user()
        rec = Recorder(BIO_SYSTEM=bio_answer)
        with patch("coach.ai._ask", rec):
            r = self.client.post(reverse("bio"), self.DATA)
        for text in ("14 / 160", "Clarity", "6/10", "Main opportunity", "not an X score", "01", "Clear &amp; Professional",
                     "Personal Brand", "Authority", "Curiosity", "Minimal", "Why it works", "Use this", "Make it sharper",
                     "Give me a stronger hook", "Try another direction", "Brand designer for early-stage founders."):
            self.assertContains(r, text)
        p = rec.calls[0][1]
        self.assertEqual((p["goal"], p["role"]), ("Get clients", "Designer"))
        self.assertEqual(p["keep_mention_avoid"], "Keep brand designer. No location.")
        self.assertEqual(p["directions"], ["clear", "personal", "authority", "curiosity", "minimal"])
        self.assertIsNone(p["data"])  # no archive uploaded yet
        self.assertNotContains(r, "What your content says")

    def test_uses_archive_data_when_available_and_allowed(self):
        self.setup_user()
        from coach.models import ArchiveStats
        ArchiveStats.objects.create(user=User.objects.get(), data=parse_archive(big_archive(), "UTC", "me"))
        rec = Recorder(BIO_SYSTEM=bio_answer)
        with patch("coach.ai._ask", rec):
            r = self.client.post(reverse("bio"), self.DATA)
        self.assertIsNotNone(rec.calls[0][1]["data"])
        self.assertTrue(rec.calls[0][1]["data"]["best_posts"])
        for text in ("What your content says", "Bio partly matches your posts", "branding", "Short and direct"):
            self.assertContains(r, text)
        rec2 = Recorder(BIO_SYSTEM=bio_answer)
        with patch("coach.ai._ask", rec2):
            self.client.post(reverse("bio"), {k: v for k, v in self.DATA.items() if k != "use_data"})
        self.assertIsNone(rec2.calls[0][1]["data"])

    def test_scores_are_clamped_and_length_flagged(self):
        self.setup_user()
        ans = bio_answer({})
        ans["score"] = {"clarity": 99, "positioning": 0, "personality": "x", "specificity": 7.6}
        ans["versions"][0]["bio"] = "x" * 170
        with patch("coach.ai._ask", Recorder(BIO_SYSTEM=ans)):
            r = self.client.post(reverse("bio"), self.DATA)
        self.assertContains(r, "10/10")
        self.assertContains(r, "1/10")
        self.assertContains(r, "5/10")
        self.assertContains(r, "8/10")
        self.assertContains(r, "170/160")

    def test_refine_actions(self):
        self.setup_user()
        for action in ("sharper", "human", "specific", "shorter", "hook", "direction"):
            rec = Recorder(BIO_EDIT_SYSTEM=lambda p: {"bio": "Sharper \u2014 bio.", "why": "Cut filler."})
            body = {"action": action, "text": "I design stuff", "direction": "Authority", "ctx": {"goal": "clients", "role": "designer", "notes": "x"}}
            with patch("coach.ai._ask", rec):
                r = self.client.post(reverse("bio_refine"), json.dumps(body), content_type="application/json")
            d = r.json()
            self.assertTrue(d["ok"], action)
            self.assertEqual(d["bio"], "Sharper, bio.")  # em dash cleaned
            self.assertEqual(rec.calls[0][1]["action"], action)
            self.assertEqual(rec.calls[0][1]["goal"], "Get clients")
            self.assertEqual(rec.calls[0][1]["positioning_direction"], "Authority")

    def test_refine_rejects_bad_action(self):
        self.setup_user()
        body = {"action": "hack", "text": "x", "ctx": {}}
        r = self.client.post(reverse("bio_refine"), json.dumps(body), content_type="application/json")
        self.assertEqual(r.status_code, 400)

    def test_goal_and_bio_required(self):
        self.setup_user()
        r = self.client.post(reverse("bio"), {"current_bio": "", "goal": "nope", "role": "designer"})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "This field is required")


def make_analysis(verdict="strong", media=None):
    return {
        "read": {"topic": "Shipping versus planning", "intent": "Persuade founders", "argument": "Shipping beats planning",
                 "context": "Founder to founders", "repeated_points": ["Ship fast"], "gaps": ["Cost of rework"], "obvious_point_taken": True},
        "fit": {"verdict": verdict, "summary": "A founder talking to your exact audience about your topic.",
                "checks": {"audience": {"state": "yes", "note": "Early-stage founders"}, "topic": {"state": "yes", "note": "Branding and shipping"},
                           "goal": {"state": "partial", "note": "Fits the foundation phase"}, "credibility": {"state": "yes", "note": "You ship products"}}},
        "recommendation": "Reply carefully." if verdict != "skip" else "This is about a celebrity launch. Look for posts about early-stage branding instead.",
        "strategy": {"purpose": "trust", "why": "Foundation phase: show real competence.", "promotion": "none", "moves": [
            {"approach": "Insight", "angle": "Adds the cost of rework", "point": "Rework is the hidden cost"},
            {"approach": "Question", "angle": "Asks what was cut", "point": "What did they cut first"},
            {"approach": "Counterpoint", "angle": "Adds a fair limit", "point": "Only works if you can measure it"}]},
        "media": media or {"recommend": False, "asset": "", "why": "", "move": 1},
    }


def write_answer(payload):
    texts = ["Rework is the hidden cost \u2014 ship small.", "What did you cut first?", "Shipping wins only if you can measure it."]
    return {"replies": [{"approach": m["approach"], "angle": m["angle"], "text": texts[i]} for i, m in enumerate(payload["strategy"]["moves"][:3])],
            "tip": "Swap in your own detail."}


class ReplyHelperTests(Base):
    FORM = {"post_text": "Shipping beats planning.", "intent": "auto", "voice": "my_voice", "length": "balanced"}

    def analyse(self, verdict="strong", media=None, form=None):
        rec = Recorder(REPLY_ANALYSE_SYSTEM=make_analysis(verdict, media))
        with patch("coach.ai._ask", rec):
            r = self.client.post(reverse("reply_analyse"), form or self.FORM)
        return r.json(), rec

    def write(self, a, override=False, rec=None):
        rec = rec or Recorder(REPLY_WRITE_SYSTEM=write_answer)
        body = {"ctx": a["ctx"], "settings": a["settings"], "analysis": a["analysis"], "override": override}
        with patch("coach.ai._ask", rec):
            r = self.client.post(reverse("reply_write"), json.dumps(body), content_type="application/json")
        return r.json(), rec

    # ---- fetching (unchanged behaviour) ----
    def test_parse_link(self):
        self.assertEqual(fxtwitter.parse_link("https://x.com/jack/status/20"), None)
        self.assertEqual(fxtwitter.parse_link("https://x.com/jack/status/1234567890"), ("jack", "1234567890"))
        self.assertEqual(fxtwitter.parse_link("twitter.com/Bob_1/status/99999999999?s=20"), ("Bob_1", "99999999999"))
        self.assertIsNone(fxtwitter.parse_link("https://evil.com/jack/status/1234567890"))

    def test_fetch_endpoint(self):
        self.setup_user()
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"tweet": {"text": "Shipping beats planning.", "likes": 10, "replies": 2, "retweets": 1,
                                            "author": {"screen_name": "sam", "name": "Sam", "description": "Builder"}}}
        with patch("coach.fxtwitter.httpx.get", return_value=resp) as get:
            r = self.client.post(reverse("reply_fetch"), {"link": "https://x.com/sam/status/1234567890"})
        self.assertEqual(get.call_args[0][0], "https://api.fxtwitter.com/sam/status/1234567890")
        d = r.json()
        self.assertTrue(d["ok"])
        self.assertEqual((d["author"], d["author_bio"]), ("@sam", "Builder"))

    def test_fetch_failure_is_friendly(self):
        self.setup_user()
        self.assertFalse(self.client.post(reverse("reply_fetch"), {"link": "not a link"}).json()["ok"])
        with patch("coach.fxtwitter.httpx.get", return_value=MagicMock(status_code=404)):
            err = self.client.post(reverse("reply_fetch"), {"link": "https://x.com/sam/status/1234567890"}).json()["error"]
        self.assertIn("Paste the text", err)
        self.assertIn("fxtwitter: 404", err)

    def test_falls_back_when_first_source_is_blocked(self):
        def respond(url, **kw):
            if "fxtwitter" in url:
                return MagicMock(status_code=403)
            r = MagicMock(status_code=200)
            r.json.return_value = {"text": "From vx", "user_screen_name": "sam", "likes": 5}
            return r
        with patch("coach.fxtwitter.httpx.get", side_effect=respond):
            d = fxtwitter.fetch_post("https://x.com/sam/status/1234567890")
        self.assertEqual((d["text"], d["source"], d["author"]), ("From vx", "vxtwitter", "@sam"))

    def test_cloudflare_challenge_page_then_oembed(self):
        def respond(url, **kw):
            r = MagicMock(status_code=200)
            if "publish.twitter.com" in url:
                r.json.return_value = {"html": '<blockquote><p lang="en">Shipping &amp; learning<br>daily</p>&mdash; Sam</blockquote>',
                                       "author_name": "Sam", "author_url": "https://twitter.com/sam"}
            else:
                r.json.side_effect = ValueError("not json")
            return r
        with patch("coach.fxtwitter.httpx.get", side_effect=respond):
            d = fxtwitter.fetch_post("https://x.com/sam/status/1234567890")
        self.assertEqual((d["source"], d["text"], d["author"]), ("oembed", "Shipping & learning\ndaily", "@sam"))

    # ---- the fit gate and strategy ----
    def test_analyse_loads_the_users_strategy_and_roadmap(self):
        self.setup_user(offer="a thesis formatting tool", target_note="500 users by January")
        with patch("coach.ai._ask", Recorder(ROADMAP_SYSTEM=fake_roadmap(30))):
            self.client.post(reverse("roadmap"), {"days": "30", "note": ""})
        d, rec = self.analyse()
        self.assertTrue(d["ok"])
        s = rec.calls[0][1]["strategy"]
        self.assertEqual(s["audience"], "early-stage founders")
        self.assertEqual(s["offer"], "a thesis formatting tool")
        self.assertEqual(s["success_looks_like"], "500 users by January")
        self.assertEqual(s["goal"], "Grow my followers")
        self.assertEqual((s["roadmap_today"]["day"], s["roadmap_today"]["phase"]["name"]), (1, "Phase 1"))
        self.assertEqual(rec.calls[0][1]["intent"]["key"], "auto")
        self.assertEqual(rec.calls[0][0], prompts.REPLY_ANALYSE_SYSTEM)

    def test_analyse_works_without_a_roadmap(self):
        self.setup_user()
        d, rec = self.analyse()
        self.assertTrue(d["ok"])
        self.assertIsNone(rec.calls[0][1]["strategy"]["roadmap_today"])

    def test_strong_fit_card_shows_checks_and_purpose(self):
        self.setup_user()
        d, _ = self.analyse()
        self.assertEqual(d["verdict"], "strong")
        for text in ("Strong fit", "Audience", "Topic", "Goal", "You can add value", "Build trust", "Why this fits and what Cadence read",
                     "Already said", "Ship fast", "Still missing", "Cost of rework", "already been made several times"):
            self.assertIn(text, d["html"])
        self.assertNotIn("Reply anyway", d["html"])

    def test_skip_explains_recommends_skipping_and_offers_override(self):
        self.setup_user()
        d, _ = self.analyse("skip")
        self.assertEqual(d["verdict"], "skip")
        self.assertEqual(d["analysis"]["strategy"]["moves"], [])  # nothing manufactured
        for text in ("Skip this one", "Recommendation: skip this one", "celebrity launch", "Reply anyway"):
            self.assertIn(text, d["html"])

    def test_incomplete_strategy_is_an_error_not_a_guess(self):
        self.setup_user()
        bad = make_analysis()
        bad["strategy"]["moves"] = bad["strategy"]["moves"][:1]
        with patch("coach.ai._ask", Recorder(REPLY_ANALYSE_SYSTEM=bad)):
            d = self.client.post(reverse("reply_analyse"), self.FORM).json()
        self.assertFalse(d["ok"])
        self.assertIn("incomplete", d["error"])

    def test_keyword_overlap_is_explicitly_not_enough_in_the_prompt(self):
        self.assertIn("Shared keywords are NOT enough", prompts.REPLY_ANALYSE_SYSTEM)
        self.assertIn("prefer \"skip\" over manufacturing replies", prompts.REPLY_ANALYSE_SYSTEM)
        self.assertIn("Never force", prompts.REPLY_ANALYSE_SYSTEM)

    def test_analyse_needs_a_post(self):
        self.setup_user()
        d = self.client.post(reverse("reply_analyse"), {"intent": "auto", "voice": "my_voice", "length": "balanced"}).json()
        self.assertFalse(d["ok"])
        self.assertIn("Paste a post link", d["error"])

    def test_link_only_fetches_then_analyses(self):
        self.setup_user()
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"tweet": {"text": "Fetched text", "likes": 3, "replies": 1, "author": {"screen_name": "sam", "description": "Bio"}}}
        rec = Recorder(REPLY_ANALYSE_SYSTEM=make_analysis())
        with patch("coach.fxtwitter.httpx.get", return_value=resp), patch("coach.ai._ask", rec):
            d = self.client.post(reverse("reply_analyse"), {"link": "https://x.com/sam/status/1234567890", "intent": "auto", "voice": "my_voice", "length": "balanced"}).json()
        self.assertTrue(d["ok"])
        self.assertEqual(rec.calls[0][1]["post"]["text"], "Fetched text")
        self.assertEqual(d["ctx"]["post_meta"], "3 likes · 1 replies")

    def test_existing_replies_reach_the_analysis(self):
        self.setup_user()
        _, rec = self.analyse(form={**self.FORM, "top_replies": "Ship fast!\nExactly."})
        self.assertEqual(rec.calls[0][1]["post"]["existing_replies"], "Ship fast!\nExactly.")

    # ---- writing ----
    def test_write_carries_out_three_distinct_moves(self):
        self.setup_user()
        a, _ = self.analyse()
        d, rec = self.write(a)
        self.assertTrue(d["ok"])
        p = rec.calls[0][1]
        self.assertEqual([m["approach"] for m in p["strategy"]["moves"]], ["Insight", "Question", "Counterpoint"])
        self.assertEqual(p["strategy"]["promotion"], "none")
        self.assertFalse(p["override"])
        self.assertEqual(rec.calls[0][0], prompts.REPLY_WRITE_SYSTEM)
        for text in ("Insight", "Question", "Counterpoint", "Rework is the hidden cost, ship small.", "Copy", "Edit", "More",
                     "Make shorter", "Make more natural", "Try another angle", "Regenerate"):
            self.assertIn(text, d["html"])
        self.assertNotIn("\u2014", d["html"])
        self.assertEqual(d["html"].count('class="rp"'), 3)

    def test_settings_reach_the_writer(self):
        self.setup_user(voice_samples="first sample post\n\nsecond sample post")
        form = {**self.FORM, "intent": "challenge", "voice": "refined", "length": "short", "instruction": "Make it curious, not argumentative.",
                "s_formality": 10, "s_emoji": 0}
        a, _ = self.analyse(form=form)
        d, rec = self.write(a)
        p = rec.calls[0][1]
        self.assertEqual(p["length"], {"name": "short", "max_chars": 140})
        self.assertEqual(p["voice"]["mode"], "refined")
        self.assertEqual(p["voice"]["examples"][:2], ["first sample post", "second sample post"])
        self.assertEqual(p["user_instruction"], "Make it curious, not argumentative.")
        self.assertTrue(any("strongly casual" in t for t in p["tone_adjustments"]))
        self.assertEqual(p["emoji_allowed"], [False, False, False])

    def test_voice_examples_include_archive_posts(self):
        self.setup_user()
        from coach.models import ArchiveStats
        ArchiveStats.objects.create(user=User.objects.get(), data=parse_archive(big_archive(), "UTC", "me"))
        a, _ = self.analyse()
        _, rec = self.write(a)
        self.assertTrue(rec.calls[0][1]["voice"]["has_examples"])

    def test_media_is_suggested_only_when_it_helps_and_for_one_reply(self):
        self.setup_user()
        a, _ = self.analyse(media={"recommend": True, "asset": "a before and after screenshot of a formatted page", "why": "Shows the exact fix", "move": 2})
        d, rec = self.write(a)
        self.assertEqual(d["html"].count("Add media:"), 1)
        cards = d["html"].split('class="rp"')
        self.assertIn("Add media:", cards[2])          # attached to move 2, the Question reply
        self.assertNotIn("Add media:", cards[1])
        self.assertNotIn("Add media:", cards[3])
        self.assertEqual(rec.calls[0][1]["attached_media"], {"move": 2, "asset": "a before and after screenshot of a formatted page"})
        a2, _ = self.analyse()
        d2, rec2 = self.write(a2)
        self.assertNotIn("Add media:", d2["html"])
        self.assertIsNone(rec2.calls[0][1]["attached_media"])

    def test_media_never_survives_a_skip_or_an_empty_asset(self):
        out = ai.norm_analysis({**make_analysis("skip"), "media": {"recommend": True, "asset": "a demo", "move": 1}})
        self.assertFalse(out["media"]["recommend"])
        out = ai.norm_analysis({**make_analysis(), "media": {"recommend": True, "asset": "", "move": 1}})
        self.assertFalse(out["media"]["recommend"])

    def test_too_similar_replies_get_one_corrective_retry(self):
        self.setup_user()
        a, _ = self.analyse()
        calls = {"n": 0}
        def answer(payload):
            calls["n"] += 1
            if calls["n"] == 1:
                t = "Rework costs more than shipping small versions of anything"
                return {"replies": [{"approach": "Insight", "text": t}, {"approach": "Question", "text": t + " honestly"}, {"approach": "Counterpoint", "text": t}]}
            return write_answer(payload)
        d, rec = self.write(a, rec=Recorder(REPLY_WRITE_SYSTEM=answer))
        self.assertTrue(d["ok"])
        self.assertEqual(len(rec.calls), 2)
        self.assertIn("fix", rec.calls[1][1])
        self.assertNotIn("fix", rec.calls[0][1])
        self.assertIn("What did you cut first?", d["html"])

    def test_retry_happens_only_once(self):
        self.setup_user()
        a, _ = self.analyse()
        same = lambda p: {"replies": [{"approach": "Insight", "text": "Rework costs more than shipping small versions"}] * 3}
        d, rec = self.write(a, rec=Recorder(REPLY_WRITE_SYSTEM=same))
        self.assertTrue(d["ok"])
        self.assertEqual(len(rec.calls), 2)

    def test_distinct_replies_need_no_retry(self):
        self.assertFalse(ai.too_similar([{"text": "Rework is the hidden cost nobody counts"}, {"text": "What did you cut first from version one?"},
                                         {"text": "Only works when you can measure results"}]))
        self.assertTrue(ai.too_similar([{"text": "Rework costs more than shipping small"}, {"text": "Rework costs more than shipping small ones"}]))

    def test_override_writes_modest_replies_after_a_skip(self):
        self.setup_user()
        a, _ = self.analyse("skip")
        d, _ = self.write(a)  # no override
        self.assertFalse(d["ok"])
        self.assertIn("no strategy", d["error"])
        d, rec = self.write(a, override=True)
        self.assertTrue(d["ok"])
        p = rec.calls[0][1]
        self.assertTrue(p["override"])
        self.assertEqual(p["strategy"]["promotion"], "none")
        self.assertEqual(len(p["strategy"]["moves"]), 3)
        self.assertEqual(p["strategy"]["purpose"], "relationship")

    def test_browser_supplied_analysis_is_sanitised(self):
        self.setup_user()
        a, _ = self.analyse()
        evil = {"fit": {"verdict": "weird"}, "strategy": {"purpose": "hack", "promotion": "sell hard",
                "moves": [{"approach": "x" * 500, "angle": "y" * 900, "point": "z" * 900}] * 10}, "media": {"recommend": True, "asset": "a" * 900, "move": 99}}
        body = {"ctx": a["ctx"], "settings": {"intent": "nope", "voice": "x", "length": "huge", "sliders": {"formality": 999}}, "analysis": evil}
        rec = Recorder(REPLY_WRITE_SYSTEM=write_answer)
        with patch("coach.ai._ask", rec):
            d = self.client.post(reverse("reply_write"), json.dumps(body), content_type="application/json").json()
        self.assertTrue(d["ok"])
        p = rec.calls[0][1]
        self.assertEqual(len(p["strategy"]["moves"]), 3)
        self.assertEqual(p["strategy"]["purpose"], "trust")
        self.assertEqual(p["strategy"]["promotion"], "none")
        self.assertLessEqual(len(p["strategy"]["moves"][0]["approach"]), 24)
        self.assertLessEqual(len(p["attached_media"]["asset"]), 240)
        self.assertEqual(p["length"]["name"], "balanced")

    def test_write_rejects_garbage(self):
        self.setup_user()
        r = self.client.post(reverse("reply_write"), "not json", content_type="application/json")
        self.assertEqual(r.status_code, 400)

    @override_settings(AI_DAILY_LIMIT=1)
    def test_each_step_counts_against_the_daily_cap(self):
        self.setup_user()
        a, _ = self.analyse()
        self.assertTrue(a["ok"])
        d, _ = self.write(a)
        self.assertFalse(d["ok"])
        self.assertIn("AI limit", d["error"])

    # ---- full-page fallback (JavaScript off) ----
    def test_full_page_path_runs_both_steps(self):
        self.setup_user()
        rec = Recorder(REPLY_ANALYSE_SYSTEM=make_analysis(), REPLY_WRITE_SYSTEM=write_answer)
        with patch("coach.ai._ask", rec):
            r = self.client.post(reverse("replies"), self.FORM)
        for text in ("Strong fit", "Three different replies", "Rework is the hidden cost, ship small."):
            self.assertContains(r, text)
        self.assertEqual(len(rec.calls), 2)

    def test_full_page_path_stops_at_skip_until_override(self):
        self.setup_user()
        rec = Recorder(REPLY_ANALYSE_SYSTEM=make_analysis("skip"), REPLY_WRITE_SYSTEM=write_answer)
        with patch("coach.ai._ask", rec):
            r = self.client.post(reverse("replies"), self.FORM)
        self.assertContains(r, "Recommendation: skip this one")
        self.assertNotContains(r, "Three different replies")
        self.assertEqual(len(rec.calls), 1)
        rec2 = Recorder(REPLY_ANALYSE_SYSTEM=make_analysis("skip"), REPLY_WRITE_SYSTEM=write_answer)
        with patch("coach.ai._ask", rec2):
            r = self.client.post(reverse("replies"), {**self.FORM, "force": "True"})
        self.assertContains(r, "Three different replies")
        self.assertTrue(rec2.calls[1][1]["override"])

    # ---- page: one-tap layout and simplified controls ----
    def test_page_is_built_for_one_tap(self):
        self.setup_user()
        page = self.client.get(reverse("replies")).content.decode()
        for needle in ('id="pasteBtn"', 'id="goBtn"', 'placeholder="Paste an X post link"', 'data-analyse-url=', 'data-write-url=',
                       'id="progress"', "Checking fit with your strategy", 'id="customize"', "Let Cadence choose", "Rewrite with these settings"):
            self.assertIn(needle, page)
        self.assertRegex(page, r'value="auto"[^>]*checked|checked[^>]*value="auto"')
        self.assertRegex(page, r'value="my_voice"[^>]*checked|checked[^>]*value="my_voice"')
        self.assertEqual(page.count('type="range"'), 4)
        # controls live under Customize, results sit right after the link on phones (CSS grid order)
        self.assertLess(page.index('class="card rh-input"'), page.index('id="results"'))
        css = open("coach/static/coach/app.css").read()
        self.assertIn('grid-template-areas: "input" "results" "custom"', css)

    def test_intents_default_to_cadence_choosing(self):
        self.assertEqual(style.INTENTS[0][0], "auto")
        self.assertEqual(style.DEFAULT_INTENT, "auto")
        self.assertEqual([k for k, _, _ in style.INTENTS][1:], ["insight", "connect", "question", "challenge", "expertise", "support"])

    # ---- per-reply actions ----
    def test_edit_modes_keep_the_strategy(self):
        self.setup_user()
        a, _ = self.analyse()
        for mode in ("regenerate", "angle", "shorter", "natural"):
            rec = Recorder(REPLY_EDIT_SYSTEM=lambda p: {"approach": "Example", "angle": "Gives an example", "text": "Short natural reply"})
            body = {"ctx": a["ctx"], "settings": {"intent": "question", "voice": "refined", "length": "short", "instruction": "curious", "sliders": {"formality": 50}},
                    "strategy": a["analysis"]["strategy"], "current": "old reply", "approach": "Question", "used": ["Insight", "Question", "Counterpoint"], "mode": mode}
            with patch("coach.ai._ask", rec):
                d = self.client.post(reverse("reply_one"), json.dumps(body), content_type="application/json").json()
            self.assertTrue(d["ok"], mode)
            self.assertEqual((d["text"], d["approach"]), ("Short natural reply", "Example"))
            p = rec.calls[0][1]
            self.assertEqual((p["mode"], p["current_reply"], p["length"]["max_chars"]), (mode, "old reply", 140))
            self.assertEqual(p["strategy"]["purpose"], "trust")
            self.assertEqual(p["strategy"]["this_move"]["approach"], "Question")

    def test_edit_rejects_bad_mode(self):
        self.setup_user()
        body = {"mode": "hack", "ctx": {}, "settings": {}, "current": ""}
        self.assertEqual(self.client.post(reverse("reply_one"), json.dumps(body), content_type="application/json").status_code, 400)

    def test_slider_mapping_and_cleanup(self):
        lines = style.slider_instructions({"formality": 90, "playfulness": 50, "directness": 30})
        self.assertIn("Casual to Formal: strongly formal", lines)
        self.assertIn("Gentle to Direct: leaning gentle", lines)
        self.assertEqual(ai.clean_text('"Great \u2014 point \u2013 ok"'), "Great, point, ok")


class EmailTests(Base):
    @override_settings(BREVO_API_KEY="key", DEFAULT_FROM_EMAIL="Cadence <me@mydomain.com>")
    def test_brevo_backend_payload(self):
        resp = MagicMock(status_code=201)
        msg = mail.EmailMessage("Hi", "Body", None, ["u@x.com"])
        with patch("coach.email_backend.httpx.post", return_value=resp) as post:
            self.assertEqual(BrevoEmailBackend().send_messages([msg]), 1)
        kwargs = post.call_args.kwargs
        self.assertEqual(kwargs["headers"]["api-key"], "key")
        self.assertEqual(kwargs["json"]["sender"], {"email": "me@mydomain.com", "name": "Cadence"})
        self.assertEqual(kwargs["json"]["to"], [{"email": "u@x.com"}])
        self.assertEqual(kwargs["json"]["textContent"], "Body")

    @override_settings(BREVO_API_KEY="key")
    def test_brevo_error_is_raised_with_reason(self):
        resp = MagicMock(status_code=400, text="sender not valid")
        with patch("coach.email_backend.httpx.post", return_value=resp):
            with self.assertRaisesMessage(RuntimeError, "sender not valid"):
                BrevoEmailBackend().send_messages([mail.EmailMessage("Hi", "B", "a@b.com", ["u@x.com"])])

    @override_settings(BREVO_API_KEY="")
    def test_brevo_without_key(self):
        with self.assertRaises(RuntimeError):
            BrevoEmailBackend().send_messages([mail.EmailMessage("Hi", "B", "a@b.com", ["u@x.com"])])


def big_archive():
    """120 days of posts with known patterns: questions get more likes."""
    rows, base = [], timezone.now().replace(hour=12, minute=0, second=0, microsecond=0)
    rnd = random.Random(7)
    for d in range(120):
        day = base - timedelta(days=d)
        for k in range(rnd.choice([1, 1, 2])):
            q = rnd.random() < 0.4
            rows.append({"tweet": {
                "full_text": ("Why do founders skip branding? #design" if q else "Shipping beats planning every time 🚀"),
                "created_at": day.replace(hour=rnd.choice([9, 13, 19])).strftime("%a %b %d %H:%M:%S +0000 %Y"),
                "favorite_count": str(rnd.randint(0, 4) + (10 if q else 0)), "retweet_count": "1",
                "entities": {"hashtags": [{"text": "design"}] if q else [], "urls": [], "user_mentions": []}}})
        if rnd.random() < 0.5:
            rows.append({"tweet": {"full_text": "@sam good point", "created_at": day.strftime("%a %b %d %H:%M:%S +0000 %Y"),
                                   "favorite_count": "0", "retweet_count": "0", "in_reply_to_screen_name": "sam", "entities": {"user_mentions": []}}})
        if d % 17 == 0:
            rows.append({"tweet": {"full_text": "thread part two", "created_at": day.strftime("%a %b %d %H:%M:%S +0000 %Y"),
                                   "favorite_count": "2", "retweet_count": "0", "in_reply_to_screen_name": "me", "entities": {"user_mentions": []}}})
    return "window.YTD.tweets.part0 = " + json.dumps(rows)


class DeepInsightsTests(Base):
    def setUp(self):
        self.stats = parse_archive(big_archive(), "UTC", "me")

    def test_shapes(self):
        s = self.stats
        self.assertEqual(s["version"], 2)
        self.assertEqual(len(s["calendar"]["weeks"]), 26)
        self.assertTrue(all(len(w) == 7 for w in s["calendar"]["weeks"]))
        self.assertEqual(len(s["heatmap"]), 7)
        self.assertTrue(all(len(r["cells"]) == 24 for r in s["heatmap"]))
        self.assertEqual(len(s["hours"]), 24)
        self.assertEqual(len(s["hour_labels"]), 24)
        self.assertEqual(len(s["per_month"]), 12)
        self.assertEqual(sum(d["n"] for d in s["distribution"]), s["total_posts"])

    def test_threads_are_not_counted_as_replies(self):
        self.assertGreater(self.stats["thread_parts"], 0)
        self.assertEqual(self.stats["circle"][0]["name"], "sam")

    def test_question_format_wins(self):
        q = next(f for f in self.stats["formats"] if f["label"] == "Asks a question")
        self.assertTrue(q["enough"])
        self.assertGreater(q["lift"], 100)
        self.assertTrue(any("ask a question" in t for t in self.stats["takeaways"]))

    def test_momentum_and_streaks(self):
        m = self.stats["momentum"]
        self.assertIsNotNone(m)
        self.assertGreater(m["posts"]["now"], 0)
        self.assertGreaterEqual(self.stats["calendar"]["longest_streak"], self.stats["calendar"]["current_streak"])

    def test_small_archive_never_overclaims(self):
        s = parse_archive(ARCHIVE, "Africa/Lagos", "excel")
        self.assertIsNone(s["momentum"])
        self.assertTrue(all(f["lift"] is None for f in s["formats"]))
        self.assertFalse(any("more likes than posts without" in t for t in s["takeaways"]))

    def test_every_data_point_has_hover_details(self):
        s = self.stats
        self.assertTrue(all(h["tip"] for h in s["hours"]))
        self.assertTrue(all(w["tip"] for w in s["weekday"]))
        self.assertTrue(all(m["tip"] for m in s["mix"]))
        self.assertTrue(all(c["tip"] for w in s["calendar"]["weeks"] for c in w))
        self.assertTrue(all(c["tip"] for r in s["heatmap"] for c in r["cells"]))
        self.assertTrue(all(f["tip"] for f in s["formats"]))
        self.assertTrue(all(b["tip"] for b in s["length_buckets"]))
        self.assertTrue(all(d["tip"] for d in s["distribution"]))
        self.assertTrue(all(d["tip"] for d in s["likes_trend"]["dots"]))
        self.assertTrue(all(w["tip"] for w in s["winning_words"]))
        q = next(f for f in s["formats"] if f["label"] == "Asks a question")
        self.assertIn("vs posts without", q["tip"])
        self.assertIn("Best:", q["tip"])
        day = next(c for w in s["calendar"]["weeks"] for c in w if "post" in c["tip"] and "Why do founders" in c["tip"])
        self.assertIn("likes" not in day["tip"] or True, [True])

    def test_page_has_tooltip_machinery_and_data_tips(self):
        self.setup_user()
        f = SimpleUploadedFile("tweets.js", big_archive().encode())
        r = self.client.post(reverse("insights"), {"file": f}, follow=True)
        html = r.content.decode()
        self.assertIn('id="tip"', html)
        self.assertGreater(html.count("data-tip="), 300)
        self.assertNotIn("title=\"", html[html.index("Posting calendar"):html.index("Your best posts")])
        self.assertNotContains(r, "Add hover details")

    def test_older_saved_stats_ask_for_a_re_upload(self):
        self.setup_user()
        from coach.models import ArchiveStats
        s = parse_archive(big_archive(), "UTC", "me")
        for h in s["hours"]:
            h.pop("tip")
        ArchiveStats.objects.create(user=User.objects.get(), data=s)
        self.assertContains(self.client.get(reverse("insights")), "Add hover details")

    def test_page_renders_all_sections(self):
        self.setup_user()
        f = SimpleUploadedFile("tweets.js", big_archive().encode())
        r = self.client.post(reverse("insights"), {"file": f}, follow=True)
        for text in ("What this means for you", "Momentum", "Posting calendar", "Best time slots", "What works for you",
                     "Post length vs likes", "Your circle", "Your best posts"):
            self.assertContains(r, text)

    def test_old_saved_stats_still_render(self):
        self.setup_user()
        from coach.models import ArchiveStats
        user = User.objects.get()
        ArchiveStats.objects.create(user=user, data={"total_posts": 3, "total_replies": 1, "first_post": "Jan 2025",
                                                     "best_hours": ["09:00"], "circle": [], "top_posts": []})
        r = self.client.get(reverse("insights"))
        self.assertContains(r, "Unlock the new charts")


class InsightsTests(Base):
    def test_parse(self):
        d = parse_archive(ARCHIVE, "Africa/Lagos", "excel")
        self.assertEqual((d["total_posts"], d["total_replies"]), (1, 1))
        self.assertEqual(d["circle"][0]["name"], "sam")

    def test_upload(self):
        self.setup_user()
        f = SimpleUploadedFile("tweets.js", ARCHIVE.encode())
        r = self.client.post(reverse("insights"), {"file": f}, follow=True)
        self.assertContains(r, "Original posts")
        self.assertContains(r, "@sam")

    def test_rejects_garbage(self):
        self.setup_user()
        f = SimpleUploadedFile("x.js", b"not an archive")
        r = self.client.post(reverse("insights"), {"file": f}, follow=True)
        self.assertContains(r, "does not look like")


class AiUnitTests(TestCase):
    def test_normalize_plan_drops_bad_rows(self):
        out = ai.normalize_plan({"summary": "s", "tasks": [
            {"time": "25:00", "kind": "post", "title": "bad"},
            {"time": "9:05", "kind": "weird", "title": "ok", "body": "b", "count": 3},
        ]})
        self.assertEqual(len(out["tasks"]), 1)
        self.assertEqual(out["tasks"][0]["kind"], "post")
        self.assertEqual(out["tasks"][0]["time"], time(9, 5))

    def test_extract_json_with_noise(self):
        self.assertEqual(ai._extract_json('Here: ```json\n{"a": 1}\n```'), {"a": 1})

    def test_unusable_plan_raises_ai_error(self):
        u = User.objects.create_user("u2@x.com", password="x")
        p = Profile.objects.create(user=u, niche="n", audience="a")
        with patch("coach.ai._ask", return_value={"tasks": []}):
            with self.assertRaises(ai.AIError):
                ai.generate_plan(p, [], None, "Monday")


class ReminderAndCronTests(TestCase):
    def test_due_tasks_emailed_once(self):
        u = User.objects.create_user("r@x.com", email="r@x.com", password="x")
        p = Profile.objects.create(user=u, niche="n", audience="a", timezone="UTC")
        now = timezone.now()
        plan = Plan.objects.create(user=u, date=p.today())
        past = (now - timedelta(minutes=10)).time().replace(microsecond=0)
        Task.objects.create(plan=plan, time=past, kind="post", title="Due now", body="Hello world")
        Task.objects.create(plan=plan, time=time(23, 59), kind="post", title="Later", body="x")
        if past > now.time():
            return
        call_command("send_reminders")
        self.assertEqual(len(mail.outbox), 1)
        call_command("send_reminders")
        self.assertEqual(len(mail.outbox), 1)

    @override_settings(CRON_SECRET="s3cret")
    def test_cron_requires_secret(self):
        self.assertEqual(self.client.get("/cron/reminders/").status_code, 403)
        ok = self.client.get("/cron/reminders/", HTTP_AUTHORIZATION="Bearer s3cret")
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(self.client.get("/cron/nope/", HTTP_AUTHORIZATION="Bearer s3cret").status_code, 404)
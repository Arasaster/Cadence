from datetime import datetime
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import models
from django.utils import timezone

from .style import ACCOUNT_TYPE_CHOICES


class Profile(models.Model):
    GOALS = [
        ("followers", "Grow my followers"),
        ("authority", "Build authority in my field"),
        ("leads", "Get clients or leads"),
        ("sales", "Sell a product"),
        ("community", "Build a community"),
    ]
    TONES = [
        ("casual", "Casual and friendly"),
        ("expert", "Expert and direct"),
        ("funny", "Funny and witty"),
        ("story", "Storyteller"),
        ("bold", "Bold and opinionated"),
    ]

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    account_type = models.CharField(max_length=20, choices=ACCOUNT_TYPE_CHOICES, default="individual")
    x_username = models.CharField(max_length=30, blank=True)
    current_followers = models.PositiveIntegerField(null=True, blank=True)
    target_followers = models.PositiveIntegerField(null=True, blank=True)
    target_note = models.CharField(max_length=300, blank=True)
    niche = models.CharField(max_length=200)
    goal = models.CharField(max_length=20, choices=GOALS, default="followers")
    audience = models.CharField(max_length=200)
    offer = models.CharField(max_length=200, blank=True)
    tone = models.CharField(max_length=20, choices=TONES, default="casual")
    admired = models.CharField(max_length=300, blank=True)
    voice_samples = models.TextField(blank=True)
    daily_minutes = models.PositiveSmallIntegerField(default=45)
    posts_per_day = models.PositiveSmallIntegerField(default=2)
    reply_target = models.PositiveSmallIntegerField(default=20)
    timezone = models.CharField(max_length=60, default="Africa/Lagos")
    email_reminders = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.user} ({self.niche})"

    @property
    def zone(self):
        try:
            return ZoneInfo(self.timezone)
        except Exception:
            return ZoneInfo("UTC")

    def now_local(self):
        return timezone.now().astimezone(self.zone)

    def today(self):
        return self.now_local().date()


class Roadmap(models.Model):
    """A 30 or 60 day plan from where the account is now to where the user wants it."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="roadmaps")
    days = models.PositiveSmallIntegerField(default=30)
    start_date = models.DateField()
    summary = models.TextField(blank=True)
    baseline = models.TextField(blank=True)
    target = models.TextField(blank=True)
    reality_check = models.TextField(blank=True)
    phases = models.JSONField(default=list)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def day_number(self, today):
        return (today - self.start_date).days + 1

    def phase_for(self, day):
        for ph in self.phases:
            if ph["start_day"] <= day <= ph["end_day"]:
                return ph
        return None


class AIUsage(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="ai_usage")
    date = models.DateField()
    count = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ("user", "date")


class Plan(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="plans")
    roadmap = models.ForeignKey(Roadmap, null=True, blank=True, on_delete=models.SET_NULL, related_name="daily_plans")
    day_number = models.PositiveSmallIntegerField(null=True, blank=True)
    date = models.DateField()
    summary = models.TextField(blank=True)
    source = models.CharField(max_length=10, default="ai")  # ai or demo
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("user", "date")
        ordering = ["-date"]

    def __str__(self):
        return f"{self.user} {self.date}"

    @property
    def progress(self):
        total = self.tasks.count()
        done = self.tasks.filter(done=True).count()
        return done, total


class Task(models.Model):
    KINDS = [
        ("post", "Post"),
        ("thread", "Thread"),
        ("reply_session", "Replies"),
        ("engage", "Engage"),
        ("review", "Review"),
    ]
    plan = models.ForeignKey(Plan, on_delete=models.CASCADE, related_name="tasks")
    time = models.TimeField()
    kind = models.CharField(max_length=20, choices=KINDS, default="post")
    title = models.CharField(max_length=160)
    body = models.TextField(blank=True)
    why = models.CharField(max_length=320, blank=True)
    count = models.PositiveSmallIntegerField(null=True, blank=True)
    done = models.BooleanField(default=False)
    reminded = models.BooleanField(default=False)

    class Meta:
        ordering = ["time", "id"]

    def __str__(self):
        return f"{self.time:%H:%M} {self.title}"

    def due_at(self):
        tz = self.plan.user.profile.zone
        return datetime.combine(self.plan.date, self.time, tzinfo=tz)


class ArchiveStats(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="archive")
    data = models.JSONField(default=dict)
    uploaded_at = models.DateTimeField(auto_now=True)

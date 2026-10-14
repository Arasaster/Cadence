from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand

from coach.ai import AIError
from coach.models import Plan, Profile
from coach.views import _build_plan


class Command(BaseCommand):
    help = "Create today's plan for every user who doesn't have one yet. Run once each morning (cron)."

    def add_arguments(self, parser):
        parser.add_argument("--email", action="store_true", help="Also email a morning summary")

    def handle(self, *args, **opts):
        made = 0
        for profile in Profile.objects.select_related("user"):
            if Plan.objects.filter(user=profile.user, date=profile.today()).exists():
                continue
            try:
                plan = _build_plan(profile.user, profile)
            except AIError as exc:
                self.stderr.write(f"{profile.user.email}: {exc}")
                continue
            made += 1
            if opts["email"] and profile.email_reminders and profile.user.email:
                lines = [f"{t.time:%H:%M}  {t.title}" for t in plan.tasks.all()]
                send_mail(
                    f"Your plan for today ({plan.date:%a %d %b})",
                    f"{plan.summary}\n\n" + "\n".join(lines) + f"\n\nOpen your plan: {settings.SITE_URL}/today/",
                    None,
                    [profile.user.email],
                )
        self.stdout.write(self.style.SUCCESS(f"Created {made} plan(s)"))

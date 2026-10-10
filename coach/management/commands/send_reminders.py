from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand

from coach.models import Profile, Task
from coach.templatetags.coach_extras import blocks


class Command(BaseCommand):
    help = "Email reminders for tasks that are due. Run every 5 to 10 minutes (cron)."

    def handle(self, *args, **opts):
        sent = 0
        for profile in Profile.objects.filter(email_reminders=True).select_related("user"):
            now = profile.now_local()
            tasks = Task.objects.filter(
                plan__user=profile.user, plan__date=now.date(), done=False, reminded=False
            ).select_related("plan")
            for task in tasks:
                due = task.due_at()
                if due > now:
                    continue
                task.reminded = True
                task.save(update_fields=["reminded"])
                if now - due > timedelta(hours=2):
                    continue  # too late to be useful, skip silently
                text = task.body
                if task.kind in ("post", "thread"):
                    text = "\n\n".join(blocks(task.body))
                send_mail(
                    f"Time to: {task.title}",
                    f"{text}\n\nWhy: {task.why}\n\nTick it off: {settings.SITE_URL}/today/",
                    None,
                    [profile.user.email],
                )
                sent += 1
        self.stdout.write(self.style.SUCCESS(f"Sent {sent} reminder(s)"))

"""Send email through Brevo's HTTPS API (port 443), so it works where SMTP ports are blocked."""
from email.utils import parseaddr

import httpx
from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend


class BrevoEmailBackend(BaseEmailBackend):
    endpoint = "https://api.brevo.com/v3/smtp/email"

    def send_messages(self, email_messages):
        if not settings.BREVO_API_KEY:
            if self.fail_silently:
                return 0
            raise RuntimeError("BREVO_API_KEY is not set")
        sent = 0
        for m in email_messages:
            name, addr = parseaddr(m.from_email or settings.DEFAULT_FROM_EMAIL)
            sender = {"email": addr}
            if name:
                sender["name"] = name
            payload = {
                "sender": sender,
                "to": [{"email": r} for r in m.to],
                "subject": m.subject,
                "textContent": m.body,
            }
            try:
                resp = httpx.post(
                    self.endpoint,
                    json=payload,
                    headers={"api-key": settings.BREVO_API_KEY, "accept": "application/json"},
                    timeout=15,
                )
            except httpx.HTTPError as exc:
                if self.fail_silently:
                    continue
                raise RuntimeError(f"Could not reach Brevo: {exc}") from exc
            if resp.status_code >= 300:
                if self.fail_silently:
                    continue
                raise RuntimeError(f"Brevo rejected the email ({resp.status_code}): {resp.text[:200]}")
            sent += 1
        return sent

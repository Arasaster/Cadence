import os
from pathlib import Path

from django.conf import settings

_STATIC = Path(__file__).resolve().parent / "static" / "coach"


def _asset_version():
    """Changes whenever app.css or app.js change, so browsers never keep stale copies."""
    try:
        return int(max(os.path.getmtime(_STATIC / f) for f in ("app.css", "app.js")))
    except OSError:
        return 0


def app_name(request):
    return {
        "APP_NAME": settings.APP_NAME,
        "COMPANY_NAME": settings.COMPANY_NAME,
        "CONTACT_EMAIL": settings.CONTACT_EMAIL,
        "GOVERNING_LAW": settings.GOVERNING_LAW,
        "ASSET_V": _asset_version(),
    }

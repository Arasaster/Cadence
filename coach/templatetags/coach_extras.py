from urllib.parse import quote

from django import template

register = template.Library()


@register.filter
def blocks(text):
    """Split thread text on lines that contain only ---."""
    parts = [p.strip() for p in (text or "").replace("\r", "").split("\n---\n")]
    return [p for p in parts if p]


@register.filter
def intent_url(text):
    return "https://x.com/intent/post?text=" + quote(text or "", safe="")


@register.filter
def group(kind):
    return {"post": "post", "thread": "post", "reply_session": "reply"}.get(kind, "other")


@register.filter
def initial(text):
    text = (text or "?").strip().lstrip("@")
    return text[:1].upper() or "?"


@register.filter
def letter(kind):
    return {"post": "P", "thread": "T", "reply_session": "R", "engage": "E", "review": "V"}.get(kind, "•")

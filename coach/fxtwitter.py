"""Read one public X post from its link, trying several free sources in turn.

1. FixTweet API  2. VxTwitter API  3. X's own public oEmbed endpoint (post text and author only)
These are unofficial/public services and any of them can block or change at any time.
Only fixed hosts are ever contacted, built from a handle and numeric id we parse ourselves.
"""
import html
import re

import httpx
from django.conf import settings

LINK = re.compile(
    r"(?:https?://)?(?:www\.|mobile\.)?(?:x|twitter|fxtwitter|fixupx|vxtwitter)\.com/([A-Za-z0-9_]{1,15})/status/(\d{5,25})"
)


class FetchError(Exception):
    pass


def parse_link(url):
    m = LINK.search(url or "")
    return (m.group(1), m.group(2)) if m else None


def _headers():
    return {"User-Agent": f"Cadence/1.0 (reply helper; {settings.CONTACT_EMAIL})", "Accept": "application/json"}


def _get(url):
    resp = httpx.get(url, timeout=10, headers=_headers(), follow_redirects=True)
    if resp.status_code != 200:
        raise FetchError(str(resp.status_code))
    try:
        return resp.json()
    except ValueError:  # e.g. a Cloudflare challenge page
        raise FetchError("not JSON")


def _fxtwitter(handle, post_id):
    t = _get(f"https://api.fxtwitter.com/{handle}/status/{post_id}")["tweet"]
    a = t.get("author") or {}
    text = t.get("text") or ""
    quote = (t.get("quote") or {}).get("text")
    if quote:
        text += f"\n\n[Quoted post: {quote}]"
    return {
        "text": text, "author": a.get("screen_name") or handle, "author_name": a.get("name") or "",
        "author_bio": a.get("description") or "", "likes": t.get("likes"), "replies": t.get("replies"),
        "reposts": t.get("retweets"),
    }


def _vxtwitter(handle, post_id):
    t = _get(f"https://api.vxtwitter.com/{handle}/status/{post_id}")
    return {
        "text": t.get("text") or "", "author": t.get("user_screen_name") or handle, "author_name": t.get("user_name") or "",
        "author_bio": "", "likes": t.get("likes"), "replies": t.get("replies"), "reposts": t.get("retweets"),
    }


def _oembed(handle, post_id):
    data = _get(
        "https://publish.twitter.com/oembed?omit_script=1&dnt=1&url="
        f"https://twitter.com/{handle}/status/{post_id}"
    )
    m = re.search(r"<p[^>]*>(.*?)</p>", data.get("html", ""), re.S)
    if not m:
        raise FetchError("no text")
    text = re.sub(r"<br\s*/?>", "\n", m.group(1))
    text = html.unescape(re.sub(r"<[^>]+>", "", text)).strip()
    url_handle = (data.get("author_url") or "").rstrip("/").rsplit("/", 1)[-1] or handle
    return {
        "text": text, "author": url_handle, "author_name": data.get("author_name") or "", "author_bio": "",
        "likes": None, "replies": None, "reposts": None,
    }


PROVIDERS = [("fxtwitter", _fxtwitter), ("vxtwitter", _vxtwitter), ("oembed", _oembed)]


def fetch_post(url):
    parsed = parse_link(url)
    if not parsed:
        raise FetchError("That does not look like an X post link.")
    handle, post_id = parsed
    tried = []
    for name, fn in PROVIDERS:
        try:
            data = fn(handle, post_id)
        except FetchError as exc:
            tried.append(f"{name}: {exc}")
            continue
        except (httpx.HTTPError, KeyError, TypeError, AttributeError) as exc:
            tried.append(f"{name}: {type(exc).__name__}")
            continue
        if not data["text"].strip():
            tried.append(f"{name}: empty")
            continue
        data["author"] = "@" + data["author"].lstrip("@")
        data["link"] = f"https://x.com/{handle}/status/{post_id}"
        data["source"] = name
        return data
    raise FetchError("Could not read that post automatically (" + "; ".join(tried) + "). Paste the text instead.")

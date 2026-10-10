"""AI calls + demo fallback. The rest of the app only talks to this module."""
import json
import re
from datetime import time

from django.conf import settings

from . import prompts

KINDS = {"post", "thread", "reply_session", "engage", "review"}


class NoApiKey(Exception):
    pass


def _client():
    if not settings.OPENROUTER_API_KEY:
        raise NoApiKey("OPENROUTER_API_KEY is not set")
    from openai import OpenAI
    return OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=settings.OPENROUTER_API_KEY,
        timeout=90,
        default_headers={"X-Title": settings.APP_NAME},
    )


def _extract_json(text):
    text = text.strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("No JSON object in model output")
    return json.loads(text[start : end + 1])


def _ask(system, user, max_tokens=3500, retries=1):
    client = _client()
    last = None
    for _ in range(retries + 1):
        resp = client.chat.completions.create(
            model=settings.OPENROUTER_MODEL,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        text = resp.choices[0].message.content or ""
        try:
            return _extract_json(text)
        except ValueError as exc:
            last = exc
    raise last


# ---------- context builders ----------

def profile_context(profile):
    return {
        "niche": profile.niche,
        "goal": profile.get_goal_display(),
        "audience": profile.audience,
        "offer": profile.offer or None,
        "tone": profile.get_tone_display(),
        "admired_accounts": profile.admired or None,
        "voice_samples": profile.voice_samples or None,
    }


def best_hours(stats):
    return (stats or {}).get("best_hours") or []


# ---------- plan ----------

def normalize_plan(data):
    tasks = []
    for t in data.get("tasks", []):
        raw = str(t.get("time", "")).strip()
        m = re.match(r"^(\d{1,2}):(\d{2})$", raw)
        if not m:
            continue
        h, mi = int(m.group(1)), int(m.group(2))
        if not (0 <= h < 24 and 0 <= mi < 60):
            continue
        kind = t.get("kind") if t.get("kind") in KINDS else "post"
        count = t.get("count")
        count = int(count) if isinstance(count, (int, float)) and count > 0 else None
        tasks.append(
            {
                "time": time(h, mi),
                "kind": kind,
                "title": str(t.get("title", "Task"))[:160],
                "body": str(t.get("body", "")),
                "why": str(t.get("why", ""))[:320],
                "count": count,
            }
        )
    if not tasks:
        raise ValueError("Plan had no valid tasks")
    tasks.sort(key=lambda x: x["time"])
    return {"summary": str(data.get("summary", "")), "tasks": tasks[:14]}


def generate_plan(profile, history, stats, weekday):
    """Returns (plan_dict, source) where source is 'ai' or 'demo'."""
    payload = {
        "today_is": weekday,
        "user": profile_context(profile),
        "limits": {
            "posts_per_day": profile.posts_per_day,
            "reply_target": profile.reply_target,
            "daily_minutes": profile.daily_minutes,
            "timezone": profile.timezone,
        },
        "best_posting_hours_local": best_hours(stats),
        "top_past_posts": ((stats or {}).get("top_posts") or [])[:3],
        "recent_history_avoid_repeating": history,
    }
    try:
        data = _ask(prompts.PLAN_SYSTEM, json.dumps(payload, ensure_ascii=False))
        return normalize_plan(data), "ai"
    except NoApiKey:
        return demo_plan(profile, weekday), "demo"


# ---------- replies ----------

def reply_angles(profile, post_text, author=""):
    payload = {
        "user": profile_context(profile),
        "post_author": author or None,
        "post_text": post_text,
    }
    try:
        data = _ask(prompts.REPLY_SYSTEM, json.dumps(payload, ensure_ascii=False), max_tokens=900)
        angles = [
            {"label": str(a.get("label", "Angle"))[:40], "reply": str(a.get("reply", ""))[:280]}
            for a in data.get("angles", [])
            if a.get("reply")
        ]
        if not angles:
            raise ValueError("No angles returned")
        return {"angles": angles, "tip": str(data.get("tip", ""))}, "ai"
    except NoApiKey:
        return demo_angles(), "demo"


# ---------- demo mode (no API key) ----------

def demo_plan(profile, weekday):
    niche = profile.niche
    n_posts = max(1, min(profile.posts_per_day, 3))
    posts = [
        (
            "09:00",
            "Post your strongest opinion",
            f"Most people in {niche} are focused on the wrong thing.\n\nThe real lever is [your real opinion here]. "
            f"I learned this when [your real example here].",
            "Opinion posts start conversations, and conversations are what the algorithm rewards.",
        ),
        (
            "13:00",
            "Share one practical lesson",
            f"One thing that would have saved me months in {niche}:\n\n[lesson in one sentence]\n\nWhy it matters: [one line].",
            "Useful posts get saved and shared, which builds trust with your audience.",
        ),
        (
            "18:30",
            "Ask your audience a question",
            f"{profile.audience}: what is the hardest part of {niche} right now?\n\nI'll reply to every answer.",
            "Questions pull replies, and replying back doubles your reach.",
        ),
    ][:n_posts]
    per = max(1, profile.reply_target // 3) if profile.reply_target else 0
    tasks = [
        {"time": time(*map(int, t.split(":"))), "kind": "post", "title": title, "body": body, "why": why, "count": None}
        for t, title, body, why in posts
    ]
    if per:
        for hhmm, label in (("10:00", "Morning"), ("14:30", "Afternoon"), ("19:30", "Evening")):
            tasks.append(
                {
                    "time": time(*map(int, hhmm.split(":"))),
                    "kind": "reply_session",
                    "title": f"{label} reply session ({per} replies)",
                    "body": f"Reply to {per} recent posts from accounts in {niche} with 1K to 50K followers. "
                    "Add a real insight or a smart question. No two replies should look alike. "
                    "Use the Reply helper if you get stuck.",
                    "why": "Replies on active accounts are the fastest way to get seen by new people.",
                    "count": per,
                }
            )
    tasks.append(
        {
            "time": time(12, 0),
            "kind": "engage",
            "title": "Engage with 5 people in your circle",
            "body": "Like and reply thoughtfully to 5 people you already talk to. Relationships compound.",
            "why": "A warm circle amplifies every post you publish.",
            "count": 5,
        }
    )
    tasks.append(
        {
            "time": time(21, 0),
            "kind": "review",
            "title": "Two-minute review",
            "body": "Open your last 3 posts. Which got the most replies? Note one thing to repeat tomorrow.",
            "why": "Small daily reviews turn guessing into a strategy.",
            "count": None,
        }
    )
    tasks.sort(key=lambda x: x["time"])
    return {
        "summary": f"{weekday} focus: one strong opinion, steady replies, and one lesson for {profile.audience}. "
        "(Demo plan. Add an API key for tailored posts.)",
        "tasks": tasks,
    }


def demo_angles():
    return {
        "angles": [
            {"label": "Adds value", "reply": "Adding to this: [your real insight or example here]."},
            {"label": "Smart question", "reply": "How did you decide on that? I'm curious what you tried first."},
            {"label": "Different view", "reply": "I see it slightly differently: [your honest take]. Curious what you think."},
        ],
        "tip": "Replace the brackets with something only you could say. (Demo mode: add an API key for real angles.)",
    }

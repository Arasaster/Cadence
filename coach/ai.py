"""OpenRouter calls. No demo mode: if something is wrong, you get a clear error.

Every function here either returns real model output or raises AIError with a
message that is safe to show in the UI.
"""
import json
import random
import re
from datetime import time

from django.conf import settings

from . import prompts
from .style import (
    BIO_DIRECTIONS, BIO_GOAL_LABELS, BIO_ROLE_LABELS, INTENT_HELP, INTENT_LABELS, LENGTH_MAX,
    emoji_flags, slider_instructions, voice_context,
)

KINDS = {"post", "thread", "reply_session", "engage", "review"}


class AIError(Exception):
    """Anything that stopped us getting a usable answer from the model."""


def _client():
    if not settings.OPENROUTER_API_KEY:
        raise AIError("OPENROUTER_API_KEY is not set. Add it to your environment and restart.")
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
    for attempt in range(retries + 1):
        try:
            resp = client.chat.completions.create(
                model=settings.OPENROUTER_MODEL,
                max_tokens=max_tokens,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
        except Exception as exc:  # auth, credits, bad model id, network
            cause = exc.__cause__ or exc
            detail = f"{type(cause).__name__}: {str(cause)[:200]}"
            raise AIError(
                f"OpenRouter request failed ({settings.OPENROUTER_MODEL}): {str(exc)[:200]} [{detail}]"
            ) from exc
        text = (resp.choices[0].message.content or "") if resp.choices else ""
        try:
            return _extract_json(text)
        except ValueError:
            continue  # retry once, the model sometimes adds prose around the JSON
    raise AIError("The model did not return valid JSON. Run it again or try a stronger model.")


# ---------- context builders ----------

def profile_context(profile):
    return {
        "voice": voice_context(profile),
        "followers_now": profile.current_followers,
        "followers_target": profile.target_followers,
        "success_looks_like": profile.target_note or None,
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


def generate_plan(profile, history, stats, weekday, roadmap_ctx=None, recent=None):
    """Returns {"summary": str, "tasks": [...]} or raises AIError."""
    flags = emoji_flags(profile.account_type, profile.posts_per_day)
    payload = {
        "today_is": weekday,
        "roadmap_today": roadmap_ctx,
        "recent_completion": recent,
        "emoji": {
            "posts_that_may_use_emoji": [i + 1 for i, f in enumerate(flags) if f],
            "max_per_post": voice_context(profile)["emoji_max_per_item"],
        },
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
    data = _ask(prompts.PLAN_SYSTEM, json.dumps(payload, ensure_ascii=False))
    try:
        return normalize_plan(data)
    except ValueError as exc:
        raise AIError(f"The model's plan was unusable: {exc}. Run it again.") from exc


# ---------- roadmap ----------

def normalize_roadmap(data, days):
    phases = []
    for ph in data.get("phases", []):
        try:
            start, end = int(ph["start_day"]), int(ph["end_day"])
        except (KeyError, TypeError, ValueError):
            continue
        phases.append(
            {
                "name": str(ph.get("name", "Phase"))[:60],
                "start_day": max(1, start),
                "end_day": min(days, end),
                "focus": str(ph.get("focus", ""))[:300],
                "actions": [str(a)[:200] for a in (ph.get("actions") or [])][:6],
                "content_pillars": [str(a)[:100] for a in (ph.get("content_pillars") or [])][:5],
                "checkpoint": str(ph.get("checkpoint", ""))[:250],
            }
        )
    phases.sort(key=lambda p: p["start_day"])
    fixed, prev_end = [], 0
    for ph in phases:  # remove overlaps and gaps so every day belongs to one phase
        ph["start_day"] = prev_end + 1
        if ph["end_day"] < ph["start_day"]:
            continue
        fixed.append(ph)
        prev_end = ph["end_day"]
    if not fixed:
        raise ValueError("Roadmap had no valid phases")
    fixed[-1]["end_day"] = days
    return {
        "summary": str(data.get("summary", "")),
        "baseline": str(data.get("baseline", "")),
        "target": str(data.get("target", "")),
        "reality_check": str(data.get("reality_check", "")),
        "phases": fixed,
    }


def generate_roadmap(profile, stats, days, note=""):
    """Returns a normalized roadmap dict or raises AIError."""
    payload = {
        "days": days,
        "user": profile_context(profile),
        "extra_note_from_user": note or None,
        "archive_stats": {
            k: (stats or {}).get(k)
            for k in ("total_posts", "total_replies", "first_post", "last_post", "best_hours", "circle")
        }
        if stats
        else None,
        "top_past_posts": ((stats or {}).get("top_posts") or [])[:3],
        "limits": {
            "posts_per_day": profile.posts_per_day,
            "reply_target": profile.reply_target,
            "daily_minutes": profile.daily_minutes,
        },
    }
    data = _ask(prompts.ROADMAP_SYSTEM, json.dumps(payload, ensure_ascii=False), max_tokens=3500)
    try:
        return normalize_roadmap(data, days)
    except ValueError as exc:
        raise AIError(f"The model's roadmap was unusable: {exc}. Run it again.") from exc


# ---------- text cleanup ----------

def clean_text(text):
    """Remove the most obvious AI tells the model sometimes ignores."""
    text = str(text or "").strip().strip('"\u201c\u201d')
    text = re.sub(r"^(reply|bio)\s*:\s*", "", text, flags=re.I)
    text = text.replace(" \u2014 ", ", ").replace("\u2014", ", ").replace(" \u2013 ", ", ").replace("\u2013", "-")
    text = re.sub(r"[ \t]{2,}", " ", text)
    return re.sub(r"\s+,", ",", text).strip()


def voice_examples(profile, stats, limit=8):
    """The user's own writing: pasted samples first, then their best archive posts."""
    out = []
    if profile.voice_samples:
        out += [p.strip() for p in re.split(r"\n{2,}|\n---\n", profile.voice_samples) if p.strip()][:5]
    out += [p["text"] for p in ((stats or {}).get("top_posts") or [])[:5]]
    return [o[:280] for o in out][:limit]


# ---------- bio positioning ----------

def _clamp10(v):
    try:
        return max(1, min(10, int(round(float(v)))))
    except (TypeError, ValueError):
        return 5


def _bio_data(stats):
    """A compact, honest summary of the user's archive for the bio coach."""
    if not stats or not stats.get("version"):
        return None
    return {
        "posts_analysed": stats.get("total_posts"),
        "words_in_best_posts": [w["word"] for w in (stats.get("winning_words") or [])][:8],
        "words_used_most": [w["word"] for w in (stats.get("frequent_words") or [])][:10],
        "hashtags": [w["word"] for w in (stats.get("top_hashtags") or [])][:5],
        "best_posts": [p["text"][:200] for p in (stats.get("top_posts") or [])[:4]],
        "what_works": [t for t in (stats.get("takeaways") or [])][:4],
        "accounts_they_talk_to": [c["name"] for c in (stats.get("circle") or [])][:5],
    }


def bio_analyze(profile, current_bio, goal, role, notes, stats=None, use_data=True):
    """Score the current bio and write 5 positioning directions. Raises AIError on failure."""
    flags = emoji_flags(profile.account_type, 5)
    data = _bio_data(stats) if use_data else None
    payload = {
        "user": profile_context(profile),
        "current_bio": current_bio,
        "current_length": len(current_bio),
        "goal": BIO_GOAL_LABELS.get(goal, goal),
        "role": BIO_ROLE_LABELS.get(role, role),
        "keep_mention_avoid": notes or None,
        "data": data,
        "directions": [d[0] for d in BIO_DIRECTIONS],
        "emoji": {
            "versions_that_may_use_emoji": [i + 1 for i, f in enumerate(flags) if f],
            "max_per_bio": voice_context(profile)["emoji_max_per_item"],
        },
    }
    raw = _ask(prompts.BIO_SYSTEM, json.dumps(payload, ensure_ascii=False), max_tokens=2200)
    by_key = {v.get("key"): v for v in raw.get("versions", []) if isinstance(v, dict) and v.get("bio")}
    listed = [v for v in raw.get("versions", []) if isinstance(v, dict) and v.get("bio")]
    versions = []
    for idx, (key, label, best_for) in enumerate(BIO_DIRECTIONS):
        v = by_key.get(key) or (listed[idx] if idx < len(listed) else None)
        if not v:
            continue
        bio = clean_text(v["bio"])
        versions.append(
            {"key": key, "number": f"{idx + 1:02d}", "label": label, "best_for": best_for, "bio": bio,
             "why": str(v.get("why", ""))[:240], "length": len(bio), "over": len(bio) > 160}
        )
    if not versions:
        raise AIError("The model returned no bio versions. Run it again.")
    sc = raw.get("score") or {}
    score = {k: _clamp10(sc.get(k)) for k in ("clarity", "positioning", "personality", "specificity")}
    match = raw.get("content_match") if data else None
    if isinstance(match, dict):
        match = {"verdict": match.get("verdict") if match.get("verdict") in ("matches", "partial", "mismatch") else "partial",
                 "note": str(match.get("note", ""))[:300]}
    else:
        match = None
    ins = raw.get("insight") if data else None
    if isinstance(ins, dict):
        ins = {"topics": [str(t)[:40] for t in (ins.get("topics") or [])][:6], "expertise": str(ins.get("expertise", ""))[:240],
               "different": str(ins.get("different", ""))[:240], "style": str(ins.get("style", ""))[:240]}
    else:
        ins = None
    return {
        "current_length": len(current_bio), "score": score, "opportunity": str(raw.get("opportunity", ""))[:400],
        "content_match": match, "insight": ins, "versions": versions, "notes": str(raw.get("notes", "")),
        "used_data": bool(data),
    }


BIO_ACTIONS = {"sharper", "human", "specific", "shorter", "hook", "direction"}


def bio_refine(profile, ctx, action, text, direction=""):
    """Rework one bio. Returns {"bio", "why", "length", "over"}."""
    flags = emoji_flags(profile.account_type, 1)
    payload = {
        "user": profile_context(profile),
        "action": action,
        "current_bio": text,
        "positioning_direction": direction or None,
        "goal": BIO_GOAL_LABELS.get(ctx.get("goal"), ctx.get("goal")),
        "role": BIO_ROLE_LABELS.get(ctx.get("role"), ctx.get("role")),
        "keep_mention_avoid": ctx.get("notes") or None,
        "emoji_allowed": flags[0],
        "emoji_max": voice_context(profile)["emoji_max_per_item"],
    }
    raw = _ask(prompts.BIO_EDIT_SYSTEM, json.dumps(payload, ensure_ascii=False), max_tokens=500)
    bio = clean_text(raw.get("bio"))
    if not bio:
        raise AIError("The model returned an empty bio. Try again.")
    return {"bio": bio, "why": str(raw.get("why", ""))[:200], "length": len(bio), "over": len(bio) > 160}


# ---------- replies ----------

def _reply_payload(profile, ctx, settings_, stats):
    examples = voice_examples(profile, stats)
    length = settings_.get("length", "balanced")
    return {
        "user": profile_context(profile),
        "post": {
            "author": ctx.get("author") or None,
            "author_bio": ctx.get("author_bio") or None,
            "text": ctx.get("post_text", ""),
            "existing_replies": ctx.get("top_replies") or None,
            "link": ctx.get("link") or None,
        },
        "intent": {
            "key": settings_.get("intent", "insight"),
            "label": INTENT_LABELS.get(settings_.get("intent"), "Add insight"),
            "meaning": INTENT_HELP.get(settings_.get("intent"), ""),
        },
        "voice": {"mode": settings_.get("voice", "my_voice"), "examples": examples, "has_examples": bool(examples)},
        "length": {"name": length, "max_chars": LENGTH_MAX.get(length, 220)},
        "user_instruction": (settings_.get("instruction") or "").strip() or None,
        "tone_adjustments": slider_instructions(settings_.get("sliders") or {}) or None,
    }


def _norm_list(v, n=4, width=200):
    return [str(x)[:width] for x in (v or []) if str(x).strip()][:n] if isinstance(v, list) else []


def reply_session(profile, ctx, settings_, stats=None, force=False):
    """Read the post and conversation, then write 3 different replies (or say there is nothing to add)."""
    sliders = settings_.get("sliders") or {}
    flags = emoji_flags(profile.account_type, 3, strength=sliders.get("emoji", 50))
    payload = _reply_payload(profile, ctx, settings_, stats)
    payload.update(
        force=bool(force),
        emoji_allowed=flags,
        emoji_max_per_reply=voice_context(profile)["emoji_max_per_item"],
    )
    raw = _ask(prompts.REPLY_SYSTEM, json.dumps(payload, ensure_ascii=False), max_tokens=2000)
    rd = raw.get("read") if isinstance(raw.get("read"), dict) else {}
    read = {
        "topic": str(rd.get("topic", ""))[:200], "intent": str(rd.get("intent", ""))[:200],
        "argument": str(rd.get("argument", ""))[:300], "context": str(rd.get("context", ""))[:300],
        "repeated_points": _norm_list(rd.get("repeated_points")), "gaps": _norm_list(rd.get("gaps")),
        "obvious_point_taken": bool(rd.get("obvious_point_taken")),
        "suggested_angle": str(rd.get("suggested_angle", ""))[:300],
        "has_replies": bool(ctx.get("top_replies")),
    }
    max_chars = payload["length"]["max_chars"]
    replies = []
    for r in (raw.get("replies") or [])[:3]:
        if isinstance(r, dict) and r.get("text"):
            text = clean_text(r["text"])[:320]
            replies.append({"approach": str(r.get("approach", "Insight"))[:24], "angle": str(r.get("angle", ""))[:160],
                            "text": text, "length": len(text), "over": len(text) > 280, "max": max_chars})
    worth = raw.get("worth_replying", True) is not False
    if not worth and not force:
        return {"read": read, "worth": False, "skip_reason": str(raw.get("skip_reason", ""))[:400], "replies": [], "tip": ""}
    if not replies:
        raise AIError("The model returned no replies. Run it again.")
    return {"read": read, "worth": True, "skip_reason": "", "replies": replies, "tip": str(raw.get("tip", ""))[:240]}


REPLY_MODES = {"regenerate", "angle", "shorter", "natural"}


def reply_edit(profile, ctx, settings_, mode, current, approach="", used=None, stats=None):
    """Rework one reply. Returns {"approach", "angle", "text", "length", "over"}."""
    sliders = settings_.get("sliders") or {}
    flags = emoji_flags(profile.account_type, 1, strength=sliders.get("emoji", 50))
    payload = _reply_payload(profile, ctx, settings_, stats)
    payload.update(
        mode=mode, current_reply=current, approach=approach or None, used_approaches=used or [],
        emoji_allowed=flags[0], emoji_max_per_reply=voice_context(profile)["emoji_max_per_item"],
    )
    raw = _ask(prompts.REPLY_EDIT_SYSTEM, json.dumps(payload, ensure_ascii=False), max_tokens=600)
    text = clean_text(raw.get("text"))[:320]
    if not text:
        raise AIError("The model returned an empty reply. Try again.")
    return {"approach": str(raw.get("approach") or approach or "Insight")[:24], "angle": str(raw.get("angle", ""))[:160],
            "text": text, "length": len(text), "over": len(text) > 280}

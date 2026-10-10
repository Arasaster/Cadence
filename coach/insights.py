"""Parse the user's own X data archive (tweets.js). No X access, no scraping."""
import json
from collections import Counter, defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

FMT = "%a %b %d %H:%M:%S %z %Y"


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def parse_archive(text, tz="UTC", username=""):
    start = text.find("[")
    if start == -1:
        raise ValueError("This does not look like tweets.js from your X archive.")
    rows = json.loads(text[start:])
    zone = ZoneInfo(tz)
    mine = username.lower().lstrip("@")

    originals, replies = [], []
    circle, months = Counter(), Counter()
    hour_stats = defaultdict(lambda: [0, 0])  # hour -> [likes, count]
    month_likes = defaultdict(lambda: [0, 0])

    for row in rows:
        t = row.get("tweet", row)
        body = t.get("full_text") or t.get("text") or ""
        if body.startswith("RT @"):
            continue
        try:
            when = datetime.strptime(t["created_at"], FMT).astimezone(zone)
        except (KeyError, ValueError):
            continue
        likes, rts = _int(t.get("favorite_count")), _int(t.get("retweet_count"))
        key = when.strftime("%Y-%m")
        months[key] += 1
        reply_to = t.get("in_reply_to_screen_name")
        if reply_to:
            replies.append(when)
            if reply_to.lower() != mine:
                circle[reply_to] += 1
        else:
            originals.append({"text": body, "likes": likes, "rts": rts, "when": when})
            hour_stats[when.hour][0] += likes
            hour_stats[when.hour][1] += 1
            month_likes[key][0] += likes
            month_likes[key][1] += 1
        for m in (t.get("entities", {}) or {}).get("user_mentions", []) or []:
            name = m.get("screen_name")
            if name and name.lower() != mine and not reply_to:
                circle[name] += 1

    if not originals and not replies:
        raise ValueError("No posts found in that file.")

    top = sorted(originals, key=lambda p: p["likes"] + 2 * p["rts"], reverse=True)[:5]
    last12 = sorted(months)[-12:]
    best = sorted(
        ((h, v[0] / v[1]) for h, v in hour_stats.items() if v[1] >= 3), key=lambda x: x[1], reverse=True
    )[:3]
    all_dates = [p["when"] for p in originals] + replies

    return {
        "total_posts": len(originals),
        "total_replies": len(replies),
        "first_post": min(all_dates).strftime("%b %Y"),
        "last_post": max(all_dates).strftime("%b %Y"),
        "per_month": [
            {"month": m, "count": months[m], "pct": round(100 * months[m] / max(months[x] for x in last12))}
            for m in last12
        ],
        "avg_likes_per_month": [
            {"month": m, "avg": round(month_likes[m][0] / month_likes[m][1], 1)}
            for m in last12
            if month_likes[m][1]
        ],
        "top_posts": [
            {"text": p["text"][:280], "likes": p["likes"], "rts": p["rts"], "date": p["when"].strftime("%d %b %Y")}
            for p in top
        ],
        "circle": [{"name": n, "count": c} for n, c in circle.most_common(10)],
        "best_hours": [f"{h:02d}:00" for h, _ in best],
    }

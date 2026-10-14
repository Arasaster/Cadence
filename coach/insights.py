"""Turn the user's own X data archive (tweets.js) into deep statistics.

Everything here comes from the archive file the user uploads: no X access, no scraping.
The archive has likes, reposts, text, dates and entities, but NOT impressions or follower history.
"""
import json
import math
import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

FMT = "%a %b %d %H:%M:%S %z %Y"
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
FULL_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
EMOJI = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B50\u2B06\u2194-\u21AA\u2300-\u23FF]")
URL = re.compile(r"https?://\S+")
MENTION = re.compile(r"@\w+")
WORD = re.compile(r"[a-z][a-z']{3,}")
STOP = set(
    """that this with have from your they their there what when which would about could should been were will
    just like more some than then them into also very much only over such even most make made does dont
    doesnt cant isnt wont youre thats here being those these because while where after before still every
    really thing things people want need know think going back """.split()
)


def _snip(p, n=70):
    t = " ".join(p["text"].split())
    return (t[:n].rstrip() + "...") if len(t) > n else t


def _best_line(posts, label="Best"):
    if not posts:
        return ""
    b = max(posts, key=lambda p: (p["likes"], p["rts"]))
    return f'{label}: "{_snip(b)}" ({b["likes"]} like{"s" if b["likes"] != 1 else ""})'


def _lines(*parts):
    return "\n".join(x for x in parts if x)


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _avg(xs):
    return round(sum(xs) / len(xs), 1) if xs else 0


def _level(v, mx):
    if v <= 0 or mx <= 0:
        return 0
    return min(4, max(1, math.ceil(4 * v / mx)))


def _days(n):
    return f"{n} day{'' if n == 1 else 's'}"


def _pct_change(new, old):
    return None if not old else round(100 * (new - old) / old)


def _pct(v, mx):
    return round(100 * v / mx) if mx else 0


def _month_back(year, month, n):
    m = year * 12 + (month - 1) - n
    return f"{m // 12:04d}-{m % 12 + 1:02d}"


def _load(text, zone, mine):
    start = text.find("[")
    if start == -1:
        raise ValueError("This does not look like tweets.js from your X archive.")
    items = []
    for row in json.loads(text[start:]):
        t = row.get("tweet", row)
        body = t.get("full_text") or t.get("text") or ""
        if body.startswith("RT @"):
            continue
        try:
            when = datetime.strptime(t["created_at"], FMT).astimezone(zone)
        except (KeyError, ValueError):
            continue
        ent = t.get("entities") or {}
        ext = t.get("extended_entities") or {}
        reply_to = t.get("in_reply_to_screen_name")
        if reply_to and mine and reply_to.lower() == mine:
            kind = "thread"
        elif reply_to:
            kind = "reply"
        else:
            kind = "original"
        items.append(
            {
                "text": body, "when": when, "kind": kind, "reply_to": reply_to,
                "likes": _int(t.get("favorite_count")), "rts": _int(t.get("retweet_count")),
                "mentions": [m.get("screen_name") for m in ent.get("user_mentions", []) or [] if m.get("screen_name")],
                "tags": [h.get("text", "").lower() for h in ent.get("hashtags", []) or [] if h.get("text")],
                "has_url": bool(ent.get("urls")) if "entities" in t else bool(URL.search(body)),
                "has_media": bool(ent.get("media") or ext.get("media")),
            }
        )
    if not items:
        raise ValueError("No posts found in that file.")
    return items


def _formats(originals):
    tests = [
        ("Asks a question", "ask a question", lambda p: "?" in p["text"]),
        ("Includes a link", "include a link", lambda p: p["has_url"]),
        ("Includes an image or video", "include an image or video", lambda p: p["has_media"]),
        ("Uses hashtags", "use hashtags", lambda p: bool(p["tags"])),
        ("Uses emoji", "use emoji", lambda p: bool(EMOJI.search(p["text"]))),
        ("Starts with a number", "start with a number", lambda p: bool(re.match(r"\s*\d", p["text"]))),
        ("Starts with 'I' (personal)", "start with 'I'", lambda p: bool(re.match(r"\s*I(\b|')", p["text"]))),
    ]
    rows = []
    for label, phrase, fn in tests:
        yes_posts = [p for p in originals if fn(p)]
        yes = [p["likes"] for p in yes_posts]
        no = [p["likes"] for p in originals if not fn(p)]
        enough = len(yes) >= 5 and len(no) >= 5
        w, wo = _avg(yes), _avg(no)
        lift = _pct_change(w, wo) if enough and wo else None
        rows.append(
            {"label": label, "phrase": phrase, "n": len(yes), "with_avg": w, "without_avg": wo, "lift": lift, "enough": enough,
             "tip": _lines(label, f"{len(yes)} posts: avg {w} likes", f"Without it: avg {wo} likes" if no else "",
                           (f"{lift:+d}% vs posts without" if lift is not None else "Needs 5+ posts with and without for a comparison"),
                           _best_line(yes_posts))}
        )
    mx = max([r["with_avg"] for r in rows] + [0.1])
    for r in rows:
        r["pct"] = _pct(r["with_avg"], mx) if r["n"] else 0
    return rows


def _length_buckets(originals):
    edges = [(0, 80, "Under 80"), (80, 140, "80 to 139"), (140, 200, "140 to 199"), (200, 260, "200 to 259"), (260, 10**6, "260+")]
    rows = []
    for lo, hi, label in edges:
        members = [p for p in originals if lo <= len(p["text"]) < hi]
        likes = [p["likes"] for p in members]
        rows.append({"label": label + " chars", "n": len(likes), "avg": _avg(likes),
                     "tip": _lines(f"{label} characters", f"{len(likes)} posts: avg {_avg(likes)} likes", _best_line(members))})
    mx = max([r["avg"] for r in rows] + [0.1])
    for r in rows:
        r["pct"] = _pct(r["avg"], mx)
    return rows


def _distribution(originals):
    edges = [(0, 0, "0 likes"), (1, 2, "1 to 2"), (3, 9, "3 to 9"), (10, 24, "10 to 24"), (25, 10**9, "25+")]
    total = len(originals) or 1
    rows = []
    for lo, hi, label in edges:
        members = [p for p in originals if lo <= p["likes"] <= hi]
        n = len(members)
        ex = members[0] if members else None
        rows.append({"label": label, "n": n, "share": round(100 * n / total),
                     "tip": _lines(label, f"{n} posts ({round(100 * n / total)}% of all)", f'Example: "{_snip(ex)}"' if ex else "")})
    mx = max([r["n"] for r in rows] + [1])
    for r in rows:
        r["pct"] = _pct(r["n"], mx)
    return rows


def _calendar(items):
    day_items = defaultdict(list)
    for i in items:
        day_items[i["when"].date()].append(i)
    by_day = Counter({d: len(v) for d, v in day_items.items()})
    end = max(by_day)
    monday = end - timedelta(days=end.weekday())
    start = monday - timedelta(weeks=25)
    weeks = []
    for w in range(26):
        cells = []
        for d in range(7):
            day = start + timedelta(weeks=w, days=d)
            n = by_day.get(day, 0)
            level = 0 if n == 0 else 1 if n == 1 else 2 if n == 2 else 3 if n <= 4 else 4
            todays = day_items.get(day, [])
            n_rep = sum(1 for i in todays if i["kind"] == "reply")
            snippets = [f'- "{_snip(i, 55)}" ({i["likes"]})' for i in todays if i["kind"] != "reply"][:3]
            tip = _lines(f"{day:%A %d %B %Y}",
                         f"{n - n_rep} post{'s' if n - n_rep != 1 else ''} · {n_rep} repl{'ies' if n_rep != 1 else 'y'}" if n else "No activity",
                         *snippets)
            cells.append({"level": level, "future": day > end, "tip": tip})
        weeks.append(cells)
    active = sorted(by_day)
    longest = run = 1
    gap = 0
    for a, b in zip(active, active[1:]):
        if (b - a).days == 1:
            run += 1
        else:
            run = 1
            gap = max(gap, (b - a).days - 1)
        longest = max(longest, run)
    current = 1
    for a, b in zip(reversed(active[:-1]), reversed(active)):
        if (b - a).days == 1:
            current += 1
        else:
            break
    window_days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    active_in_window = sum(1 for d in window_days if by_day.get(d))
    return {
        "weeks": weeks,
        "longest_streak": longest,
        "current_streak": current,
        "longest_gap": gap,
        "active_days": active_in_window,
        "window_days": len(window_days),
        "active_pct": round(100 * active_in_window / len(window_days)),
        "end": f"{end:%d %b %Y}",
    }


def _topics(originals):
    tags, words = defaultdict(list), defaultdict(list)
    best = {}
    for p in originals:
        for tag in set(p["tags"]):
            tags[tag].append(p["likes"])
            if tag not in best or p["likes"] > best[tag]["likes"]:
                best[tag] = p
        clean = MENTION.sub(" ", URL.sub(" ", p["text"].lower()))
        for w in set(WORD.findall(clean)):
            if w not in STOP:
                words[w].append(p["likes"])
                if w not in best or p["likes"] > best[w]["likes"]:
                    best[w] = p
    top_tags = sorted(tags.items(), key=lambda kv: len(kv[1]), reverse=True)[:8]
    frequent = sorted(((w, v) for w, v in words.items() if len(v) >= 3), key=lambda kv: len(kv[1]), reverse=True)[:12]
    winning = sorted(((w, v) for w, v in words.items() if len(v) >= 3), key=lambda kv: _avg(kv[1]), reverse=True)[:10]
    shape = lambda pairs: [
        {"word": w, "n": len(v), "avg": _avg(v),
         "tip": _lines(w, f"Used in {len(v)} posts: avg {_avg(v)} likes", _best_line([best[w]], "Best post"))}
        for w, v in pairs
    ]
    return shape(top_tags), shape(frequent), shape(winning)


def parse_archive(text, tz="UTC", username=""):
    zone = ZoneInfo(tz)
    mine = username.lower().lstrip("@")
    items = _load(text, zone, mine)

    originals = [i for i in items if i["kind"] == "original"]
    threads = [i for i in items if i["kind"] == "thread"]
    replies = [i for i in items if i["kind"] == "reply"]
    posting = originals + threads  # everything you published on your own timeline
    all_when = [i["when"] for i in items]
    end_dt = max(all_when)

    # --- circle: who you talk to ---
    circle = Counter(i["reply_to"] for i in replies if i["reply_to"] and i["reply_to"].lower() != mine)
    for i in posting:
        for m in i["mentions"]:
            if m.lower() != mine:
                circle[m] += 1

    # --- months (last 12 calendar months, zeros included) ---
    months = [_month_back(end_dt.year, end_dt.month, n) for n in range(11, -1, -1)]
    count_all, count_posts, count_replies = Counter(), Counter(), Counter()
    likes_by_month = defaultdict(list)
    originals_by_month = defaultdict(list)
    for i in items:
        key = i["when"].strftime("%Y-%m")
        count_all[key] += 1
        (count_replies if i["kind"] == "reply" else count_posts)[key] += 1
        if i["kind"] == "original":
            likes_by_month[key].append(i["likes"])
            originals_by_month[key].append(i)

    def month_tip(m):
        name = datetime.strptime(m, "%Y-%m").strftime("%B %Y")
        likes = likes_by_month[m]
        return _lines(name, f"{count_posts[m]} posts · {count_replies[m]} replies",
                      f"Avg {_avg(likes)} likes · {sum(likes)} likes in total" if likes else "No original posts",
                      _best_line(originals_by_month[m]))

    mx_all = max([count_all[m] for m in months] + [1])
    per_month = [{"month": m, "count": count_all[m], "pct": _pct(count_all[m], mx_all), "tip": month_tip(m)} for m in months]
    mix = [
        {"month": m, "posts": count_posts[m], "replies": count_replies[m], "tip": month_tip(m),
         "posts_pct": _pct(count_posts[m], mx_all), "replies_pct": _pct(count_replies[m], mx_all)}
        for m in months
    ]
    avg_months = [{"month": m, "avg": _avg(likes_by_month[m])} for m in months if likes_by_month[m]]
    mx_avg = max([a["avg"] for a in avg_months] + [0.1])
    xs = {m: 30 + idx * (540 / 11) for idx, m in enumerate(months)}
    dots = [{"x": round(xs[a["month"]]), "y": round(160 - 130 * a["avg"] / mx_avg), "month": a["month"], "avg": a["avg"],
             "tip": month_tip(a["month"])} for a in avg_months]
    likes_trend = {
        "points": " ".join(f"{d['x']},{d['y']}" for d in dots),
        "dots": dots,
        "max": round(mx_avg, 1),
        "labels": [{"x": round(xs[m]), "text": m[5:]} for m in months],
    }

    # --- when you post ---
    wd = []
    for d in range(7):
        members = [p for p in posting if p["when"].weekday() == d]
        likes = [p["likes"] for p in members]
        wd.append({"day": DAYS[d], "full": FULL_DAYS[d], "posts": len(likes), "avg": _avg(likes),
                   "tip": _lines(FULL_DAYS[d], f"{len(likes)} posts: avg {_avg(likes)} likes · {sum(likes)} in total", _best_line(members))})
    mx_wd = max([w["avg"] for w in wd] + [0.1])
    for w in wd:
        w["pct"] = _pct(w["avg"], mx_wd)
    hour_likes = defaultdict(list)
    hour_posts = defaultdict(list)
    cell = defaultdict(list)
    cell_posts = defaultdict(list)
    for p in posting:
        hour_likes[p["when"].hour].append(p["likes"])
        hour_posts[p["when"].hour].append(p)
        cell[(p["when"].weekday(), p["when"].hour)].append(p["likes"])
        cell_posts[(p["when"].weekday(), p["when"].hour)].append(p)
    hours = []
    mx_h = max([len(v) for v in hour_likes.values()] + [1])
    for h in range(24):
        v = hour_likes.get(h, [])
        hours.append({"hour": f"{h:02d}:00", "n": len(v), "avg": _avg(v), "pct": _pct(len(v), mx_h),
                      "tip": _lines(f"{h:02d}:00 to {h:02d}:59", f"{len(v)} posts: avg {_avg(v)} likes" if v else "No posts at this hour",
                                    _best_line(hour_posts.get(h, [])))})
    cell_avgs = {k: _avg(v) for k, v in cell.items()}
    mx_cell = max(list(cell_avgs.values()) + [0.1])
    heatmap = [
        {"day": DAYS[d], "cells": [
            {"level": _level(cell_avgs.get((d, h), 0), mx_cell) if cell.get((d, h)) else 0,
             "tip": _lines(f"{FULL_DAYS[d]} {h:02d}:00",
                           (f"{len(cell[(d, h)])} posts: avg {cell_avgs[(d, h)]} likes" if cell.get((d, h)) else "No posts in this slot"),
                           _best_line(cell_posts.get((d, h), [])))}
            for h in range(24)]}
        for d in range(7)
    ]
    best_hours = [
        f"{h:02d}:00" for h, _ in sorted(((h, _avg(v)) for h, v in hour_likes.items() if len(v) >= 3), key=lambda x: x[1], reverse=True)[:3]
    ]

    # --- what works ---
    formats = _formats(originals)
    lengths = _length_buckets(originals)
    dist = _distribution(originals)
    like_values = [p["likes"] for p in originals]
    median_likes = statistics.median(like_values) if like_values else 0
    overall_avg = _avg(like_values)
    top_tags, frequent, winning = _topics(originals)

    # --- momentum (needs 60+ days of history) ---
    momentum = None
    if min(all_when) <= end_dt - timedelta(days=60):
        c1, c2 = end_dt - timedelta(days=30), end_dt - timedelta(days=60)
        now_p = [p for p in posting if p["when"] > c1]
        prev_p = [p for p in posting if c2 < p["when"] <= c1]
        now_r = [r for r in replies if r["when"] > c1]
        prev_r = [r for r in replies if c2 < r["when"] <= c1]
        now_l = _avg([p["likes"] for p in now_p if p["kind"] == "original"])
        prev_l = _avg([p["likes"] for p in prev_p if p["kind"] == "original"])
        momentum = {
            "posts": {"now": len(now_p), "prev": len(prev_p), "change": _pct_change(len(now_p), len(prev_p))},
            "replies": {"now": len(now_r), "prev": len(prev_r), "change": _pct_change(len(now_r), len(prev_r))},
            "likes": {"now": now_l, "prev": prev_l, "change": _pct_change(now_l, prev_l)},
        }

    cal = _calendar(items)

    # --- plain-English takeaways (only claims the data can support) ---
    takeaways = []
    days_ok = [w for w in wd if w["posts"] >= 3 and w["avg"] > 0]
    if days_ok and overall_avg:
        b = max(days_ok, key=lambda w: w["avg"])
        takeaways.append(f"Your {b['full']} posts do best: {b['avg']} likes on average, against {overall_avg} across all your posts.")
    if best_hours:
        takeaways.append(f"Posts around {', '.join(best_hours)} (your local time) get the most likes.")
    with_lift = [f for f in formats if f["lift"] is not None]
    if with_lift:
        up = max(with_lift, key=lambda f: f["lift"])
        down = min(with_lift, key=lambda f: f["lift"])
        if up["lift"] >= 20:
            takeaways.append(f"Posts that {up['phrase']} get {up['lift']}% more likes than posts without ({up['with_avg']} vs {up['without_avg']}, from {up['n']} posts).")
        if down["lift"] <= -20 and down is not up:
            takeaways.append(f"Posts that {down['phrase']} get {abs(down['lift'])}% fewer likes ({down['with_avg']} vs {down['without_avg']}, from {down['n']} posts).")
    len_ok = [r for r in lengths if r["n"] >= 4]
    if len(len_ok) >= 2 and overall_avg:
        b = max(len_ok, key=lambda r: r["avg"])
        takeaways.append(f"Your best length is {b['label']}: {b['avg']} likes on average from {b['n']} posts.")
    takeaways.append(f"You posted on {cal['active_days']} of the last {cal['window_days']} days ({cal['active_pct']}%). Longest streak: {_days(cal['longest_streak'])}. Longest break: {_days(cal['longest_gap'])}.")
    if momentum and momentum["posts"]["change"] is not None:
        m = momentum
        takeaways.append(f"Last 30 days: {m['posts']['now']} posts vs {m['posts']['prev']} the 30 days before ({m['posts']['change']:+d}%). Average likes: {m['likes']['now']} vs {m['likes']['prev']}.")
    if dist[0]["share"] >= 30 and len(originals) >= 10:
        takeaways.append(f"{dist[0]['share']}% of your posts got zero likes, so consistency of quality matters more than volume.")

    top = sorted(originals, key=lambda p: p["likes"] + 2 * p["rts"], reverse=True)[:5]
    return {
        "version": 2,
        # basics (also used by the plan and roadmap prompts)
        "total_posts": len(originals),
        "total_replies": len(replies),
        "thread_parts": len(threads),
        "first_post": min(all_when).strftime("%b %Y"),
        "last_post": end_dt.strftime("%b %Y"),
        "median_likes": median_likes,
        "avg_likes": overall_avg,
        "best_hours": best_hours,
        "circle": [{"name": n, "count": c, "tip": f"@{n}\n{c} interaction{'s' if c != 1 else ''} (replies and mentions)"} for n, c in circle.most_common(10)],
        "top_posts": [
            {"text": p["text"][:280], "likes": p["likes"], "rts": p["rts"], "date": p["when"].strftime("%d %b %Y")} for p in top
        ],
        # charts
        "per_month": per_month,
        "avg_likes_per_month": [{"month": a["month"], "avg": a["avg"]} for a in avg_months],
        "mix": mix,
        "likes_trend": likes_trend,
        "weekday": wd,
        "hours": hours,
        "hour_labels": [("12a" if h == 0 else f"{h % 12 or 12}{'a' if h < 12 else 'p'}") if h % 3 == 0 else "" for h in range(24)],
        "heatmap": heatmap,
        "calendar": cal,
        "formats": formats,
        "length_buckets": lengths,
        "distribution": dist,
        "momentum": momentum,
        "top_hashtags": top_tags,
        "frequent_words": frequent,
        "winning_words": winning,
        "takeaways": takeaways[:7],
    }

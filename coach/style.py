"""Account-type voices, emoji policy and slider mapping. Pure functions, easy to tune."""
import random

ACCOUNT_TYPES = {
    "individual": {
        "label": "Individual / personal brand",
        "voice": (
            "First person singular (I, my). Sounds like one real person talking to a peer. Honest, specific, "
            "opinions allowed, small stories and lessons from experience. Imperfect is fine."
        ),
        "reply_stance": "Reply as a person to a peer. Be curious, warm, willing to disagree politely.",
        "emoji_rate": 0.4,
        "emoji_max": 2,
    },
    "founder": {
        "label": "Founder / solo business",
        "voice": (
            "First person singular, building in public. Mixes personal story with the business: decisions, "
            "mistakes, numbers the user actually gave you, lessons. Confident but not salesy."
        ),
        "reply_stance": "Reply as a builder talking to other builders and customers. Practical and generous.",
        "emoji_rate": 0.35,
        "emoji_max": 2,
    },
    "creator": {
        "label": "Creator / influencer",
        "voice": (
            "First person, high personality, strong hooks, relatable moments, entertaining. Trends and humor "
            "are welcome. Talks to an audience of fans, not customers."
        ),
        "reply_stance": "Reply like a friendly creator: playful, quick, makes the other person feel seen.",
        "emoji_rate": 0.5,
        "emoji_max": 3,
    },
    "company": {
        "label": "Company / brand",
        "voice": (
            "Brand voice: we, our, us. Warm, clear, helpful and consistent. No hot takes on politics or religion, "
            "no insults, no slang overload. Assume customers, press and competitors read every line. "
            "Show the product through value, tips and customer stories (never invent customers or results)."
        ),
        "reply_stance": (
            "Reply as the brand: helpful, polite, never argumentative, never defensive. Disagree only gently "
            "and with facts. Never promise what the company has not said."
        ),
        "emoji_rate": 0.25,
        "emoji_max": 1,
    },
    "agency": {
        "label": "Agency / service provider",
        "voice": (
            "Expert team voice: we. Educational and process-focused: show how the work is done, what clients "
            "get wrong, frameworks and lessons. Results only as placeholders like [client result]. "
            "Authoritative but approachable."
        ),
        "reply_stance": (
            "Reply as a smart practitioner: add a useful insight or framework, respect the other person's "
            "expertise, never pitch services in replies."
        ),
        "emoji_rate": 0.25,
        "emoji_max": 1,
    },
    "community": {
        "label": "Community / nonprofit / ministry",
        "voice": (
            "Warm, inclusive, mission-led. Encouraging and grounded, speaks to shared values and real needs. "
            "Gratitude and invitation over hype. Respectful of every audience member."
        ),
        "reply_stance": "Reply with care and encouragement. Listen first, never preach or argue.",
        "emoji_rate": 0.3,
        "emoji_max": 2,
    },
}

ACCOUNT_TYPE_CHOICES = [(k, v["label"]) for k, v in ACCOUNT_TYPES.items()]

EMOJI_RULES = (
    "Emoji rules: use emoji only where an item is marked allowed. Where allowed use at most the stated maximum, "
    "placed where it fits naturally (end of a line or beside the idea it supports), never as bullet decoration. "
    "Vary which emoji you use and where they sit. Do not reuse the same emoji across items. Avoid cliche "
    "filler (rocket, fire, 100, clapping hands) unless it truly fits. Never use emoji on serious, sad or "
    "sensitive topics. Items marked not allowed must contain no emoji at all."
)


def account(profile_or_key):
    key = getattr(profile_or_key, "account_type", profile_or_key)
    return ACCOUNT_TYPES.get(key, ACCOUNT_TYPES["individual"])


def emoji_flags(account_type, n, strength=None, rng=random):
    """Random per-item flags: which items may contain emoji.

    strength 0..100 (optional slider): 0 = never, 50 = the account's normal rate, 100 = often.
    """
    rate = account(account_type)["emoji_rate"]
    if strength is not None:
        s = max(0, min(100, int(strength)))
        rate = 0 if s == 0 else (rate * s / 50 if s <= 50 else rate + (0.85 - rate) * (s - 50) / 50)
    return [rng.random() < rate for _ in range(n)]


def voice_context(profile):
    a = account(profile)
    return {
        "account_type": a["label"],
        "voice": a["voice"],
        "reply_stance": a["reply_stance"],
        "emoji_max_per_item": a["emoji_max"],
    }


# ---------- reply helper ----------
INTENTS = [
    ("insight", "Add insight", "Add something useful the post does not say yet."),
    ("connect", "Build a connection", "Warm and personal. Make the author feel seen."),
    ("question", "Ask a thoughtful question", "A sharp question that invites a real answer."),
    ("challenge", "Challenge an idea", "Respectful disagreement or a missing nuance."),
    ("expertise", "Show expertise", "Specific knowledge that proves you know the field."),
    ("support", "Support the author", "Back them up with something specific."),
]
INTENT_LABELS = {k: label for k, label, _ in INTENTS}
INTENT_HELP = {k: h for k, _, h in INTENTS}
DEFAULT_INTENT = "insight"

VOICES = [("my_voice", "My voice"), ("refined", "My voice, refined")]
LENGTHS = [("short", "Short", 140), ("balanced", "Balanced", 220), ("detailed", "Detailed", 280)]
LENGTH_CHOICES = [(k, label) for k, label, _ in LENGTHS]
LENGTH_MAX = {k: n for k, _, n in LENGTHS}

# Advanced tone controls (collapsed by default). Emoji is handled by emoji_flags.
SLIDERS = [
    ("formality", "Casual", "Formal"),
    ("playfulness", "Serious", "Playful"),
    ("directness", "Gentle", "Direct"),
    ("emoji", "No emoji", "More emoji"),
]


def _band(v, low, high, mid=None):
    v = max(0, min(100, int(v)))
    if v <= 20:
        return f"strongly {low}"
    if v <= 40:
        return f"leaning {low}"
    if v >= 80:
        return f"strongly {high}"
    if v >= 60:
        return f"leaning {high}"
    return mid


def slider_instructions(values):
    """Turn 0-100 slider values into plain-language instructions the model can follow."""
    out = []
    for key, low, high in SLIDERS:
        if key == "emoji":
            continue  # handled by emoji_flags
        band = _band(values.get(key, 50), low.lower(), high.lower())
        if band:
            out.append(f"{low} to {high}: {band}")
    return out


# ---------- bio tool ----------
BIO_GOALS = [
    ("personal_brand", "Build a personal brand"),
    ("clients", "Get clients"),
    ("audience", "Grow an audience"),
    ("expertise", "Showcase expertise"),
    ("hired", "Get hired"),
    ("build_in_public", "Build in public"),
    ("network", "Network & connect"),
    ("creator", "Creator / general personal brand"),
]
BIO_GOAL_LABELS = dict(BIO_GOALS)

BIO_ROLES = [
    ("founder", "Founder"),
    ("builder", "Builder / Developer"),
    ("designer", "Designer"),
    ("creator", "Creator"),
    ("writer", "Writer"),
    ("consultant", "Consultant"),
    ("student", "Student"),
    ("business", "Business"),
    ("other", "Other"),
]
BIO_ROLE_LABELS = dict(BIO_ROLES)

BIO_DIRECTIONS = [
    ("clear", "Clear & Professional", "Best for immediately communicating what you do."),
    ("personal", "Personal Brand", "More personality while keeping the positioning clear."),
    ("authority", "Authority", "Emphasizes expertise and what you know."),
    ("curiosity", "Curiosity", "Creates an interesting reason to explore the profile."),
    ("minimal", "Minimal", "Very concise and memorable."),
]

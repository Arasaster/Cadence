import hmac
import json
from datetime import timedelta
from io import StringIO

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.decorators import login_required
from django.core.management import call_command
from django.db.models import F
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import ai, fxtwitter
from .forms import ArchiveForm, BioForm, ProfileForm, ReplyForm, RoadmapForm, SignupForm
from .insights import parse_archive
from .models import AIUsage, ArchiveStats, Plan, Profile, Roadmap, Task
from .style import INTENT_LABELS, INTENTS, INTENT_HELP, LENGTH_MAX, SLIDERS

User = get_user_model()


# ---------- helpers ----------

def _profile(user):
    return Profile.objects.filter(user=user).first()


def _charge(user):
    """Count one AI call against the user's daily cap (protects your OpenRouter credits)."""
    usage, _ = AIUsage.objects.get_or_create(user=user, date=timezone.now().date())
    if usage.count >= settings.AI_DAILY_LIMIT:
        raise ai.AIError("You have reached today's AI limit. It resets tomorrow.")
    AIUsage.objects.filter(pk=usage.pk).update(count=F("count") + 1)


def _active_roadmap(user):
    return Roadmap.objects.filter(user=user, active=True).first()


def _roadmap_ctx(roadmap, today):
    """What the daily planner needs to know about where today sits in the roadmap."""
    if not roadmap:
        return None
    day = roadmap.day_number(today)
    if not 1 <= day <= roadmap.days:
        return None
    phase = roadmap.phase_for(day)
    if not phase:
        return None
    return {
        "day": day,
        "of": roadmap.days,
        "phase": {k: phase[k] for k in ("name", "focus", "actions", "content_pillars", "checkpoint")},
        "is_checkpoint_day": day == phase["end_day"],
        "target": roadmap.target,
    }


def _stats(user):
    a = ArchiveStats.objects.filter(user=user).first()
    return a.data if a else None


def _recent(user, today):
    """How much of the recent plans the user actually did. The planner uses it to size today."""
    prev = Plan.objects.filter(user=user, date__lt=today).first()
    if not prev:
        return None
    tasks = list(prev.tasks.all())
    undone = [t.title for t in tasks if not t.done]
    week = Task.objects.filter(plan__user=user, plan__date__gte=today - timedelta(days=7), plan__date__lt=today)
    total, done = week.count(), week.filter(done=True).count()
    return {
        "last_plan_date": str(prev.date),
        "last_plan_done": len(tasks) - len(undone),
        "last_plan_total": len(tasks),
        "last_plan_unfinished": undone[:6],
        "last_7_days_completion_pct": round(100 * done / total) if total else None,
    }


def _history(user, n=7):
    out = []
    for plan in Plan.objects.filter(user=user)[:n]:
        out += [t.title for t in plan.tasks.filter(kind__in=["post", "thread"])]
    return out


def _build_plan(user, profile):
    """Raises ai.AIError on failure. The existing plan is only replaced after success."""
    _charge(user)
    stats = getattr(getattr(user, "archive", None), "data", None)
    today = profile.today()
    roadmap = _active_roadmap(user)
    ctx = _roadmap_ctx(roadmap, today)
    data = ai.generate_plan(profile, _history(user), stats, today.strftime("%A"), ctx, _recent(user, today))
    Plan.objects.filter(user=user, date=today).delete()
    plan = Plan.objects.create(
        user=user,
        date=today,
        summary=data["summary"],
        roadmap=roadmap if ctx else None,
        day_number=ctx["day"] if ctx else None,
    )
    Task.objects.bulk_create([Task(plan=plan, **t) for t in data["tasks"]])
    return plan


# ---------- public pages ----------

def landing(request):
    if request.user.is_authenticated:
        return redirect("today")
    return render(request, "landing.html")


def terms(request):
    return render(request, "terms.html")


def privacy(request):
    return render(request, "privacy.html")


def signup(request):
    if request.user.is_authenticated:
        return redirect("today")
    form = SignupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"]
        user = User.objects.create_user(username=email, email=email, password=form.cleaned_data["password"])
        login(request, user)
        return redirect("onboarding")
    return render(request, "registration/signup.html", {"form": form})


# ---------- setup & settings ----------

@login_required
def onboarding(request):
    profile = _profile(request.user)
    editing = profile is not None
    form = ProfileForm(request.POST or None, instance=profile)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        obj.user = request.user
        obj.save()
        messages.success(request, "Saved. Your next plan will use these settings.")
        return redirect("today")
    return render(request, "onboarding.html", {"form": form, "editing": editing})


# ---------- today ----------

@login_required
def today(request):
    profile = _profile(request.user)
    if not profile:
        return redirect("onboarding")
    plan = Plan.objects.filter(user=request.user, date=profile.today()).first()
    now = profile.now_local()
    roadmap = _active_roadmap(request.user)
    rctx = _roadmap_ctx(roadmap, profile.today())
    ctx = {"plan": plan, "profile": profile, "now": now, "done": 0, "total": 0, "tasks": [], "roadmap": roadmap, "rctx": rctx,
           "needs_plan": plan is None, "carry": [], "show_carry": False}
    if not plan:
        # A new day (by the user's own timezone) has no plan yet. If yesterday was left unfinished, ask first.
        prev = Plan.objects.filter(user=request.user, date__lt=profile.today()).first()
        undone = list(prev.tasks.filter(done=False)) if prev else []
        if undone and not request.GET.get("go"):
            ctx.update(carry=undone, carry_date=prev.date, show_carry=True)
    if plan:
        tasks = list(plan.tasks.all())
        undone = [t for t in tasks if not t.done]
        upcoming = [t for t in undone if t.time >= now.time()]
        groups = {"post": ("post", "thread"), "reply": ("reply_session",), "other": ("engage", "review")}
        ctx.update(
            tasks=tasks,
            done=len(tasks) - len(undone),
            total=len(tasks),
            reply_total=sum(t.count or 0 for t in tasks if t.kind == "reply_session"),
            post_total=sum(1 for t in tasks if t.kind in ("post", "thread")),
            next_task=(upcoming or undone or [None])[0],
            counts={"all": len(tasks), **{g: sum(1 for t in tasks if t.kind in ks) for g, ks in groups.items()}},
            first_id=(undone or tasks or [None])[0].pk if tasks else None,
        )
    return render(request, "today.html", ctx)


@login_required
@require_POST
def generate(request):
    wants_json = request.headers.get("X-Requested-With") == "fetch"
    profile = _profile(request.user)
    if not profile:
        return JsonResponse({"ok": False, "error": "Finish setup first."}) if wants_json else redirect("onboarding")
    try:
        _build_plan(request.user, profile)
    except ai.AIError as exc:
        if wants_json:
            return JsonResponse({"ok": False, "error": str(exc)})
        messages.error(request, str(exc))
        return redirect("today")
    return JsonResponse({"ok": True}) if wants_json else redirect("today")


@login_required
@require_POST
def toggle_task(request, pk):
    task = get_object_or_404(Task, pk=pk, plan__user=request.user)
    task.done = not task.done
    task.save(update_fields=["done"])
    done, total = task.plan.progress
    return JsonResponse({"done": task.done, "done_count": done, "total": total})


# ---------- roadmap ----------

@login_required
def roadmap(request):
    profile = _profile(request.user)
    if not profile:
        return redirect("onboarding")
    form = RoadmapForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        days = int(form.cleaned_data["days"])
        stats = getattr(getattr(request.user, "archive", None), "data", None)
        try:
            _charge(request.user)
            data = ai.generate_roadmap(profile, stats, days, form.cleaned_data["note"])
        except ai.AIError as exc:
            messages.error(request, str(exc))
        else:
            Roadmap.objects.filter(user=request.user, active=True).update(active=False)
            Roadmap.objects.create(user=request.user, days=days, start_date=profile.today(), **data)
            messages.success(request, f"Your {days}-day roadmap is ready. Generate today's plan to start.")
            return redirect("roadmap")
    rm = _active_roadmap(request.user)
    view = None
    if rm:
        day = rm.day_number(profile.today())
        phases = []
        for ph in rm.phases:
            state = "done" if day > ph["end_day"] else "current" if ph["start_day"] <= day <= ph["end_day"] else "next"
            phases.append({**ph, "state": state})
        shown = max(0, min(day, rm.days))
        view = {
            "day": day,
            "shown_day": shown,
            "pct": round(100 * shown / rm.days),
            "finished": day > rm.days,
            "not_started": day < 1,
            "phases": phases,
        }
    return render(request, "roadmap.html", {"form": form, "rm": rm, "view": view, "profile": profile})


# ---------- bio ----------

@login_required
def bio(request):
    profile = _profile(request.user)
    if not profile:
        return redirect("onboarding")
    stats = _stats(request.user)
    form = BioForm(request.POST or None)
    result = regen = None
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            _charge(request.user)
            result = ai.bio_analyze(profile, d["current_bio"], d["goal"], d["role"], d["notes"], stats, d["use_data"])
        except ai.AIError as exc:
            messages.error(request, str(exc))
        else:
            regen = {"goal": d["goal"], "role": d["role"], "notes": d["notes"]}
    return render(request, "bio.html", {"form": form, "result": result, "regen": regen, "has_data": bool(stats and stats.get("version"))})


@login_required
@require_POST
def bio_refine(request):
    profile = _profile(request.user)
    if not profile:
        return JsonResponse({"ok": False, "error": "Finish setup first."})
    try:
        body = json.loads(request.body)
        action = body["action"]
        if action not in ai.BIO_ACTIONS:
            raise ValueError
        text = str(body["text"]).strip()[:300]
        if not text:
            raise ValueError
        ctx = {k: str(body["ctx"].get(k, ""))[:300] for k in ("goal", "role", "notes")}
        direction = str(body.get("direction", ""))[:60]
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return JsonResponse({"ok": False, "error": "Bad request."}, status=400)
    try:
        _charge(request.user)
        out = ai.bio_refine(profile, ctx, action, text, direction)
    except ai.AIError as exc:
        return JsonResponse({"ok": False, "error": str(exc)})
    return JsonResponse({"ok": True, **out})


# ---------- replies ----------

def _reply_ctx(form):
    d = form.cleaned_data
    return {k: d.get(k, "") for k in ("link", "post_text", "author", "author_bio", "top_replies", "post_meta")}


@login_required
def replies(request):
    profile = _profile(request.user)
    if not profile:
        return redirect("onboarding")
    stats = _stats(request.user)
    form = ReplyForm(request.POST or None)
    out = regen = None
    if request.method == "POST" and form.is_valid():
        ctx = _reply_ctx(form)
        error = None
        if not ctx["post_text"]:  # link only: read the post for them
            try:
                post = fxtwitter.fetch_post(ctx["link"])
                bits = [f"{post[k]} {label}" for k, label in (("likes", "likes"), ("replies", "replies")) if post.get(k) is not None]
                ctx.update(post_text=post["text"], author=ctx["author"] or post["author"],
                           author_bio=ctx["author_bio"] or post["author_bio"], post_meta=" · ".join(bits))
            except fxtwitter.FetchError as exc:
                error = str(exc)
        if error:
            messages.error(request, error)
        else:
            settings_ = form.settings()
            try:
                _charge(request.user)
                out = ai.reply_session(profile, ctx, settings_, stats, force=form.cleaned_data["force"])
            except ai.AIError as exc:
                messages.error(request, str(exc))
            else:
                regen = {"ctx": ctx, "settings": settings_}
    return render(request, "replies.html", {
        "form": form, "out": out, "regen": regen, "intent_help": INTENT_HELP,
        "has_examples": bool(ai.voice_examples(profile, stats)),
    })


@login_required
@require_POST
def reply_fetch(request):
    try:
        return JsonResponse({"ok": True, **fxtwitter.fetch_post(request.POST.get("link", ""))})
    except fxtwitter.FetchError as exc:
        return JsonResponse({"ok": False, "error": str(exc)})


@login_required
@require_POST
def reply_one(request):
    """Rework a single reply: regenerate, try another angle, make it shorter, make it more natural."""
    profile = _profile(request.user)
    if not profile:
        return JsonResponse({"ok": False, "error": "Finish setup first."})
    try:
        body = json.loads(request.body)
        mode = body["mode"]
        if mode not in ai.REPLY_MODES:
            raise ValueError
        ctx = {k: str(body["ctx"].get(k, ""))[:1500] for k in ("link", "post_text", "author", "author_bio", "top_replies", "post_meta")}
        raw = body["settings"]
        settings_ = {
            "intent": raw.get("intent") if raw.get("intent") in INTENT_LABELS else "insight",
            "voice": raw.get("voice") if raw.get("voice") in ("my_voice", "refined") else "my_voice",
            "length": raw.get("length") if raw.get("length") in LENGTH_MAX else "balanced",
            "instruction": str(raw.get("instruction", ""))[:160],
            "sliders": {k: max(0, min(100, int(raw.get("sliders", {}).get(k, 50)))) for k, _, _ in SLIDERS},
        }
        current = str(body.get("current", ""))[:400]
        approach = str(body.get("approach", ""))[:24]
        used = [str(x)[:24] for x in body.get("used", [])][:8]
    except (ValueError, KeyError, TypeError, AttributeError, json.JSONDecodeError):
        return JsonResponse({"ok": False, "error": "Bad request."}, status=400)
    try:
        _charge(request.user)
        out = ai.reply_edit(profile, ctx, settings_, mode, current, approach, used, _stats(request.user))
    except ai.AIError as exc:
        return JsonResponse({"ok": False, "error": str(exc)})
    return JsonResponse({"ok": True, **out})


# ---------- insights ----------

@login_required
def insights(request):
    profile = _profile(request.user)
    if not profile:
        return redirect("onboarding")
    form = ArchiveForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        raw = form.cleaned_data["file"].read().decode("utf-8", errors="ignore")
        try:
            data = parse_archive(raw, profile.timezone, profile.x_username)
        except (ValueError, json.JSONDecodeError) as exc:
            messages.error(request, str(exc) or "Could not read that file.")
        else:
            ArchiveStats.objects.update_or_create(user=request.user, defaults={"data": data})
            messages.success(request, "Archive analysed. Your plans will now use it.")
            return redirect("insights")
    stats = ArchiveStats.objects.filter(user=request.user).first()
    return render(request, "insights.html", {"form": form, "stats": stats.data if stats else None, "profile": profile})


# ---------- scheduler hook ----------

@csrf_exempt
def cron(request, job):
    """Lets any scheduler (cron-job.org, Render, Vercel Cron) trigger the daily jobs over HTTPS."""
    secret = settings.CRON_SECRET
    sent = request.headers.get("Authorization", "")
    if not secret or not hmac.compare_digest(sent, f"Bearer {secret}"):
        return HttpResponse("Forbidden", status=403)
    commands = {"reminders": ("send_reminders",), "plans": ("generate_plans", "--email")}
    if job not in commands:
        raise Http404
    out = StringIO()
    call_command(*commands[job], stdout=out)
    return HttpResponse(out.getvalue(), content_type="text/plain")

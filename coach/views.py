import hmac
import json
from io import StringIO

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.decorators import login_required
from django.core.management import call_command
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import ai
from .forms import ArchiveForm, ProfileForm, ReplyForm, SignupForm
from .insights import parse_archive
from .models import ArchiveStats, Plan, Profile, Task

User = get_user_model()


def landing(request):
    if request.user.is_authenticated:
        return redirect("today")
    return render(request, "landing.html")


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


def _profile(user):
    return Profile.objects.filter(user=user).first()


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


def _history(user, n=7):
    out = []
    for plan in Plan.objects.filter(user=user)[:n]:
        out += [t.title for t in plan.tasks.filter(kind__in=["post", "thread"])]
    return out


def _build_plan(user, profile):
    stats = getattr(getattr(user, "archive", None), "data", None)
    today = profile.today()
    weekday = today.strftime("%A")
    try:
        data, source = ai.generate_plan(profile, _history(user), stats, weekday)
    except Exception as exc:  # network, auth, bad JSON after retry
        data, source = ai.demo_plan(profile, weekday), "demo"
        data["summary"] += f" (AI error: {type(exc).__name__}. Showing a demo plan.)"
    Plan.objects.filter(user=user, date=today).delete()
    plan = Plan.objects.create(user=user, date=today, summary=data["summary"], source=source)
    Task.objects.bulk_create([Task(plan=plan, **t) for t in data["tasks"]])
    return plan


@login_required
def today(request):
    profile = _profile(request.user)
    if not profile:
        return redirect("onboarding")
    plan = Plan.objects.filter(user=request.user, date=profile.today()).first()
    now = profile.now_local()
    ctx = {"plan": plan, "profile": profile, "now": now, "done": 0, "total": 0, "tasks": []}
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
    profile = _profile(request.user)
    if not profile:
        return redirect("onboarding")
    plan = _build_plan(request.user, profile)
    if plan.source == "demo":
        messages.info(request, "Demo plan created. Add OPENROUTER_API_KEY to get tailored posts.")
    return redirect("today")


@login_required
@require_POST
def toggle_task(request, pk):
    task = get_object_or_404(Task, pk=pk, plan__user=request.user)
    task.done = not task.done
    task.save(update_fields=["done"])
    done, total = task.plan.progress
    return JsonResponse({"done": task.done, "done_count": done, "total": total})


@login_required
def replies(request):
    profile = _profile(request.user)
    if not profile:
        return redirect("onboarding")
    form = ReplyForm(request.POST or None)
    result = None
    if request.method == "POST" and form.is_valid():
        try:
            result, source = ai.reply_angles(profile, form.cleaned_data["post_text"], form.cleaned_data["author"])
        except Exception as exc:
            result, source = ai.demo_angles(), "demo"
            messages.error(request, f"AI error ({type(exc).__name__}). Showing demo angles.")
        result["source"] = source
    return render(request, "replies.html", {"form": form, "result": result})


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
            messages.success(request, "Archive analysed. Your daily plans will now use it.")
            return redirect("insights")
    stats = ArchiveStats.objects.filter(user=request.user).first()
    return render(request, "insights.html", {"form": form, "stats": stats.data if stats else None, "profile": profile})


@csrf_exempt
def cron(request, job):
    """Lets any scheduler (Vercel Cron, cron-job.org, Railway) trigger the daily jobs over HTTPS."""
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

# Cadence — daily X coach (MVP)

Django app. The user posts manually; the app plans, writes, and reminds. Nothing connects to X, so no API cost and no bot risk.

## Run it on your machine

```bash
python -m venv venv && source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                  # then edit .env
python manage.py migrate
python manage.py runserver
```
Open http://localhost:8000, sign up, fill the setup form, tap **Generate today's plan**.

- No `OPENROUTER_API_KEY` in `.env` → **demo mode** (template plan, so you can test the UI and flow).
- With a key → tailored posts, reply angles, and plans in your voice.

## Features
Account types (individual, founder, creator, company, agency, community) change the voice of everything. 30 or 60 day
roadmap that drives the daily Today tasks. Bio upgrader. Reply helper with post-link fetch (FixTweet), angle picker,
sliders and per-angle regenerate. Natural emoji (shuffled in code, capped per account type). Landing page, Terms, Privacy.

## New day, new tasks
Today's plan is built automatically the first time you open Today on a new day (by your own timezone). If yesterday
has unfinished tasks you can tick them or continue without finishing; the planner is told what was actually done and
makes the day lighter if a lot was skipped. "Rebuild plan" replaces today's plan.

## Email
Render's free plan blocks SMTP ports, so the app sends through Brevo's HTTPS API.
1. Create a Brevo account, verify a sender, create an API key.
2. Set `BREVO_API_KEY` and `DEFAULT_FROM_EMAIL=Cadence <your-verified-sender>`.
3. In the app: Settings, then "Send me a test email".
A verified sender on your own domain delivers best. Free mail addresses (Gmail and similar) often land in spam.
Without `BREVO_API_KEY`, emails print in the server console.

## Daily automation
```bash
python manage.py generate_plans --email     # once each morning
python manage.py send_reminders             # every 5-10 minutes
```
On a host, use the `/cron/...` URLs below instead.

## Where to tune things

| File | What |
|---|---|
| `coach/prompts.py` | All prompt engineering: plan rules, reply rules, JSON schemas |
| `coach/ai.py` | AI calls, JSON validation, demo fallback |
| `coach/insights.py` | Parses tweets.js from the user's own X archive |
| `coach/templates/`, `static/coach/app.css` | UI |

## Supabase database

1. In Supabase, create a project and save the database password.
2. Open the project's **Connect** panel and copy a **pooler** connection string (not the direct one, which is IPv6-only on many hosts).
   - Railway or any always-on server: **Session pooler**, port 5432.
   - Vercel or other serverless: **Transaction pooler**, port 6543 (the app switches on the needed settings automatically).
3. Put your password in the string. If it has symbols like `@ # / :`, URL-encode them (for example `@` becomes `%40`).
4. Set it as `DATABASE_URL` in your host, then run migrations once: `DATABASE_URL="..." python manage.py migrate` (Railway's `Procfile` also does this on every deploy).
5. **Lock the tables.** Supabase can expose tables through its public API. Run this once in the Supabase SQL editor after migrating, and again after any future migration that adds tables:

```sql
DO $$ DECLARE r record; BEGIN
  FOR r IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', r.tablename);
  END LOOP;
END $$;
```
Django connects with the database owner role, which is not affected by this, so the app keeps working. You can also turn off the project's Data API since this app doesn't use it.

## Deploy: GitHub, Render, Supabase

1. **Supabase:** create a project. Go to Connect, copy the **Session pooler** connection string (host ends in `pooler.supabase.com`, port 5432, user looks like `postgres.yourprojectref`), and put your database password in it. Do not use the "Direct connection" string: Render's free plan is IPv4 only and that one is IPv6 only.
2. **GitHub:** push this folder to a repo. `.env` is already git-ignored.
3. **Render:** New, then Blueprint (reads `render.yaml`), or New Web Service with:
   - Build: `pip install -r requirements.txt && python manage.py collectstatic --noinput && python manage.py migrate --noinput`
   - Start: `gunicorn config.wsgi --timeout 120` (AI calls can take a while)
4. **Environment variables** (the Blueprint creates the list): `DATABASE_URL` (the Supabase string), `OPENROUTER_API_KEY`, `SITE_URL` (your onrender.com URL), `BREVO_API_KEY`, `DEFAULT_FROM_EMAIL`, `CONTACT_EMAIL`. `DJANGO_SECRET_KEY` and `CRON_SECRET` are generated for you. Copy `CRON_SECRET` for step 5.
5. **Schedulers** (Render cron is paid, so use free cron-job.org). Call these URLs with the header `Authorization: Bearer <CRON_SECRET>`:
   - `GET /cron/plans/` once each morning (creates plans and emails the summary)
   - `GET /cron/reminders/` every 5 to 10 minutes (emails due tasks)

Render's free web service sleeps after about 15 minutes idle and takes ~30 seconds to wake. The 5-10 minute reminder ping also keeps it awake.

## Known limits (v1)
- Insights come from the uploaded archive: posting trend, best hours, top posts, circle. The archive has no follower-count history, so "growth" means activity and engagement.
- Reply tasks give guidance and angles, not bulk text, on purpose.

## Before you launch
- Have a lawyer review `terms.html` and `privacy.html`. They are a starting draft.
- Set `CONTACT_EMAIL`, `COMPANY_NAME` and `GOVERNING_LAW` so the legal pages are correct.
- The post-link fetch uses FixTweet, an unofficial service. It can change or fail; the paste fallback always works.

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

## Daily automation (cron or Railway cron)

```bash
python manage.py generate_plans --email     # once each morning
python manage.py send_reminders             # every 5-10 minutes
```
Emails print to your terminal until you set SMTP values in `.env` (Brevo, Resend SMTP, Gmail app password).

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
   - Start: `gunicorn config.wsgi`
4. **Environment variables** (the Blueprint creates the list): `DATABASE_URL` (the Supabase string), `OPENROUTER_API_KEY`, `SITE_URL` (your onrender.com URL), SMTP values. `DJANGO_SECRET_KEY` and `CRON_SECRET` are generated for you. Copy `CRON_SECRET` for step 5.
5. **Schedulers** (Render cron is paid, so use free cron-job.org). Call these URLs with the header `Authorization: Bearer <CRON_SECRET>`:
   - `GET /cron/plans/` once each morning (creates plans and emails the summary)
   - `GET /cron/reminders/` every 5 to 10 minutes (emails due tasks)

Render's free web service sleeps after about 15 minutes idle and takes ~30 seconds to wake. The 5-10 minute reminder ping also keeps it awake.

## Known limits (v1)
- Insights come from the uploaded archive: posting trend, best hours, top posts, circle. The archive has no follower-count history, so "growth" means activity and engagement.
- Reply tasks give guidance and angles, not bulk text, on purpose.

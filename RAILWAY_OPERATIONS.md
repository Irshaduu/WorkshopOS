# Railway Operations Manual — WorkshopOS (Titan)

**What this is:** everything about running this app on Railway — creating the
project, deploying, shipping updates after go-live, the database, backups,
costs, and what to do when something breaks.

**What it is not:** the go-live procedure. That is `GO_LIVE_RUNBOOK.md`, a
one-time ordered checklist. This file is the reference you come back to for the
next two years. Where they overlap, the runbook links here rather than
repeating.

> Written as Markdown rather than `.txt` on purpose: `.gitignore` carries a
> blunt `*.txt` rule, so a `.txt` file would be silently absent from the repo —
> the same trap that nearly lost `robots.txt`.

---

## 1. The mental model

Five words to understand before touching anything.

| Term | What it means here |
|---|---|
| **Project** | The container for everything. One project = WorkshopOS production. |
| **Service** | One running thing. You have two: `web` (Django) and `Postgres`. |
| **Environment** | A copy of the services. You need only `production`. Ignore the rest. |
| **Variables** | Environment variables per service. This is where all configuration lives. |
| **Deployment** | One build+run of your code. Railway keeps the old ones so you can roll back. |

### The single most important fact

**The container filesystem is ephemeral.** Everything written while the app runs
— uploaded files, generated reports, database dumps — is **destroyed on the next
deploy**, and deploys happen every time you push code.

Only two things survive: the **Postgres service** (separate, with its own
storage) and anything you **download off the machine**.

Consequences that bite in practice:

- `manage.py backup_db` writes into `BASE_DIR/backups` — inside the container.
  On Railway that backup is deleted by the next deploy. **See §6.**
- Uploaded brand logos do not survive, and are not served at all in production
  anyway. **See §11.**
- Log files on disk are pointless. Use Railway's log viewer.

---

## 2. Creating the production project from scratch

Do this once. It takes about 20 minutes.

### 2.1 The account — use a neutral email, and decide ownership later

Who ends up owning this — you, the workshop, or both — does **not** have to be
settled before you deploy. Only one choice actually locks you in:

**Do not create the account with your personal email.** An account is bound to
the address that made it, and that is the thing that is hard to undo.

Create one neutral identity and use it for Railway, for Resend, and for anything
else the system comes to depend on:

- a mailbox on the domain the workshop already owns —
  `system@formuladservice.in`, or
- a dedicated address created for the purpose

All three outcomes then cost nothing to choose later:

| Outcome | What it takes |
|---|---|
| The workshop holds everything | Hand over the password |
| You hold and maintain it | Keep the password; the owners never need it |
| Shared | Both know it |

**The trap:** if you enable 2FA on that account with only your phone, you have
made it yours whatever the email says — handing over the password later still
leaves the owners locked out. If handover is even a possibility, **save the 2FA
recovery codes and give the owners a copy at setup**, not when you need them.

For reference, what is genuinely easy to move later: Railway projects transfer
between accounts, GitHub repos transfer in a few clicks, environment variables
are just text, and the domain is already the workshop's. The email and the 2FA
holder are the only real one-way doors.

Note that **who owns the hosting account and who owns the code are separate
questions**, the second being commercial rather than technical. The repository
currently lives in a personal GitHub account. That is an ordinary arrangement
for freelance work — it is only a problem if it was never stated out loud.

### 2.2 Subscribe to Hobby

$5/month, which includes $5 of usage credit. Do this before deploying so you are
not racing a trial expiry.

### 2.3 Create the project and the two services

1. New Project → Deploy from GitHub repo → select `WorkshopOS`
2. In the same project: New → Database → **Add PostgreSQL**

Both services now live in one project and can talk over Railway's private
network.

### 2.4 Configure the `web` service

**Settings → Build:**

| Field | Value |
|---|---|
| Custom Build Command | `python manage.py collectstatic --noinput` |

**Settings → Deploy:**

| Field | Value |
|---|---|
| Pre-deploy Command | `python manage.py migrate --noinput` |
| Custom Start Command | `gunicorn formulad_workshop.wsgi:application` |

Do **not** put `migrate` in the start command as well. Pre-deploy runs once per
deployment; the start command runs on every container start.

**Leave Serverless OFF.** See §8.

### 2.5 Variables

See §3 for the full list.

### 2.6 Custom domain

Settings → Networking → Add `app.formuladservice.in`, then add the CNAME Railway
gives you at your DNS provider. HTTPS is issued automatically within a few
minutes.

---

## 3. Environment variables — full reference

Set these on the **`web`** service. The Postgres service configures itself.

### Required — the app will not work without them

| Variable | Value / where it comes from |
|---|---|
| `DJANGO_ENV` | `production` — **no default exists; the app refuses to boot without it** |
| `SECRET_KEY` | A fresh random string. Never reuse the development one. |
| `DEBUG` | `False` |
| `ALLOWED_HOSTS` | `app.formuladservice.in` |
| `CSRF_TRUSTED_ORIGINS` | `https://app.formuladservice.in` |
| `DB_NAME` | `${{Postgres.PGDATABASE}}` |
| `DB_USER` | `${{Postgres.PGUSER}}` |
| `DB_PASSWORD` | `${{Postgres.PGPASSWORD}}` |
| `DB_HOST` | `${{Postgres.PGHOST}}` |
| `DB_PORT` | `${{Postgres.PGPORT}}` |
| `DB_SSLMODE` | `prefer` — **not `require`**, see below |

Generate the secret key:

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

**Use the `${{Postgres.*}}` reference syntax, not pasted values.** Railway
substitutes them at deploy time, so rotating the database password does not
silently break the app.

**Why `DB_SSLMODE=prefer`:** `base.py` defaults to `require`, which suited a
hosted database reached over the public internet and is wrong for every
environment in use today. Railway's Postgres is reached over the project's
private network, where TLS may not be offered — `require` then refuses to connect
at all. (Local development sets `disable`.) `prefer` uses TLS when available and plain when not, and the
traffic never leaves Railway's network either way.

### Required for email (password reset)

| Variable | Value |
|---|---|
| `RESEND_API_KEY` | From the Resend dashboard |
| `DEFAULT_FROM_EMAIL` | `Formula D <noreply@mail.formuladservice.in>` |
| `BUSINESS_NAME` | `Formula D` |

### Required for push notifications

| Variable | Notes |
|---|---|
| `VAPID_PUBLIC_KEY` | **Set once, never change** |
| `VAPID_PRIVATE_KEY` | **Set once, never change** |
| `VAPID_ADMIN_EMAIL` | A contact address |

**Regenerating VAPID keys invalidates every existing push subscription**, and
every owner then has to re-enable push by hand on their own phone. Treat them as
permanent. Generating fresh ones is only safe before anyone has subscribed.

### Required for photos

| Variable | Notes |
|---|---|
| `PHOTO_S3_ACCOUNT_ID` | Cloudflare account id — the R2 endpoint hostname is built from it |
| `PHOTO_S3_ACCESS_KEY_ID` | R2 API token |
| `PHOTO_S3_SECRET_ACCESS_KEY` | R2 API token secret |
| `PHOTO_S3_BUCKET` | **A different bucket from development** |
| `PHOTO_S3_PREFIX` | Optional key prefix. Leave unset when using separate buckets. |

**If the payment card is not available**, Cloudflare will not let you create an
R2 bucket at all — it wants a card on file even for the free tier. Supabase
Storage needs none and speaks the same protocol, so it is a drop-in: set
`PHOTO_S3_ENDPOINT=<project-ref>.storage.supabase.co`,
`PHOTO_S3_PATH_PREFIX=storage/v1/s3`, `PHOTO_S3_REGION=<your region>`, and the
three keys above. Moving to R2 later is the same three settings again. Verified
end to end against Supabase: signed PUT, signed GET, signed
DELETE, and the `Content-Disposition` filename override all behave as S3 does,
and the browser upload needs no CORS configuration there.

Two Supabase caveats that do not apply to R2: the free tier is **1 GB** (against
~1.8 GB/year of real use), and a free project **pauses after about a week
idle** — open the dashboard before a demo. Both are reasons it is a bridge and
not the destination.

**Whichever provider, set the bucket to PRIVATE and cap it.** Every read the app
performs is a signed URL, so public access buys nothing and makes the bucket
enumerable. Also set a **2 MB file-size limit** and restrict the MIME type to
`image/jpeg`: the app checks the size the browser declares before signing, but a
presigned PUT cannot enforce it, so the bucket-level limit is the only real
ceiling. The uploader emits ~200 KB, so 2 MB is ten times the headroom it needs.

**Set none of them and photos are simply off in production** — the box does not
render, the endpoints answer 503, and everything else behaves identically. The
local-disk backend is DEBUG-only on purpose and can never engage here, because
this filesystem is wiped on every deploy.

Railway sells no object storage — only Volumes, which `backup_db` cannot see.
Photos are evidence in a damage dispute, so they go to Cloudflare R2 instead:
free at this workshop's volume (~1.8 GB/year against a 10 GB tier), zero egress,
and independent of the host. See the photos entry in `CLAUDE.md` for the
reasoning.

**Each bucket needs a CORS policy or every upload fails**, with a browser error
that looks exactly like a signing bug:

```json
[{ "AllowedOrigins": ["https://your-app-domain"],
   "AllowedMethods": ["PUT", "GET"],
   "AllowedHeaders": ["content-type"] }]
```

**Use separate buckets for development and production.** They are free, and a
shared bucket means `purge_business_data` run against dev queues deletions for
real photos.

These are all optional. With none of them set the photo box is not rendered, the
endpoints answer 503, and the rest of the app is unaffected — the same
degradation push has with no VAPID keys.

**Schedule `sweep_photo_blobs --yes`** (a Railway cron, monthly is plenty). It
deletes storage objects whose database rows are gone. It is time-based and
idempotent, so it is safe to run twice and safe to skip for months.

**`purge_old_photos --yes` is the retention sweep**, and is deliberately NOT
scheduled out of the box. It deletes photos older than a year — except on cards
still `PENDING` or `PARTIAL`, which it always keeps, because an unpaid year-old
bill is an open argument and those photos are the evidence in it. Run it by hand
first and read the dry run. Once you are happy, a monthly Railway cron is the
right home for it; run it *before* `sweep_photo_blobs` so the objects it queues
are collected in the same pass.

### Not used in production

`EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`,
`EMAIL_USE_TLS` — these drive the SMTP backend, which production does not use
(Railway blocks outbound SMTP below the Pro plan). Leave them unset. Development
still uses them.

---

## 4. How a deploy actually works

```
git push origin main
   ↓
Railway detects the push
   ↓
BUILD      pip install -r requirements.txt
           python manage.py collectstatic --noinput
   ↓
PRE-DEPLOY python manage.py migrate --noinput
   ↓
START      gunicorn formulad_workshop.wsgi:application
   ↓
Railway switches traffic to the new container
```

Things worth knowing:

- **A failed build does not take the site down.** The old deployment keeps
  serving until the new one is healthy.
- **A failed pre-deploy stops the release.** If `migrate` errors, the new
  version never takes traffic. This is the behaviour you want.
- **`collectstatic` runs at build time**, so `staticfiles/` is baked into the
  image. That is why it survives, while runtime-written files do not.
- ⚠ **Without the Build Command the app does not start degraded — it 500s.**
  Every frontend asset now ships from `static/vendor/` instead of a CDN, and the
  manifest storage raises on a missing entry rather than emitting a dead link.
  The setting lives in the Railway dashboard, not in the repo, so a new service
  starts without it.
- Deploy logs and build logs are separate tabs. Build problems are in Build.

---

## 5. Shipping an update after go-live

The system is now running a business. Treat every deploy accordingly.

### 5.1 The safe sequence

```bash
# 1. Work locally, with the full test suite green
python manage.py test workshop inventory

# 2. Commit
git add -A
git commit -m "..."

# 3. BACK UP THE DATABASE FIRST — see §6
#    Especially if this change includes a migration.

# 4. Push. Railway builds and deploys automatically.
git push origin main
```

### 5.2 After every deploy — 60 seconds of checking

- ☐ Deploy log shows the container started, no crash loop
- ☐ Open the app, hard-refresh, check the browser console for errors
- ☐ Exercise the thing you changed
- ☐ Check one unrelated page still works

### 5.3 Deploys that need extra care

| Change | Why, and what to do |
|---|---|
| **Any migration** | Back up first. A migration is the one thing a rollback cannot undo. |
| **A destructive migration** (dropping a column/table) | The old code cannot run against the new schema. Rolling back needs a database restore too. |
| **Changing `ALLOWED_HOSTS` / domain** | Get it wrong and every request 400s. |
| **Anything touching auth** | Keep a signed-in session open in another browser so you are not locked out while testing. |
| **New environment variable** | Set it in Railway **before** pushing the code that reads it. |

### 5.4 Best time to deploy

The workshop runs 09:00–20:00 and owners use it at night. There is no dead
window, so prefer **early morning**, and never deploy something risky on a
Friday evening.

### 5.5 Rolling back

Railway → Deployments → find the last good one → **Redeploy**.

**A rollback does not undo a migration.** If the bad deploy changed the schema,
you must roll the code back *and* restore the database, or old code meets a
schema it does not understand.

---

## 6. Database — backups and restore

**The most important section in this file.**

### 6.1 The trap

`manage.py backup_db` writes into `BASE_DIR/backups` — inside the container.
On Railway that directory is **destroyed by the next deploy**. Running it there
and walking away produces nothing.

It remains a perfectly good command; it is just built for a machine with a real
disk. On Railway you must either download the file immediately, or use §6.3.

### 6.2 Railway's own backups — turn these on

Railway → Postgres service → Backups.

- ☐ Enable them
- ☐ Note the retention period
- ☐ **Restore one at least once** so you have done it before you need it

These are your first line of defence and they need no effort once enabled.

### 6.3 Your own backup, onto your machine

Do this before any migration, and on a routine schedule (§9).

Railway gives the Postgres service a **public** connection string as well as the
private one. Use the public one, from your own machine, so the file lands on a
disk that persists:

```bash
# Get the public connection string from:
#   Railway → Postgres → Variables → DATABASE_PUBLIC_URL

pg_dump "<DATABASE_PUBLIC_URL>" -Fc -f titan_2026-08-10.dump
```

`-Fc` is custom format, restored with `pg_restore`. Requires the PostgreSQL
client tools installed locally.

Then **copy it somewhere that is not Railway and not only your laptop** — Google
Drive, an external disk, anywhere. A backup on the same machine as the only copy
of everything else is not a backup.

### 6.4 Restoring

Into a scratch database first, always. Never practise on production.

```bash
# create an empty target, then:
pg_restore -d "<target connection string>" --clean --if-exists titan_2026-08-10.dump
```

**Record the command that actually worked** in `GO_LIVE_RUNBOOK.md` §5.2. You
want it in front of you on a bad day, not reconstructed from memory.

### 6.5 Routine database maintenance

Postgres is largely self-maintaining and this database is small. There is no
vacuum schedule to run, no index rebuild, no tuning to do.

What is worth watching, monthly:

- **Size.** ~50 job cards a month is tiny; if it ever grows unexpectedly,
  something is wrong (a runaway loop, a table filling with junk).
- **The `Notification` table.** Read rows are swept after 14 days, unread are
  kept forever. Millions of rows here would mean the sweep is not running.
- **`UserSession` rows**, which grow with every device/session.

### 6.6 Never do these

- **Never run `purge_business_data` against production** after go-live. It
  clears every business table. It exists for resetting demo data.
- **Never run the test suite against production.** It CREATEs and DROPs a
  database. The settings prevent it (tests force SQLite), but do not go looking
  for a way around that.
- **Never edit data directly in a SQL client** unless you have a backup from
  minutes earlier. The app maintains denormalised totals (`total_bill_amount`,
  shop balances, stock counts) through signals — a raw `UPDATE` bypasses all of
  it and leaves figures that disagree with each other.

---

## 7. Monitoring — what to look at, and when

| Where | What it tells you | Check when |
|---|---|---|
| **Deployments** | Did the last deploy succeed | Every deploy |
| **Deploy Logs** | Runtime errors, crash loops | After a deploy; when something is wrong |
| **Build Logs** | `collectstatic` output, pip failures | When static files or dependencies misbehave |
| **Metrics** | Memory, CPU, network | Monthly, and when the bill surprises you |
| **Postgres → Metrics** | Database size and connections | Monthly |

There is deliberately no error-tracking service (Sentry and similar). With four
users who will phone you, the logs are enough. Revisit only if that stops being
true.

**What a healthy deploy log looks like:**

```
Starting Container
[INFO] Starting gunicorn 26.0.0
[INFO] Listening at: http://0.0.0.0:8080 (1)
[INFO] Booting worker with pid: 2
```

Gunicorn writes its startup lines to stderr, so Railway tags them `[err]`. That
is normal and not an error.

---

## 8. Cost control

### What you actually pay for

Railway bills **per second of resource usage**: memory at ~$0.0139/GB-hour and
CPU at ~$0.0278/vCPU-hour. Hobby is a $5/month subscription that **includes** $5
of usage — beyond that you pay the difference.

For this workload (4–6 logins, ~50 job cards a month) the cost is driven almost
entirely by **the two containers existing 24/7**, not by anything they do.
Expect roughly **$5–9/month**.

**Do not trust that estimate — measure it.** Railway → service → Metrics shows
your real memory and CPU. Your workload will not grow much.

### Three things to do

1. **Set a spending limit** — around $20. High enough never to trip normally,
   low enough that a runaway process cannot produce a $200 bill. This is the
   single best protection you have.
2. **Delete the test project** once production is live, or you pay for both.
3. **Check usage monthly** for the first three months, then stop worrying.

### Do not enable Serverless

It scales containers to zero and would genuinely cut the bill. It is the wrong
trade here: requests queue while the container wakes, so the owners hit a cold
start every time they open Analysis at night, and the Floor tablet feels broken
after any quiet spell. This is a business system; it must feel instant during
shop hours.

### Do not "optimise" by dropping to one service

Running Postgres inside the app container to save ~$2/month puts the database on
the **ephemeral filesystem**, where a deploy destroys it. This is not a
theoretical risk; it is the guaranteed outcome.

---

## 9. Maintenance schedule

### Weekly (2 minutes)
- ☐ Open the app, confirm it loads
- ☐ Skim deploy logs for repeated errors

### Monthly (15 minutes)
- ☐ **Take a manual backup and move it offsite** (§6.3)
- ☐ Check Railway usage against the $5 credit
- ☐ Check database size
- ☐ Confirm Railway's own backups are still running

### Quarterly (1 hour)
- ☐ **Restore a backup into a scratch database.** A backup you have not restored
  in three months is a backup you are guessing about.
- ☐ Update dependencies: `pip list --outdated`, then Django patch releases.
  Run the full test suite before deploying.
- ☐ Confirm password reset still works end to end — Resend keys and domain
  verification can lapse silently.

### Yearly
- ☐ Renew `formuladservice.in` (or confirm auto-renew — a lapsed domain takes
  the app down *and* the public site)
- ☐ Review the Django version against its support window
- ☐ Rotate `SECRET_KEY` if you have reason to think it leaked (this signs out
  everyone)

---

## 10. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Pages load but unstyled; console says `Refused to apply style … MIME type ('text/html')` | `collectstatic` did not run | Check Build Command and the build log. Confirm the storage backend: `manage.py shell -c "from django.contrib.staticfiles.storage import staticfiles_storage; print(staticfiles_storage.__class__)"` — it must be the WhiteNoise one |
| `DisallowedHost` / 400 on every request | `ALLOWED_HOSTS` missing the domain | Add it, redeploy |
| CSRF failures on every form | `CSRF_TRUSTED_ORIGINS` missing `https://` prefix or the domain | Fix the variable |
| App will not boot, `ImproperlyConfigured` | `DJANGO_ENV` not set | Set it to `production` |
| Cannot connect to database | `DB_SSLMODE=require` on the private network | Set `prefer` |
| Yellow Django traceback in the browser | `DEBUG` is True — **serious, leaks settings and SQL** | Set `DEBUG=False`, confirm `DJANGO_ENV=production`, redeploy immediately |
| Reset emails not arriving | Resend key, domain verification, or rate limit | Search deploy logs for `Resend rejected a message` — the reason is logged |
| Deploy stuck / crash loop | Start command wrong, or the app raises on boot | Read Deploy Logs top to bottom |
| Both owners locked out | — | `GO_LIVE_RUNBOOK.md` §5.3 |
| `Address already in use` in the console | You tried to start gunicorn manually | Don't — the container already runs it. Console is for `manage.py` commands only |

### Using the Railway console safely

Railway → service → Console gives you a shell in the running container.

**Safe:** `manage.py migrate`, `manage.py shell`, `manage.py sync_owner_identity`,
reading files.

**Not safe:** starting `gunicorn` (port already bound), anything writing files
you expect to keep (ephemeral), `purge_business_data`.

---

## 11. Known limitations of this deployment

Documented so they are not rediscovered as emergencies.

### Uploaded images do not work in production

`CarBrand.logo_image` is the **one** `ImageField` left in the codebase, and
`CarBrandForm` exposes it. In production it is **silently broken**:
`formulad_workshop/urls.py` serves media through Django's `static()` helper,
which returns an empty list when `DEBUG=False` — so the file is written, never
servable (404), and destroyed by the next deploy.

Decorative master data, so not a go-live blocker. If you want it working, it
needs a Railway Volume mounted at `/app/media` plus a way to serve `MEDIA_URL`.
If you do not, removing `logo_image` from `CarBrandForm` is more honest than an
upload button that does nothing. Logged as `AUD-0088` in `TECH_DEBT.md`.

*(An earlier version of this note also named `CarModel.sample_image`. That field
does not exist — `CarModel` carries only `brand`, `name` and `created_at`.)*

⚠ **This is not the photo path.** Job-card photos never touch the Django
filesystem — the browser PUTs them straight to the bucket on a presigned URL, so
they are unaffected by any of the above. See §3 "Required for photos".

### No staging environment

You deploy from `main` straight to production. The mitigation is the test suite
plus §5.2's post-deploy check. Adding a staging environment doubles the bill for
a system with four users.

### No CI

Nothing runs the tests on push. **Run them locally before you push.** This is a
discipline, not a tool.

### No uptime monitoring

Nobody is alerted if the app goes down. Four users will phone you, which at this
scale is faster than any alert.

### Deploys are not zero-downtime in the strict sense

There is a brief switchover. At this traffic level nobody will see it.

---

## 12. Quick reference

```bash
# Local development
$env:DJANGO_ENV = "development"        # PowerShell
python manage.py runserver

# Full test suite (SQLite, 20-80 min — load-dependent)
python manage.py test workshop inventory

# Ship an update
python manage.py test workshop inventory
git add -A && git commit -m "..." && git push origin main

# Backup onto YOUR machine (not the container)
pg_dump "<DATABASE_PUBLIC_URL>" -Fc -f titan_$(date +%F).dump

# In the Railway console (management commands only)
python manage.py migrate
python manage.py sync_owner_identity --yes
python manage.py set_owner_email <user> <email> --yes
```

| Doc | Owns |
|---|---|
| `GO_LIVE_RUNBOOK.md` | The one-time go-live procedure |
| `RAILWAY_OPERATIONS.md` | This file — the platform, day to day |
| `CLAUDE.md` | How to work in the codebase; deliberate decisions |
| `TITAN_MASTER_HANDOVER.md` | Mission, roadmap, deliberately out of scope |
| `TECH_DEBT.md` | Known issues, not yet scheduled (local only) |

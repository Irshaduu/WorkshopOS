# Go-Live Runbook — WorkshopOS (Titan)

**What this is:** the ordered steps to put this system into real use at Formula
D, written to be followed rather than remembered. Tick items off as you go.

**Who it is for:** the person doing the deployment. It assumes no prior
production experience and states the reason for each step, because a step whose
purpose you understand is one you can recover from.

**The one rule:** if a step fails, stop and read the error. Do not run the next
step hoping it clears. Every destructive command here has a dry run — use it.

| | |
|---|---|
| **Target host** | Railway (Hobby plan) |
| **Target database** | Railway PostgreSQL, same project |
| **App URL** | `app.formuladservice.in` |
| **Mail** | Resend HTTPS API, sending from `mail.formuladservice.in` |
| **Public website** | `formuladservice.in` — WordPress, **not touched by any of this** |

---

## Part 0 — Understand the shape of the change

Two things people expect to happen here, which do **not**:

- **The demo data does not move.** You are not migrating the Neon database.
  The real system starts with an empty database and the workshop's real
  opening figures. Copying demo data across and then deleting it leaves far
  more room for a mistake than never copying it.
- **The public website is not involved.** Adding `app.` and `mail.` subdomains
  adds two lines to a DNS table. It edits nothing that already exists. If you
  deleted both lines afterwards, `formuladservice.in` would not notice.

---

## Part 1 — Before the day

Do these whenever. None need the owners or DNS access.

### 1.1 Rehearse a database restore ☐

**Do this first, and do not skip it.** A backup nobody has restored is not a
backup — it is a file you hope about. `TECH_DEBT.md` AUD-0063 has flagged this
as untested since the original audit.

```bash
python manage.py backup_db
```

Then restore that file into a scratch database (not the live one) and compare
row counts against the source. The file extension tells you which tool to use:

| Extension | Restore with |
|---|---|
| `.dump` | `pg_restore` |
| `.sql` | `psql` |
| `.sqlite3` | file copy |

Write down the exact command that worked, in section 5.2 below. You want it in
front of you on the day you need it, not in your head.

### 1.2 Railway: build and deploy commands ☐

The `Procfile` only defines `web:`, so nothing currently runs `collectstatic`
or `migrate`. This is why the test deployment served no CSS or JavaScript.

Railway dashboard → service → **Settings**:

| Setting | Value |
|---|---|
| Build Command | `python manage.py collectstatic --noinput` |
| Pre-Deploy Command | `python manage.py migrate --noinput` |
| Start Command | `gunicorn formulad_workshop.wsgi:application` |

Pre-deploy is the correct home for migrations: it runs once before the new
version takes traffic, rather than on every restart of every replica.

### 1.3 Railway: environment variables ☐

```
DJANGO_ENV=production          ← without this the app refuses to boot, by design
SECRET_KEY=<a fresh one>       ← NOT the value from your local .env
DEBUG=False
ALLOWED_HOSTS=app.formuladservice.in
CSRF_TRUSTED_ORIGINS=https://app.formuladservice.in
BUSINESS_NAME=Formula D
DEFAULT_FROM_EMAIL=Formula D <noreply@mail.formuladservice.in>
RESEND_API_KEY=<from Resend, section 2.3>
VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY / VAPID_ADMIN_EMAIL
DB_NAME / DB_USER / DB_PASSWORD / DB_HOST / DB_PORT
DB_SSLMODE=prefer
```

Generate the secret key:

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

Notes that will save you time:

- **`DB_SSLMODE=prefer`, not `require`.** The default in `base.py` is `require`,
  which suits Neon over the public internet. Railway's Postgres is reached over
  its private network where TLS may not be offered, and `require` then fails to
  connect. `prefer` uses TLS when available and plain when not; the traffic
  never leaves Railway's own network either way.
- **Use the Railway-provided `PG*` variables** to fill `DB_*` — reference them
  rather than pasting values, so a credential rotation does not silently break
  the app.
- **Never regenerate the VAPID keys.** Doing so invalidates every push
  subscription and every device has to re-enable push by hand.

### 1.4 Redeploy and check the browser console ☐

Open the app, then DevTools → Console and Network.

- ☐ No 404s under `/static/`
- ☐ Filenames look like `script.4950246ea5b3.js` (content-hashed)
- ☐ No "Refused to apply style" or "Refused to execute script" errors
- ☐ `/robots.txt` returns the disallow file
- ☐ Any page returns the header `X-Robots-Tag: noindex, nofollow`

If static files still 404, the build command did not run — check the deploy log
for the `collectstatic` output before changing anything else.

### 1.5 Confirm production settings are really in force ☐

Visit a URL that does not exist, e.g. `/definitely-not-a-page`.

- ☐ You see the app's own styled 404 page
- ☐ You do **not** see Django's yellow debug traceback

A debug traceback in production leaks settings, file paths and SQL. If you see
one, `DJANGO_ENV` or `DEBUG` is wrong — fix it before going further.

---

## Part 2 — With the owners (DNS)

### 2.1 Record what exists before changing it ☐

Ask for access to the DNS panel for `formuladservice.in`, then:

- ☐ **Screenshot every existing record.** This is your undo.
- ☐ Note whether any `MX` record exists (mail for the domain)
- ☐ Note any existing `TXT` record starting `v=spf1`

If mail already exists on the domain, that is fine — you are working on the
`mail.` subdomain and will not touch it. But know before you type.

### 2.2 Add two records ☐

Nothing existing is edited or removed.

| Type | Name | Value | Purpose |
|---|---|---|---|
| CNAME | `app` | *(Railway gives you this)* | the workshop system |
| TXT ×3 | `mail` | *(Resend gives you these)* | SPF / DKIM for sending |

### 2.3 Resend ☐

- ☐ Create an account
- ☐ Add domain — enter **`mail.formuladservice.in`**, *not* `formuladservice.in`
- ☐ Add the DNS records it shows, wait for it to report Verified
- ☐ Create an API key, put it in Railway as `RESEND_API_KEY`

**Why the subdomain matters:** SPF/DKIM records added at the root can disturb
mail for the business domain itself, now or whenever the owners set up a
`@formuladservice.in` address. On `mail.` they are isolated and cannot.

### 2.4 Railway custom domain ☐

Railway → Settings → Networking → Add `app.formuladservice.in`. The HTTPS
certificate is issued automatically; it can take a few minutes.

- ☐ `https://app.formuladservice.in` loads with a valid padlock

**Do not** submit `formuladservice.in` to the HSTS preload list. `production.py`
sends the `preload` directive, which is inert unless you submit it — submitting
would force HTTPS on the WordPress site too and could break it.

---

## Part 3 — The day

Order matters here. Read the whole part before starting.

### 3.1 Prepare the database ☐

Starting empty, on Railway Postgres:

```bash
python manage.py migrate
python manage.py setup_groups
python manage.py load_master_data
```

- ☐ `migrate` reports no errors
- ☐ `setup_groups` created Owner / Office / Floor

### 3.2 If any demo data reached this database, remove it ☐

Only if something was seeded here by accident. Dry run first — it prints what
it would delete and changes nothing:

```bash
python manage.py purge_business_data
python manage.py purge_business_data --yes
```

This clears every business table. It does not touch logins, groups or the
master lists.

### 3.3 Owner accounts and real email addresses ☐

The developer test addresses must not survive into production — password reset
codes go to `User.email`, so a stale address points account recovery at a
mailbox the owners do not read.

```bash
python manage.py set_owner_email <username> <real@address>        # dry run
python manage.py set_owner_email <username> <real@address> --yes
python manage.py sync_owner_identity          # dry run
python manage.py sync_owner_identity --yes
```

- ☐ Both owners' emails are their real ones
- ☐ `sync_owner_identity` reports both in the Owner group, `is_staff=False`

### 3.4 Prove password reset works ☐

Before the owners depend on it:

- ☐ Request a reset for one owner account
- ☐ The email actually arrives (check spam too, on first send from a new domain)
- ☐ The code is visible in the **subject line** / notification banner
- ☐ Completing the reset signs that account out everywhere

If the mail does not arrive, check the Railway logs for `Resend rejected a
message` — the reason is logged there.

### 3.5 Opening balances ☐

Enter the workshop's real starting position. This is the one step nobody else
can do for you and it is worth doing unhurried.

- ☐ Staff roster and current salaries
- ☐ Opening warehouse stock
- ☐ Outstanding spare-shop / supplier balances
- ☐ Any unpaid customer or fleet balances

### 3.6 Owner devices ☐

Do this **last**, and only once the URL is final. A PWA install and its push
subscription are bound to the exact origin — change the URL later and every
device repeats this.

For each owner, on their own phone:

- ☐ Open `https://app.formuladservice.in` in Safari
- ☐ Share → **Add to Home Screen** (iOS gives push to installed apps only)
- ☐ Open the *installed* app, sign in
- ☐ Notifications page → enable push
- ☐ Trigger something (a login from another device) and confirm the phone buzzes

### 3.7 Smoke test the real thing ☐

- ☐ Create a job card, add a spare and a labour line, check the total
- ☐ Print an invoice — confirm it fits one A4 sheet
- ☐ Take a payment, confirm it appears in Paid Bills
- ☐ Add a Cashbook entry, confirm the Profit page moves
- ☐ Sign in as Office and as Floor; confirm each sees only what it should
- ☐ Open the app on the Floor tablet at its real screen size

---

## Part 4 — Immediately after

- ☐ **Take a backup and restore-test it**, now that real data exists (§1.1)
- ☐ Turn on Railway's own Postgres backups; note the retention period
- ☐ Store one backup somewhere that is not Railway (offsite copy)
- ☐ Record the go-live date in `TITAN_MASTER_HANDOVER.md`
- ☐ Watch the Railway logs for the first few days

---

## Part 5 — When something goes wrong

### 5.1 Rolling back a deploy

Railway keeps previous deployments. Dashboard → Deployments → redeploy the last
good one.

**A rollback does not undo a migration.** If the bad deploy migrated the
database, roll the code back *and* restore the database from backup, or the old
code meets a schema it does not understand.

### 5.2 Restoring the database

Write the command that actually worked in §1.1 here, before you need it:

```
(fill this in during the rehearsal)
```

### 5.3 Both owners locked out

Owners cannot reset each other by design, and `manage_reset_password` refuses
Owner accounts. If email delivery is also broken, the route back is the Railway
shell:

```bash
python manage.py shell -c "
from django.contrib.auth.models import User
u = User.objects.get(username='<owner>')
u.set_password('<a temporary password>')
u.save()
print('reset', u.username)"
```

Have the owner change it immediately afterwards at `/change-password/`.

**Keeping this route available is the reason the system does not need a second
authentication factor.** The gap a TOTP app would supposedly fill is a fallback
when the recovery channel fails — and this is that fallback, with nothing to
carry, nothing to lose and nothing to expire.

### 5.4 Static files broken again

Symptom: unstyled pages, console shows `Refused to apply style ... MIME type
('text/html')`.

Cause is nearly always that `collectstatic` did not run. Check the build log.
Confirm the storage backend is the WhiteNoise one — if this ever prints
`StaticFilesStorage`, the `STORAGES` setting has been reverted:

```bash
python manage.py shell -c "from django.contrib.staticfiles.storage import staticfiles_storage; print(staticfiles_storage.__class__)"
```

### 5.5 Mail stops sending

Check Railway logs for `Resend rejected a message: HTTP <code>` — the provider's
own reason is logged. Common causes: the domain's DNS records were changed, the
API key was rotated, or the free tier's monthly limit was reached (3,000/month;
this app sends single digits per year, so this one would be a surprise).

---

## Appendix — Things deliberately not done

Recorded so they are not raised as oversights. See
`TITAN_MASTER_HANDOVER.md` §VII for the product-scope list, and `CLAUDE.md` §
Deliberate decisions for the engineering ones.

- **No CI/CD pipeline.** Deploys are a git push. One developer, one app.
- **No staging environment.** The demo-data deployment served that purpose.
- **No error-tracking service.** Railway's logs are the log.
- **No TOTP / second factor.** See §5.3.
- **No uptime monitoring.** Four users who will phone you.

Each of these is a reasonable thing to add later. None is a reason to delay
shipping.

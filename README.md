# Honeycombe Arts Hub — Website

The website for **Honeycombe Arts Hub**, with a built-in
staff admin (CMS) — no technical knowledge needed to update events, photos and more.

## Run the website

You need nothing except Python 3 (already on every Mac):

```bash
cd "Honeycombe Arts Hub"
python3 server.py
```

Then open:

| What | Where |
|---|---|
| Public website | http://localhost:8000 |
| Staff admin (CMS) | http://localhost:8000/admin |

## Staff sign-in

Every staff member has their **own account** (email + password) and signs in
with a 6-digit code from an authenticator app as well (Google or Microsoft
Authenticator, 1Password…), because the booking system holds children's
details. What each person can see and do depends on their roles (Owner,
Administrator, Manager, Session staff, Designated Safeguarding Lead, SEND
lead, Finance) — manage them in the admin's **Staff** tab. Every sign-in and
change is recorded in the **Audit log**.

**First time (upgrading from the shared team password):** open `/admin` and
create the owner account. You'll be asked for the current team password once;
after that the shared password stops working. (On a brand-new install there
is no team password: set `ADMIN_PASSWORD` in the host's environment first.
The old published default password is never accepted.)

**Adding staff:** Staff → *Invite a staff member* gives you a one-time link to
send them. **Lost phone or forgotten password:** an administrator uses *Reset
password & 2FA* to send a new link. Keep your recovery codes somewhere safe.

## What staff can edit in the admin

- **What's On** — add/edit/reorder events, upload photos, mark one as "featured"
- **Past Events** — the chronological scrapbook timeline
- **Gallery** — upload photos, captions and categories
- **Testimonials** — quotes shown on the homepage slider and testimonials page
- **Impact & Values** — the statistics and value cards
- **Page text** — every word and photo on the **Contact** and **Get Involved**
  pages: headings, intro, the contact cards, the "ways to get involved" rows
  (photo, text, button) and how each page appears in Google
- **Inbox** — messages sent from the contact form (reply by email in one click)
- **Newsletter** — subscriber list, CSV download, copy-all-emails
- **Settings** — announcement bar, contact details, booking/donate/social links,
  charity numbers, optional email notifications, system & backups
- **My account** — your password, signing out other devices
- **Staff** and **Audit log** — for owners and administrators

In the Get Involved text boxes, a blank line starts a new paragraph,
`**stars**` make words bold and `[label](https://…)` makes a link — everything
else is shown exactly as typed.

Nothing goes live until you press **Save & publish**. **Discard** reverts to the
last published version. A backup of the previous version is kept automatically
(`data/content.backup.json`).

## Where things live

```
server.py            — start the site: python3 server.py
hah/                 — the web server + CMS API (zero dependencies)
data/content.json    — all editable website content
data/messages.json   — contact form inbox
data/subscribers.json— newsletter signups
data/uploads/        — images uploaded through the admin
data/booking.db      — booking system database (SQLite)
data/backups/        — nightly backups (kept a week)
public/              — the website (pages, css, js, images)
public/docs/         — Policy Handbook PDF
partials/            — shared header/footer used by every page
tests/               — automated checks (see below)
docs/booking/        — design of the new booking system (replacing MagicBooking)
```

## Tests

```bash
python3 -m unittest -v
```

This runs the real server against a throwaway data folder, so it never
touches `data/`. GitHub runs the same tests on every pull request, and the
deploy workflow only deploys a commit once they pass. To make Render's own
auto-deploy wait for them too: Render dashboard → the service → *Settings →
Auto-Deploy* → **After CI checks pass**.

## Going live (hosting)

Everything staff edit lives in one folder — `data/` (content, inbox,
subscribers, uploaded images) — so any host that runs Python and gives you a
persistent disk works. **You do not need Vercel or Supabase.**

Recommended: **Render.com** (a `render.yaml` blueprint is included):

1. Push this folder to a GitHub repository.
2. In Render: *New → Blueprint*, select the repo — it creates the service and
   the persistent disk automatically.
3. In the service's *Settings → Custom Domains*, add `honeycombeartshub.org.uk`
   and `www.honeycombeartshub.org.uk`, then add the DNS records Render shows
   you at your domain registrar. HTTPS is automatic.
4. Set `ADMIN_PASSWORD` in the service's *Environment*, then open
   `https://honeycombeartshub.org.uk/admin` and create the owner account.

Every merge to `main` deploys automatically. Render does this itself when
Auto-Deploy is on; the included `.github/workflows/deploy.yml` can do it
instead (or as a backstop) once you add a `RENDER_DEPLOY_HOOK_URL` repository
secret — Render dashboard → the service → *Settings → Deploy Hook*. To check
what the live domain is actually serving, run the **Live site check** workflow
from the repo's Actions tab.

Alternatives that work the same way: Railway, PythonAnywhere, or any small VPS
(`python3 server.py 8000` behind the host's HTTPS proxy). Keep `data/` backed up.

To have contact-form messages forwarded to a real email inbox, fill in the SMTP
details under admin → Settings (your email host — e.g. Google Workspace,
Zoho, or your registrar's mail service — supplies these). Messages always
appear in the admin Inbox regardless.

## Backups

Every night at 02:30 (UK time) the site writes a backup of everything under
`data/` to `data/backups/`: the booking database (a consistent snapshot, even
while people are using the site), the JSON content, inbox and newsletter
files, and uploaded images. Seven days of these are kept. They sit on the same
disk as the live data, so they protect against mistakes rather than losing the
disk, which Render's own daily disk snapshots cover.

**Before any real family data goes in, set up off-site backups.** Each night the
archive is then encrypted and uploaded to a storage bucket in the UK or EU. The
server only has the *public* certificate, so it can't read its own backups. The
trustees keep the private key offline (e.g. on an encrypted USB stick in the
safe, plus a second copy held by another trustee).

1. On a trustee's computer, make the key pair once:
   ```bash
   openssl req -x509 -newkey rsa:4096 -nodes -days 3650 -subj "/CN=Honeycombe Arts Hub backups" \
       -keyout trustees-backup.key -out trustees-backup.crt
   ```
   Keep `trustees-backup.key` offline. **Without it, the backups can't be opened.**
2. Create a bucket with any S3-compatible provider in a UK/EU region, with
   object lock / versioning (≥ 35 days) switched on, and an access key that can
   only upload to that bucket.
3. In Render → the service → *Environment*, set `BACKUP_CERT` (paste the whole
   `trustees-backup.crt` text), `BACKUP_S3_ENDPOINT`, `BACKUP_S3_REGION`,
   `BACKUP_S3_BUCKET`, `BACKUP_S3_ACCESS_KEY` and `BACKUP_S3_SECRET_KEY`.
4. Admin → Settings → *System & backups* → **Back up now**, and check that the
   result says "off-site copy uploaded".

To restore, download the `.p7m` file and run:
```bash
openssl cms -decrypt -inform DER -binary -in hah-YYYYMMDD-HHMMSS.tar.gz.p7m \
    -inkey trustees-backup.key -out backup.tar.gz
tar -xzf backup.tar.gz      # booking.db, *.json, uploads/
```
Practise a restore at least once before launch.

Admin → Settings → *System & backups* shows when the last backup ran and warns
if one hasn't succeeded in the last 26 hours. `/healthz` is Render's health
check (it confirms the site and database are answering).

## Security settings

The site sets strict security headers on every response and a
Content-Security-Policy on every page. Optional environment variables (Render
→ the service → *Environment*):

| Variable | Default | What it does |
|---|---|---|
| `ADMIN_PASSWORD` | — | Password for the very first admin login (then change it in Settings) |
| `HAH_CSP` | `report` | `report`: browsers only *report* anything the policy would block (look for `[csp]` lines in the logs). Switch to `enforce` once a week of normal use shows no reports |
| `TRUSTED_PROXY_HOPS` | `1` on Render | How many proxies add to `X-Forwarded-For`; used to find the visitor's IP for rate limits. After deploying, log in to the admin and open `/api/admin/request-info`: `ip` should be your own public IP address |
| `HAH_MAX_CONCURRENT` | `64` | Requests handled at once before the site answers "busy" |
| `HAH_DATA_DIR` | `data/` | Where editable state lives (tests and staging point it elsewhere) |

Uploads in the admin are for **public website photos only**: JPEG, PNG, GIF
or WebP, checked from the file's contents. Location and camera details are
removed from JPEG photos automatically. Fonts are served from this site
(no Google Fonts), and the contact-page map only loads from Google when a
visitor clicks "Show the map".

## Notes

- Contact details (email, phone, address, opening times), social links and the
  booking portal URL live in admin → Settings; the Contact and Get Involved
  pages pick which of them each card and button shows, so they can never drift
  out of step. Update them in Settings — update them there when the new domain email addresses
  and social accounts are ready.
- The Policy Handbook PDF at `public/docs/HAH-Policy-Handbook.pdf` is the
  HAH-branded edition; replace the file when trustees issue a new revision.

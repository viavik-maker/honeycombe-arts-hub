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

**Default admin password: `honeycomb2026`** — change it on the admin → Settings tab
as soon as you log in.

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
  charity numbers, optional email notifications, admin password

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
4. Open `https://honeycombeartshub.org.uk/admin`, log in, and immediately
   change the password in Settings.

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

## Notes

- Contact details (email, phone, address, opening times), social links and the
  booking portal URL live in admin → Settings; the Contact and Get Involved
  pages pick which of them each card and button shows, so they can never drift
  out of step. Update them in Settings — update them there when the new domain email addresses
  and social accounts are ready.
- The Policy Handbook PDF at `public/docs/HAH-Policy-Handbook.pdf` is the
  HAH-branded edition; replace the file when trustees issue a new revision.

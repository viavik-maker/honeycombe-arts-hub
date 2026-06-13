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
- **Inbox** — messages sent from the contact form (reply by email in one click)
- **Newsletter** — subscriber list, CSV download, copy-all-emails
- **Settings** — announcement bar, contact details, booking/donate/social links,
  charity numbers, optional email notifications, admin password

Nothing goes live until you press **Save & publish**. **Discard** reverts to the
last published version. A backup of the previous version is kept automatically
(`data/content.backup.json`).

## Where things live

```
server.py            — the web server + CMS API (zero dependencies)
data/content.json    — all editable website content
data/messages.json   — contact form inbox
data/subscribers.json— newsletter signups
data/uploads/        — images uploaded through the admin
public/              — the website (pages, css, js, images)
public/docs/         — Policy Handbook PDF
partials/            — shared header/footer used by every page
```

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

Alternatives that work the same way: Railway, PythonAnywhere, or any small VPS
(`python3 server.py 8000` behind the host's HTTPS proxy). Keep `data/` backed up.

To have contact-form messages forwarded to a real email inbox, fill in the SMTP
details under admin → Settings (your email host — e.g. Google Workspace,
Zoho, or your registrar's mail service — supplies these). Messages always
appear in the admin Inbox regardless.

## Notes

- Contact emails, social links and the booking portal URL are all editable in
  admin → Settings — update them there when the new domain email addresses
  and social accounts are ready.
- The Policy Handbook PDF at `public/docs/HAH-Policy-Handbook.pdf` is the
  HAH-branded edition; replace the file when trustees issue a new revision.

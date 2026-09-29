# Staging: a private copy of the site for testing

Before the booking system goes live, and before each big release, test it on a **staging** copy. The copy
uses test payment keys, a mail catcher and no real family data, so nothing can reach a real parent or card.

## Set it up once (Render)

1. Render → *New → Web Service*, pick this repository.
   - Branch: the one being tested.
   - Runtime: Python.
   - Start command: `python3 server.py`.
   - Health check path: `/healthz`.
   - Region: the same as live.
2. Add a small **persistent disk** mounted at `/opt/render/project/src/data`, so data survives restarts.
3. Environment variables:

   | Variable | Staging value |
   |---|---|
   | `ADMIN_PASSWORD` | a one-off password, to create the first staff account |
   | `HAH_CSP` | `enforce`, so anything the policy would block shows up here first |
   | `SMTP_*` / `MAIL_FROM` | a mail-catcher service (e.g. Mailtrap sandbox), so no email reaches real people |
   | `SMS_PROVIDER` | `log` (texts are written to the log, not sent) |
   | `STRIPE_*` | Stripe **test mode** keys (once card payments are added) |
   | `BACKUP_*` | leave unset (staging holds no real data) |
   | `HAH_NOINDEX` | `1` (tells search engines never to index staging) |

4. Keep the staging URL private. **Never copy live family data to staging.**

## Before launch: things to check on staging

See the checklist in `DESIGN.md` §8.3. In particular:
- `/api/admin/request-info` shows your real IP address. If not, set `TRUSTED_PROXY_HOPS`.
- Test email and test text arrive, via admin → Settings → System & backups.
- Restore last night's backup into staging and check the site still works.
- Browser consoles show no Content-Security-Policy reports.

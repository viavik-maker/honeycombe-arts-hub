# Honeycombe Arts Hub — booking system design

This is the living specification for the in-house booking system that replaces MagicBooking. Every booking PR follows
it, and every PR that changes the design updates this file in the same change.

- **Part A** records decisions agreed with the charity, the review amendments and the delivery order. **Where Part A
  and Part B disagree, Part A wins.**
- **Part B** is the original technical design: schema, routes, flows and tests.
- The two independent reviews it was checked against are in [`reviews/`](reviews/):
  - [requirements coverage](reviews/requirements-coverage.md)
  - [security & data protection](reviews/security-data-protection.md)

---

# Part A — decisions, amendments and delivery order

## A1. Decisions agreed with the charity's representative
- **Payments.** Parents pay online by card with Stripe Checkout. Staff can also record cash, card-machine, bank-transfer,
  childcare-voucher and Tax-Free Childcare payments, and can book and take payment on the spot at reception. Anything
  unpaid is tracked as a delayed payment.
- **SMS** goes live at launch through Twilio, behind a provider interface.
- **Self-registration is for 18+ only.** Anyone under 18 is always registered by a parent or carer. The mockup's
  "Teens" card becomes "Young adults (18+)".
- **The SEND support intake form** (mockup 6, with EHCP uploads) is phase 2 — built first in Phase 2 (`hah/send_support.py`, `hah/private_files.py`): the parent signs in (email verified) before sending it, so special-category data never arrives under an unverified address.
- **No booking migration.** Families (parents and children) are imported from the MagicBooking export. Passwords can't
  be carried over.
- **No membership fee.**

## A2. Review amendments (these override Part B)

### Registration and accounts (overrides §3.2–§3.4)
1. **Email first, then child details.** Registration follows mockup 3's stepper. The parent's details come first, and
   the email is verified with a 6-digit code typed into the same tab. Only then are the child sections collected, and
   each step is saved on the server.
   - No child data is ever sent under an unverified email.
   - The verify step needs a button press, never a GET, so link scanners can't trigger it.
   - If the email already has an account, a neutral email goes to that address (activate, sign in or reset) and no
     child data has been lost.
2. **Levels are per child.** "Add child → what will they come to?" sets that child's level. One family can have a
   short-level baby and a full-level holiday-club child.
   - The "missing sections" check covers the child's sections, the account's sections (such as the address) and the
     number of family contacts.
   - An account holder attending a mixed-age activity only needs `adult` level.
3. **Parents can only edit their own details.**
   - Each form section has a whitelist of fields parents may write. Parents may set `haf_status` only to
     claimed_eligible, not_eligible or not_sure.
   - `pay_later_allowed`, `needs_review`, `level`, `status` and the `f_*` flags can never be set by a parent.
   - Every `/api/account/*` query is scoped by `account_id`.
   - Price and pay mode are always worked out on the server.
4. **Changes to who can collect a child need re-authentication.** This covers the collection password, `can_collect`
   contacts, collection alerts, going home alone, and the account email.
   - The parent must re-enter their password (valid for 10 minutes).
   - The change sends a "collection details changed" email and SMS.
   - If the child has a booking within 7 days, it raises an in-tray item.
5. **Imported families.** The activation challenge asks for a child's date of birth, falls back to postcode, and then
   to staff-assisted activation. Consents are reconfirmed on one screen for the whole family. Families who activate
   before bookings open see a "Bookings open on …" message.
6. **Guest bookings.** A guest's bookings (made with the same email) attach to their account once they create one and
   verify that email.
7. **Contact at baby and toddler level.** At the short (parent-stays) level the emergency contact is optional.
   Parent-stays and going-home-alone callouts are driven by `activity.parent_must_stay`, not by the level.

### Booking engine (overrides §1.4, §3.5, §3.7, §4.3)
8. **Order of checks when confirming.** Capacity and the waiting list are checked **first**. Approval or HAF status is
   applied only when a place is actually free; otherwise the booking goes on the waiting list. (Part B reserved the
   place first and checked capacity afterwards, which could overbook.)
9. **HAF rules.**
   - "Not sure" becomes **"Request a HAF place"**: the booking is `pending_approval` and a `haf_verify` in-tray item is
     raised.
   - Staff mark claims verified one at a time or in bulk, and a declined request offers paid days instead.
   - Each activity has `haf_allowance_days`.
   - A general check stops a child being booked onto two sessions that overlap in time.
   - Whether HAF and paid places share one room's capacity is open question 2 (`capacity_group` is optional).
10. **Waiting list.**
    - Sibling-aware: entries made in one checkout share a `waitlist_group`. A place is offered only when the whole group
      fits, or the offer says "1 of 2" explicitly.
    - The offer goes to the first entry that fits.
    - In manual mode, direct booking resumes after N hours if staff take no action.
    - `waitlist_enabled=0` returns 409 "Full" instead of waitlisting.
11. **Selecting and ages.** There's a "Select all days this week" option. The sticky bar counts child-places. Ages show
    as "6 from 10 Aug". Multi-week holiday clubs default to checking age on each session date; term blocks check it at
    the first session.
12. **Signed-out visitors on `/book`.**
    - For guest-level sessions, "Book" goes to `/book/<slug>/guest?session=`.
    - For everything else, "Sign in or register to book" goes to `/register?for=<level>&next=`.
13. **Pay-later and vouchers.**
    - "I'll pay by vouchers or Tax-Free Childcare" creates a staff-approval in-tray item.
    - A due date is never earlier than the invoice's issue date.
    - Account credit is applied automatically in the quote. Credit = Σ succeeded payments − Σ allocations − Σ non-credit
      refunds.
14. **Walk-ins.**
    - `accounts.email` becomes nullable for `source IN ('walkin','staff')`, with a partial unique index; login and
      activation need an email to have been set.
    - The walk-in minimum is one contact, allergies, and first-aid consent recorded as `staff_verbal`.
15. **Guest bookings.**
    - `registration_level='guest'` implies `parent_must_stay=1`, enforced by a CHECK.
    - Free guest places are held as `pending_confirmation` until the emailed link is clicked (within 30 minutes).
    - Caps apply per email per session.
    - The form has an optional "Name for the booking" and the declaration "The person booking is 18+".
    - The guest manage page is deferred; staff cancel on request.
16. **Pre-sold MagicBooking bookings.** Future sessions already sold in MagicBooking are keyed in with
    `funding='prepaid_legacy'`, by a staff bulk screen or CSV. They count towards capacity and appear on registers and
    reports, but raise no invoice.
17. **Moving a booking.** Phase 1 only moves bookings between sessions with the same price; otherwise staff cancel and
    rebook.
18. **Free and HAF bookings.** A basket that is entirely free or HAF gets a confirmation, not an invoice. Mixed baskets
    show £0 lines on the invoice.

### Registers, incidents and in-tray (overrides §2.9, §4.1, §4.5, §4.7)
19. **Incidents can involve people without a child record.** `incident_participants.participant_id` is nullable, and
    `person_name`, `booking_id` and `guest_contact_id` are added, so incidents can cover guest-event children and
    adults.
20. **Collection-password attempts.** After the attempt limit, staff fall back to the phone-verification procedure
    rather than locking the child's record. The sign-out dialog lists the account holders as well as `can_collect`
    contacts.
21. **Register access for session staff.** Session staff can open registers for today and tomorrow only. An alert
    fires if one user views more than N health records an hour. The HAF (free school meals) flag is hidden from session
    staff.
22. **New in-tray types:**
    - `absence_reported`
    - `parent_cancelled`
    - `new_registration_needs` (SEND, SEMH or anaphylaxis given at registration, so the SEND lead calls the family)
    - `safeguarding_info_submitted`, which is reviewed by the DSL; `f_safeguarding` is set by the DSL, not taken from
      what the parent typed
    - `duplicate_child` (a name + DOB match across accounts goes to the DSL)
23. **HAF export for BCP Council** ships with the registers PR.

### Security (overrides §1.3–§1.5, §6, §7)
24. **Tokens in the outbox.** Emails that carry a token are marked sensitive. The token is inserted at send time from a
    column that is nulled once the send finishes, and the stored body has the link redacted. These emails never appear
    in the archive or in a data-subject access request (SAR). A test greps `message_deliveries` for raw tokens.
25. **Staff roles and 2FA.**
    - 2FA is required for **all** staff by default; the owner can relax it and is notified when it changes.
    - Only the owner can grant `owner` or `dsl`; any DSL grant alerts the existing DSLs.
    - Nobody can grant a role they don't hold themselves.
    - Audit rows about safeguarding are visible only to DSLs.
    - Restricted incidents are left out of dashboard counts and in-tray titles for users who aren't DSLs.
26. **Stripe webhook.**
    - The `stripe_events` insert and its effects happen in one transaction; on failure it rolls back and returns 500 so
      Stripe retries.
    - An event counts as a duplicate only when `processed_at` is set.
    - `amount_total`, `currency=gbp` and `payment_status=paid` are all checked.
    - A malformed signature header returns 400.
    - No child names go to Stripe; line items use booking references.
27. **Login throttling.**
    - Throttle on the (email, IP) pair with progressive delays; there is no hard lockout that an attacker could trigger.
    - "Email me a sign-in link" unlocks.
    - At most 2 password hashes run at once; beyond that, 429.
    - Per-IP limits for public endpoints are about 30 an hour. How `X-Forwarded-For` arrives must be checked on staging;
      if the client IP can't be parsed, don't throttle globally.
28. **Server plumbing.**
    - The concurrency semaphore wraps `handle_one_request`, with a 5-second keep-alive idle timeout.
    - `/healthz` reflects the web process only; a stale worker raises an in-tray item and an email alert.
    - Each worker job runs in its own thread with a timeout.
    - One SQLite connection per request, closed in `finally`.
    - `last_seen_at` is updated at most once a minute.
    - Imports and purges are chunked into transactions of about 200 ms or less.
29. **CSV and templates.** `csv_safe` escapes text cells only; numbers are written as numbers. Security email templates
    (reset, activate, verify, invite) are locked from editing.
30. **CSP rollout.** Self-host the fonts **before** the CSP is enforced (both in the hardening PR). Run the CSP in
    report-only mode for a week first. The contact-page Google Map loads only on click.
31. **Uploads.** The upload tool is labelled "Public website images only" and accepts images only. EXIF data (including
    GPS) is stripped from JPEGs. Multipart bodies are parsed with `email.parser.BytesParser`.
32. **Smaller hardening items.**
    - Refuse `HAH_DEV=1` when the `RENDER` env var is set.
    - Support rotating `HAH_SECRET_KEY` as a list of keys.
    - Strip CR/LF from mail header values.
    - Bound the rate limiter's memory.
    - Reject card-number-like digit runs (Luhn check) in payment reference and notes fields.
    - Bootstrap the first owner with a one-time `ADMIN_BOOTSTRAP_TOKEN`.
    - Search containing personal data uses POST, so names never appear in URLs or logs.

### Data protection (overrides §2.4, §3.9, §3.10, §5.4, §7.4)
33. **Lawful bases** (Part B's table is corrected):
    - A child's data is **not** covered by "contract". Use legal obligation (Ofsted-registered provision) or legitimate
      interests with an LIA.
    - Health data uses Sch 1 para 18 for children where consent can't reasonably be sought, and explicit consent for
      the optional 18+ health fields.
    - Safeguarding family information and collection alerts may contain Art 10 (criminal offence) data, which the APD
      must cover.
    - HAF sharing is **never** consent. Controller or processor status under the HAF agreement must be settled.
    - Funder sharing asks per funder, or is dropped if funders only need anonymised data. Small numbers (<5) are
      suppressed in statistics.
34. **Existing newsletter subscribers** are migrated as consented (`source='legacy_newsletter'`, keeping their sign-up
    date). **No re-permission email** (ICO: Flybe, Honda).
35. **Governance comes first.** The DPIA, ROPA, Appropriate Policy Document, processor DPAs and a breach runbook
    (breach log, 72-hour decision path, "revoke all sessions and rotate secrets") are a **gate** before any production
    registration or import. MFA and least privilege apply on Render, GitHub, Stripe, Twilio and the email provider.
36. **Backups.** Off-site encrypted backups are in Phase 1. They are encrypted to a public key held offline by the
    trustees, stored in a UK or EU bucket with object lock of at least 35 days, and tested with a restore drill.
37. **Minimising data.**
    - Incident notifications say only "we've added a note about Maya's session today — sign in or call".
    - Activation and verification emails never name children.
    - Parent IPs in audit and consent records are truncated after 90 days.
    - On erasure, child names are scrubbed from in-tray titles and any account credit is refunded.
38. **Retention.** Accident records for children are kept until **DOB + 25 years**. HAF grant-condition retention is to
    be confirmed (question 8).
39. **Subject access requests (SAR).**
    - Phase 1: a staff-run export from the parent record, including staff notes, non-restricted incidents with
      acknowledgements, contact messages, guest bookings under the same email, and collected-by details.
    - Parent-written family information is included; only material written by the DSL is withheld (DPA 2018 Sch 3
      Part 5 para 21).
    - Parents get a "request my data" button in Phase 1; the self-service download is Phase 2.
40. **Imports.** Only identity and contact fields, plus each child's name and DOB. No special-category data is imported;
    families enter it when they activate. Get a deletion certificate from the MagicBooking processor after cutover.
41. **Photos and consents.** The photography question stays as the single merged 3-option question the charity asked
    for; names are never published alongside photos. First aid, plasters and emergency treatment are labelled parental
    *permissions*; the privacy line is an *acknowledgement*, not a consent.

### Scope cuts (to keep the critical path short)
42. **Phase 1 retention jobs** are limited to:
    - user-requested erasure
    - unverified accounts purged after 7 days
    - token and session cleanup
    - raw import rows purged after 30 days

    Long-range automation (inactivity warnings, 21st/25th-birthday holds, audit expiry) is Phase 2. The schema is kept.
43. **Phase 2 items:**
    - school-year age basis
    - scheduled campaigns and SMS cost estimates
    - age-band marketing audiences
    - database overrides for all 28 templates (Phase 1 keeps file templates plus editable intro text for 3–4 key emails)
    - staff digest email and disk alerts
    - automatic late-Stripe-payment handling (Phase 1 raises an in-tray item instead)
44. **The CMS tabs are not ported in Phase 0.** The old `admin.js` gets the new login and CSRF handling; only the new
    back-office areas are built as ES modules. Porting the CMS tabs is Phase 2.
45. **Region.** Moving hosting to Render Frankfurt is recommended but is the charity's decision (question 11). Either
    way, transfers to Render Inc, Twilio and the email provider need the UK Addendum or the UK–US data bridge.

## A3. Delivery order (replaces §9)
Phase 0 lands as one draft PR with one commit per chunk. Phase 1 chunks follow as their own PRs.

**Phase 0 — foundations and hardening** (no child data is stored until this is done):
- 0.1 This document; the test harness; CI; deploys gated on tests.
- 0.2 `hah/` package split with no change in behaviour; route registry.
- 0.3 Security headers and CSP; self-hosted fonts; limits and timeouts; log redaction; rate limiter; upload checks;
  `csv_safe`; corruption-safe storage.
- 0.4 SQLite layer and migrations; worker; backups; `/healthz`.
- 0.5 Staff accounts, roles, 2FA, sessions, CSRF, audit; retire the shared password.
- 0.6 Mail, outbox and Twilio provider; contact form and inbox moved to SQLite.
- 0.7 Staging service, env secrets, governance pack.

**Phase 1 — launch:**
- 1.1 Families schema, formspec, validation.
- 1.2 Parent auth and portal shell.
- 1.3 Registration journeys.
- 1.4 Activities admin and the `booking_live` flag.
- 1.5 Book activities page.
- 1.6 Booking engine.
- 1.7 Invoices and credit.
- 1.8 Stripe.
- 1.9 Registers and HAF export.
- 1.10 Incidents.
- 1.11 Staff bookings, walk-in and finance.
- 1.12 Family import and legacy re-key.
- 1.13 Guest booking.
- 1.14 Search and records.
- 1.15 In-tray.
- 1.16 Messaging.
- 1.17 GDPR self-service and privacy notice.
- 1.18 Public site integration.
- 1.19 Attendance dashboard. This can follow cutover by up to 4 weeks, because registers collect the data from day one.
- 1.20 Launch readiness and cutover runbook: close MagicBooking sales → re-key pre-sold bookings → flip `booking_live`.

**Phase 2:**
- SEND intake with a private file store
- self-service data download
- trials UI
- e-shop (if needed)
- saved searches and bulk actions
- Stripe reconciliation and discounts
- historic MagicBooking totals
- extra carer logins
- offline registers
- Twilio delivery callbacks
- "turns 18" handover
- long-range retention
- CMS port to modules
- scheduled campaigns
- optional `staff.` subdomain

## A4. Open questions for the charity (defaults in brackets; none block Phase 0)
1. Cancellation and refund policy [48 h cutoff; card refund ≥7 days before, otherwise credit]. Who may pay later? Do
   unpaid bookings auto-cancel? [no]
2. HAF: funded days per child; whether HAF and paid places share a room's capacity; what BCP needs, and in what format.
3. Pricing without membership: sibling or week discounts; Saturday Club price.
4. MagicBooking features: what the e-shop sells and whether it's needed; how trials are used; what the in-tray holds
   today.
5. The MagicBooking export: a redacted sample; whether it has future bookings, credit balances, or several logins per
   family.
6. The collection password is checked on a tablet and never printed. The DSL needs to sign this off. Minimum age for
   going home alone [11].
7. Who holds each role (DSL and deputies, SEND lead, finance, managers)? 2FA for everyone? [yes]
8. Retention [accidents DOB+25; registers 3 years; finance 6 years; inactive accounts 3 years; HAF per the grant]. Who
   is the named data-protection lead?
9. Funders that receive identifiable data, and whether consent is asked once or per funder. Keep "religious
   requirements" as asked, or reword it to "dietary or cultural requirements"?
10. Ages: "0 to 1" = under 2? Is eligibility checked on each session date or the first? Which activities are drop-off
    (Home Ed, the 12–17 groups)?
11. Providers:
    - email sender and SPF/DKIM
    - Twilio sender ID and budget
    - a Stripe account in the charity's name
    - bank details for invoices
    - Render plan (Standard recommended)
    - hosting region (Frankfurt recommended)
12. Arts Award and Seesaw eligibility without membership. Which centres or venues need registers?
13. Which reporting year to use, and the session categories for the daily breakdown.

---

# Part B — original technical design

_Line references are to commit `f02be31`. Where Part A amends a section, Part A takes precedence._


Status: design for implementation across about 30 PRs. All paths are relative to `` unless absolute. Line references are to the current `main` (commit `f02be31`).

---

## 0. Decisions at a glance, plus four findings that change the plan

**Decisions.** Each one is justified in the section it belongs to.

| Problem | Decision |
|---|---|
| Runtime | Python standard library only, with Python 3.12 pinned (`.python-version` plus `PYTHON_VERSION` in `render.yaml`). A single process on Render with a persistent disk. |
| Booking data | SQLite at `data/booking.db` (`HAH_DATA_DIR` can override the directory). WAL, `foreign_keys=ON`, `synchronous=FULL`, `busy_timeout=5000`. Every write runs inside `BEGIN IMMEDIATE`. Booking data never goes in `content.json`. |
| Code layout | New `hah/` package. `server.py` becomes a 10-line shim. Nothing imports `server`. |
| Routing | A route registry with `@route(method, pattern, auth=, perm=, csrf=, body_limit=, rate=)`. Only GET and POST are used; mutations are verb-suffixed POSTs. |
| Staff auth | Per-staff accounts with roles, stored in SQLite. PBKDF2-SHA256 at 600k iterations. TOTP (RFC 6238, using stdlib `hmac`), enforced for owner, admin, DSL and finance roles. Everything is audit-logged. Replaces `auth.json`/`sessions.json`. |
| Parent auth | A separate table and a separate `__Host-hah_acct` cookie (SameSite=Lax). Staff use `__Host-hah_staff` (SameSite=Strict). Session tokens are stored hashed. |
| CSRF | A per-session synchronizer token sent in an `X-CSRF-Token` header, plus a strict `Origin` check against `SITE_ORIGIN` and a JSON-only body. |
| Registration model | "Registration level" is a property of how complete each participant's profile is: `short` < `full` for children, `adult` for 18+ self-registration, `guest` for bookings with no participant record. Each activity declares the level it needs; booking prompts the parent to fill in whatever sections are missing. |
| Forms | Form sections and fields are defined once in Python (`hah/booking/formspec.py`) and rendered generically by `public/js/portal/formkit.js`. Client and server validation therefore cannot drift, and the "complete missing sections" flow comes for free. |
| Collection password | Stored as a peppered PBKDF2 hash and never shown to anyone. Staff check it at the door by typing what the adult says, and the server answers match or no match. Every check is audited. Reasoning is in §2.5. |
| Capacity | Every active booking row has a `places` count. Capacity is checked and the row inserted in the same `BEGIN IMMEDIATE` transaction. Holds count until the worker releases them explicitly. When a waiting list exists, freed places go to it before anyone else. |
| Payments | Stripe Checkout called through `urllib`. The webhook verifies `Stripe-Signature` with HMAC and deduplicates by event id. Offline payments are recorded by staff. Pay-later is available only where both the activity and the family allow it. |
| Invoices | A numbered invoice is issued when a booking is confirmed (never for abandoned checkouts). Numbers come from a `counters` table inside the transaction. Cancellations produce numbered credit notes. The invoice is HTML: emailed, shown on a printable account page, and saved as PDF from the browser. |
| Messaging | A transactional outbox (`message_deliveries`) written in the same transaction as the domain change. A background worker sends via SMTP (verified TLS) and Twilio (`urllib`). Service and marketing messages are separated to comply with PECR. |
| Tokens in emails | Put in the URL fragment (`/activate#t=…`), so they never reach the request line or the logs. The one exception is RFC 8058 unsubscribe, which needs a path token; those paths are redacted from the log. |
| Admin UI | One shell page plus native ES modules under `public/admin/js/`. The existing CMS tabs move across unchanged. Navigation is built from the modules the user has permission for. |
| Portal UI | Server-rendered page shells in `public/portal/*.html` plus JSON APIs. The existing design tokens and fonts are used. Mockups define flow and fields only; the green "[YOUR ORGANISATION]" styling is not used. |
| Charts | Inline SVG generated in JS, with an accessible data-table fallback. |

**Four findings that affect scope.**

1. **Hosting region.** `render.yaml` sets no `region`, so the service is almost certainly in Render's default region, Oregon (US). Storing children's health, SEND and safeguarding data in the US is an international transfer (it needs the UK IDTA or Addendum plus a transfer risk assessment). A Render service cannot change region, so the choice has to be made before any booking data exists: either recreate the service in `region: frankfurt` (Phase 0, PR 0.8) or document the US transfer. I recommend Frankfurt.
2. **Deploys are not gated by CI today.** CI gating only works if Render's own Auto-Deploy is switched off (`autoDeploy: false` in `render.yaml`) and deploys happen only through the deploy hook, after the tests pass. The README's "Every merge to main deploys automatically" says Render auto-deploy is on at the moment.
3. **The repo contains a folder called `hah assets `** (trailing space, 41 MB of source PNGs). It doesn't clash with the new `hah/` package, but it is confusing. Move it out of the repo or rename it (`design-assets/`) in PR 0.2.
4. **Membership is referenced in places beyond the booking pages.** Arts Award eligibility (`public/arts-award.html:5,36-37,46,49,58`) and Seesaw access both depend on it. Removing membership leaves an eligibility gap there (open question Q9).

---

## 1. Architecture and module layout

### 1.1 Package layout

```
server.py                    # shim: `from hah.app import main` / `if __name__ == "__main__": main()`
hah/
  __init__.py
  app.py                     # main(): config → db.migrate() → bootstrap owner → start worker → BoundedThreadingHTTPServer(Handler)
  config.py                  # ROOT/PUBLIC/PARTIALS/SEED; DATA = env HAH_DATA_DIR or ROOT/data; SITE_ORIGIN; TZ=Europe/London;
                             #   secrets from env (see §4.14); DEV mode; feature flags
  http.py                    # Handler (thin), Request, Response, route registry, decorators, security headers, body limits,
                             #   client IP (X-Forwarded-For), log redaction, error mapping (ValueError→400, Forbidden→403…)
  ratelimit.py               # in-memory token buckets keyed on (bucket, ip) and (bucket, email); thread-safe
  storage.py                 # JSON-file storage moved from server.py:46-61 + update_json() locked RMW + corruption-safe load
  db.py                      # per-thread sqlite3 connections, PRAGMAs, tx() (BEGIN IMMEDIATE), migrations runner, backup()
  migrations/0001_core.sql … # numbered, checksummed, forward-only
  security.py                # hash_password/verify (pbkdf2_sha256$iter$salt$hash, rehash-on-login), new_token/hash_token,
                             #   TOTP (RFC 6238) + recovery codes, HMAC-signed stateless tokens (unsubscribe), common-password check
  sessions.py                # staff + account session create/lookup/touch/revoke (token hashed at rest), CSRF token
  permissions.py             # ROLE_PERMS matrix (§4.0), has_perm()
  audit.py                   # audit.record(req, action, entity, …) — field names only, never special-category values
  html.py                    # _attr/_html/_safe_url/_ext/_rich moved from server.py:215-253; csv_safe(); money/date formatting
  site.py                    # render_page (server.py:473-495) + BLOCKS (417-427) + _seo_head (449-470); adds nonce, seo=False, noindex
  cms.py                     # existing CMS handlers moved from server.py:674-807, registered as routes
  mail.py                    # SMTP sender (verified TLS, 465/587, HTML+text, headers)
  sms.py                     # SmsProvider protocol, TwilioProvider (urllib), LogProvider, DisabledProvider; UK→E.164
  templating.py              # tiny {{var}} (escaped) / {{{block}}} (raw) renderer for email/SMS templates
  outbox.py                  # enqueue_email/enqueue_sms (INSERT into message_deliveries inside the caller's tx)
  worker.py                  # background thread: job table, dispatcher, holds, waitlist, publishing, reminders, backup, retention
  templates/email/*.txt|*.html, templates/invoice.html, templates/register_print.html
  booking/
    formspec.py              # registration sections/fields/levels (single source of truth for client + server)
    validate.py              # email, UK phone, postcode, DOB (day/month/year), lengths, enum checks
    accounts.py              # register/verify/activate/login/reset, account CRUD, closing
    participants.py          # profiles, level computation, flags cache, collection password
    consents.py              # consent types (versioned), record/supersede, current-for-booking
    activities.py            # activities, sessions, generator, duplicate, publishing, export
    eligibility.py           # age in months, school year, level gaps, booking windows
    bookings.py              # quote, confirm (capacity tx), approve/decline/cancel/move, walk-in, party bookings
    waitlist.py              # join, offer, accept, expire, manual promote
    pricing.py               # line pricing (hook for Phase 2 discounts)
    invoices.py              # numbering, invoice/credit note creation, render_invoice_html
    payments.py              # offline payments, allocation, refunds, delayed payments ageing
    stripe.py                # urllib client, checkout sessions, expire, refunds, webhook verify + handlers
    registers.py             # register queries, sign-in/out, collection checks, weekly grid, print
    incidents.py             # incidents, participants, amendments, notifications, restriction
    messaging.py             # audiences, campaigns, templates, preferences, unsubscribe
    reports.py               # attendance dashboard, utilisation, HAF, bookings, finance summaries
    search.py                # advanced search (whitelisted filters → parameterised SQL), exports
    intray.py                # item creation from domain events + queries
    importer.py              # CSV parse, mapping, dry run, commit, rollback, activation sends
    gdpr.py                  # SAR export (zip), erasure, retention holds, purge
    staff.py                 # staff users, invites, roles, 2FA reset
  routes/
    pages.py                 # page routes (public shells, portal shells with auth redirect, printable invoice)
    public_api.py            # /api/book/*, /api/account/*, /api/unsubscribe, /api/stripe/webhook
    staff_api.py             # /api/staff/*
tests/                       # §8
public/portal/*.html         # portal page shells (§3.1)
public/js/portal/*.js        # portal ES modules
public/css/portal.css        # portal components (tokens come from style.css)
public/admin/js/**           # admin ES modules (§1.6)
partials/portal-header.html, partials/portal-footer.html, partials/portal-head.html
```

**Why `server.py` becomes a shim.** Moving everything into `hah/` removes the pitfall where `server.py` runs as `__main__` and any `import server` loads a second copy with its own `_lock` and `_seed_cache`. `render.yaml`'s `startCommand: python3 server.py` and the README instructions keep working.

### 1.2 What happens to each part of `server.py` (PR 0.2 moves code; behaviour must not change)

| server.py lines | Today | Destination and change |
|---|---|---|
| 11-26, 28-42 | imports, paths, `DEFAULT_PASSWORD`, `SESSION_TTL`, `MAX_UPLOAD`, `_lock` | `hah/config.py`. `DATA` comes from `HAH_DATA_DIR`. `DEFAULT_PASSWORD` (the `"honeycomb2026"` fallback) is **deleted** in PR 0.5. |
| 46-61 | `load_json`/`save_json` | `hah/storage.py`. A missing file returns the default. A **corrupt** file raises `StorageCorrupt`, logs, and refuses to save over it. Writes call `fsync` before `os.replace`. New `update_json(name, default, fn)` holds a module `RLock` across load → fn → save. |
| 65-115 | shared-password auth, file sessions | Replaced by `hah/security.py`, `hah/sessions.py` and the `staff_users` tables (PR 0.5). |
| 119-139 | `try_send_email` | `hah/mail.py` (generalised, §7.1). The contact form goes through the outbox. |
| 143-159 | `PRETTY` | Kept as a dict in `hah/routes/pages.py` and registered as GET page routes. `/admin` renders with `seo=False`. |
| 161-495 | render pipeline, BLOCKS, SEO | `hah/site.py`. `render_page(filename, canonical=None, *, nonce, seo=True, noindex=False)`. The `<!--#data-->` script gets `nonce="…"`. |
| 498-549 | Handler helpers | `hah/http.py`. `_send` (503-512) adds the security headers from §6. `log_message` (548) redacts. |
| 552-601 | `do_GET` if-chain | `Handler.do_GET/do_HEAD/do_POST` → `dispatch(method)` → registry. Unmatched GETs fall through to `_static` (611-633). Unmatched `/api/*` returns a JSON 404. |
| 639-672 | `do_POST` if-chain and single admin gate | Replaced by per-route `auth=`/`perm=`. The `ValueError → 400` behaviour is kept in `dispatch`. |
| 674-807 | CMS handlers | `hah/cms.py`, registered as `@route("POST","/api/contact",csrf=False,rate="public_form",body_limit=16_000)`, and so on. The `/api/admin/*` CMS routes become `auth="staff", perm="site.content"`. |
| 810-835 | `bootstrap_seed`, `main` | `hah/app.py`. |

### 1.3 Request pipeline and route registry (`hah/http.py`)

```python
ROUTES: list[Route] = []          # Route(method, regex, fn, auth, perm, csrf, body_limit, rate, page)

def route(method, pattern, *, auth=None, perm=None, csrf=True, body_limit=64_000, rate=None):
    """pattern like "/api/staff/bookings/<int:id>/cancel"; auth in {None,"account","staff"};
    perm = permission string from hah.permissions (requires auth="staff")."""

# dispatch(handler, method):
#  1. rid = token_hex(6); parse path (unquote, strip trailing "/"); query via parse_qs (never logged)
#  2. match ROUTES (first match); none → GET: _static(); /api/* → 404 JSON
#  3. method is POST: reject Transfer-Encoding: chunked (411); Content-Length must be an int 0..route.body_limit else 400/413
#  4. csrf=True and POST: Origin must equal SITE_ORIGIN (missing Origin → 403); Content-Type must be application/json
#     (multipart only on routes that declare it); after auth, X-CSRF-Token must equal session.csrf (compare_digest)
#  5. rate: ratelimit.hit(route.rate, req.ip[, key]) → 429 with Retry-After
#  6. auth: "staff" → sessions.staff(req) (mfa_passed required except on /api/staff/auth/totp) else 401;
#           "account" → sessions.account(req) else 401 (page routes: 302 /login?next=<local path>)
#  7. perm: has_perm(staff, perm) else 403 (plus audit "authz.denied")
#  8. call fn(req) → Response; exceptions: ValidationError(field_errors) → 422 {"errors":{field:msg}}; ValueError → 400;
#     Conflict → 409; anything else → 500 {"error":"Something went wrong","ref":rid} + traceback to stderr with rid
```

- `Request` exposes `.ip`, `.json()`, `.form_multipart()`, `.cookie()`, `.staff`, `.account`, `.session` and `.rid`.
- `req.ip` is the rightmost entry of `X-Forwarded-For` at depth `TRUSTED_PROXY_HOPS` (default 1). Confirm the value on staging (§8.3).
- `Response.json()` always sets `Cache-Control: no-store`.
- Page routes use `page("/account/bookings", "portal/bookings.html", auth="account", noindex=True)`.

### 1.4 SQLite access layer (`hah/db.py`)

- **Connections.** One connection per thread (`threading.local`), opened with `isolation_level=None` (explicit transactions) and `check_same_thread=True`. `row_factory = sqlite3.Row`. The worker has its own connection.
- **PRAGMAs on open:** `journal_mode=WAL`, `foreign_keys=ON`, `synchronous=FULL` (low write volume, and money is involved), `busy_timeout=5000`, `temp_store=MEMORY`.
- **`with db.tx() as c:`** issues `BEGIN IMMEDIATE`, commits or rolls back, and retries once on `SQLITE_BUSY`. Reads outside `tx()` run in autocommit.
- **Never do network I/O inside `tx()`** (Stripe, SMTP, Twilio). The pattern is tx1 (reserve) → commit → network call → tx2 (record) → on failure tx3 (compensate).
- **Types.** Store ISO strings explicitly (Python 3.12 deprecates the default date adapters). Instants are UTC `YYYY-MM-DDTHH:MM:SSZ`. Session dates and times are Europe/London wall-clock `YYYY-MM-DD` / `HH:MM`, with helpers to convert to instants using `zoneinfo`.
- **Migrations.** `hah/migrations/NNNN_name.sql` files are applied in order inside a transaction and recorded in `schema_migrations(version, name, checksum, applied_at)`. A checksum mismatch fails startup. Content migrations (edits to the live `content.json`) are Python functions registered as `content:NNNN_*` in the same table (§5.3).
- **Startup assertion.** The code uses no features beyond SQLite 3.31 (partial indexes, triggers, `ON CONFLICT`; no STRICT, no RETURNING, no JSON1 in hot paths). Startup logs `sqlite3.sqlite_version` and asserts at least 3.31.

**Capacity pattern.** This is the single place overbooking is prevented:

```python
with db.tx() as c:
    s = c.execute("SELECT capacity, status FROM activity_sessions WHERE id=?", (sid,)).fetchone()
    used = c.execute("""SELECT COALESCE(SUM(places),0) FROM bookings WHERE session_id=?
                        AND status IN ('pending_payment','pending_approval','confirmed','offered')""", (sid,)).fetchone()[0]
    queued = c.execute("SELECT COUNT(*) FROM bookings WHERE session_id=? AND status='waitlisted'", (sid,)).fetchone()[0]
    status = "waitlisted" if (queued or used + places > s["capacity"]) else initial_status
    c.execute("INSERT INTO bookings(...) VALUES (...)")      # partial unique index blocks double-booking a child
    outbox.enqueue_email(c, ...)                              # same transaction
```

### 1.5 Auth model

| | Staff | Parent / adult account |
|---|---|---|
| Table | `staff_users`, `staff_roles`, `staff_auth_sessions` | `accounts`, `account_auth_sessions` |
| Cookie | `__Host-hah_staff`: HttpOnly, Secure, Path=/, SameSite=Strict | `__Host-hah_acct`: HttpOnly, Secure, Path=/, SameSite=Lax. Lax is needed so the parent is still signed in after Stripe's top-level GET redirect back to the site. |
| Session lifetime | 12 h absolute, 60 min idle | 30 days absolute, 7 days idle |
| 2FA | TOTP. Enforced for owner, admin, DSL and finance (setting `mfa_required_roles`); optional for others. | None |
| Revocation | On password change or 2FA reset; "sign out everywhere"; owner can force-logout any staff member. | On password change or reset; "sign out everywhere" in My details. |
| Lockout | 5 failures → 15-minute lock, doubling each time; owner is alerted by in-tray item. | 10 failures → 15-minute lock; plus an IP bucket. |

- In dev (`HAH_DEV=1`, plain http on localhost) the cookie names drop the `__Host-` prefix. Browsers accept `Secure` cookies on `http://localhost`.
- The legacy `hah_session` cookie, `data/auth.json` and `data/sessions.json` are retired in PR 0.5. The migration deletes both files after the first owner signs in.
- **Bootstrapping the first owner.** If `staff_users` is empty, the app creates an owner from `ADMIN_BOOTSTRAP_EMAIL` plus `ADMIN_PASSWORD` (both env) with `must_change_password=1` and TOTP set-up forced. If either variable is missing, staff login is refused with a clear log line. There is no hard-coded default password, and the README line 24 is removed.

### 1.6 Admin SPA: ES-module split (PR 0.6)

```
public/admin/index.html         # shell: login (email → password → TOTP step), #app with <nav id="sideNav"> built by JS, <main id="panel">,
                                #   <dialog id="modal">, #toast, <script type="module" src="/admin/js/app.js">
public/admin/js/app.js          # GET /api/staff/me → {staff, perms, csrf}; imports area modules; filters by perm; hash router
                                #   (#bookings/123, #registers?date=2026-10-27); calls area.onShow(params) lazily; idle-timeout warning
public/admin/js/core/api.js     # api(path,{method,body}) adds X-CSRF-Token; 401 → re-login overlay WITHOUT discarding in-memory state
                                #   (fixes admin.js:18/42/52 overwrite-on-relogin); 403 → toast; 422 → field errors
public/admin/js/core/dom.js     # $, $$, esc (adds &#39;), h(tag, attrs, ...children) builder using textContent, safeUrl()
public/admin/js/core/ui.js      # toast, modal(), confirmDialog() (replaces native confirm), statusChip(status), emptyState, spinner
public/admin/js/core/table.js   # table({columns, fetchPage, sort, filters, onRow, selectable}) with server-side paging + overflow wrapper
public/admin/js/core/forms.js   # escaped field builders (text/date/time/number/select/checkbox/radio-pills), bindForm(), showErrors()
public/admin/js/core/chart.js   # stackedBarChart({series, categories, colours}) → <svg> + <table class="sr-only">
public/admin/js/areas/*.js      # one module per area (§4), each: export default {id,label,icon,group,perm,render(root),onShow(params)}
public/admin/js/website/*.js    # the existing CMS tabs moved from admin.js: store.js (content, saved, dirty(), save bar — only here),
                                #   events.js (eventForm + activity link §5.2), past.js, gallery.js, testimonials.js, mission.js,
                                #   pages.js, newsletter.js, site-settings.js
public/admin/admin.css          # + status chips, dialog, .table-wrap{overflow-x:auto}, date/time/tel/search inputs (fix admin.css:122-123),
                                #   print stylesheet for registers/invoices/incidents, .sr-only
```

- **Navigation groups:** Today (Dashboard, In-tray, Registers); Bookings (Bookings, Waiting list, Activities); People (Search, Incidents); Messages; Finance; Reports; Website (the eight CMS tabs); Admin (Staff, Settings, Import, Audit).
- **Code smells from `admin.js` not to carry over:**
  - Listeners stacking on persistent roots (`admin.js:821`). Use `root.onclick = …` or bind once.
  - Editing state held as array indexes (`admin.js:12`, `185-188`). Use ids.
  - The unescaped `f()` helper (`admin.js:763-766`).
  - Escaping before slicing (`admin.js:263`).
- The old `admin.js` is deleted at the end of PR 0.6.

### 1.7 Portal front end

- **Shells.** Pages live in `public/portal/*.html`. Each includes `portal-head.html` (which loads `style.css` then `portal.css`, and sets `noindex`), `portal-header.html` and `portal-footer.html`. Each loads `<script type="module" src="/js/portal/<page>.js">`.
- **Header (mockup 2).** Logo on the left. Nav: "Book activities · My bookings · My family", then "Account ▾" (My details, Invoices, Privacy & preferences) and "Sign out". Signed-out visitors see "Sign in" and "Create account".
- **No personal data in the page.** Portal pages never use `<!--#data-->` for user data. They fetch from `/api/account/*`. `_static` returns 404 for direct requests to `/portal/*`.
- **JS modules:**
  - `api.js`: CSRF header; a 401 redirects to `/login?next=`.
  - `formkit.js`: renders `formspec` sections; GOV.UK-style error summary; `aria-invalid`/`aria-describedby`; day/month/year date-of-birth inputs.
  - `basket.js`: `localStorage` with a 24 h TTL. It holds only session ids and participant ids, no personal data. It survives the email-verification tab switch.
  - Page modules: `book.js`, `review.js`, `register.js`, `account.js`, `family.js`, `bookings.js`, `invoices.js`, `privacy.js`, `guest.js`.
- **`public/css/portal.css`** (reuses tokens from `style.css:13-38`):
  - Layout: `.stepper` (numbered house badges reused from `.objective`); `.choice-card` (radio card for the account-type chooser); `.seg-tabs` (Holiday clubs / Events segmented control).
  - Session rows: `.session-row` with `__date` (honey-tint tile, navy-d text), `__title`, `__meta`, `__avail`, `__action`; `.sticky-bar` ("N selected · Continue").
  - Forms: `.fieldset`/`legend`; `.radio-pill`; `.check` (port of `admin.css:131-132`, `accent-color: var(--orange-solid)`); `.field--error`; `.error-summary`.
  - Status and summaries: `.status-chip--confirmed|pending|waitlist|offered|cancelled|unpaid`; `.summary-table`; `.invoice`.
  - Focus and disabled: `:focus-visible { outline: 3px solid var(--navy); outline-offset: 2px }`; `.btn:disabled`. Inputs use a `--muted` border (the existing `--line` border is only 1.26:1).
- **Colour.** The primary action (Select, Continue, Confirm) is `.btn--orange` (`--orange-solid` with white, 4.97:1). The selected state is navy fill with white text. Availability text:
  - "N places left": `#44682f`
  - "Only N left": `--orange-d` `#a8490a`, 5.34:1 on cream
  - "Full": navy
  - The berry/crimson colour in mockup 2 is **not** used.
- **Fonts.** Fredoka, Nunito and Caveat (OFL) are self-hosted in `public/fonts/`, removing Google Fonts from `partials/head.html:6-8` and `public/admin/index.html:10-12`. This takes a third-party transfer out of the privacy notice and tightens the CSP.

---

## 2. SQLite schema

**Conventions.**
- `id INTEGER PRIMARY KEY`.
- Public references are random Crockford-base32 `ref` strings (e.g. `B-7K3M9Q`) so they can't be enumerated.
- Money is `INTEGER` pence. Booleans are `INTEGER CHECK(x IN (0,1))`.
- `*_at` columns are UTC ISO timestamps. Session `date`/`time` columns are Europe/London local.
- Every table has `created_at`, and `updated_at` where rows change.
- FKs use `ON DELETE RESTRICT` unless stated. Erasure is anonymisation (§3.10), not `DELETE`, wherever other records reference the row.

### 2.1 Platform

```sql
schema_migrations(version INTEGER PRIMARY KEY, name TEXT NOT NULL, checksum TEXT NOT NULL, applied_at TEXT NOT NULL)
counters(name TEXT PRIMARY KEY, value INTEGER NOT NULL)          -- 'invoice:2026', 'credit_note:2026', …
settings(key TEXT PRIMARY KEY, value TEXT NOT NULL /*JSON*/, updated_at TEXT, updated_by_staff_id INTEGER)
scheduled_jobs(name TEXT PRIMARY KEY, interval_s INTEGER, next_run_at TEXT, last_started_at TEXT,
               last_finished_at TEXT, last_status TEXT, last_error TEXT)
```

### 2.2 Staff and auth

```sql
staff_users(id, email TEXT NOT NULL UNIQUE COLLATE NOCASE, name TEXT NOT NULL, password_hash TEXT,
  password_changed_at TEXT, must_change_password INTEGER NOT NULL DEFAULT 1,
  totp_secret TEXT, totp_enabled INTEGER NOT NULL DEFAULT 0, totp_last_step INTEGER, recovery_codes TEXT /*JSON of hashes*/,
  status TEXT NOT NULL CHECK(status IN ('invited','active','disabled')),
  failed_logins INTEGER NOT NULL DEFAULT 0, locked_until TEXT, last_login_at TEXT, created_at TEXT, created_by INTEGER)
staff_roles(staff_id INTEGER REFERENCES staff_users ON DELETE CASCADE,
  role TEXT CHECK(role IN ('owner','admin','manager','session_staff','dsl','send_lead','finance')),
  PRIMARY KEY(staff_id, role))
staff_auth_sessions(id, token_hash TEXT NOT NULL UNIQUE, staff_id INTEGER NOT NULL REFERENCES staff_users ON DELETE CASCADE,
  csrf_token TEXT NOT NULL, mfa_passed INTEGER NOT NULL DEFAULT 0, created_at, last_seen_at, idle_expires_at, expires_at,
  ip TEXT, user_agent TEXT)
account_auth_sessions(id, token_hash TEXT NOT NULL UNIQUE, account_id INTEGER NOT NULL REFERENCES accounts ON DELETE CASCADE,
  csrf_token TEXT NOT NULL, created_at, last_seen_at, idle_expires_at, expires_at, ip, user_agent)
auth_tokens(id, purpose TEXT NOT NULL CHECK(purpose IN ('verify_email','activate','reset_password','email_change',
  'staff_invite','guest_manage')), token_hash TEXT NOT NULL UNIQUE,
  account_id INTEGER REFERENCES accounts, staff_id INTEGER REFERENCES staff_users, guest_contact_id INTEGER REFERENCES guest_contacts,
  payload TEXT /*JSON, e.g. new email*/, attempts INTEGER NOT NULL DEFAULT 0, expires_at TEXT NOT NULL, used_at TEXT, created_at)
```

- Only `sha256(token)` is stored. Raw tokens exist only in the cookie or email.
- Token lifetimes: reset 60 min; verify 48 h; activate 30 days, with a date-of-birth challenge (§3.4); staff invite 72 h; guest manage until the session date + 1 day.
- Unsubscribe tokens are stateless: `base64url(kind:id:channel).HMAC(HAH_SECRET_KEY)`. No table.

### 2.3 Families and participants

```sql
accounts(id, ref TEXT UNIQUE NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('family','adult')),
  email TEXT NOT NULL UNIQUE COLLATE NOCASE, email_verified_at TEXT, password_hash TEXT /*NULL until activated*/,
  status TEXT NOT NULL CHECK(status IN ('unverified','pending_activation','active','locked','closed','anonymised')),
  first_name TEXT NOT NULL, last_name TEXT NOT NULL, mobile TEXT /*E.164*/, address_line1, address_line2, town, postcode,
  source TEXT NOT NULL CHECK(source IN ('self','import','staff','walkin')), import_batch_id INTEGER REFERENCES import_batches,
  legacy_ref TEXT /*MagicBooking id*/, pay_later_allowed INTEGER NOT NULL DEFAULT 0, staff_notes TEXT,
  stripe_customer_id TEXT, failed_logins INTEGER DEFAULT 0, locked_until TEXT, last_login_at TEXT,
  activation_sent_at TEXT, activated_at TEXT, reconfirmed_at TEXT, created_at, updated_at, closed_at, erase_after TEXT, anonymised_at TEXT)

participants(id, ref TEXT UNIQUE NOT NULL, account_id INTEGER NOT NULL REFERENCES accounts,
  is_account_holder INTEGER NOT NULL DEFAULT 0,               -- 1 for the 18+ self-registrant
  first_name TEXT NOT NULL, last_name TEXT NOT NULL, preferred_name TEXT,
  dob TEXT NOT NULL CHECK(dob GLOB '[12][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]'), gender TEXT,
  education TEXT CHECK(education IN ('school','home_educated','not_applicable')), school_name TEXT,
  haf_status TEXT NOT NULL DEFAULT 'unknown' CHECK(haf_status IN ('unknown','claimed_eligible','not_eligible','not_sure','verified')),
  haf_code TEXT, haf_verified_at TEXT, haf_verified_by INTEGER, haf_verified_until TEXT,
  level TEXT NOT NULL DEFAULT 'none' CHECK(level IN ('none','short','full','adult')),        -- cached completeness
  -- cached flags for registers/search (recomputed on every health/consent save):
  f_allergy, f_anaphylaxis, f_medical, f_dietary, f_send, f_semh, f_safeguarding INTEGER NOT NULL DEFAULT 0,
  photo_consent TEXT CHECK(photo_consent IN ('online','internal','none')), go_home_alone INTEGER,
  collection_alert TEXT,           -- "Do not release to …" — deliberately visible to register staff
  collection_pw_hash TEXT, collection_pw_set_at TEXT,
  needs_review INTEGER NOT NULL DEFAULT 0,   -- imported or staff-entered: parent must check before booking
  status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','archived','retention_hold','anonymised')),
  created_at, updated_at, anonymised_at)
CREATE INDEX ix_part_account ON participants(account_id); CREATE INDEX ix_part_name ON participants(last_name, first_name);

participant_health(participant_id INTEGER PRIMARY KEY REFERENCES participants ON DELETE CASCADE,
  allergies TEXT, anaphylaxis INTEGER, adrenaline_pen INTEGER, medical_conditions TEXT, medication TEXT,
  dietary TEXT, send_needs TEXT, semh_needs TEXT, religious_requirements TEXT, access_needs TEXT /*adults*/,
  updated_at, updated_by_account INTEGER, updated_by_staff INTEGER)

participant_safeguarding(participant_id INTEGER PRIMARY KEY REFERENCES participants ON DELETE CASCADE,  -- DSL only
  family_info TEXT, updated_at, updated_by_account INTEGER, updated_by_staff INTEGER, dsl_reviewed_at TEXT, dsl_reviewed_by INTEGER)

emergency_contacts(id, account_id INTEGER NOT NULL REFERENCES accounts ON DELETE CASCADE,  -- family-level, shared by siblings
  full_name TEXT NOT NULL, relationship TEXT NOT NULL, phone TEXT NOT NULL, phone_alt TEXT,
  can_collect INTEGER NOT NULL DEFAULT 0, priority INTEGER NOT NULL, created_at, updated_at)

participant_gp(participant_id INTEGER PRIMARY KEY REFERENCES participants ON DELETE CASCADE,
  surgery_name TEXT, doctor_name TEXT, surgery_phone TEXT, surgery_postcode TEXT, updated_at)

guest_contacts(id, email TEXT NOT NULL COLLATE NOCASE, phone TEXT, name TEXT, created_at, last_booking_at, anonymised_at)
CREATE UNIQUE INDEX ux_guest_email ON guest_contacts(email) WHERE anonymised_at IS NULL;
```

### 2.4 Consents (versioned) and marketing preferences

```sql
consent_types(id, key TEXT NOT NULL, version INTEGER NOT NULL, scope TEXT NOT NULL CHECK(scope IN ('participant','account')),
  label TEXT NOT NULL, help_text TEXT, options TEXT NOT NULL /*JSON [["online","Yes, including online and social media"],…]*/,
  required_levels TEXT NOT NULL /*JSON ["full"] etc*/, min_age_months INTEGER, active INTEGER NOT NULL DEFAULT 1,
  created_at, created_by INTEGER, UNIQUE(key, version))
consents(id, consent_type_id INTEGER NOT NULL REFERENCES consent_types, account_id INTEGER NOT NULL REFERENCES accounts,
  participant_id INTEGER REFERENCES participants, value TEXT NOT NULL,
  source TEXT NOT NULL CHECK(source IN ('registration','profile','reconfirm','import','staff_paper','staff_verbal')),
  recorded_by_staff_id INTEGER, ip TEXT, created_at TEXT NOT NULL, superseded_at TEXT)
CREATE INDEX ix_consent_current ON consents(participant_id, consent_type_id) WHERE superseded_at IS NULL;
```

- The table is append-only. A new answer sets `superseded_at` on the previous row.
- A consent is "current for booking" only if `superseded_at IS NULL`, its type version is the active version, and `source != 'import'`.
- Seeded types (v1):

| key | scope | options | required for |
|---|---|---|---|
| `photo` | participant | online / internal / none | short, full, adult |
| `first_aid` | participant | yes / no | full |
| `plasters` | participant | yes / no | full |
| `emergency_treatment` | participant | yes / no | full |
| `go_home_alone` | participant | yes / no | full; shown only if `min_age_months` is reached (default 132 = 11 years; Q11) |
| `funder_share` | participant | yes / no | optional; the funders are named in `help_text` |
| `info_correct` | account | — | all levels |
| `privacy_ack` | account | — | all levels |

- The `photo` type **merges the two overlapping photography questions** into one 3-option question, exactly as in mockup 1.
- Marketing is not a consent type. It lives in its own table because it also covers guests and newsletter sign-ups who have no account:

```sql
marketing_preferences(id, email TEXT NOT NULL UNIQUE COLLATE NOCASE, phone TEXT, name TEXT,
  account_id INTEGER REFERENCES accounts, guest_contact_id INTEGER REFERENCES guest_contacts,
  email_opt_in INTEGER NOT NULL DEFAULT 0, email_opt_in_at TEXT, email_confirmed_at TEXT /*double opt-in*/,
  sms_opt_in INTEGER NOT NULL DEFAULT 0, sms_opt_in_at TEXT, wording_version TEXT NOT NULL,
  source TEXT CHECK(source IN ('newsletter_form','registration','guest_booking','account_settings','legacy_newsletter')),
  unsubscribed_email_at TEXT, unsubscribed_sms_at TEXT, created_at, updated_at)
```

`data/subscribers.json` is migrated into this table as `source='legacy_newsletter'` with `email_confirmed_at=NULL`. There is no consent evidence for those entries, so they get a one-off re-permission email (Q18).

### 2.5 Collection password: hashed, not visible (decision)

- **Storage.** `collection_pw_hash = pbkdf2_sha256(HMAC(HAH_PEPPER, normalise(pw)), per-row salt, 100_000)`. `normalise` casefolds, trims and collapses internal whitespace, so "Blue Tiger" matches "blue  tiger".
- **At the door.** The register sign-out dialog has a "Password given" box. `POST /api/staff/attendance/<booking>/check-password` returns only `{match: bool}`. It is rate-limited to 5 attempts per child per day; a sixth wrong attempt creates an in-tray alert for the manager and DSL. Every check writes `audit_log('collection.check', result)`.
- **Why hashed rather than visible:**
  1. It is a security credential; the mockup even has a "Confirm collection password" field. If staff can read it, a register left open on a tablet, a printed register, or a screenshot leaks it.
  2. Checking at the door is one field and one tap, and each check leaves an auditable trail.
  3. Printed backup registers show only "🔑 set", so paper never carries it.
  4. The pepper, held in env and not in the database, means a stolen database or backup can't be brute-forced offline even though these passwords are low-entropy words.
- **Cost of this choice.** If connectivity fails at pick-up, staff fall back to the existing procedure: phone the parent or emergency contact on the printed register. A forgotten password is reset by the parent in My family, or by staff on a documented request (audited). Q11 asks the DSL to confirm this procedure.

### 2.6 Catalogue

```sql
centres(id, name TEXT NOT NULL UNIQUE, address TEXT, postcode TEXT, active INTEGER NOT NULL DEFAULT 1)
activity_categories(id, key TEXT UNIQUE, name TEXT NOT NULL,
  report_group TEXT NOT NULL,     -- 'HAF','Holiday club','Saturday club','Home Ed','Baby & toddler','Events','Young adults'
  colour TEXT, sort INTEGER)
activities(id, slug TEXT NOT NULL UNIQUE, title TEXT NOT NULL, category_id INTEGER NOT NULL REFERENCES activity_categories,
  centre_id INTEGER NOT NULL REFERENCES centres, summary TEXT, description TEXT, image TEXT,
  status TEXT NOT NULL CHECK(status IN ('draft','scheduled','published','unpublished','archived')), publish_at TEXT,
  booking_opens_at TEXT, booking_closes_hours INTEGER NOT NULL DEFAULT 0,
  min_age_months INTEGER NOT NULL DEFAULT 0, max_age_months INTEGER NOT NULL DEFAULT 1200, CHECK(min_age_months <= max_age_months),
  age_basis TEXT NOT NULL DEFAULT 'first_session' CHECK(age_basis IN ('session_date','first_session','school_year')),
  min_school_year INTEGER, max_school_year INTEGER,          -- Reception=0, Y1=1 … when age_basis='school_year'
  registration_level TEXT NOT NULL CHECK(registration_level IN ('guest','short','full','adult')),
  requires_approval INTEGER NOT NULL DEFAULT 0, parent_must_stay INTEGER NOT NULL DEFAULT 0, haf_only INTEGER NOT NULL DEFAULT 0,
  price_pence INTEGER NOT NULL DEFAULT 0, adult_price_pence INTEGER NOT NULL DEFAULT 0,
  capacity_default INTEGER NOT NULL, capacity_counts TEXT NOT NULL DEFAULT 'children' CHECK(capacity_counts IN ('children','all_people')),
  max_party_size INTEGER NOT NULL DEFAULT 8,
  waitlist_enabled INTEGER NOT NULL DEFAULT 1, waitlist_mode TEXT NOT NULL DEFAULT 'auto_offer' CHECK(waitlist_mode IN ('auto_offer','manual')),
  allow_pay_later INTEGER NOT NULL DEFAULT 0, allow_trial INTEGER NOT NULL DEFAULT 0, cancel_policy TEXT /*JSON override*/,
  created_by INTEGER, created_at, updated_at, archived_at)
activity_sessions(id, activity_id INTEGER NOT NULL REFERENCES activities, date TEXT NOT NULL, start_time TEXT NOT NULL,
  end_time TEXT NOT NULL, theme TEXT /* "Puppetry" */, centre_id INTEGER REFERENCES centres /*override*/,
  capacity INTEGER NOT NULL CHECK(capacity >= 0), price_pence INTEGER /*override*/,
  status TEXT NOT NULL DEFAULT 'scheduled' CHECK(status IN ('scheduled','cancelled')), cancelled_reason TEXT, staff_notes TEXT,
  created_at, UNIQUE(activity_id, date, start_time))
CREATE INDEX ix_sess_date ON activity_sessions(date);
```

- **"Past" is derived:** the latest session date is before today.
- **Ages are stored in months.** "Giggles and Wriggles Babies (0 to 1)" becomes 0-23 months and "Toddlers (2 to 4)" becomes 24-59. The admin UI edits ages as years plus months.
- **HAF is modelled as its own activity** ("Summer HAF", `haf_only=1`, price 0). That matches how the charity already lists it and avoids sub-quotas inside the paid club.

### 2.7 Bookings, waiting list and registers

```sql
checkouts(id, ref TEXT UNIQUE NOT NULL, account_id INTEGER REFERENCES accounts, guest_contact_id INTEGER REFERENCES guest_contacts,
  idempotency_key TEXT NOT NULL, amount_pence INTEGER NOT NULL,
  pay_mode TEXT NOT NULL CHECK(pay_mode IN ('stripe','pay_later','free','staff_offline','staff_link')),
  status TEXT NOT NULL CHECK(status IN ('creating','awaiting_payment','completed','expired','failed','cancelled')),
  stripe_session_id TEXT UNIQUE, stripe_url TEXT, expires_at TEXT, completed_at TEXT, created_at,
  UNIQUE(account_id, idempotency_key), UNIQUE(guest_contact_id, idempotency_key))

bookings(id, ref TEXT UNIQUE NOT NULL, checkout_id INTEGER REFERENCES checkouts,
  session_id INTEGER NOT NULL REFERENCES activity_sessions, activity_id INTEGER NOT NULL REFERENCES activities,
  kind TEXT NOT NULL CHECK(kind IN ('participant','party')),
  participant_id INTEGER REFERENCES participants, account_id INTEGER REFERENCES accounts, guest_contact_id INTEGER REFERENCES guest_contacts,
  party_adults INTEGER NOT NULL DEFAULT 0, party_children INTEGER NOT NULL DEFAULT 0, places INTEGER NOT NULL CHECK(places > 0),
  status TEXT NOT NULL CHECK(status IN ('pending_payment','pending_approval','confirmed','waitlisted','offered','cancelled','expired')),
  funding TEXT NOT NULL DEFAULT 'paid' CHECK(funding IN ('paid','haf','free','staff_comp')),
  price_pence INTEGER NOT NULL DEFAULT 0, is_trial INTEGER NOT NULL DEFAULT 0,
  hold_expires_at TEXT, offer_expires_at TEXT, waitlist_priority INTEGER NOT NULL DEFAULT 0,
  approved_at TEXT, approved_by INTEGER, cancelled_at TEXT, cancelled_by_staff INTEGER, cancelled_by_account INTEGER, cancel_reason TEXT,
  created_via TEXT NOT NULL CHECK(created_via IN ('online','staff','walkin','guest')), created_by_staff INTEGER, notes TEXT,
  created_at, updated_at,
  CHECK((kind='participant' AND participant_id IS NOT NULL AND places=1) OR (kind='party' AND participant_id IS NULL)),
  CHECK(account_id IS NOT NULL OR guest_contact_id IS NOT NULL))
CREATE UNIQUE INDEX ux_booking_child_session ON bookings(session_id, participant_id)
  WHERE participant_id IS NOT NULL AND status NOT IN ('cancelled','expired');
CREATE INDEX ix_book_session ON bookings(session_id, status);   -- + (account_id), (participant_id), (status, hold_expires_at), (status, offer_expires_at)
booking_party_children(booking_id INTEGER REFERENCES bookings ON DELETE CASCADE, participant_id INTEGER REFERENCES participants,
  PRIMARY KEY(booking_id, participant_id))    -- named children when a signed-in family books a guest-level event

attendance(id, booking_id INTEGER NOT NULL UNIQUE REFERENCES bookings, session_id INTEGER NOT NULL REFERENCES activity_sessions,
  participant_id INTEGER REFERENCES participants,
  status TEXT NOT NULL DEFAULT 'expected' CHECK(status IN ('expected','present','absent','absent_notified')),
  arrived_count INTEGER, late INTEGER NOT NULL DEFAULT 0,
  signed_in_at TEXT, signed_in_by INTEGER, signed_out_at TEXT, signed_out_by INTEGER,
  release_method TEXT CHECK(release_method IN ('password','went_home_alone','parent_stayed','known_adult_verified','other')),
  collected_by_name TEXT, collected_by_relationship TEXT, incident_discussed INTEGER, notes TEXT,
  snap_age_months INTEGER, snap_haf INTEGER, snap_send INTEGER, snap_category TEXT,   -- reporting survives anonymisation
  updated_at)
CREATE INDEX ix_att_session ON attendance(session_id);
```

- **Booking status meanings:**
  - `pending_payment`: in Stripe Checkout; the place is held until `hold_expires_at` and the worker releases it.
  - `pending_approval`: the place is reserved while staff decide.
  - `waitlisted`: queued, ordered by `waitlist_priority DESC, created_at`.
  - `offered`: the place is reserved until `offer_expires_at`.
  - `expired`: a hold or offer that lapsed.
- **Attended and no-show are not booking statuses.** They come from `attendance`: a no-show is a confirmed booking whose attendance is `absent` and was not notified.
- An `attendance` row is created when a booking becomes `confirmed`.

### 2.8 Money

```sql
invoices(id, number TEXT NOT NULL UNIQUE /* HAH-2026-00042 */, account_id INTEGER REFERENCES accounts,
  guest_contact_id INTEGER REFERENCES guest_contacts, checkout_id INTEGER REFERENCES checkouts,
  bill_to_name TEXT NOT NULL, bill_to_email TEXT, bill_to_address TEXT,                        -- snapshots (survive erasure)
  issue_date TEXT NOT NULL, due_date TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('issued','part_paid','paid','void','credited')),
  total_pence INTEGER NOT NULL, paid_pence INTEGER NOT NULL DEFAULT 0, credited_pence INTEGER NOT NULL DEFAULT 0,
  notes TEXT, created_by_staff INTEGER, sent_at TEXT, voided_at TEXT, void_reason TEXT, created_at)
invoice_lines(id, invoice_id INTEGER NOT NULL REFERENCES invoices, booking_id INTEGER REFERENCES bookings,
  description TEXT NOT NULL, service_date TEXT, participant_name TEXT, quantity INTEGER NOT NULL DEFAULT 1,
  unit_pence INTEGER NOT NULL, amount_pence INTEGER NOT NULL, funding_note TEXT)
payments(id, ref TEXT UNIQUE NOT NULL, account_id INTEGER REFERENCES accounts, guest_contact_id INTEGER REFERENCES guest_contacts,
  amount_pence INTEGER NOT NULL CHECK(amount_pence > 0),
  method TEXT NOT NULL CHECK(method IN ('stripe_card','cash','card_terminal','bank_transfer','childcare_voucher',
                                        'tax_free_childcare','account_credit','other')),
  voucher_provider TEXT,       -- Edenred, Computershare, Kiddivouchers, Sodexo/Pluxee, Care 4 (holiday-club.html:41)
  reference TEXT, status TEXT NOT NULL CHECK(status IN ('pending','succeeded','failed')),
  stripe_payment_intent_id TEXT UNIQUE, stripe_checkout_session_id TEXT, stripe_charge_id TEXT,
  received_at TEXT NOT NULL, recorded_by_staff INTEGER, notes TEXT, created_at)
payment_allocations(payment_id INTEGER REFERENCES payments, invoice_id INTEGER REFERENCES invoices, amount_pence INTEGER NOT NULL,
  PRIMARY KEY(payment_id, invoice_id))
credit_notes(id, number TEXT NOT NULL UNIQUE /* CN-2026-0007 */, invoice_id INTEGER NOT NULL REFERENCES invoices,
  account_id INTEGER, reason TEXT NOT NULL, total_pence INTEGER NOT NULL, created_by_staff INTEGER, created_by_account INTEGER, created_at)
credit_note_lines(id, credit_note_id INTEGER NOT NULL REFERENCES credit_notes, booking_id INTEGER, invoice_line_id INTEGER,
  description TEXT NOT NULL, amount_pence INTEGER NOT NULL)
refunds(id, credit_note_id INTEGER REFERENCES credit_notes, payment_id INTEGER NOT NULL REFERENCES payments,
  amount_pence INTEGER NOT NULL, method TEXT NOT NULL CHECK(method IN ('stripe','cash','bank_transfer','voucher_provider','account_credit')),
  status TEXT NOT NULL CHECK(status IN ('pending','succeeded','failed')), stripe_refund_id TEXT UNIQUE,
  created_by_staff INTEGER, created_at, processed_at, failure_reason TEXT)
stripe_events(id TEXT PRIMARY KEY /* evt_… */, type TEXT, received_at TEXT, processed_at TEXT, status TEXT, error TEXT)
```

- An account's credit balance is `Σ refunds(method='account_credit', succeeded) − Σ payments(method='account_credit')`.
- Invoice `balance = total − paid − credited`.
- Invoice and credit-note numbers are allocated with `UPDATE counters SET value=value+1 … ` inside the confirming transaction, so they are gapless.
- Invoices are never deleted. `void` is allowed only when there are no payments.

### 2.9 Care records

```sql
incidents(id, ref TEXT UNIQUE NOT NULL,
  type TEXT NOT NULL CHECK(type IN ('injury','illness','incident','behaviour','near_miss','medication','safeguarding_concern')),
  restricted INTEGER NOT NULL DEFAULT 0,                 -- forced 1 for safeguarding_concern (DSL-only)
  session_id INTEGER REFERENCES activity_sessions, centre_id INTEGER REFERENCES centres, occurred_at TEXT NOT NULL, location TEXT,
  description TEXT NOT NULL, injury_details TEXT, first_aid_given INTEGER NOT NULL DEFAULT 0, first_aid_by TEXT,
  action_taken TEXT, witnesses TEXT, severity TEXT CHECK(severity IN ('minor','moderate','serious')),
  notifiable INTEGER NOT NULL DEFAULT 0, ofsted_notified_at TEXT,
  parent_notify TEXT NOT NULL CHECK(parent_notify IN ('not_required','at_collection','now')), not_required_reason TEXT,
  status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','closed')),
  reported_by_staff INTEGER NOT NULL, reviewed_by_staff INTEGER, reviewed_at TEXT, retention_until TEXT, created_at,
  CHECK(type <> 'safeguarding_concern' OR (restricted = 1 AND parent_notify = 'not_required')))
incident_participants(incident_id INTEGER REFERENCES incidents, participant_id INTEGER REFERENCES participants,
  role TEXT NOT NULL CHECK(role IN ('injured','involved','witness')),
  parent_notified_at TEXT, notified_via TEXT CHECK(notified_via IN ('in_person','phone','email','sms')), notified_by_staff INTEGER,
  parent_ack_at TEXT, parent_ack_name TEXT, message_delivery_id INTEGER, PRIMARY KEY(incident_id, participant_id))
incident_updates(id, incident_id INTEGER NOT NULL REFERENCES incidents, staff_id INTEGER NOT NULL, at TEXT NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('note','amendment','dsl_action','closure')), text TEXT NOT NULL)
-- append-only: CREATE TRIGGER incident_updates_ro BEFORE UPDATE ON incident_updates BEGIN SELECT RAISE(ABORT,'append-only'); END; (+ BEFORE DELETE)
```

After creation, an incident's core fields can be changed only by the owner or DSL. Every edit also writes an `incident_updates` amendment containing the previous values.

### 2.10 Messaging, in-tray, governance

```sql
message_templates(key TEXT PRIMARY KEY, channel TEXT CHECK(channel IN ('email','sms')), kind TEXT CHECK(kind IN ('service','marketing')),
  subject TEXT, body TEXT NOT NULL, is_system INTEGER NOT NULL DEFAULT 0, updated_at, updated_by INTEGER)
message_campaigns(id, kind TEXT NOT NULL CHECK(kind IN ('service','marketing')),
  channels TEXT NOT NULL CHECK(channels IN ('email','sms','email+sms')), subject TEXT, body_email TEXT, body_sms TEXT,
  audience TEXT NOT NULL /*JSON filter spec*/, status TEXT NOT NULL CHECK(status IN ('draft','scheduled','sending','sent','cancelled')),
  scheduled_for TEXT, created_by INTEGER NOT NULL, created_at, sent_at, recipient_count INTEGER, archived_at TEXT)
message_deliveries(id, campaign_id INTEGER REFERENCES message_campaigns, template_key TEXT, channel TEXT NOT NULL, kind TEXT NOT NULL,
  account_id INTEGER, guest_contact_id INTEGER, marketing_pref_id INTEGER, staff_id INTEGER, participant_id INTEGER, booking_id INTEGER,
  to_address TEXT NOT NULL, subject TEXT, body_text TEXT, body_html TEXT, headers TEXT /*JSON*/,
  status TEXT NOT NULL CHECK(status IN ('queued','sending','sent','failed','suppressed','cancelled')),
  attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at TEXT, locked_until TEXT, provider_id TEXT, error TEXT,
  created_at, sent_at, body_purged_at TEXT)
CREATE INDEX ix_deliv_queue ON message_deliveries(status, next_attempt_at);
contact_messages(id, name, email, phone, message, created_at, read_at, handled_by INTEGER)   -- migrated from data/messages.json
intray_items(id, type TEXT NOT NULL, title TEXT NOT NULL, entity_type TEXT, entity_id INTEGER, account_id INTEGER, participant_id INTEGER,
  centre_id INTEGER, activity_id INTEGER, session_id INTEGER, required_perm TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','snoozed','done')), snooze_until TEXT, assigned_staff_id INTEGER,
  created_at, resolved_at, resolved_by INTEGER)
audit_log(id, at TEXT NOT NULL, actor_type TEXT NOT NULL CHECK(actor_type IN ('staff','account','guest','system')), actor_id INTEGER,
  ip TEXT, action TEXT NOT NULL, entity_type TEXT, entity_id INTEGER, participant_id INTEGER, account_id INTEGER,
  details TEXT /*JSON: changed field NAMES, never special-category values*/, request_id TEXT)
-- CREATE TRIGGER audit_no_update BEFORE UPDATE ON audit_log BEGIN SELECT RAISE(ABORT,'append-only'); END;
-- CREATE TRIGGER audit_no_early_delete BEFORE DELETE ON audit_log WHEN OLD.at > strftime('%Y-%m-%dT%H:%M:%SZ','now','-6 years')
--   BEGIN SELECT RAISE(ABORT,'retention'); END;
import_batches(id, filename TEXT, file_sha256 TEXT, uploaded_by INTEGER, uploaded_at TEXT,
  status TEXT CHECK(status IN ('uploaded','mapped','dry_run','committed','rolled_back')), mode TEXT CHECK(mode IN ('row_per_child','two_files')),
  mapping TEXT /*JSON*/, stats TEXT /*JSON*/, committed_at TEXT, purge_after TEXT)
import_rows(id, batch_id INTEGER REFERENCES import_batches ON DELETE CASCADE, row_no INTEGER, raw TEXT /*JSON, purged 30d after commit*/,
  status TEXT CHECK(status IN ('ok','warning','error','skipped','duplicate')), messages TEXT, account_id INTEGER, participant_id INTEGER)
gdpr_requests(id, account_id INTEGER, kind TEXT CHECK(kind IN ('export','erasure','rectification','objection')),
  status TEXT CHECK(status IN ('open','in_progress','done','refused')), requested_at, due_at, completed_at, handled_by INTEGER, notes TEXT)
retention_holds(id, participant_id INTEGER, account_id INTEGER,
  reason TEXT NOT NULL CHECK(reason IN ('incident','safeguarding','legal_claim','financial')), until TEXT, created_by INTEGER, created_at)
-- Phase 2: private_files(id, owner_type, owner_id, filename, mime, size, sha256, path, required_perm, uploaded_by, created_at)
```

---

## 3. Public pages and APIs

### 3.1 Page routes

All of these send `X-Robots-Tag: noindex, nofollow`, and `render_page(seo=False)` omits the OG and JSON-LD tags.

| Path | Shell | Auth | Purpose |
|---|---|---|---|
| `/book` | portal/book.html | none | Book activities (mockup 2). Tabs are built from categories: Holiday clubs · Classes · Events · Young adults. |
| `/book/<slug>` | portal/book.html | none | Same page, filtered to one activity (the What's On "Book now" target). |
| `/book/<slug>/guest` | portal/guest-book.html | none | Quick booking for one-off events (mockup 4). |
| `/book/review` | portal/review.html | account | Selections, eligibility and level gaps, price, pay choice, T&Cs. |
| `/book/checkout/return` | portal/checkout-return.html | none | Stripe `success_url`. Polls the status endpoint. |
| `/register` | portal/register.html | none | Account-type chooser (mockup 3, four cards). |
| `/register/family` | portal/register-form.html | none | Holiday clubs and drop-off activities (full level). |
| `/register/little-ones` | portal/register-form.html | none | Baby and toddler, parent stays (short level). |
| `/register/adult` | portal/register-form.html | none | Young adults 18+ (adult level). |
| `/register/sent` | portal/register-sent.html | none | "Check your inbox". |
| `/login`, `/forgot-password`, `/reset-password`, `/activate`, `/verify-email` | portal/auth-*.html | none | Tokens are read from `location.hash`. |
| `/account` | portal/account.html | account | Dashboard: "things to do", upcoming bookings, offers, balance. |
| `/account/bookings` | portal/bookings.html | account | Upcoming, waiting list and offers, past, cancelled. |
| `/account/family`, `/account/family/<ref>` | portal/family*.html | account | Children and self; sections; complete missing information. |
| `/account/contacts` | portal/contacts.html | account | Emergency contacts. |
| `/account/details` | portal/details.html | account | My details, change email (verified), change password, sign out everywhere. |
| `/account/invoices`, `/account/invoices/<number>` | list shell; invoice is server-rendered HTML | account | Invoice with print CSS and "Pay now". |
| `/account/privacy` | portal/privacy.html | account | Marketing preferences, consents overview, download my data, delete account. |
| `/unsubscribe/<token>` | portal/unsubscribe.html | none | GET shows a confirm button. POST unsubscribes (including RFC 8058 one-click). |
| `/guest/manage` | portal/guest-manage.html | none (token in fragment) | A guest views or cancels their booking. |

### 3.2 Registration journeys (fields mapped to the email and mockup 1)

The chooser (mockup 3, re-cut to the email's four options: "Maybe these 4 options pop up") shows four cards. The "Teens" card becomes "Young adults (18+)".

1. **Holiday clubs and drop-off activities** → `/register/family` (full). The card text lists Holiday Club, Saturday Club and Home Education.
2. **Baby and toddler classes** (a parent stays) → `/register/little-ones` (short).
3. **Young adults (18+)** → `/register/adult`. The card says "Under 18? A parent or carer registers you."
4. **One-off events** → `/book?tab=events` (quick booking, no account).

Footer text: "Already registered? Sign in · Coming from our old booking system? Activate your account".

`?for=<level>&next=/book/<slug>` preselects a card when the visitor arrives from a Book button.

**Home Education sits under full, not "shorter".** It is drop-off provision where staff act in loco parentis, so first aid consent, GP and collection password all apply. The required level is a per-activity field, so staff can change it without code (Q8).

**Full level: `/register/family`.** Single long page of numbered section cards, with a sticky progress aside on desktop.

| # | Section | Fields (bold = required) | Stored in |
|---|---|---|---|
| 1 | Your details | **first name, last name, email, mobile**, **address line 1**, line 2, **town**, **postcode**, **password** (show/hide, ≥10 chars, common-password check) | accounts |
| 2 | About your child | **first, last name**, **DOB** (day/month/year; shows the age), gender (optional), **education** (at school / home educated), school name (if at school), **HAF status** (eligible for a HAF place / not eligible / not sure) plus HAF code (optional, if eligible) | participants |
| 3 | Emergency contacts | **2 contacts**, each **full name, relationship, phone**, plus "can collect" (new). Hint: "People we call if we can't reach you." | emergency_contacts |
| 4 | Doctor / GP | **surgery name**, doctor (optional), **surgery phone**, surgery postcode | participant_gp |
| 5 | Health, needs and requirements | allergies, plus "severe allergy / anaphylaxis" and "carries adrenaline pen" (new); **medical conditions and medication** (added; neither the email nor the mockup has it, but staff administering first aid need it); dietary; SEND; SEMH; religious requirements. Blank means none; the step-9 confirmation covers it. | participant_health |
| 6 | Family information | "Anything relevant to your child's safety" (**only our safeguarding lead sees this**); separately, "Anyone who must not collect your child" (**shown to session staff at pick-up**) | participant_safeguarding / participants.collection_alert |
| 7 | Permissions and consents | **photography** (one merged 3-option question), **first aid**, **plasters**, **emergency medical treatment**, **going home alone** (only if the child is at least the minimum age); funder data-sharing (optional, names the funders); marketing email / SMS (optional, unticked) | consents, marketing_preferences |
| 8 | Collection password | **password plus confirm** (`autocomplete="off"`) | participants.collection_pw_hash |
| 9 | Check and confirm | **"The information is correct and I'll tell you if anything changes"**, **"I've read the privacy notice and safeguarding policy"** (both linked) | consents (info_correct, privacy_ack) |

"Add another child" repeats sections 2 and 4-8, with "Same GP / same consents / same collection password as Maya" shortcuts. Contacts are family-level, so they aren't asked again.

**Short level: `/register/little-ones`.**
- Your details: name, email, mobile, postcode, password (address optional).
- Child: name, DOB.
- Emergency contact 1 (required), contact 2 (optional).
- Allergies, plus medical (optional).
- Photography.
- Confirm and privacy.
- A callout: "A parent or carer must stay for the whole session."

**Adult level: `/register/adult`.**
- Your details, with DOB (must be 18 or over on the registration date).
- Emergency contact 1.
- Allergies, medical and access needs (all optional).
- Photography; funder share (optional); marketing (optional).
- Confirm and privacy.
- Creates `accounts(kind='adult')` plus `participants(is_account_holder=1)`.

**Guest quick booking** (mockup 4) is covered in §3.7.

### 3.3 Levels, completeness and the "complete missing sections" flow

- `formspec.py` defines `SECTIONS = [Section(key, fields, required_for={'short','full',…}), …]`.
- `participants.level` is recomputed on every save as the highest level whose required sections are complete, considering only current consents (so imported ones don't count). The order is none < short < full; adult is separate.
- `eligibility.check(participant, session)` returns `ok`, `age` ("Ages 6-12"), `level_gap` (list of section keys), `needs_review`, `consent_reconfirm` or `haf_required`.
- On `/book`, an eligible-by-age child with a gap shows "Complete Leo's details to book (about 5 minutes)". That links to `/account/family/<ref>?complete=full&next=/book/review`, which renders only the missing sections. The basket survives in `localStorage`.
- This is how a holiday-club family books a one-off event (guest ≤ any level) and how a baby-class family later books holiday club: they're asked only for sections 3-8 that are missing.
- Age eligibility is filtered server-side. Children outside the age range are not offered the session; if no child is eligible, the row says "For ages 6-12".
- **Age in months** at the reference date is `(y2−y1)*12 + (m2−m1) − (d2<d1)`.
- **School year** is `academic_year_start(ref) − cohort(dob) − 5`, where the cohort starts on 1 September. Reception = 0. Example: a child born in October 2019 is in Year 2 in 2026/27.

### 3.4 Auth flows

- **Register.** The account is created with `status='unverified'` and the verify email is enqueued. The response is always "Check your inbox" (`/register/sent`).
  - If the email already exists, the submission is discarded and the owner of that address gets "Someone tried to register with your email. Sign in or reset your password". This prevents account enumeration: at a children's charity, knowing that an address is a customer is sensitive.
  - The link (`/verify-email#t=`) activates the account, signs the user in and returns them to `next` or `/book`.
  - Unverified accounts are purged after 7 days.
- **Login.** `POST /api/account/login {email,password}` returns a generic error on failure, then redirects to `/book` ("straight to booking portal") or `next`. `next` is validated as a local path: it must start with `/` and not `//`.
- **Forgot password.** `POST /api/account/password/forgot` always returns 200.
  - Active account: reset link (60 min).
  - `pending_activation`: activation link.
  - No account: a "no account with this email, register here" message sent to that address.
- **Activate (imported families).** `/activate#t=` → `POST /api/account/activate {token, child_dob, password}`.
  - The **DOB challenge** asks for the date of birth of any child on the account. It limits the damage from a mistyped email in the MagicBooking export or a forwarded activation email. Five attempts, then the token is burned and staff get an in-tray item.
  - Activation sets `email_verified_at` and signs the user in. They land on "Check your family's details", which reconfirms consents and clears `needs_review`.
  - `/activate` without a token shows an "enter your email" form that uses the same neutral send.
- **Password policy.** At least 10 characters, checked against a bundled `hah/data/common-passwords.txt` (top 10k, about 80 KB), no composition rules (NIST 800-63B). Hashing is PBKDF2-SHA256 at 600k iterations, rehashed on login if the stored iteration count is lower.
- **Email change.** Verification goes to the new address; a notice goes to the old one.

### 3.5 Book activities (mockup 2)

- **Catalogue.** `GET /api/book/catalogue?tab=&q=&from=&to=&activity=` returns published activities with sessions from today onwards within their booking window. Each session carries `places_left` (exact), `state` (`open` / `few` when ≤3 / `full` / `waitlist` / `not_open_yet` (plus `opens_at`) / `closed`), price and theme.
  - When signed in, each session also carries `per_participant` eligibility for the "Booking for Maya (8) and Leo (11) · Change" chips.
  - The **Change** control toggles which children are booking. Their ages are computed at the activity's age basis.
- **Select** adds the (session × every chosen eligible child) pairs to the basket.
  - If `places_left` is less than the number of children, the row shows "Only 1 left: choose fewer children or join the waiting list".
  - **Full** switches the button to **Waiting list**, which calls `POST /api/book/waitlist` straight away (no payment).
- **Sticky bar.** "N selected · Continue" goes to `/book/review`.
- **Review.** `POST /api/book/quote {items}` returns lines, prices, HAF and funding treatment, blocking issues (level gaps, reconfirmation, unverified email) and available pay options.
  - Pay options: **Pay by card now** (Stripe). **Pay later** (childcare vouchers, Tax-Free Childcare, bank transfer) appears only if `activity.allow_pay_later AND (account.pay_later_allowed OR settings.pay_later_for_all)`.
  - Free, HAF and approval-required lines show "No payment now".
- **Confirm.** `POST /api/book/confirm {items, pay_mode, accept_terms, idempotency_key}`, with the idempotency key generated when the review page loads. It runs one transaction; per item, in this order:
  1. Guards: activity published; booking window open; session scheduled; eligibility; `haf_only` requires `haf_status` of verified or claimed_eligible.
  2. Status:
     - `requires_approval` or a HAF claim that isn't verified → `pending_approval` (place reserved).
     - Otherwise capacity check, with any existing waiting list taking priority: full → `waitlisted`.
     - Otherwise: price 0 → `confirmed`; pay later → `confirmed` with an unpaid invoice; Stripe → `pending_payment` with `hold_expires_at = now + 35 min`.
  3. Write the checkout row, bookings and attendance rows (for confirmed bookings), invoice and lines (for confirmed paid or pay-later bookings), outbox emails and in-tray items (approval, HAF verification).
  4. Commit. If there are Stripe items, create the Checkout Session (§3.6) and return `{checkout_url}`. Otherwise return `{refs, summary}`.
  - Items that ended up waitlisted are reported clearly on the result page.
- **Approval** (staff, §4.3). Approve → `confirmed`, an invoice if the price is above 0, and a "Pay now" link email (Stripe) or pay-later terms. Decline → `cancelled`, which frees the place for the waiting list.
- **Waiting list.**
  - While any `waitlisted` row exists for a session, new direct bookings are waitlisted too (FIFO fairness).
  - When a place frees up (cancellation, decline, expiry, capacity increase), the worker handles it in `auto_offer` mode: the first entry becomes `offered` with `offer_expires_at` (default 24 h; 2 h if the session is within 48 h) and an email plus SMS goes out.
  - `POST /api/book/offer/<ref>/accept` confirms. Paid offers go through the Stripe flow, and the place stays reserved while the hold runs. `/decline` frees it, and on expiry the offer moves to the next person.
  - In `manual` mode, the waiting list and in-tray show the freed place and staff choose.

### 3.6 Checkout and Stripe (`hah/booking/stripe.py`)

- **Client.** `urllib.request` with `ssl.create_default_context()` and a 20 s timeout. Headers: `Authorization: Bearer $STRIPE_SECRET_KEY`, pinned `Stripe-Version`, `Idempotency-Key: checkout.ref`. Bodies are form-encoded with bracketed nested keys. `STRIPE_API_BASE` can be overridden so tests can use a fake.
- **Create.** `POST /v1/checkout/sessions` with:
  - `mode=payment`, `currency=gbp`
  - `line_items[i][price_data][unit_amount]`, `[product_data][name]` (e.g. "Summer Club 2026 · Mon 27 Jul · Maya"), `quantity=1`
  - `customer_email`, `client_reference_id=checkout.ref`, `metadata[checkout_ref]`, `payment_intent_data[metadata][checkout_ref]`
  - `success_url={SITE}/book/checkout/return?c={ref}&session_id={CHECKOUT_SESSION_ID}` and `cancel_url={SITE}/book/review?cancelled=1`
  - `expires_at=now+30min` (Stripe's minimum)
  - `locale=en-GB`
  - The session id and URL are saved in a second transaction. If Stripe errors, a compensating transaction expires the bookings and the user sees "Payment couldn't start. Nothing was booked."
- **Webhook.** `POST /api/stripe/webhook` with `csrf=False`, a 256 KB limit and the raw body:

```python
def verify(raw: bytes, header: str, secret: str, tolerance=300) -> bool:
    parts = dict(p.split("=", 1) for p in header.split(",") if "=" in p)   # t=…, v1=… (may repeat: collect all v1)
    t = int(parts["t"]); sigs = [v for k, v in (p.split("=",1) for p in header.split(",")) if k == "v1"]
    expected = hmac.new(secret.encode(), f"{t}.".encode() + raw, hashlib.sha256).hexdigest()
    return abs(time.time() - t) <= tolerance and any(hmac.compare_digest(expected, s) for s in sigs)
```

  - Deduplication is `INSERT OR IGNORE INTO stripe_events`; an event that was already processed returns 200.
  - `checkout.session.completed` with `payment_status=paid`, and `checkout.session.async_payment_succeeded`, both call `confirm_checkout(ref)`. That function is idempotent. In one transaction it moves bookings `pending_payment → confirmed`, creates the payment (`stripe_card`, `payment_intent`), the invoice and lines, the allocation and attendance rows, and enqueues "Booking confirmed + invoice (PAID)".
  - `checkout.session.expired` and `async_payment_failed` → `release_checkout(ref)`.
  - `charge.refunded` and `refund.updated` update `refunds.status`.
  - `charge.dispute.created` → in-tray (finance).
- **Late payment.** If a payment succeeds for bookings that have already expired: re-confirm if capacity allows; otherwise auto-refund, apologise by email and add an in-tray item. Holds are released only after the worker has called `POST /v1/checkout/sessions/{id}/expire` (or confirmed the session is `expired`), which makes this very unlikely.
- **Return page.** It polls `GET /api/book/checkout/<ref>/status`. If the webhook hasn't arrived after about 10 s, the server calls `GET /v1/checkout/sessions/{id}` and runs the same `confirm_checkout`.
- **Paying an invoice later** (`POST /api/account/invoices/<number>/pay`, or a staff "send pay link") creates a Checkout Session for the balance with `metadata[invoice_number]`. The webhook allocates the payment to that invoice.
- **Refunds.** `POST /v1/refunds {payment_intent, amount, metadata[credit_note]}`.
- **Pay-later policy.**
  - The invoice's `due_date` is `min(issue + terms_days, first_session − 3 days)`.
  - Reminders go at due −3 days, on the due date, and at +7 days.
  - Unpaid invoices are never auto-cancelled in Phase 1; they appear under **delayed payments**, which staff act on (Q2).
  - Invoices list the Ofsted URN EY496668 and the payment reference format for Tax-Free Childcare and voucher providers.

### 3.7 Guest one-off booking (mockup 4)

- `GET /book/<slug>/guest?session=<id>` shows the "You're booking" card, then:
  1. Your contact details: email, phone.
  2. How many places: adults and children as selects up to `max_party_size`; the age range is shown with a "children attending are within the age range" checkbox; "Keep me updated about future events" (optional, unticked); a just-in-time privacy line.
- `POST /api/book/guest` (rate-limited, honeypot, idempotency key) upserts `guest_contacts` by email and creates a `party` booking with `places = children (+ adults if capacity_counts='all_people')`.
  - Free events → `confirmed`, confirmation email with a manage link (`/guest/manage#t=`) and the event details.
  - Paid events → Stripe Checkout as in §3.6, with an invoice billed to the guest's email.
- An opt-in creates or updates `marketing_preferences` (source `guest_booking`) and sends a double opt-in confirmation.
- **Signed-in families** booking a guest-level event use the normal `/book` flow. The review page creates one party booking with named children in `booking_party_children`, so their allergy flags appear on the register, plus an "adults attending" count. The email's "parents from holiday club booking one-off events" is therefore handled without a separate registration.
- "Have an account? Sign in to book faster" is shown above the guest form.

### 3.8 My bookings, My family, invoices

- **My bookings.**
  - Upcoming bookings are grouped by date, with a status chip, the child, price and paid state.
  - Waiting list shows the position (`#3`). Offers have Accept and Decline buttons with a countdown.
  - Cancel (`POST /api/account/bookings/<ref>/cancel`) is allowed until `cancel_cutoff_hours` before the session (default 48). The outcome follows the policy in settings, overridable per activity:
    - ≥ `refund_days` before: full refund to card for Stripe payments; otherwise account credit.
    - Between the cutoff and `refund_days`: account credit only.
    - After the cutoff: only "Tell us your child can't come", which records `absent_notified` with no money back.
  - Every cancellation of an invoiced line issues a credit note. Pending-payment bookings simply expire (Q1).
- **My family.** Cards per child show age, level badge ("Ready for holiday clubs" / "Baby & toddler only") and "Needs checking" if `needs_review` is set. Sections can be edited individually. Health edits for a child booked within 7 days create an in-tray item "Health info changed for Maya (booked Mon)". The collection password can be changed (the current one is never shown). A child can be archived ("no longer attends").
- **Invoices.** List with status and balance; `/account/invoices/<number>` server-renders `templates/invoice.html` (the same renderer as the email). It includes charity name, address, charity and Ofsted numbers, bill-to, lines (date · activity · child), HAF or free lines at £0, total, paid, balance, bank details and payment references. There is a "Print / save as PDF" button (browser print; no PDF library) and a "Pay now" button.

### 3.9 Communication preferences and unsubscribe

- `/account/privacy` has toggles for email and SMS marketing. Each change is recorded with `wording_version`. Service messages can't be switched off, and the page explains that.
- **Unsubscribe.** Every marketing email has a footer link plus `List-Unsubscribe: <{SITE}/unsubscribe/{token}>` and `List-Unsubscribe-Post: List-Unsubscribe=One-Click` (RFC 8058).
  - `GET /unsubscribe/<token>` renders a page with a button. A browser GET never unsubscribes, which protects against link scanners.
  - `POST /unsubscribe/<token>` (CSRF-exempt; the token is the credential) unsubscribes immediately and returns 200.
  - `log_message` rewrites `/unsubscribe/<anything>` to `/unsubscribe/[redacted]`.
  - Marketing SMS ends with "Opt out: {SITE}/u/{short_token}", a short HMAC token, also redacted in the log. Twilio's own STOP handling covers replies on long-code numbers.
- **Footer newsletter** (`/api/newsletter`, currently `server.py:702-718`) moves to `marketing_preferences` with double opt-in. The "confirm your subscription" link uses a fragment token. This fixes the promise at `public/privacy.html:45`.

### 3.10 Data export (SAR) and account deletion

**Export.** `POST /api/account/export {password}` (step-up) synchronously streams a `zipfile` containing `my-data.json` (account, participants, health, consents with history, contacts, GP, bookings, attendance, invoices, payments, credit notes, message metadata and bodies, marketing preferences) and `my-data.html` (human-readable).
- Restricted records (safeguarding family information, safeguarding concerns) are **excluded** from the self-service export. That's a lawful exemption where disclosure could cause serious harm, and it applies to data about other people. Instead, a `gdpr_requests(kind='export')` item goes to the DSL for a manual response within one month.
- The export is audited.

**Delete account.** `POST /api/account/delete {password, confirm:"DELETE"}`.

*Immediately (one transaction):*
1. Cancel future bookings under the normal policy; refunds go to credit notes.
2. Revoke sessions, unsubscribe everything, set `status='closed'` and `erase_after = now + 14 days` (a cooling-off period for accidental deletion; staff can reverse it within that window).
3. Create a `gdpr_requests` erasure item.
4. Enqueue a confirmation email to the account address before it is anonymised.

*Erasure job (worker, after `erase_after`). Retention exceptions are applied per record:*

| Data | What happens | Why |
|---|---|---|
| Invoices, credit notes, payments, refunds | **Kept** 6 years after the end of the financial year. `bill_to_*` snapshots stay. `account_id` link kept; the account row itself is anonymised. | Charities Act 2011 s.131 and HMRC accounting records (legal obligation). |
| Invoice lines | `participant_name` replaced with "Child (deleted)" | Keep the financial record, drop the child's identity |
| Attendance rows | Kept until the standard register retention (default 3 years after the session), then `participant_id` is nulled and the `snap_*` columns keep reports working. On erasure, only the link to a named participant is removed early if no hold applies. | Childcare Register record-keeping (Q6) |
| Participants with incidents (injury or illness) | `status='retention_hold'`. Only name, DOB and the incident rows are kept. Health, GP, consents, contacts and collection password are erased. `retention_holds.until` = the child's 21st birthday or incident + 3 years, whichever is later. | Limitation period for personal-injury claims by children |
| Participants with safeguarding concerns | Hold until at least the child's 25th birthday. **Never auto-erased.** The DSL reviews it through an in-tray item. | Safeguarding guidance; Art 17(3)(b)/(e) exemptions |
| Everything else (health, safeguarding family info, contacts, GP, consents, marketing, messages, participants without holds) | Deleted, or the row anonymised where FKs require it: names "Deleted", email `deleted-<id>@invalid`, phones and addresses nulled, `anonymised_at` set | Art 17 |
| Audit log | Kept for 6 years. It holds field names only, never values. | Accountability and security (legitimate interests) |
| Backups | Rotate out within 35 days (§7.4). The privacy notice says so. | |

**Retention job defaults** (nightly; all confirmed in Q6):
- Accounts inactive for 3 years: warning email, then anonymise after 30 days.
- Unverified accounts: 7 days.
- Imported accounts never activated: 12 months.
- Guest contacts: 12 months after their last event.
- Message bodies: 24 months.
- Import raw rows: 30 days after commit.
- `stripe_events`: 90 days.
- Expired sessions and tokens: daily.

### 3.11 Public and account API summary

All are JSON. POST routes need CSRF (except where marked) and have rate buckets.

| Method and path | Auth | Notes |
|---|---|---|
| GET `/api/book/catalogue`, `/api/book/activity/<slug>` | optional | Availability plus per-child eligibility when signed in |
| POST `/api/book/quote`, `/api/book/confirm`, `/api/book/waitlist` | account | confirm is idempotent by key |
| POST `/api/book/offer/<ref>/accept`, `/decline` | account | |
| GET `/api/book/checkout/<ref>/status` | none | Checkout ref only, no personal data |
| POST `/api/book/guest` | none | Honeypot, `rate=guest_booking` (5/h/IP) |
| POST `/api/guest/manage`, `/api/guest/cancel` | token in body | |
| POST `/api/stripe/webhook` | signature | csrf=False |
| POST `/api/account/register`, `/login`, `/logout`, `/password/forgot`, `/password/reset`, `/activate`, `/email/verify` | none (logout: account) | Neutral responses; rate buckets |
| GET `/api/account/me` | account | Includes `csrf` and `todo[]` |
| GET/POST `/api/account/profile`, `/contacts` | account | |
| GET `/api/account/participants`, `/participants/<ref>?sections=` | account | |
| POST `/api/account/participants` (add), `/participants/<ref>/update {section, fields}`, `/participants/<ref>/collection-password`, `/participants/<ref>/archive` | account | Validated against formspec |
| POST `/api/account/consents {items:[{participant, key, value}]}` | account | Append-only |
| GET `/api/account/bookings`; POST `/api/account/bookings/<ref>/cancel`, `/absent` | account | |
| GET `/api/account/invoices`; POST `/api/account/invoices/<number>/pay` | account | |
| GET/POST `/api/account/preferences` | account | |
| POST `/api/account/export`, `/api/account/delete`, `/api/account/sessions/revoke-all` | account + password | |
| POST `/api/newsletter`, `/api/newsletter/confirm`, `/unsubscribe/<token>` | none | |
| GET `/healthz` | none | DB ok plus worker heartbeat age (becomes Render's `healthCheckPath`) |

---

## 4. Admin back office

### 4.0 Roles and permissions (`hah/permissions.py`)

| Permission | owner | admin | manager | session_staff | dsl | send_lead | finance |
|---|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| site.content (CMS) | ✓ | ✓ | ✓ | | | | |
| activities.view / .manage | ✓/✓ | ✓/✓ | ✓/✓ | ✓/– | ✓/– | ✓/– | ✓/– |
| bookings.view / .manage | ✓/✓ | ✓/✓ | ✓/✓ | ✓/– | ✓/– | ✓/– | ✓/– |
| bookings.override (capacity, age, window) | ✓ | ✓ | ✓ | | | | |
| payments.record (reception) | ✓ | ✓ | ✓ | ✓ | | | ✓ |
| finance.view / .manage (refunds, credit notes, void) | ✓/✓ | ✓/✓ | ✓/– | | | | ✓/✓ |
| registers.view / .mark | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | |
| people.view_basic | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| people.view_health (full child record) | ✓ | ✓ | ✓ | register only | ✓ | ✓ | |
| people.edit | ✓ | ✓ | ✓ | | ✓ | ✓ | |
| safeguarding.view (family info, restricted incidents) | ✓* | | | | ✓ | | |
| send.view (Phase 2 intake and EHCP) | ✓ | | ✓ | | ✓ | ✓ | |
| incidents.log / .view / .manage | ✓/✓/✓ | ✓/✓/✓ | ✓/✓/✓ | ✓/own/– | ✓/✓/✓ | ✓/✓/– | |
| messaging.service / .marketing | ✓/✓ | ✓/✓ | ✓/✓ | | | | |
| reports.view | ✓ | ✓ | ✓ | | ✓ | ✓ | ✓ |
| import.run, settings.manage, staff.manage | ✓ | ✓ (not owners) | | | | | |
| audit.view, gdpr.manage | ✓ | | | | ✓ | | |

- \* The owner has `safeguarding.view` only if the owner is also a designated DSL. By default it is an explicit role grant, not implied by owner.
- Health data reaches session staff through the register endpoint only (children booked on the session being viewed). They can't search for arbitrary health records.
- Every read of a health or safeguarding section writes an `audit_log` row (`participant.health.view`, `participant.safeguarding.view`, `register.view`).
- The server returns only the sections the viewer is allowed to see. Sections are never sent to the browser and hidden there.

### 4.1 Dashboard and In-tray

- **Dashboard tiles:** today's sessions (booked / arrived), pending approvals, offers expiring, delayed payments (£), open incidents, failed messages. Each tile deep-links to its area.
- **In-tray** (`GET /api/staff/intray?type&centre&activity&session&status&q&page`) is populated by domain events:

| Type | Raised when |
|---|---|
| `contact_message` | Contact form submission (migrated `messages.json`) |
| `approval_needed` | A booking goes to `pending_approval` |
| `haf_verify` | A HAF claim needs verification |
| `waitlist_manual` / `offer_expired` | A place frees in manual mode, or an offer lapses |
| `health_changed` | Health info changes for a child booked within 7 days |
| `incident_followup` | Parent not yet notified or acknowledged |
| `safeguarding_new` | New safeguarding concern (DSL permission only) |
| `payment_failed` / `payment_overdue` / `dispute` | Payment problems |
| `message_failed` | A delivery failed after retries |
| `gdpr_request` | Export or erasure request |
| `import_issue` | Problem during family import |
| `activation_problem` | DOB challenge burned |
| `collection_pw_mismatch` | Sixth wrong collection password attempt |

- Items are filtered by the `required_perm` the viewer holds.
- Actions: `POST /api/staff/intray/<id>/resolve|snooze|assign`.
- Filtering by centre, activity and session is native because every item stores `centre_id`, `activity_id` and `session_id` (the email's requirement).

### 4.2 Activities

- **List.** Status tabs: Draft · Scheduled · Published · Unpublished · Past · Archived · All. Search, category and centre filters. Columns: next session, sessions, booked/capacity, status chip.
- **Editor.** Four cards:
  - Details: title, stable slug, category, centre, summary, description, image picker (reuses the upload widget).
  - Who and how: age range (years+months, or school years), age basis, registration level, requires approval, parent must stay, HAF only, capacity counts.
  - Booking: opens at, closes N hours before, waiting list on/mode, pay later allowed, trial allowed (Phase 2 UI).
  - Price: price per session, adult price for party bookings, cancellation policy override.
- **Status transitions** (`POST /api/staff/activities/<id>/status {to, publish_at}`):
  - draft → scheduled (the worker publishes at `publish_at`) → published.
  - published ↔ unpublished: hidden from `/book`; existing bookings stand.
  - Any state → archived: read-only, no new bookings.
  - Publishing is blocked with explicit errors if there are no sessions, no capacity, or a paid activity while Stripe isn't configured.
- **Sessions tab.** Editable table (date, times, theme, capacity, price override, booked, status). Per-session "Cancel session…" opens a dialog: reason, refund mode, notify by email/SMS. The result is a bulk cancel, credit notes and service messages.
- **Session generator** (`POST …/sessions/generate`): date range, weekdays, start and end times, capacity, skip dates (with an "England bank holidays" preset kept in settings), a themes list (the first theme goes on the first generated day, and so on), and `dry_run` that returns a preview before the real insert.
- **Duplicate for next term** (`POST …/duplicate {new_start_date, title}`): copies the activity as a draft and shifts every session by the day offset from the first session, keeping weekdays aligned. The slug gets `-2027-t1` or a similar suffix.
- **Export:** `GET /api/staff/activities/export.csv` (list) and `/<id>/export.csv` (sessions with booked, waitlisted and attended counts). Both use `csv_safe()`.
- **Seed data.** Categories: HAF, Holiday club, Saturday club, Home Ed, Baby & toddler, Events, Young adults. One centre (the Boscombe hub). The five activities from the email are created as drafts:

| Activity | Ages |
|---|---|
| Giggles and Wriggles Babies | 0-23 months, short, parent stays |
| Giggles and Wriggles Toddlers | 24-59 months, short, parent stays |
| Home Ed Programme Term 5 2026 | 5-10, full |
| Summer Club 2026 | 6-12, full, £30 per day |
| Summer HAF | 6-12, full, HAF only, requires approval unless verified |

### 4.3 Bookings

- **List** (`GET /api/staff/bookings?status&activity&session&from&to&funding&trial&q&page`). Quick chips: Under approval · Waiting list · Unpaid · Cancelled · Trials.
- **Detail drawer:** booking, child (with flags), family, invoice and payments, audit trail, messages.
- **Actions:**

| Action | Endpoint | Behaviour |
|---|---|---|
| Approve | POST `/api/staff/bookings/<id>/approve` | Confirmed; invoice if price > 0; email with pay link or pay-later terms |
| Decline | `/decline {reason}` | Cancelled; place freed to the waiting list |
| Cancel | `/cancel {reason, outcome: none/credit/refund_card/refund_offline, amount}` | Credit note; Stripe refund or offline refund record; service email |
| Move | `/move {session_id}` | Capacity-checked on the target. Price up → supplementary invoice for the difference; price down → credit note. Attendance row moves. Audited `booking.move` with from/to. |
| Trial | `/mark-trial` | Phase 2 UI; column exists |
| Resend confirmation | `/resend` | |

- **Book on behalf** (`POST /api/staff/bookings/create`). Staff pick a family or create one, choose participants and sessions (overrides require `bookings.override` and a reason), then choose payment:
  - Record now (method, amount, voucher provider, reference).
  - Email a card payment link (Stripe).
  - Leave unpaid (delayed).
  - Missing profile levels don't block staff. The booking is flagged "⚠ profile incomplete" on the register, and the family gets "Please complete Maya's details before Monday".
- **Walk-in: book and pay on the spot** (`POST /api/staff/walkin`), one reception screen:
  1. Find or create the family: parent name, email or mobile, child name and DOB, one emergency contact, allergies, photo consent, first aid consent. Consents given on paper or verbally are recorded as `staff_paper` / `staff_verbal`.
  2. Choose today's session.
  3. Take the payment: cash, card machine, voucher or TFC.
  4. Sign the child in.
  - Everything happens in one transaction. The family gets an activation email so the parent can complete details online.

### 4.4 Waiting list

Per session, the list shows position, family, child, joined date and status (waitlisted, offered with countdown, expired). Actions: offer (with expiry hours), confirm directly, raise priority (`waitlist_priority`, e.g. for a sibling or a HAF priority; audited), remove. The activity-level view shows totals so staff can decide whether to add capacity or sessions.

### 4.5 Registers

- **Filters:** centre, activity (with "Include past and archived activities"), date (defaults to today), and "All sessions in one register", which combines every session on the date grouped by activity.
- **Session register** (`GET /api/staff/registers/session/<id>`, audited `register.view`). One row per booking, with columns:

| Column | Content |
|---|---|
| Child | Name, age, status chip, "profile incomplete" / "needs review" |
| Flags (icon plus text, never colour alone) | 🔴 allergy (anaphylaxis/pen), medical, dietary, SEND, SEMH, religious; 📷 photo: online / internal / **NO PHOTOS**; HAF; "Parent stays"; "Check with DSL before release" (`f_safeguarding`); **collection alert text**; "May go home alone"; 🔑 password set |
| Contacts | Emergency contacts (tap to reveal; audited) |
| Sign in | Button → time plus initials; "late" toggle |
| Sign out | Dialog: collected by (pick a `can_collect` contact or enter name and relationship); password check (§2.5) or "went home alone" (only if consented) or "parent stayed"; shows **"Incident to discuss: bumped knee 11:20"** if an at-collection incident exists, and **sign-out can't complete until "Parent informed" is ticked** (records `incident_participants.parent_notified_at`, `notified_via='in_person'`, `parent_ack_name`) |
| Absent | Absent / absent (notified) |
| Notes | |

  - Expanding a row shows health detail text for the session's children.
  - Party bookings show a row per party: adult and child counts, named children with their flags, and an arrived count.
- **Weekly attendance** (`GET /api/staff/registers/weekly?activity&week=2026-W43`): a children × days grid with P / A / AN / – cells and totals per day and per child.
- **Print** (`GET /api/staff/registers/session/<id>/print`, or the combined date register): server-rendered `templates/register_print.html`, A4 landscape. Includes flags, collection alerts and emergency numbers, with empty sign-in and sign-out columns. **No collection passwords and no safeguarding detail.** The page footer reads "Confidential: shred after use".
- **Actions:** `POST /api/staff/attendance/<booking>/sign-in | sign-out | absent | undo | check-password`.
- **Offline.** Phase 1 has no offline mode. The procedure is to print the day's registers each morning. A service-worker offline mode is Phase 2.

### 4.6 People and Advanced search

- **Search page** (`GET /api/staff/search?entity=&q=&filters…&page`) with an entity selector matching the MagicBooking list:

| Entity | Main filters |
|---|---|
| **Parents** | Name, email, phone, postcode, status (active / pending activation / closed), import batch, has unpaid balance, pay-later allowed, marketing opt-in |
| **Children** | Name, age range, DOB, activity or session booked, HAF status, SEND / allergy / SEMH flags (flag filters need `people.view_health`), photo consent value, level, needs review |
| **Bookings** | Status, activity, session, date range, funding, created via |
| **Trials** | Bookings with `is_trial=1` |
| **Cancelled bookings** | Date range, reason |
| **Under approval** | Pending approvals |
| **Waiting list** | Waitlisted and offered entries |
| **Staff** | Name, role, status, 2FA on/off |
| E-shop orders | Admin → Shop (search by order ref, name or email). Built in Phase 2 as a general shop, off by default, until Q4 is answered |

- **Implementation.** `search.py` has a whitelist of filters per entity. Each filter maps to a parameterised SQL fragment, so no string-built SQL. Matching is `LIKE … COLLATE NOCASE`, which is fine at this scale. Pages are 50 rows, sortable by whitelisted columns.
- **Export CSV** (`/api/staff/search/export.csv`): `csv_safe` cells; health columns only with `people.view_health`; audited with the filter spec and row count.
- **Child record** (`GET /api/staff/participants/<id>?sections=…`), tabs:
  - Overview
  - Health & needs (health permission; view audited)
  - Consents (current plus history, with source and who recorded it)
  - Contacts & GP
  - Collection (set or not, last changed, reset on documented request)
  - **Safeguarding** (not rendered without `safeguarding.view`; view audited; DSL "reviewed" stamp)
  - Bookings & attendance
  - Incidents (restricted ones DSL-only)
  - Messages
  - Audit (with `audit.view`: who viewed or changed what, and when)
  - Edits go to `POST …/update {section, fields}` and are audited with field names.
- **Parent record** (`/api/staff/accounts/<id>`): details, children, contacts, bookings, invoices, payments and balance, credit, messages, marketing preferences, staff notes, pay-later toggle. Actions: send activation, send password reset, close account (runs §3.10), SAR export, "sign out all sessions". Merging duplicate accounts is Phase 2.

### 4.7 Incidents and injuries

- **Log form** (from a register row, a child record, or Incidents → New):
  - type; children involved (each with a role); session (prefilled); time and location; description; injury details; first aid given and by whom; action taken; witnesses; severity; notifiable (Ofsted/RIDDOR) checkbox.
  - **Parent notification:**
    - **Notify now:** the `incident_notice` email and/or SMS is enqueued. It gives the child's first name, type, time, "first aid given", and "sign in to read the full report". No free-text detail goes by email or SMS.
    - **Tell parent at collection:** the default when first aid was given. The register blocks sign-out until it's done.
    - **Not required:** a reason is mandatory.
- The parent sees the report in `/account/bookings` → "Reports" and can click **"I've read this"**, which records `parent_ack_at`.
- **Safeguarding concern** is a variant of the same form:
  - It forces `restricted=1` and `parent_notify='not_required'`. This is enforced by a DB CHECK, so the UI can't bypass it.
  - It's visible only with `safeguarding.view`.
  - It raises an in-tray item for the DSL and an email with no detail ("A safeguarding concern was logged at 11:20. Sign in to view.").
  - Staff who log a concern can see their own entry for 24 hours for corrections, and then no longer.
- **List:** filters for type, date, child, status and restricted. Printable incident form. Amendments are append-only (`incident_updates`).
- Endpoints: `GET/POST /api/staff/incidents`, `GET /…/<id>`, `POST /…/<id>/amend|notify|acknowledged|close`.

### 4.8 Messaging (email and SMS)

- **Compose.**
  - Kind: **Service** or **Marketing**. The kind decides the allowed audiences and the footer.
  - Channel: Email, SMS, or Both.
  - Audience builder (JSON spec):
    - Families with bookings on session(s) or activity(s) in a date range (service).
    - Families with waitlisted children for X (service).
    - All active families (service, reserved for operational notices).
    - Marketing opt-ins (email and/or SMS), optionally filtered by children's age band or past attendance.
  - Merge fields: `{{first_name}}`, `{{child_names}}`, `{{session_date}}`, `{{activity}}`.
  - Preview shows the recipient count and a sample render. The SMS preview shows the GSM-7/UCS-2 segment count and an estimated cost.
  - Test send to self, schedule, send.
- **PECR guardrails.**
  - Marketing audiences only ever resolve to `email_opt_in=1 AND email_confirmed_at IS NOT NULL AND unsubscribed_email_at IS NULL` (and the SMS equivalent). Messages get the unsubscribe footer and `List-Unsubscribe` headers.
  - Service messages carry a UI warning ("Service messages must not promote other activities") and no marketing footer.
- **Templates** (`GET/POST /api/staff/messages/templates`): all system notifications are editable in the admin (subject plus body in the `_rich` subset plus merge fields). Structured blocks such as `{{{booking_table}}}` and `{{{invoice_table}}}` are inserted by code. "Reset to default" restores the file version.
- **Archive.** Campaign list (active / archived) → per-recipient delivery status (queued, sent, failed, suppressed) with retry. The parent record has a Messages tab.
- Endpoints: `/api/staff/messages/campaigns` (list, create), `/<id>/update|preview-audience|test-send|send|cancel|archive`, `GET /<id>/deliveries`.

### 4.9 Finance

- **Invoices:** filters for status, overdue and date range. View and print; send or resend; void (only with no payments; reason required); manual invoice (e.g. for a £250 cinema party booked by phone).
- **Delayed payments** (`GET /api/staff/finance/delayed`): confirmed bookings whose invoice balance is above 0, bucketed 0-7, 8-30 and 30+ days past due. Actions: send reminder, record payment, open family.
- **Payments:** list by method and date. **Record payment** (`POST /api/staff/finance/payments {account, amount, method, voucher_provider, reference, allocations[]}`), where allocations default to the oldest invoices first. **Daily takings** (`/takings?date`) totals cash, card machine, Stripe, vouchers, TFC and bank transfer for reconciliation.
- **Credit notes and refunds:** create a credit note against an invoice's lines with a refund mode (Stripe refund, offline refund marked paid later, or account credit).
- **Exports:** `/api/staff/finance/export/{invoices,payments,credit-notes}.csv?from&to`, a flat CSV for the treasurer or accounting software (format in Q13). Stripe payout reconciliation and aged-debt reporting are Phase 2.

### 4.10 Reports and attendance dashboard

`GET /api/staff/reports/attendance?period=year|quarter|month|week|day&date=YYYY-MM-DD&centre&category`:

- **Period selector:** Year (with a year basis setting of calendar, financial year from April, or academic year from September; Q14), Quarter, Month, ISO Week (Mon-Sun), Day. There are previous and next arrows.
- **KPI tiles** (`.statgrid`): sessions run, attendances, unique children, HAF attendances, SEND attendances, no-show rate, and utilisation (attended ÷ capacity).
- **Monthly attendance chart:** inline SVG stacked bars by `report_group` for the 12 months containing the period. Colours are navy, honey, sage-d, orange-solid, muted and navy-d, and each series is also identified by an adjacent legend with direct labels. Each bar has a `<title>`. The chart comes with a `<table class="sr-only">`.
- **Daily breakdown:** a table with a row per day and columns per session category (HAF · Holiday club · Saturday club · Home Ed · Baby & toddler · Events · Total). Clicking a cell drills into that day's registers. Exports as CSV.
- **Counting rule.** An attendance counts if `attendance.status='present'`, and a party counts `arrived_count`. The query groups on `attendance.snap_category`, so reports survive anonymisation.
- **Other Phase 1 reports:** capacity utilisation by activity and session; a HAF report (per day: HAF attendances, SEND count, age bands; CSV in the shape BCP needs, confirmed by Q7); bookings summary; finance summary (invoiced, paid, outstanding by month).
- **History.** This is a fresh start, so the dashboard shows data from go-live. Year-on-year comparison needs a one-off import of MagicBooking attendance totals (aggregates only) into a `historic_attendance(date, report_group, count)` table. That's Phase 2 and depends on Q15.

### 4.11 Staff and roles; audit

- **Staff list:** name, roles, 2FA, last login, status. **Invite** (email plus roles) sends a token email; the invitee sets a password and enrols TOTP where their role requires it.
- **TOTP enrolment:** shows the base32 secret in groups of four and an `otpauth://` link (which opens an authenticator app on mobile), then confirms with a code. The QR image is Phase 2 (it needs a vendored JS encoder). Ten recovery codes are shown once.
- **Actions:** edit roles, disable, reset 2FA, sign out all sessions. All audited.
- **Audit viewer** (`audit.view`): filters for actor, action, entity, child, and date. Export.

### 4.12 Booking settings (`settings` table; `GET/POST /api/staff/settings/booking`)

- **Policies:** hold minutes (35), parent cancellation cutoff hours, refund days, refund method preference, waitlist offer hours, pay later for everyone (off), reminder timings, go-home-alone minimum age, bank holiday list, session reminder SMS on/off.
- **Invoices:** issuer name and address, charity and Ofsted numbers, bank name, sort code and account number for transfers, payment terms (days), footer text, number prefixes.
- **Security:** 2FA-required roles, staff idle timeout.
- **Retention periods:** read-only display plus owner edit (audited).
- **DSL notification addresses.**
- **Consent types:** editing wording creates a **new version**. A checkbox decides whether families must reconfirm.

**Integrations** (`GET /api/staff/settings/integrations`) shows status only: "Stripe: live mode ✓ / webhook secret ✓", "SMTP: smtp.example ✓", "Twilio: ✓ sender HoneycombeH". Buttons: Send test email, Send test SMS. **No secrets are in the DB or in `content.json`.**

### 4.13 Family import (MagicBooking export)

1. **Upload** (`POST /api/staff/import/upload`, multipart, ≤5 MB, `.csv` only, `import.run`). The raw file goes to `data/private/imports/<batch>.csv` (not web-served) and is deleted 30 days after commit. The admin page is the secure transfer route: the file doesn't need to be emailed, and staff delete local copies afterwards.
2. **Mode:** "One row per child (parent columns repeated)", grouped by parent email; or "Two files: parents plus children" joined by a family key column.
3. **Map columns.** Headers are auto-suggested by fuzzy name match to formspec fields plus `legacy_ref`. Unmapped columns are ignored. Dates are accepted in DD/MM/YYYY, D/M/YYYY, YYYY-MM-DD and DD-MM-YY (two-digit years are resolved by age plausibility). Phones are normalised to E.164.
4. **Dry run** returns counts (families, children, contacts), warnings (missing required fields for full level, over-18s, children with no DOB), errors (invalid email, unparsable DOB), duplicates (email already exists: skip or merge-children-only), and a downloadable `errors.csv`.
5. **Commit** runs in one transaction. Created records are `accounts(status='pending_activation', source='import')` and `participants(needs_review=1)`. Imported consents are stored with `source='import'` for evidence but don't count as current, so reconfirmation is required before booking. Every row keeps `import_batch_id`.
6. **Send activation emails** in throttled batches (settings `MAIL_RATE_PER_MIN`), tracking sent, activated and bounced. Automatic reminders go on day 7 and day 21.
7. **Rollback** is allowed only while no imported account has logged in or booked. It deletes by `import_batch_id`.

**The login problem.** Passwords can't be migrated (MagicBooking only holds hashes, and even those aren't exported). The answer:
- Imported families get an "Activate your account" email with a one-time link and the DOB challenge.
- The login page has "Coming from our old booking system? Activate your account" (enter your email to get a new link).
- "Forgot password" also works for these accounts.
- Staff can resend from the parent record.
- **Start the activation campaign 3-4 weeks before the first bookings open.** That spreads the load, gets consents reconfirmed early, and catches bad email addresses while there's still time to fix them.

---

## 5. Integration with the public site

### 5.1 Book Now and booking links

- `partials/header.html:23` becomes `<a class="btn btn--honey btn--sm" data-booking href="/book">Book Now</a>` with no `target`/`rel`. Beside it add `<a class="nav__account" href="/account">My account</a>`. It is a static link because public pages don't know the session; `/account` redirects to `/login` when signed out.
- `partials/footer.html:21` also drops `target="_blank"`. In the legal nav (`:55-61`) add "My account" and "Email preferences" (`/account/privacy`).
- `public/js/main.js:31` becomes: `$$("[data-booking]").forEach(a => { const u = S.bookingUrl || "/book"; a.href = u; if (u.startsWith("/")) { a.removeAttribute("target"); a.removeAttribute("rel"); } });`.
- **Cutover switch.** `settings.bookingUrl` (Settings → Links, `admin.js:785`, relabelled "Book Now goes to") stays the single switch. Before launch it points at MagicBooking; at launch staff change it to `/book`. No deploy is needed, and it can be reverted.
- The `target="_blank"` attributes at `public/index.html:22,141`, `public/whats-on.html:27` and `public/holiday-club.html:64` are removed; the JS above handles old markup anyway.

### 5.2 What's On event to activity link

- The content model gains an optional `event.activity` (activity slug), stored in `content.json`. It is non-sensitive display data only.
- In `admin.js:229` (moved to `website/events.js`), the "“Book now” goes to the booking portal" checkbox becomes a "Book button" select: `Enquire (contact page)` · `Booking portal (Settings link)` · `Activity: [published and scheduled activities from /api/staff/activities?status=published,scheduled]`.
- Also fix `admin.js:238`: only derive `ev.id` from the title when the event is new and has no id yet, and check uniqueness. Otherwise renaming an event breaks `/whats-on/<id>` links.
- `public/js/main.js:217-219` becomes:
  - If `ev.activity`: "Book now" → `/book/${ev.activity}` (same tab).
  - Else if `ev.bookable`: `S.bookingUrl`.
  - Else "Enquire".
  - Optional progressive enhancement: `fetch('/api/book/activity/'+slug+'/summary')` → "From £30 · places available" under the button.
- `/whats-on/<id>` stays indexable. `/book*` is not indexed, so What's On pages remain the SEO surface.

### 5.3 Removing membership copy

- **Static pages (in the PR):**
  - `public/whats-on.html:18,28`
  - `public/holiday-club.html:29-30` (rewrite the Membership section to "Booking & prices"), `:36` ("include full membership"), `:47` ("Membership fees are non-refundable" → link to the cancellation policy), `:51`, `:62` (row → "Price: £30/day · HAF free")
  - `public/index.html:27` (hero meta)
  - `public/arts-award.html:5,36-37,46,49,58`, pending Q9
  - `public/policies.html:40` ("family and member data")
- **`content.json` (live disk).** A content migration `content:0001_booking_inhouse`, run at boot and recorded in `schema_migrations`, **patches a field only if it still equals the old seed text exactly**. If staff have edited the field, it leaves it alone and raises an in-tray item "Please review membership wording in <event>". Targets:
  - `events[*].description` and `price` at `data/content.json:167,181,237,242,251`
  - `pages.getInvolved.metaDescription` (`:93`)
  - the `login` section (`:148-156`): eyebrow "Already booked with us?", heading "Your account", body updated, `buttonLink:"custom"`, `buttonUrl:"/account"`
  - `settings.bookingUrl` is **not** changed; that's the cutover switch.
  - `settings.smtp` is removed once the SMTP env vars are set (§7.1).
- `seed/content.json` gets the same edits in the PR.
- `README.md:24,37,96`: remove the default password, document the env vars, booking system, backups and runbook.

### 5.4 Privacy notice rewrite (`public/privacy.html`, full rewrite)

Outline:
1. Who we are (controller: Honeycombe Arts Hub, charity 1127371), data protection lead and contact.
2. What we collect, by journey: account, children's profiles (health, SEND, SEMH, dietary, religious requirements, safeguarding information), emergency contacts and GP, consents, bookings and attendance, payments, incident and injury records, messages, guest bookings, newsletter, website security logs.
3. **Lawful bases table:**

| Purpose | Basis |
|---|---|
| Bookings and account | Contract |
| Safety and health information | Art 6(1)(b)/(c) plus Art 9(2)(g) with DPA 2018 Sch 1 para 18 (safeguarding children), backed by an **Appropriate Policy Document** |
| Registers and accident records | Legal obligation (Ofsted Childcare Register) |
| Photos, funder sharing, marketing | Consent |
| Audit and security | Legitimate interests |
| HAF | BCP Council's public task, under the HAF data-sharing arrangement |

4. Who sees what inside the charity (role-based; safeguarding information DSL-only).
5. **Processors:** Render (hosting, region stated; §0 finding 1), Stripe, the email provider, Twilio, and Google Maps on the contact page. Google Fonts is dropped by self-hosting (§1.7).
6. **Sharing:** BCP Council for HAF (what and why); named funders only with consent; anonymised statistics otherwise. This replaces `privacy.html:38`'s "never information that identifies your family", which becomes conditional on consent.
7. International transfers and their safeguards.
8. **Retention table** (§3.10).
9. **Your rights,** including the **self-service** routes: download my data, delete account (with the exceptions explained), unsubscribe, preferences.
10. Children's data and when young people can exercise rights themselves.
11. **Cookies:** strictly necessary session cookies only (`__Host-hah_acct`, `__Host-hah_staff`) and `sessionStorage hah-ann-hide`. No banner is needed under PECR's strictly-necessary exemption.
12. Complaints to the ICO; last-reviewed date.

Also add one line to `public/safeguarding.html` about how incidents and concerns are recorded and restricted.

### 5.5 Indexing

- The `X-Robots-Tag: noindex, nofollow` header is set by the page-route option for `/book*`, `/register*`, `/login`, `/activate`, `/reset-password`, `/verify-email`, `/forgot-password`, `/account*`, `/unsubscribe*`, `/guest/*` and `/admin`. Those pages also get the meta tag.
- `robots.txt`: add `Disallow: /account`. **Don't** disallow `/book`, or crawlers can't see its noindex.
- `sitemap.xml` is unchanged.
- Add the skip link to `public/404.html:9` while touching partials.

---

## 6. Security hardening required before any special-category data is stored (Phase 0 gate)

| # | Gap (map reference) | Fix |
|---|---|---|
| 1 | Shared password, no identity (`server.py:65-94,720-728`), default `honeycomb2026` (`:38`, README:24) | Per-staff accounts, roles, audit, TOTP (§1.5, §4.11). Bootstrap only from env. Delete `DEFAULT_PASSWORD`. |
| 2 | Silent password reset on a corrupt `auth.json` (`:82`) | Removed with #1. The DB replaces the file. |
| 3 | Cookie lacks `Secure`; no idle timeout or revocation (`:726`) | `__Host-` cookies, Secure, SameSite (Strict for staff, Lax for parents), hashed tokens, idle and absolute expiry, revoke on password or 2FA change, sign out everywhere. |
| 4 | CSRF relies on SameSite plus an Origin check that passes when Origin is missing (`:541-546`) | Missing Origin → 403 on cookie-auth POSTs. Origin must equal `SITE_ORIGIN`. JSON content-type. Synchronizer `X-CSRF-Token`. |
| 5 | Brute force: a 0.4 s sleep that parallel requests bypass (`:722`) | `ratelimit.py` buckets per IP (from XFF) and per email; lockouts on the account rows; in-tray alerts. |
| 6 | Unbounded bodies; a negative Content-Length hangs a thread (`:517-521`) | Per-route `body_limit` (default 64 KB; import 5 MB; CMS upload 15 MB). Reject non-integer or negative lengths and chunked encoding. |
| 7 | Unbounded threads, no socket timeout (`:825`, `Handler.timeout=None`) | `Handler.timeout = 30`. `BoundedThreadingHTTPServer` with a semaphore of 64 (acquired in `process_request`, released in `process_request_thread`'s `finally`), 503 when saturated. |
| 8 | Only `nosniff` (`:507`) | `_send` adds, on every response: `Content-Security-Policy: default-src 'self'; script-src 'self' 'nonce-{n}'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; frame-src https://www.google.com; connect-src 'self'; form-action 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'none'`. Plus `Referrer-Policy: same-origin`, `Permissions-Policy: camera=(), microphone=(), geolocation=()`, `Cross-Origin-Opener-Policy: same-origin`, and `Strict-Transport-Security: max-age=31536000; includeSubDomains` when `X-Forwarded-Proto=https`. `render_page` adds the nonce to the `window.HAH` script (`:492-494`). JSON-LD is a data block and isn't affected. `'unsafe-inline'` styles are accepted for existing inline `style=` attributes. |
| 9 | API JSON has no Cache-Control (`:561-568`) | `Response.json` always sends `no-store`. Pages already do (`:608-609`). |
| 10 | SVG uploads are a stored-XSS vector; extension-only checks (`:769`, `:584-591`) | Drop `.svg` from new uploads. Magic-byte sniffing for jpg, png, gif, webp and pdf. Existing `/uploads/*.svg` served with `Content-Security-Policy: sandbox; default-src 'none'`. PDFs get `Content-Disposition: inline; filename=…`. Private files are never under `/uploads` (Phase 2 `private_files` route with auth and `attachment`). |
| 11 | Tokens and query strings in the logs (`:548-549`) | `log_message` logs `method path-without-query status rid` and redacts `/unsubscribe/…` and `/u/…`. Bodies are never logged. |
| 12 | SMTP doesn't verify TLS; secrets live in `content.json` and are returned to the browser (`:131-132`, `:565`, `admin.js:800-811`) | `ssl.create_default_context()`, 465/587 support, secrets in env (§7.1). The overview returns `smtp:{host,port,user,notifyTo,passwordSet}` only. |
| 13 | CSV formula injection (`:574-579`) | `html.csv_safe()`: cells starting with `= + - @ \t \r` get a `'` prefix. Use the `csv` module everywhere. |
| 14 | Corrupt JSON treated as empty and overwritten; RMW races (`:49-61`) | Handled by `storage.py` (§1.2). messages and subscribers move to SQLite in Phase 1. |
| 15 | `DATA` not configurable; Python not pinned; no tests or CI | `HAH_DATA_DIR`; `.python-version` 3.12 plus `PYTHON_VERSION`; tests and CI (§8); Render `autoDeploy: false`. |
| 16 | No backups beyond one content generation | Nightly SQLite online backup, rotation, and a restore drill (§7.4). Verify Render's daily disk snapshots. |
| 17 | Hosting region and transfers (§0) | Recreate the service in `frankfurt` (PR 0.8), or document the IDTA plus transfer risk assessment. Record Render disk encryption at rest (verify) in the DPIA. |
| 18 | Admin escaping gaps (`admin.js:6-7` no `'`, `763-766`, `263`) | New `core/dom.js` and `forms.js` (§1.6). |
| 19 | Field-level encryption | **Decision: not implemented.** The stdlib has no AES, and hand-rolled crypto is a bigger risk. Mitigations: disk encryption at rest (Render), role-based access plus audit, peppered hashes for the collection password, no special-category data in email or SMS bodies, exports gated and audited. Recorded in the DPIA. |
| 20 | Governance | Before Phase 1 launch: DPIA, Record of Processing Activities, Appropriate Policy Document, processor DPAs (Render, Stripe, email provider, Twilio), updated retention policy, staff data-protection briefing. |

---

## 7. Email, SMS and the background worker

### 7.1 Email (`hah/mail.py`)

- **Config (env):** `SMTP_HOST`, `SMTP_PORT` (465 → `SMTP_SSL(context=ctx)`, otherwise `SMTP` plus `starttls(context=ctx)`), `SMTP_USER`, `SMTP_PASSWORD`, `MAIL_FROM` ("Honeycombe Arts Hub <bookings@honeycombeartshub.org.uk>"), `MAIL_REPLY_TO` (info@), `MAIL_RATE_PER_MIN` (60).
- Backward compatibility: if the env vars are missing, fall back to `content.json settings.smtp` and show an admin banner "Move SMTP settings to Render environment". Once the env is present, the Settings SMTP card is read-only status and a content migration strips `smtp`.
- **`send(to, subject, text, html=None, reply_to=None, headers=None)`:**
  - `EmailMessage` with `set_content(text)` then `add_alternative(html, subtype="html")`.
  - `Message-ID` from `make_msgid(domain=…)`; `Date` from `formatdate(localtime=False)`.
  - List-Unsubscribe headers for marketing.
  - 20 s timeout.
  - The worker reuses one connection per batch.
- **Templates.** `hah/templates/email/base.html` is a table layout with inline CSS in brand navy and honey, with a logo URL. Each `{key}.txt|.html` pair can be overridden by `message_templates`. `templating.render` escapes `{{x}}` and inserts `{{{x}}}` raw (used only for blocks built by code).
- **Keys:** `verify_email`, `register_existing`, `activate_account`, `activation_reminder`, `password_reset`, `email_changed`, `staff_invite`, `booking_confirmed` (with `{{{invoice_table}}}`), `booking_pending_approval`, `booking_approved`, `booking_declined`, `booking_cancelled` (with credit note), `waitlist_joined`, `waitlist_offer`, `offer_expired`, `invoice_issued`, `payment_receipt`, `payment_reminder`, `session_reminder`, `session_changed`, `session_cancelled`, `incident_notice`, `guest_booking_confirmed`, `newsletter_confirm`, `sar_ready`, `account_deletion_scheduled`, `dsl_alert` (no detail), `staff_digest`.
- **Deliverability** (charity action): SPF, DKIM and DMARC for the sending domain, and a provider with an SMTP relay and suitable volume limits (Q12).

### 7.2 SMS (`hah/sms.py`)

```python
class SmsProvider(Protocol):
    def send(self, to_e164: str, body: str) -> str: ...   # returns provider message id; raises SmsError(permanent: bool)
```

- **TwilioProvider:** `POST https://api.twilio.com/2010-04-01/Accounts/{SID}/Messages.json`, form-encoded `To`, `From` (alphanumeric sender ID, max 11 characters, e.g. `HoneycombeH`, one-way in the UK) or `MessagingServiceSid`, and `Body`.
  - `Authorization: Basic base64(SID:TOKEN)`, verified TLS, 15 s timeout.
  - 429 or 5xx is retryable. 4xx is permanent: 21211 invalid number, 21610 opted out → `suppressed`.
- **Other providers:** `LogProvider` (dev and tests) and `DisabledProvider`, chosen by `SMS_PROVIDER=twilio|log|disabled`.
- **UK normalisation:** `07…` → `+447…`; mobiles only.
- **Bodies:** service SMS ends "Replies aren't read. Call 07932 772905". Marketing SMS appends the opt-out link (§3.9).
- **Launch scope:** waitlist offers, session changes and cancellations, optional day-before reminders, staff service messages to session attendees, and marketing to opted-in contacts. Twilio delivery status callbacks are Phase 2.

### 7.3 Outbox and worker (`hah/outbox.py`, `hah/worker.py`)

- **Enqueue.** Domain code calls `outbox.enqueue_email(c, template_key, to, context, account_id=…, kind='service')` **inside its transaction**. The template is rendered at enqueue time and stored in `message_deliveries` (which doubles as the archive). A rollback therefore never sends, and a commit always eventually sends.
- **Worker thread.** A daemon thread started by `app.main()`, guarded by `fcntl.flock(data/worker.lock)` so only one runs. It loops every 5 s over `scheduled_jobs` and stops cleanly on SIGTERM through an Event. Render stops the old instance before starting the new one, because the disk is single-attach.

| Job | Every | Work |
|---|---|---|
| dispatch_outbox | 5 s | Claim ≤20 `queued` rows with `next_attempt_at ≤ now` (set `sending`, `locked_until=+5 min`); send; `sent` or retry with backoff 1 m, 5 m, 30 m, 2 h, 12 h (max 6), then `failed` plus an in-tray item. Throttle to `MAIL_RATE_PER_MIN`. Suppress marketing to unsubscribed contacts at send time as well. |
| release_holds | 1 min | `pending_payment` past `hold_expires_at`: call Stripe expire (or read the status) → expire bookings and the checkout → freed places trigger waitlist processing. |
| waitlist | 1 min, and on the `capacity_freed` event | Expire offers; make new offers in auto mode. |
| publish | 1 min | Activities with `status='scheduled' AND publish_at ≤ now` → published. Scheduled campaigns → expand the audience into deliveries. |
| reminders | hourly | Session reminders (24 h / 48 h per settings), payment reminders, activation reminders (day 7, day 21), offer-expiring nudges. |
| nightly (02:30 Europe/London) | daily | Backup (§7.4), retention purge (§3.10), `PRAGMA optimize`, `wal_checkpoint(TRUNCATE)`, clean expired sessions and tokens, staff digest email (open in-tray counts only, no personal data). |
| weekly | Sunday | `PRAGMA integrity_check`; disk-usage check (in-tray alert above 70%). |

- **Heartbeat.** `scheduled_jobs.last_finished_at` feeds `/healthz`, which returns 503 if the heartbeat is more than 5 minutes old. Render's health check then restarts the service.

### 7.4 Backups

- **Nightly:** `sqlite3.connect(tmp).backup()` from the live DB → gzip → `data/backups/booking-YYYYMMDD.db.gz`. Keep 14 daily and 4 weekly copies (≤35 days). Log the size.
- **Disk.** Increase the Render disk from 1 GB to 5 GB (`render.yaml:14`) for headroom.
- **Snapshots.** Render's daily disk snapshots cover disk loss (verify how long they're retained).
- **Restore drill** before launch (§8.3): restore a backup into a staging service and run the verification suite.
- Off-site encrypted copies (e.g. `openssl enc` subprocess plus S3-compatible upload signed with SigV4 through `hmac`/`urllib`) are Phase 2.

---

## 8. Testing strategy and verification

### 8.1 Test harness (stdlib `unittest`)

- **`tests/support.py`:**
  - Creates a temp directory, sets `HAH_DATA_DIR`, `HAH_DEV=1`, `HAH_SECRET_KEY`, `HAH_PEPPER`, `SMS_PROVIDER=log`, `MAIL_BACKEND=memory` (captures messages in a list), and `STRIPE_API_BASE=http://127.0.0.1:<fake>`.
  - Runs migrations.
  - Starts `BoundedThreadingHTTPServer(("127.0.0.1", 0), Handler)` in a thread (port 0), with the worker in manual-tick mode (`worker.tick()` is called by tests).
  - `Client` class: `http.client` with a cookie jar and automatic `X-CSRF-Token`.
- **`tests/fake_stripe.py`:** a tiny HTTP server implementing `POST /v1/checkout/sessions`, `/expire`, `GET` retrieve and `POST /v1/refunds`, plus a helper that signs webhook payloads with the test secret.
- **Suites:**
  - `test_security.py`
    - Password hash and rehash.
    - TOTP against RFC 6238 vectors (SHA1 secret `12345678901234567890`, T=59 → `94287082` in 8 digits).
    - Stripe signature (valid, tampered, stale, multiple `v1`).
    - CSRF (no Origin → 403; wrong token → 403).
    - Rate limits.
    - Headers present on HTML, JSON and static responses.
    - Negative, huge and chunked bodies.
    - Log redaction (captured stderr has no query strings or tokens).
    - SVG upload rejected.
    - `csv_safe`.
  - `test_routes_authz.py`: **enumerates `ROUTES`** and asserts every `/api/staff/*` route has `auth="staff"` and a `perm`, and every mutation has `csrf=True` unless it is on the explicit allow-list (webhook, unsubscribe). For each role, a sample of routes returns 403 as the matrix says.
  - `test_no_public_leak.py`: seed a child with a distinctive name and allergy; assert `/api/content`, every `PRETTY` page and `/whats-on/<id>` never contain them.
  - `test_eligibility.py`: month boundaries, leap-day DOB, school-year cohorts, first-session basis.
  - `test_booking_capacity.py`: **50 threads racing for the last 3 places → exactly 3 non-waitlisted**; the double-book index; waitlist priority over new bookers; offer, accept and expire; approval reserve and decline.
  - `test_checkout.py`: full Stripe flow through the fake (webhook idempotency; late payment after expiry → refund path; return-page fallback).
  - `test_money.py`: invoice numbering is gapless under concurrency; credit notes, refunds and balances; offline payments with allocation; pay-later due dates.
  - `test_registers.py`: sign-in/out, collection password match or mismatch and the attempt cap, go-home-alone gating, incident blocking sign-out.
  - `test_incidents.py`: the safeguarding-concern CHECK; restricted visibility; append-only trigger.
  - `test_messaging.py`: a marketing audience excludes non-opted and unsubscribed contacts; RFC 8058 POST; a rollback enqueues nothing; retry and backoff.
  - `test_import.py`: date formats, grouping, duplicates, dry run matches commit, rollback guard, activation plus DOB challenge.
  - `test_gdpr.py`: SAR contents (restricted items excluded); deletion keeps invoices with anonymised child names, keeps held incidents, erases health.
  - `test_migrations.py`: from scratch; checksum-mismatch refusal; `content:0001` only patches untouched text.
  - `test_storage.py`: a corrupt JSON file is not overwritten; `update_json` concurrency.
- **JS:** `node --check` on every file under `public/**/*.js` in CI (a CI tool only, not a runtime dependency). UI flows are covered by the manual checklist in §8.3.

### 8.2 CI (`.github/workflows/ci.yml`) and gated deploy

- **`ci.yml`:** on `pull_request` and `push`. `actions/setup-python@v5` with 3.12, then `python -m compileall -q hah server.py`, then `python -m unittest discover -s tests -v`, then `find public -name '*.js' -print0 | xargs -0 -n1 node --check`.
- **`deploy.yml`:** add a `test` job (the same steps) and make `deploy` `needs: test`. Set `autoDeploy: false` in `render.yaml` and in the Render dashboard, so the hook is the only path to production.
- **Staging service.** A second Render service using Stripe test mode, the Twilio test or log provider, and a mail sink. Required from Phase 1, PR 1.2 onwards.

### 8.3 Verification checklist (run on staging before launch; repeat the key items after each release)

- [ ] `curl -sI` on `/`, `/book`, `/api/account/me`, `/uploads/x.svg`: CSP, HSTS, `frame-ancestors`, `no-store` on the API, `X-Robots-Tag` on portal pages, and the correct cookie attributes.
- [ ] Logged `X-Forwarded-For` shape confirmed and `TRUSTED_PROXY_HOPS` set; the rate limiter keys on real client IPs.
- [ ] Staff: invite → set password → TOTP enrol → login needs a code; five bad passwords lock the account; a session staff member gets 403 on `/api/staff/participants/<id>?sections=safeguarding`; each health view produces an audit row.
- [ ] Register a family (full), a baby class (short) and an adult; email verification needed; the booking page shows only age-eligible sessions; a level gap prompts only the missing sections.
- [ ] Two browsers booking the last place: one confirmed, one offered the waiting list. Cancel → the offer goes out by email and SMS → accept → pay.
- [ ] Stripe test card: success, decline, abandoned session (place released after expiry); `stripe trigger` replays are idempotent; a refund shows up on the credit note.
- [ ] Pay later: the invoice shows as unpaid under delayed payments; recording a voucher payment clears it.
- [ ] Guest quick booking (free and paid); the manage link cancels.
- [ ] Register: sign-in and sign-out with password match and mismatch; the incident "discuss at pick-up" blocks sign-out; printed register contains no passwords or safeguarding text.
- [ ] Incident with "notify now": parent email contains no detail; parent acknowledges in the portal.
- [ ] Marketing campaign excludes non-opted contacts; unsubscribe link and one-click work without login; the token doesn't appear in Render logs.
- [ ] Import a sample MagicBooking export: dry run, commit, activation email, DOB challenge, consent reconfirmation, booking.
- [ ] SAR download contents; delete account → after the cooling-off period: invoices keep bill-to names, child lines are anonymised, a held incident child is retained minimally, marketing is gone.
- [ ] Page source of `/whats-on` contains no personal data (the automated test, plus a manual spot check).
- [ ] Accessibility: keyboard-only completion of `/register/family` and `/book` → `/book/review`; the error summary is announced; Lighthouse accessibility ≥ 95; 200% zoom; contrast of the status chips.
- [ ] Load: a script simulating 200 parents booking a summer week within 2 minutes; p95 below 1 s; no overbooking.
- [ ] Backup restore drill: restore last night's backup into staging and rerun the smoke checks.
- [ ] Deploy downtime observed (~30-60 s); Stripe webhook retries recover.

---

## 9. Phased delivery

Sizing: S ≤ 2 days, M 3-4 days, L 5-8 days (one experienced engineer). Rough totals: Phase 0 about 22 days, Phase 1 about 95 days, which is roughly 5-6 months for one engineer or about 3 months for two working in parallel after PR 1.3.

**Launch window.** Open the new system for a low-volume period (e.g. February half-term 2027 bookings), not the summer club rush. MagicBooking stays live to deliver bookings it already holds.

### Phase 0: foundations and hardening (nothing sensitive is stored until this is done)

| PR | Content | Size | Depends on |
|---|---|---|---|
| 0.1 | Tests harness (`tests/support.py`), `ci.yml`, deploy gated on tests, `HAH_DATA_DIR`, `.python-version`, `render.yaml` (`autoDeploy:false`, `PYTHON_VERSION`, disk 5 GB, `healthCheckPath:/healthz` later), `.gitignore` (`data/booking.db*`, `data/backups/`, `data/private/`, `data/worker.lock`), move `hah assets ` out | S | – |
| 0.2 | Package split: `hah/config|storage|html|site|cms|http|app`; route registry; `server.py` shim; behaviour-neutral (existing pages and CMS pass smoke tests) | M | 0.1 |
| 0.3 | Hardening: security headers and CSP nonce, body limits, timeouts, bounded threads, log redaction, XFF IP, rate limiter, upload sniffing, no SVG, `csv_safe`, corruption-safe storage, `update_json` | M | 0.2 |
| 0.4 | `db.py`, migrations runner, `scheduled_jobs`, worker skeleton with flock and SIGTERM, nightly backup, `/healthz` | M | 0.2 |
| 0.5 | Staff accounts, roles, permissions, sessions, CSRF, TOTP, invites, lockout, audit log, bootstrap owner from env; retire `auth.json`/`sessions.json`/`DEFAULT_PASSWORD` | L | 0.3, 0.4 |
| 0.6 | Admin ES-module split: core modules, router, CMS tabs moved, email+password+TOTP login, Staff area, audit viewer; `admin.js` deleted | M-L | 0.5 |
| 0.7 | `mail.py` (verified TLS, env secrets, HTML+text), `templating.py`, outbox plus dispatcher, `sms.py` (Twilio, log), Settings → Integrations status and test buttons; contact form via outbox | M | 0.4 |
| 0.8 | Infra: recreate the Render service in Frankfurt (or sign off the US transfer), move env secrets, verify disk encryption and snapshots, staging service | S | 0.1 |

### Phase 1: launch (everything needed to switch off MagicBooking)

| PR | Content | Size | Why it's needed at launch |
|---|---|---|---|
| 1.1 | Schema for families: accounts, participants, health, safeguarding, contacts, GP, consent types and consents, marketing preferences, guest contacts; `validate.py`; `formspec.py` | M | Everything else depends on it |
| 1.2 | Parent auth (register, verify, login, forgot, reset, activate plus DOB challenge), portal partials, `portal.css`, self-hosted fonts, `formkit.js` | L | "Simple login with email and password straight to booking portal" |
| 1.3 | Registration journeys: chooser, family (full), little ones (short), adult; add child; complete missing sections; collection password; versioned consents incl. merged photo, funder, marketing | L | Email requirements a/c/d |
| 1.4 | **Family import** (mapping, dry run, commit, rollback, throttled activation sends, reminders) | L | Must run 3-4 weeks before bookings open |
| 1.5 | Activities and sessions admin: CRUD, statuses (draft, scheduled, published, unpublished, archived, past), generator, duplicate for next term, export, categories and centres, seed the five activities | L | Nothing can be booked without it |
| 1.6 | Book activities page, catalogue API, eligibility, basket, review and quote | L | Mockup 2 |
| 1.7 | Booking engine: confirm transaction, capacity, approval, waiting list and offers, parent cancel with policy, My bookings; attendance rows | L | Approve and cancel bookings; waiting list |
| 1.8 | Invoices and credit notes: numbering, HTML email, account pages with print CSS, "Pay now" | M | "Automatic invoices sent to parents when a booking is made" |
| 1.9 | Stripe Checkout, webhook, refunds, fake Stripe, late-payment handling | L | Online payment (decided) |
| 1.10 | Staff bookings: approve, decline, cancel, move, book on behalf, **walk-in pay on the spot**, offline payments, delayed payments, daily takings, finance exports | L | "Pay and book on the spot"; finance records; delayed payments |
| 1.11 | Guest one-off booking (free and paid), guest manage link, signed-in party bookings | M | Email requirement b |
| 1.12 | Registers: filters, include past, all-in-one, weekly view, sign-in/out, collection check, flags, print | L | Legally required daily records; carried over from MagicBooking |
| 1.13 | Incidents and injuries, parent notification (now or at collection), DSL-restricted concerns | M | New requirement; Ofsted duty to inform parents the same day |
| 1.14 | People / advanced search (all entities except e-shop), child and parent records with permission-gated tabs and view audit, CSV export | L | Staff can't operate without finding families; carried over |
| 1.15 | In-tray (incl. contact messages migrated from `messages.json`) | M | Carried over; where approvals and HAF checks surface |
| 1.16 | Messaging: campaigns, email and SMS, service vs marketing, editable templates, archive; newsletter moved to `marketing_preferences` with double opt-in; unsubscribe and preferences | L | Carried over; unsubscribe is a GDPR/PECR constraint |
| 1.17 | GDPR: SAR export, delete account, retention jobs and holds, privacy notice rewrite, `gdpr_requests` | M-L | Constraints list ("delete their accounts", GDPR) |
| 1.18 | Attendance dashboard and reports (utilisation, HAF, bookings, finance summary) | M-L | See note 1 |
| 1.19 | Public site integration: header, footer and main.js booking links, event → activity link, `admin.js:238` fix, membership copy and content migration, noindex, robots | M | "Back end of the website through the Book Now link"; "No membership fee" |
| 1.20 | Launch readiness: DPIA, ROPA, APD sign-off, load test, restore drill, verification checklist, runbook (cutover = flip `bookingUrl`), staff training notes | M | Go/no-go gate |

**Note 1: when the dashboard ships.** The client says the reporting must be "the same level", so it belongs in Phase 1. However, with a fresh start there is no data on day one, so PR 1.18 is the one Phase 1 item that may ship up to about 4 weeks after cutover without blocking it. Registers (1.12), the source of that data, must be live on day one.

**Recommended order for two engineers:**
- Engineer A: 1.1 → 1.2 → 1.3 → 1.4 → 1.17 → 1.19.
- Engineer B: 1.5 → 1.6 → 1.7 → 1.8 → 1.9 → 1.10 → 1.11.
- Then 1.12 → 1.13 → 1.14 → 1.15 → 1.16 → 1.18, split between them.
- 1.20 last.

### Phase 2 (after MagicBooking is switched off)

- **SEND support intake** (mockup 6): a `send_intakes` table and a private file store (`private_files` served through an authenticated `Content-Disposition: attachment` route, 10 MB, PDF/DOCX/JPG/PNG, magic-byte checks) for EHCP and plan uploads; SEND lead workflow (call, visit, agree plan → "support plan agreed" flag on the child); `send.view`. **M-L**
- Trials: UI, trial pricing, conversion report. **S-M**
- E-shop, if Q4 says yes. **L**
- Advanced search extras: saved searches, bulk actions (message, export, add to waiting list), duplicate-account merge. **M**
- Finance: Stripe payout reconciliation (`/v1/balance_transactions`), aged debt, accounting-software export format, sibling or multi-day discounts in `pricing.py` (per Q3). **M**
- Historic MagicBooking attendance aggregates for year-on-year charts. **S**
- Additional carer logins per family. **M**
- Offline-capable register (service worker plus a sync queue). **L**
- Twilio status callbacks and inbound STOP; QR codes for 2FA enrolment; body map for injuries; off-site encrypted backups. **S each**

---

## 10. Open questions for the charity

Only genuinely unresolved business decisions are listed. Where there is a sensible default, it's stated so building isn't blocked.

1. **Cancellation and refund policy.** Notice period for parents (default 48 h cutoff). Full refund vs credit (default: card refund ≥7 days before, credit between 7 days and the cutoff, nothing after). Any admin fee. Illness exceptions. HAF no-show rules (e.g. repeated no-shows lose priority).
2. **Pay later and delayed payments.** Who may pay later: all families, flagged families, or voucher/TFC payers only (default: flagged families and voucher/TFC payers). Payment due dates. Should unpaid bookings be auto-cancelled N days before the session (default: no, staff decide)? Which voucher providers stay accepted (the site lists Government Childcare Choices, Computershare, Edenred, Kiddivouchers, Sodexo/Pluxee, Care 4)?
3. **Pricing.** Sibling, full-week or early-bird discounts? The price of Saturday Club and other "members" activities once membership goes? Weekly price for Summer Club?
4. **E-shop.** What does the MagicBooking e-shop sell today? Is it needed in the new system, and when?
5. **Trials.** How are trials used today (free first session? which activities?) and do they need their own reporting?
6. **Retention periods and data protection lead.** Please confirm the defaults: inactive accounts 3 years; registers 3 years after the session; accident records until the child is 21; safeguarding records until at least 25 with DSL review; financial records 6 years; guest contacts 12 months; message bodies 24 months; imported accounts never activated 12 months. Who is the named data protection lead?
7. **Funders and HAF data flows.** Which funders should receive *identifiable* data with consent (e.g. National Lottery Community Fund, BCP, Arts Council), and what exactly? What does BCP Council need for HAF (names, DOB, school, FSM, SEND, daily attendance), in what format and how often? Is there a HAF eligibility code to capture and verify?
8. **Home Education and drop-off.** Do parents stay for Home Ed sessions? The default is no, so Home Ed needs the full registration. Which other activities are drop-off vs parent-stays?
9. **Arts Award and Seesaw without membership.** Who qualifies for free Arts Award enrolment and Seesaw access now? Do Seesaw portfolios need their own consent, since Seesaw is a third-party processor of children's work and images?
10. **Centres.** Only the Boscombe hub, or also outreach venues or schools that need their own registers?
11. **Collection and roles.** Is checking the collection password on a tablet at the door acceptable to the DSL (it is never displayed or printed)? Minimum age for "going home alone" (default 11)? Who are the DSL and deputies, SEND lead, finance and managers? Should 2FA be required for every staff member, or just privileged roles?
12. **Providers.** Which email service and sending address (SPF/DKIM setup)? Twilio sender ID or number and monthly SMS budget? Confirm the Stripe account is in the charity's name and who administers it. Bank details to print on invoices.
13. **Finance exports.** Which accounting package or format does the treasurer use?
14. **Reporting year.** Should "Year" mean calendar, financial (April) or academic (September) year? Confirm the session categories for the daily breakdown (HAF, Holiday club, Saturday club, Home Ed, Baby & toddler, Events, Young adults).
15. **MagicBooking export.** Can you send a sample export with fake or redacted rows as soon as possible, to finalise the mapping? Can MagicBooking also export (a) historic attendance totals for year-on-year charts and (b) any outstanding credits or balances owed to families that the fresh start must honour?
16. **Age rules.** For clubs spanning a birthday, is eligibility on the first day of the booking or on each session date? Do any activities go by school year (e.g. "Reception to Year 6")?
17. **Young adults and guest events.** Which activities are for young adults (18+ self-booking)? Under-18s in 14-22 groups are registered by a parent. For family events, do adults count towards capacity? Should paid parties (cinema parties at £250) stay as enquiries or become bookable?
18. **Existing newsletter list.** `subscribers.json` has no record of consent. May we send a one-off "please confirm you still want our newsletter" email and drop anyone who doesn't confirm? Once the Data (Use and Access) Act 2025's charity soft opt-in is in force, do you want to rely on it for families who book?
19. **Hosting region.** Do you agree to move hosting to Render's EU (Frankfurt) region before launch (recommended), rather than documenting a US transfer?

---

### Critical files for implementation
- `server.py`: every hook point in §1.2. Being split into `hah/`.
- `public/admin/admin.js`: to be split into `public/admin/js/**` ES modules (§1.6); lines 229, 238 and 785 feed the event-to-activity link and the cutover switch.
- `public/js/main.js`: lines 31 and 217-219 carry the Book Now and event booking integration.
- `partials/header.html` and `partials/footer.html`: Book Now, account link, legal links.
- `public/css/style.css`: design tokens (lines 13-38) and form styles (lines 362-378) that the new `public/css/portal.css` builds on.
- Also affected: `render.yaml` (region, `autoDeploy`, disk, health check), `.github/workflows/deploy.yml`, `data/content.json` and `seed/content.json` (membership copy, `bookingUrl`, `smtp`), and `public/privacy.html`.

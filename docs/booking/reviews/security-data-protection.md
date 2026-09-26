# Adversarial review: Honeycombe booking system design

I checked the design against `server.py`, `admin.js`, `main.js`, `partials/`, `render.yaml`, `README.md` and `privacy.html` at commit f02be31. Most line references in the design are accurate. One is wrong: the default password is at README line 22, not 24. On a 4-core dev machine, PBKDF2 takes 0.18 s at 600k iterations and 0.027 s at 100k.

Priority key:
- **B** = blocker: must be fixed before launch.
- **S** = should fix.
- **N** = nice to have.

---

## 1. AppSec

**A1 [B] The outbox stores live secrets.** Email bodies are rendered and stored in `message_deliveries.body_text/body_html` when they are queued, and kept for 24 months. That covers reset, activate, verify, staff_invite, guest-manage and email_change links, all of which carry the raw token.
- These bodies appear in the admin Messages archive, the parent Messages tab, SAR zips and backups.
- So anyone with `messaging.*` rights, a DB copy or a backup can take over an account. Activation tokens last 30 days, and a staff invite can be turned into a staff account.
- This defeats the "only sha256(token) stored" claim in §2.2.
- Fix:
  - Mark token-bearing templates `sensitive`.
  - Insert the token only at send time, from a column that is nulled once the send succeeds or fails.
  - Store the body with the link redacted.
  - Never show these bodies in the archive or include them in a SAR.
  - Add a test that greps `message_deliveries` for the raw tokens.

**A2 [B] Registration sends special-category data before the email is verified, and the verify link signs the user in.**
- The full-level form posts the child's health, safeguarding and collection details under an unverified email. If the parent mistypes the address, whoever owns the typo'd address clicks the link, gets signed in (§3.4) and can read that child's record.
- The same auto sign-in is a login-CSRF route. An attacker registers an account, sends their own verify link to a victim, and the victim then enters their child's data into the attacker's account.
- When the email already exists, the whole 9-section form is silently thrown away.
- Fix:
  - Create the account first (mockup 3's "Your details" step) and verify it with a 6-digit emailed code typed into the same tab. The alternative is a link that marks the email verified but still requires the password.
  - Collect child sections only once the account is verified.
  - The verify page must need a button press. Some email scanners run JS and would otherwise burn the one-time token.

**A3 [B] Parent APIs are open to mass assignment and IDOR.**
- The generic `participants/<ref>/update {section, fields}` lets a parent set `haf_status='verified'`, which gets them free `haf_only` places without approval.
- Profile updates could likewise set `pay_later_allowed`, `needs_review`, `level`, `status` and the `f_*` flags.
- Fix:
  - Keep a per-section whitelist of fields parents may write, in `formspec`.
  - Parents may only choose `haf_status` from claimed_eligible, not_eligible or not_sure.
  - `/api/book/confirm` must derive price and pay_mode on the server. Reject client-supplied `free`, `staff_offline` and `staff_link`, and re-check `allow_pay_later` at confirm time, not only at quote time.
- Every `/api/account/*` query must include `AND account_id = ?`. Refs being random is not authorisation, and invoice numbers (`HAH-2026-00042`) are sequential.
- Add `test_account_idor.py`: parent B calls every account route with parent A's refs and numbers, and each call must return 404.

**A4 [B] Staff privilege escalation and weak role scoping.**
- `staff.manage` (admin) can grant `dsl`, which bypasses the "safeguarding is DSL-only" rule.
  - Only the owner may grant `dsl` or `owner`.
  - Any DSL grant alerts the existing DSLs.
  - Granters can't give out roles they don't hold themselves.
  - Changing `mfa_required_roles` needs the owner and sends a notification.
- MFA is not required for manager or send_lead, yet both can search, view and export health data. Require TOTP for every role holding `people.view_health` or any export permission.
- `registers.view` is not scoped, so session staff can open any date's register and harvest health flags for every child.
  - Limit session_staff to today and tomorrow, or add a `session_staff_assignments` table.
  - Alert when one user views more than N health records an hour.
- Audit rows such as `participant.safeguarding.view` shown to owner-only `audit.view` reveal which children have DSL activity. Filter those rows to the DSL. Exclude restricted incidents from dashboard counts and in-tray titles for non-DSL users.

**A5 [B] Changes that affect who can collect a child have no step-up or notification.**
- A hijacked session, or a shared family device with a 30-day session, can change the collection password, `can_collect` contacts, `collection_alert`, go-home-alone, or the email address.
- Fix:
  - Require the password again (within 10 minutes) for these changes.
  - Send email and SMS to the account: "Collection details changed".
  - Raise an in-tray item if the child has a booking within 7 days.

**A6 [B] Stripe webhook deduplication loses events.** `INSERT OR IGNORE INTO stripe_events` before processing means that if processing fails, Stripe's retry is ignored and the payment is never confirmed.
- Fix:
  - Insert the event and apply its effects in one `tx()`.
  - If processing fails, roll back and return 500 so Stripe retries.
  - Treat an event as a duplicate only when `processed_at` is not null.
  - Check `amount_total == checkouts.amount_pence`, `currency == 'gbp'` and `payment_status == 'paid'`.
  - `verify()` raises KeyError on a malformed header, which gives a 500. It should return 400.

**A7 [B] The client IP from `X-Forwarded-For` must be checked before launch.** Render sits behind its own load balancer and, reportedly, Cloudflare. With `TRUSTED_PROXY_HOPS=1`, the rightmost entry may be a proxy IP.
- If so, every visitor shares one bucket, and `guest_booking` at 5/h/IP blocks the whole public after 5 bookings.
- The same limit is too tight anyway: UK mobile carriers put many users behind one IP (CGNAT).
- Fix:
  - On staging, send spoofed `XFF`, `CF-Connecting-IP` and `True-Client-IP` headers and log which values arrive.
  - Set per-IP public limits to about 30/h and rely on per-email and per-event caps.
  - If IP parsing fails, do not throttle globally.

**A8 [S] Guest bookings can exhaust capacity.** Free guest bookings are confirmed immediately against an unverified email, with up to 8 places each and 5 per IP per hour, so one script can fill an event.
- Fix:
  - Hold free guest places as `pending_confirmation` for 30 minutes until the emailed link is clicked.
  - Cap guest places per email per session and pending checkouts per account.
  - Add a declaration: "The person booking is 18+".

**A9 [S] Account lockouts can be used to lock people out.** Locking by email alone (10 failures for parents; 5 for staff, doubling each time) lets an attacker lock out every DSL on a session morning, or parents during the summer rush.
- Fix:
  - Throttle on (email, IP) with progressive delays instead of a hard account lock.
  - Keep the message generic.
  - Offer "email me a sign-in or reset link" to unlock.

**A10 [S] Password hashing CPU cost.** 600k iterations is about 0.18 s locally and roughly 0.4–0.8 s on Render starter (0.5 CPU). Logins cost CPU without authentication, and unknown emails need a dummy hash so timing doesn't reveal which emails exist.
- Fix:
  - Rate-limit before hashing.
  - Allow at most 2 hash operations at once, answering 429 beyond that.
  - Size the plan (at least Standard) using the 200-parent load test.

**A11 [S] The design puts the connection cap in the wrong place.** Acquiring the 64-slot semaphore in `process_request` counts keep-alive connections, which Render's proxy pools, rather than requests actually in flight. That causes false 503s, and 64 slow clients can hold every slot.
- Fix: acquire the semaphore around `handle_one_request`, and use a short idle keep-alive timeout of about 5 s, separate from the 30 s read timeout.

**A12 [S] The health check is tied to the worker.** `/healthz` returns 503 when the worker heartbeat is more than 5 minutes old, so Render restarts the web process mid-register if a nightly backup, integrity check or SMTP stall runs long.
- Fix:
  - Base the health check on the web process only.
  - Report a stale worker as an in-tray and email alert.
  - Give each job a timeout and its own thread, so the outbox can't delay hold release.

**A13 [S] Too many SQLite writes.** Touching `last_seen_at` on every authenticated GET makes each read a `BEGIN IMMEDIATE` write, which adds lock contention in the rush. Only update it when more than 60 s have passed.
- Import commits, campaign expansion and retention purges should be chunked into transactions of no more than about 200 ms, so booking confirms don't hit the 5 s busy timeout.
- Use one connection per request, closed in a `finally` block. `threading.local` gives no reuse with a thread per connection and leaves closing to garbage collection.

**A14 [S] CSV export breaks numbers.** `csv_safe` prefixes `-`, which turns `-12.50` credit notes into `'-12.50` and breaks finance imports. Apply it to text cells only and write numbers as numbers.

**A15 [S] Staff can edit security email templates.** A compromised manager account could rewrite password_reset, activate, verify or invite templates to point at a phishing domain. Lock these templates (`is_system`, not editable).

**A16 [S] Admin shares an origin with the public site.** Any XSS on the public site or CMS can read `csrf` from `/api/staff/me` or `/api/account/me` and act as the logged-in user.
- Serve the back office from its own host (`staff.` subdomain, routed by Host header, with its own `__Host-` cookie).
- Enforce the CSP (not report-only) before any child data exists.
- Validate the `bookingUrl` scheme in the new `main.js:31` code: it must start with `https:` or `/`.

**A17 [S] Upload misuse.** The only upload tool in Phase 1 is public `/uploads/` with public caching. Staff will scan paper consent forms into it.
- Label it "Public website images only", restrict it to images, and send `X-Robots-Tag: noindex`.
- Strip EXIF (including GPS) from JPEGs of children with a small stdlib APP1 segment stripper.
- Parse multipart with `email.parser.BytesParser`, not the hand-split parser.

**A18 [S] The sequential worker delays holds.** Covered in A12: slow SMTP holds up hold release and waiting-list offers.

**A19 [N] Smaller hardening items.**
- Encrypt `totp_secret` at rest, or accept the risk in the DPIA.
- Rotate the session token once MFA passes.
- Refuse `HAH_DEV=1` when the `RENDER` env var is set.
- Allow a list of `HAH_SECRET_KEY` values so old unsubscribe links keep working after rotation.
- Store a pepper version prefix on collection password hashes.
- Confirm `includeSubDomains` for HSTS.
- Rate-limit the checkout-status Stripe fallback to one call per checkout per 10 s. Use the stored `stripe_session_id`, not the `session_id` query parameter.
- Strip CR/LF from header values in `mail.py`. The current contact form puts a name from a public form into the Subject.
- Bound the size of the rate limiter's dictionary.
- Reject PAN-like digit runs (Luhn check) in payment `reference` and `notes` fields to stay out of PCI scope.
- Redirect www to the apex before the Origin check.
- Bootstrap the owner only with a one-time `ADMIN_BOOTSTRAP_TOKEN`.
- `backup()` direction: it is `live.backup(dst)`.

**A20 [N] Render may log paths itself.** Redacting paths only covers the app log. If Render's own request logs are enabled, unsubscribe paths and GET search queries (`?q=Maya Smith`) appear there. Use POST for PII search.

---

## 2. Data protection

**D1 [B] Several lawful bases are wrong.**
- **Contract (6(1)(b)) doesn't cover the child's data.** The ICO says contract "does not apply if … the contract is with someone else", and the contract is with the parent. Use 6(1)(c) legal obligation for Ofsted-registered provision and 6(1)(f) plus a legitimate interests assessment elsewhere. Emergency contacts and "must not collect" persons also fall under 6(1)(f).
- **Sch 1 para 18 only applies when consent can't be sought, and it doesn't cover adults not at risk.**
  - It is defensible for safeguarding family information and concerns, and arguably for young children's health data.
  - It is wrong for the 18+ adult level's medical and access needs. Use explicit consent (9(2)(a)) there, which fits because those fields are optional.
  - Remove "religious requirements" and ask about "dietary or cultural requirements" instead.
  - Get the Appropriate Policy Document legally reviewed.
- **Criminal offence data.** `family_info` and `collection_alert` will hold court-order and conviction information, which is Art 10 data. The APD must cover it (Sch 1 Part 3 para 36).
- **HAF.** "BCP's public task" is BCP's basis, not the charity's. Settle whether the charity is BCP's processor or a separate controller under the HAF data-sharing agreement, and state its own basis (6(1)(b) or (e)). HAF sharing must never be framed as consent.
- **Funder sharing.** One yes/no covering several funders is bundled consent. Either confirm funders only need anonymised data and drop the question, or ask per funder. Consent is invalid where a funded place depends on it. Suppress small numbers (<5) in "anonymised" statistics.

**D2 [B] Don't send a re-permission email to newsletter subscribers (Q18 is wrong).** The ICO fined Flybe (£70k) and Honda (£13k) for exactly this.
- The footer form ("Join our newsletter… Sign up", `partials/footer.html:33-37`) was a clear affirmative act, and `subscribers.json` holds the date.
- Migrate them as consented: `source='legacy_newsletter'`, `email_opt_in_at = date`, with the wording evidenced from git history.
- Just send the next newsletter with an unsubscribe link.

**D3 [B] Governance has to start now, not at PR 1.20.**
- The DPIA is mandatory (special-category data plus children as vulnerable data subjects) and should shape Phase 0.
- Add a breach procedure: a breach log (Art 33(5)), the 72-hour ICO decision path, a contact tree, a "revoke all sessions and rotate secrets" runbook, and processor notification clauses.
- Require MFA and least-privilege membership on Render, GitHub, Stripe, Twilio and the email provider. Render shell access means full DB access, so record who has it.

**D4 [B] Backups aren't really backups until they're off-site.** Copies on the same Render disk are unencrypted at the app level and are lost if the service is deleted, which is exactly what the Frankfurt re-creation in PR 0.8 does.
- Move off-site backup into Phase 1.
- Encrypt with public-key `openssl cms -encrypt` via subprocess, so the server can't decrypt its own backups; the trustees hold the private key offline.
- Upload to a UK or EU S3-compatible bucket using SigV4 via `hmac`/`urllib`, with object lock of at least 35 days.
- Run a restore drill.
- On encryption at rest: I agree not to build field-level encryption by hand. Relying on Render disk encryption plus access control plus encrypted off-site backups is acceptable if it is recorded in the DPIA. Vendoring `cryptography` is the only real alternative, and it gains little while the key sits in the same process.

**D5 [S] Frankfurt doesn't remove international transfers.** Render Inc (US) staff can still access the data remotely, so the Render DPA needs the UK Addendum or the UK–US data bridge. Twilio (SMS bodies) and possibly the email provider are US transfers too.

**D6 [S] The "no cookie banner needed" claim is false.** The Google Maps iframe (`server.py:347-357`) loads google.com, which can set third-party cookies. Make the map click-to-load, or use a static image with a link.

**D7 [S] Photo consent is too coarse.** Split it into:
- internal displays and Seesaw
- website and print
- social media
- press and funder reports
- video and audio

Never publish a name with a photo by default. For children aged 12 and over, prompt "have you discussed this with your child?". Note that "first aid", "plasters" and "emergency treatment" are parental permissions, not GDPR consents. `privacy_ack` is an acknowledgement, not a consent; label them accordingly.

**D8 [S] Minimise what goes to Stripe and Twilio.**
- Take child names out of Stripe `product_data[name]` and metadata; use booking refs.
- The incident SMS and email (injury type, "first aid given") is health data sent to a US processor and kept 24 months. Send "We've added a note about Maya's session today. Sign in or call" instead.
- Activation and verify emails must contain no child names, because a mistyped address makes them a breach.

**D9 [S] Retention changes.**
- Keep accident records for children until date of birth + 25 years, in line with the IRMS schools toolkit, not the 21st birthday.
- HAF attendance and eligibility records may need retaining for 6+ years under the grant conditions, which conflicts with the 3-year register retention. Add this to Q6 and Q7.
- Truncate parent IPs in `audit_log` and `consents` after 90 days, and keep staff IPs.
- Scrub child names from `intray_items.title` on erasure.
- Refund any account credit on deletion.

**D10 [S] SAR completeness and exemptions.**
- Add to the export: `staff_notes`, non-restricted incidents with acknowledgements, `contact_messages`, guest bookings under the same email, and attendance `collected_by_*`.
- Parent-authored `family_info` is the parent's own data and should be exported.
- Withhold only material written by the DSL. The right exemption is "child abuse data" (DPA 2018 Sch 3 Part 5 para 21), not the "serious harm" test.

**D11 [S] Children and adults.**
- Add a "turns 18" job. It notifies the family, removes the parent's access to that person's health data, and invites the young adult to take over the record or have it archived. This matters because youth groups run to age 22–25.
- Adding an 18+ participant to a family account needs an authority declaration.
- Record a Children's Code assessment in the DPIA: teens reach `/book` and the guest form, so keep high-privacy defaults and no analytics.

**D12 [S] Safeguarding operations.**
- Detect the same child under two accounts (name + date of birth match → DSL in-tray). Separated parents otherwise sidestep `collection_alert`.
- `f_safeguarding` ("check with DSL") should be set by the DSL after review, not derived from whatever the parent typed.
- Hide the HAF (free school meals) flag from session staff on registers.

**D13 [S] Imports.**
- Import only identity and contact fields plus the child's name and date of birth. Stale special-category data sitting in never-activated accounts for 12 months is risk with no benefit.
- "Merge children only" requires manual review.
- Get a deletion certificate from the MagicBooking / Creative Kids processor after cutover.

**D14 [N] Marketing options.** The ordinary commercial soft opt-in (PECR reg 22(3)) is available for paid bookings if there's an opt-out box at booking. The charity soft opt-in under the Data (Use and Access) Act 2025 applies only once it's in force. This is a business decision. Consider requiring approval before a "service" message goes to "All active families".

---

## 3. Wrong or risky in the design itself

1. **[B] CSP sequencing breaks the live site.** PR 0.3 enforces `style-src 'self'; font-src 'self'`, but Google Fonts (`partials/head.html:6-8`, `admin/index.html:10-12`) aren't self-hosted until PR 1.2. Move font self-hosting into PR 0.3. Also run the CSP report-only for a week first: the live `content.json` on the disk may contain external image URLs that staff pasted in.
2. **[S] The waiting list can block bookings indefinitely.** "Any waitlisted row → waitlist every new booker" stalls places in `manual` mode, and a party of 3 at the head of the queue blocks a single free place forever. Offer to the first entry that fits, and let direct booking resume after N hours of no action.
3. **[S] Guest activities with unnamed children.**
   - `registration_level='guest'` must imply `parent_must_stay=1` (enforce with a CHECK). Drop-off provision needs named children and an emergency contact.
   - `incident_participants` requires a `participant_id`, so an injury to an unnamed guest-event child can't be recorded. Add `guest_contact_id` and a free-text name.
4. **[S] The collection-password limit contradicts itself.** "5 attempts per day" and "the 6th wrong attempt alerts" can't both hold. A hard lock at pick-up strands a genuine parent: after the limit, fall back to the phone-verification procedure rather than a lock.
5. **[S] "Unsubscribe tokens redacted from logs" covers only the app log** (see A20).
6. **[N] Emergency contact for parent-stays classes.** At the short level, an emergency contact is arguably unnecessary when the parent stays; make it optional. `gender` should be collected only if a funder requires it.

### Critical Files for Implementation
- server.py (lines 49-61 storage, 65-115 auth, 119-139 mail, 347-357 Maps iframe, 503-549 headers/body/Origin/log, 746-777 upload)
- partials/head.html and public/admin/index.html (Google Fonts, which must be self-hosted before the CSP)
- partials/footer.html (lines 32-40: the newsletter wording that evidences legacy consent)
- render.yaml (region, autoDeploy, plan size, healthCheckPath)
- public/privacy.html (lawful-basis and cookie rewrite)

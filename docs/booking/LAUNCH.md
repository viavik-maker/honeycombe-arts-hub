# Launch runbook: switching from MagicBooking to the new booking system

The new system is built and switched **off**. Families keep using MagicBooking until the day you turn online
booking on in Admin → Booking settings. This page is the order of work for that switch, the checks to do first,
and what the load test showed.

Pick a **quiet period** for the switch, such as a half-term week (for example February half-term 2027), not the
summer rush.

---

## 1. Before anything else (weeks 1–4)

- [ ] **Governance sign-off.** The trustees and the data protection lead approve `docs/booking/governance/`:
  - DPIA
  - ROPA
  - Appropriate Policy Document
  - LIAs
  - retention schedule
  - processor list and DPAs
  - breach runbook

  No real family data goes in before this.
- [ ] **Open questions** (`DESIGN.md` A4 and §10): each has a "Going with" default already in the software. Change
  only what the charity disagreed with, in Booking settings and in the activities. Settings to check:
  - cancellation cutoff
  - refund days
  - pay-later policy
  - HAF allowance per child
  - reporting year start
  - go-home-alone age
- [ ] **Staff accounts.** Every staff member has their own account with two-step sign-in. The DSL and deputies
  have the DSL role, and only an owner can grant it.
- [ ] **Providers set up** (README), each with MFA on the account and its DPA accepted
  ([processors.md](governance/processors.md)):
  - `SITE_URL`
  - **Brevo** SMTP relay, sending from bookings@honeycombeartshub.org.uk, with SPF, DKIM and DMARC on the domain
  - **Twilio**, alphanumeric sender "Honeycombe", with a spending limit
  - **Stripe** (account in the charity's name): live keys and webhook
  - **Backblaze B2** off-site backups: EU Central (Amsterdam) bucket, Object Lock 35 days, an upload-only
    application key, the trustees' backup certificate; a restore practised
- [ ] **Hosting.** The Render service is re-created in the **Frankfurt (EU)** region on the **Standard** plan
  (password hashing and busy booking mornings need the CPU), and Render's DPA is accepted in the dashboard.
- [ ] **Privacy notice.** The data protection lead reviews `public/privacy.html`.

## 2. Staging rehearsal (week 4–5)

Run the whole checklist below on staging with Stripe **test** keys, and record who did it and when.

## 3. Setting up the real catalogue (2–3 weeks before switching)

1. **Activities.**
   - Finish the seeded drafts: ages, sessions (use *Add a run of sessions*), prices, capacities, and the HAF
     allowance on the HAF activity.
   - Add any other activities.
   - To have a What's On event's Book button open an activity, link it with *What's On event id*.
2. **Publish.** You can publish before booking is live: families can't book until the switch is on, but staff can
   preview the Book page.

## 4. Families (3–4 weeks before switching)

1. Export families from MagicBooking (a CSV).
2. Admin → **Import families**:
   - choose the file and check the column matching;
   - press **Check** (nothing is saved) and fix any rows it lists in the CSV, then check again;
   - press **Import**.
3. When you're ready, press **Send activation emails**.
   - Families prove who they are with a child's date of birth, choose a password, then complete their children's
     health details and permissions.
   - A week later, press **Send reminder**.
4. Watch the In-tray for *activation problems* (5 wrong answers). Help those families by phone. From the family's
   record you can send a fresh link.
5. If an import goes wrong, **Roll back** removes every family from that import who hasn't activated or booked.

## 5. Switch-over day

1. **Close sales in MagicBooking** (or stop taking new bookings there).
2. **Re-key places already sold in MagicBooking** for future sessions.
   - Use People → the child → **Add pre-sold places (MagicBooking)**.
   - These count towards capacity and appear on registers, but raise no invoice.
   - Check a few registers against MagicBooking's.
3. Admin → **Booking settings** → tick **Online booking is live** → Save. At that moment:
   - every Book Now link on the website goes to `/book` (event pages open their activity);
   - "My account" appears in the header;
   - the "no membership fee" wording replaces the membership wording on the pages;
   - the editable page text is patched wherever it's still the original membership wording.
4. Make a test booking yourself, and cancel it.
5. Tell families: email everyone booked (Messages → *About a booking*), and send the newsletter (Messages → *News*).
6. After cutover, ask MagicBooking's processor for a **deletion certificate** for the data they held.

## 6. The first weeks

- Check the **In-tray** daily: approvals, HAF checks, voucher payments, absences, safeguarding items for the DSL.
- **Finance → Unpaid** each week: send reminders, and record voucher and bank payments as they arrive.
- Set **`HAH_CSP=enforce`** after a quiet week with no `[csp]` lines in the logs.
- Do a **backup restore drill** (README → Backups) and note the date.

---

## Verification checklist (staging, then spot-check live)

| # | Check | How | Done |
|---|---|---|---|
| 1 | Security headers on every page | `curl -sI https://…/` shows CSP, `nosniff`, `X-Frame-Options`, HSTS | |
| 2 | Real visitor IP | Signed in as staff, `/api/admin/request-info` shows your own IP (else set `TRUSTED_PROXY_HOPS`) | |
| 3 | Staff sign-in with two-step codes | Invite a colleague and have them sign in; lost phone → reset link | |
| 4 | All four registration journeys, keyboard only | Chooser → full / baby & toddler / 18+ / guest event | |
| 5 | Two browsers racing for the last place | Only one gets it; the other is offered the waiting list | |
| 6 | Card payment | Stripe test card `4242…`; also abandon a checkout and check the place is released | |
| 7 | Pay later, then a voucher payment | Finance → Record payment → invoice shows paid | |
| 8 | Guest one-off booking | Confirmation link within 30 minutes; the unconfirmed place is released | |
| 9 | Registers | Sign in and out with the right and a wrong collection password; incident blocks sign-out until discussed | |
| 10 | Messages | A news email goes only to opted-in people; one-click unsubscribe works | |
| 11 | Import | A sample MagicBooking export: check, import, activation email, activate | |
| 12 | Delete account | Cooling-off; the retention job erases everything except invoices and incident records | |
| 13 | Accessibility | Lighthouse ≥ 95 on /register, /book, /book/review, /account/bookings | |
| 14 | Load | `tools/loadtest.py` (below): p95 under 1 s, no overbooking | |
| 15 | Backup restore drill | Restore last night's backup into staging; sign in; see bookings | |
| 16 | Emails and texts arrive | Admin → Settings → System & backups → test email and test text | |

## Load test

`tools/loadtest.py` (instructions at the top of the file) creates 200 fully registered families on a
**throwaway** copy. It then has them all open the Book page, get a quote and confirm the same three sessions at
the same moment.

Results on the development machine:

| Families pressing Confirm at the same instant | Book page p95 | Quote p95 | Confirm p95 | Overbooked sessions |
|---|---|---|---|---|
| 50 | 0.87 s | 0.68 s | 1.04 s | none (510 correctly waitlisted) |
| 20 | 0.29 s | 0.30 s | 0.40 s | none |

Confirming is deliberately one-at-a-time: each takes about 8 ms inside the database, and that's what makes the
last place safe. So the only slow case is dozens of families pressing Confirm within the same second, and even
then it's about a second. Re-run the test on staging on the chosen Render plan before launch.

## If something goes wrong on the day

- **Pause online booking**: untick *Online booking is live*. Book Now links go back to the old booking URL at
  once, and existing bookings are unaffected.
- **Card payments failing**: families can still choose *pay later* or *vouchers* where allowed. Check Admin →
  Booking settings → Card payments, and the Stripe dashboard's webhook log.
- **Suspected data breach**: follow `docs/booking/governance/breach-runbook.md`. It includes "sign everyone out and
  rotate secrets".

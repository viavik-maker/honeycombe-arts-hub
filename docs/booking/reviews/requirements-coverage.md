# Coverage audit: in-house booking system design vs the Honeycombe email and mockups 1–5

Verdicts: **C** = covered, **P** = partial, **M** = missing, **X** = contradicts (the design disagrees with the email, a mockup, or itself). "§" refers to the design's sections.

## 1. Traceability table

| # | Requirement (source) | Design § | Verdict | Gap |
|---|---|---|---|---|
| **Migration** |||||
| E1 | Fresh start, no booking migration | §9 | P | Nothing handles MagicBooking (MB) bookings already sold for sessions after cutover (a4) |
| E2 | Families export file imported | §4.13, PR 1.4 | C | Export format unknown (Q15) |
| E3 | Login details can't be migrated | §3.4, §4.13 | C | The date-of-birth (DOB) challenge fails in some cases (c9) |
| E4 | Screenshots show look and flow | §1.7 | C | Site tokens used instead of the mockup palette, per steering |
| **Advanced search** |||||
| S1–S3 | Parents, Children, Bookings | §4.6 | C | |
| S4 | Trials | §4.6, §4.3 | P | Trials are searchable, but there is no way to create one until Phase 2 (`/mark-trial` UI is Phase 2) |
| S5–S7 | Cancelled, Under approval, Waiting list | §4.6 | C | |
| S8 | E-shop orders | §4.6 | M | Greyed out; waits on Q4 |
| S9 | Staff | §4.6 | C | |
| S10 | Search *and* filter, plus export | §4.6 | C | |
| **Registers and in-tray** |||||
| R1–R3 | Registers by centre, activity, date | §4.5 | C | |
| R4 | Include past activities | §4.5 | C | |
| R5 | All sessions in one register | §4.5 | C | |
| R6 | Weekly attendance | §4.5 | C | |
| R7–R9 | In-tray searchable by centre, activity, session | §4.1 | C | |
| R10 | In-tray does what MB's in-tray does | §4.1 | P | The design invents its own item types. Parent absence and cancellation, new registrations with needs, and parent-entered safeguarding info never reach it (a9) |
| **Activities** |||||
| A1 | Giggles & Wriggles Babies (0–1) | §2.6, §4.2 | C | Stored as 0–23 months, which assumes "0 to 1" means "under 2". Needs confirming |
| A2 | Toddlers (2–4) | §2.6 | C | 24–59 months |
| A3 | Home Ed Term 5 2026 (5–10) | §4.2 | C / X | Set to full level, but mockups 3 and 5 put it under the shorter form (Q8). The £5 price (content.json:186) is missing from the seed table |
| A4 | Summer Club 2026 (6–12) | §4.2 | C | |
| A5 | Summer HAF (6–12) | §2.6, §4.2 | P | No check for overlap with Summer Club on the same day, no cap on funded days per child, and its capacity is separate from the club's room (a5) |
| A6–A11 | Drafts, export, unpublished, scheduled, past, archived | §2.6, §4.2 | C | |
| **Messaging** |||||
| M1–M3 | Email and SMS to parents, archived messages | §4.8, §7 | C | |
| M4 | Message specific families (implied) | §4.8 | P | Audiences are session, activity, waitlist, all families or opt-ins only. Bulk actions from search are Phase 2 (a13) |
| **Attendance reporting** |||||
| AR1–AR5 | Filter by year, quarter, month, week, day | §4.10 | C | Year basis is Q14 |
| AR6 | Monthly attendance chart | §4.10 | C | |
| AR7 | Daily breakdown by session type (HAF, holiday clubs, Saturday club) | §4.10 | C | Uses `report_group` |
| AR8 | "Same level" as today at switch-off | §9 note 1 | P | PR 1.18 may slip about 4 weeks; no history (Q15) |
| **Booking management** |||||
| B1 | Approve bookings | §3.5, §4.3 | X | The approval path skips the capacity check (a1) |
| B2 | Cancel bookings (staff and parent) | §4.3, §3.8 | C | |
| B3 | Automatic invoice when a booking is made | §3.5, §3.8, PR 1.8 | C / P | Issued at confirmation, which is fine. But §3.5 says free/HAF bookings get no invoice while §3.8 says they appear as £0 lines (a17) |
| B4 | Reports including attendance | §4.10 | C | |
| B5 | Finance records | §2.8, §4.9 | C | |
| B6 | Delayed payments | §4.9 | C | |
| B7 | Pay and book on the spot | §4.3 walk-in | X | Walk-in takes "email or mobile", but the schema has `accounts.email NOT NULL` (a2) |
| **Incidents** |||||
| I1–I2 | Injury and incident logged on the child's record | §4.7, §2.9 | C | |
| I3–I4 | With or without notifying parents | §4.7 | C | Notify now, at collection, or not required with a reason |
| I5 | Injuries to guest-event children or to adults (implied) | §2.9 | M | `incident_participants` requires a `participant_id` (a6) |
| **Form (a): holiday club, under 18** |||||
| FA1 | 2 emergency contacts | §3.2 s3 | C | Held per family; the mockup has "relationship to child" per child |
| FA2 | Doctor/GP | s4 | C | |
| FA3, FA14 | Photography, merged into one question | s7, §2.4 | C | |
| FA4–FA8 | SEND, allergies, dietary, SEMH, religious | s5 | C | Medical conditions and anaphylaxis were added |
| FA9 | Family information for safeguarding | s6 | C / P | Split into two fields. The register shows a "Check with DSL" flag, but the form tells parents "only seen by our safeguarding lead", so the copy needs fixing |
| FA10–FA12 | First aid, emergency treatment, plasters | s7 | C | |
| FA13 | Going home alone | s7 | C / P | Only shown above a minimum age. No re-prompt when a child reaches that age |
| FA15 | School or home educated | s2 | C | |
| FA16 | Collection password | s8, §2.5 | C | Stored hashed. Needs DSL sign-off (Q11, c15) |
| FA17 | Age and DOB | s2 | C | |
| FA18 | HAF status | s2 | C / P | A "Not sure" answer leads nowhere (c3) |
| **Form (b): one-off events** |||||
| FB1 | Email and phone only | §3.7 | C | No name is collected, so the register row has no label (a22) |
| FB2 | No need to register children | §3.7 | C | |
| **Form (c): 18+** |||||
| FC1 | Separate area to register and book for themselves | §3.2 adult | C | |
| FC2 | Mixed-age groups, e.g. 14–22 (content.json:213, 269) | §2.6 | P | How adult and child levels combine within one activity is undefined (a11) |
| **Form (d): baby and toddler** |||||
| FD1–FD3 | Emergency contacts, allergies, photo permission | §3.2 short | C | |
| FD4 | Parents always stay | `parent_must_stay` | C | The callout is tied to the level rather than the activity flag (c12) |
| **Chooser** |||||
| CH1 | Four options leading to the right form | §3.2 | C | |
| CH2 | Holiday-club parents can book one-off events | §3.3, §3.7 | C | |
| CH3 | Not all events suit all ages | §3.3, §3.7 | C | |
| CH4 | One family needing two form types (implied) | §3.2 | P | See c1 |
| **Constraints** |||||
| K1 | Booking attached to the website via the Book Now link | §5.1 | C | The event-to-activity link bypasses the cutover switch (d3) |
| K2 | Simple email and password login straight to the booking portal | §3.4 | C | |
| K3 | No membership fee | §5.3 | C | Arts Award and Seesaw eligibility still open (Q9) |
| K4 | Easily editable from the back end | §4.2, §4.8, §4.12 | P | Form fields exist only in code; events and activities are entered twice (a15) |
| K5–K6 | GDPR compliant, secure, data protection in place | §6, §3.10, §5.4 | C | Governance work is scheduled too late (d1) |
| K7 | Consent to share data with funders | §2.4 | P | Collected but not enforced in any export or filter (a14) |
| K8 | Self-service unsubscribe | §3.9 | C | |
| K9 | Self-service account deletion | §3.10 | C | |
| **Decisions** |||||
| DC1 | Stripe plus offline payments, vouchers, Tax-Free Childcare | §3.6, §4.9 | C / P | Account credit can't be spent online (a3) |
| DC2 | Twilio SMS behind a provider interface | §7.2 | C | |
| DC3 | 18+ self-registration; "Teens" becomes "Young adults (18+)" | §3.2 | C | |
| DC4 | SEND intake in Phase 2 | §9 | C | |
| DC5 | Import families without passwords | §4.13 | C | |
| **Mockup 1** |||||
| 1a | Header Help and Sign in | §1.7 | P | No Help link in the portal header |
| 1b | Back to account types, tag, "once per child, add more at the end" | §3.2 | C | |
| 1c–1l | Sections 1–9, every field, Back, Create account | §3.2 | C | |
| **Mockup 2** |||||
| 2a | Nav: Book activities, My bookings, My family, Sign out | §1.7 | C | |
| 2b | "Hi Sarah" greeting | — | M | Trivial |
| 2c | Search box | §3.5 | C | |
| 2d | Holiday clubs / Events tabs | §3.1 | C | Four tabs |
| 2e | "Booking for Maya (8) and Leo (11) · Change" | §3.5 | C | Shows today's age rather than age at the session (c6) |
| 2f | Date tile, "title · theme", time · ages | §1.7 | C | |
| 2g | Places left / Only N left / Full | §3.5 | C | |
| 2h | Select / Selected / Waiting list buttons | §3.5 | C | |
| 2i | Sticky "N selected · Continue" | §3.5 | C | N isn't defined: sessions or child-places? |
| 2j | Berry colour | §1.7 | X | Intentional, per steering |
| **Mockup 3** |||||
| 3a | Stepper: Account type → Your details → Who's attending → Review | §3.2 | P | Design uses one long page with no Review step |
| 3b | Cards | §3.2 | C | Four cards, per the email |
| 3c | "We'll ask for" chips on each card | — | P | Not specified; could be generated from the form definition |
| 3d | Home education under the shorter form | §3.2 | X | Q8 |
| 3e | Teens card | §3.2 | C | Replaced per decision |
| 3f | "You can create a full account later" | — | P | Guest bookings are never linked to a later account (a12) |
| 3g | Footer: "Not sure which to choose? Get in touch · Already registered? Sign in" | §3.2 | P | "Get in touch" is missing |
| **Mockup 4** |||||
| 4a–4f | Back, tag, "No account needed", event card, email, phone, adults/children selects, keep-me-updated, Book places | §3.7 | C | |
| **Mockup 5** |||||
| 5a | "What are you signing up for?" (Home ed / Baby / Teens) | §3.2 | X | Split into cards: Home Ed goes to full, Teens to adult |
| 5b, 5c | Parent-stays and teen callouts | §3.2 | C | |
| 5d–5i | Your details, child, contact 1 plus optional contact 2, allergies, photo, confirm | §3.2 short | C | Design adds a relationship field and a privacy acknowledgement |
| Mockup 6 | SEND intake | Phase 2 | C | Per decision |

## 2(a). Missing or partial items, with a fix (most severe first)

1. **Approval bookings skip the capacity check (§3.5, step 2).** `requires_approval` bookings and unverified HAF claims become `pending_approval` ("place reserved") *before* the capacity and waiting-list check. That overbooks and jumps the queue.
   - **Fix:** run the §1.4 capacity-and-queue check first for every item. Set `pending_approval` only when a place is free; otherwise `waitlisted`. Add a case to `test_booking_capacity`.
2. **Walk-in contradicts the schema.** Walk-in accepts "email or mobile" (§4.3), but `accounts.email` is `NOT NULL UNIQUE` (§2.3).
   - **Fix:** make email nullable for `source IN ('walkin','staff')`, with a partial unique index `WHERE email IS NOT NULL`. Allow login and activation only once an email is set.
3. **Account credit can't be used online, and the balance formula is wrong.**
   - Credit is the main cancellation outcome (§3.8), but quote and confirm have no "apply credit" step and `pay_mode` has no credit value.
   - The §2.8 balance ignores unallocated payments, such as voucher or Tax-Free Childcare lump sums and overpayments.
   - **Fix:** credit = Σ succeeded payments − Σ allocations − Σ non-credit refunds. Apply it automatically in the quote. Give staff an "allocate credit" action. Import MB balances as opening payments (`method='legacy_credit'`).
4. **No way to carry over bookings already sold in MagicBooking** for sessions after cutover (term blocks, summer weeks).
   - **Fix:** add `funding='prepaid_legacy'`. It raises no invoice but counts toward capacity and appears on registers and reports.
   - Add a staff bulk re-key screen or a CSV import of future bookings.
   - Runbook: close MB sales from the cutover session date.
5. **HAF rules are missing.**
   - There's no cap on how many funded days one child can book. HAF funds a fixed number of days per holiday.
   - A child can be booked on Summer Club and Summer HAF on the same day. Nothing checks for overlapping times.
   - The two activities have separate capacities, but the site says funded places are "offered each day" within one room (`public/holiday-club.html:33`).
   - **Fix:** add `haf_allowance_days` per activity and check it in eligibility. Add a general check that the child isn't already booked on a session that overlaps in time. Either add an optional `capacity_group` shared across activities, or confirm that separate caps are fine.
6. **Incidents can only be recorded against registered children** (§2.9). Children at guest events, and adults such as a parent at a baby class, can't be recorded.
   - **Fix:** make `participant_id` nullable and add `person_name`, `booking_id` and `guest_contact_id`.
7. **Trials can be searched but not created** in Phase 1.
   - **Fix:** add an `is_trial` checkbox and price override to staff book-on-behalf in PR 1.10 (small). Alternatively, Q5 confirms trials aren't used.
8. **E-shop is missing.**
   - **Fix:** answer Q4 *before* switch-off. In the meantime, use a §4.9 manual invoice with a product line.
9. **In-tray doesn't match MagicBooking.**
   - **Fix:** add these item types:
     - `absence_reported` (the §3.8 "can't come" after the cutoff).
     - `parent_cancelled`.
     - `new_registration_needs`: SEND, SEMH or anaphylaxis at registration, so the SEND lead calls the family. This is the interim path until the Phase 2 intake, and the holiday club page (`holiday-club.html:44`) promises a call.
     - `safeguarding_info_submitted`: parent-entered family info for the DSL to review. It uses the existing `dsl_reviewed_at` column.
   - Ask the charity what MB's in-tray holds today.
10. **The "missing sections" check only looks at the child** (§3.3). Full level also needs the account address (optional at short level) and a second family contact.
    - **Fix:** the gap is participant sections plus account sections plus the family contact count. Recompute every child's level when contacts or the address change.
11. **Adults in mixed-age activities.** The rule for one activity that holds both adults and children isn't defined.
    - **Fix:** a participant who is the account holder needs only the `adult` level. The activity's level applies to children. An activity whose level is `adult` means 18+ only.
12. **Guest bookings aren't linked when the guest later creates an account**, even though mockup 3 promises this.
    - **Fix:** after email verification, attach `guest_contacts` with the same email (bookings, invoices, marketing preferences) to the account.
13. **Staff can't message hand-picked families.**
    - **Fix:** add an `audience.account_ids` option, with a "Message" button on the parent record and on selected search results (small).
14. **Funder-sharing consent isn't enforced.**
    - **Fix:** add a `funder_share` filter to children search and export. Any identifiable funder export includes only "yes" answers; HAF data for BCP is exempt under public task.
15. **Not easily editable.** Form labels and help text exist only in `formspec.py`, and staff enter each event and its activity twice (§5.2).
    - **Fix:** add settings-level copy overrides keyed by field. Give the event editor a "fill from activity" option. Make the live price/dates/places summary mandatory, not optional.
16. **No booking terms page.** Both `accept_terms` and §5.3 link to "the cancellation policy", but no PR creates it.
    - **Fix:** add a `/booking-terms` page in PR 1.19.
17. **Free/HAF invoice inconsistency (§3.5 vs §3.8).**
    - **Fix:** mixed baskets show £0 lines; a booking that is purely free or HAF gets a confirmation, not an invoice.
18. **Waiting list when disabled.** With `waitlist_enabled=0`, the §1.4 code still waitlists a full session.
    - **Fix:** return 409 "Full" instead.
19. **Cookie notice omits the basket.** §5.4 item 11 doesn't list the `localStorage` basket.
    - **Fix:** list it as strictly necessary.
20. **Parents can't be picked at sign-out.** The dialog lists only `can_collect` emergency contacts.
    - **Fix:** include the account holder(s) by default.
21. **Bounce tracking won't work.** "Tracking bounced" (§4.13, step 6) isn't possible over plain SMTP.
    - **Fix:** tie it to the email provider's webhook (Q12), or drop it.
22. **Guest bookings have no name** for the register.
    - **Fix:** add an optional "Name for the booking", or take the Stripe billing name.
23. **Upper age bound.** "Ages 6–12" must mean up to 155 months inclusive, consistent with 0–23 months for "0 to 1".
    - **Fix:** state the conversion rule in the admin UI and cover it in tests.
24. **Minor mockup items to add:**
    - "We'll ask for" chips on the chooser cards.
    - "Not sure which to choose? Get in touch" in the chooser footer.
    - A "Help" link in the portal header.
    - "Hi {first name}" on the booking page.
    - A going-home-alone prompt when a child reaches the minimum age.

## 2(b). Over-engineering: cut or defer

1. **Self-service data export (SAR zip, §3.10).** The email asks only for self-service unsubscribe and delete.
   - Keep the staff-run export from the parent record through `gdpr_requests`; defer self-service. Saves about 3–4 days.
2. **Long-range retention automation:** 3-year inactivity warnings and anonymisation, 21st/25th-birthday holds, the 6-year audit-delete trigger.
   - After a fresh start, nothing reaches these limits before about 2028.
   - Launch with only: user-requested erasure, purging unverified accounts after 7 days, token and session cleanup, and purging import rows after 30 days.
   - Keep the schema. Put the rest in a dated Phase 2 item.
3. **Automatic handling of late Stripe payments** (re-confirm or auto-refund, §3.6). The design already calls this "very unlikely". Replace it with an in-tray item and a staff action.
4. **Moving a booking between sessions at a different price** (supplementary invoice or credit note, §4.3). In Phase 1, allow moves between same-price sessions only; otherwise cancel and rebook.
5. **School-year age basis** (`age_basis='school_year'`, school-year columns). Unresolved in Q16; defer until it's needed.
6. **Guest manage page and `guest_manage` tokens** (§3.7). Defer; guests can phone or reply, and staff cancel for them.
7. **Messaging extras:** scheduled campaigns, SMS cost estimate, age-band and past-attendance marketing audiences, and database overrides with reset for about 28 templates.
   - Defer these. Keep file-based templates plus editable intro text for 3–4 key emails.
8. **Full port of the CMS tabs to ES modules in PR 0.6** (M–L). It has no user value and carries regression risk on Phase 0's critical path.
   - Patch the old `admin.js` `api()` for the new login and CSRF instead. Build only the new areas as modules, and port the CMS tabs later.
9. **Small extras:** defer the staff digest email and the weekly disk-usage alert. Drop the unused `stripe_customer_id` column. Self-hosted fonts are fine but not needed for launch.

## 2(c). UX and flow problems

1. **A family with a baby and a holiday-club child.**
   - The chooser forces one path, and "Add another child" repeats full-level sections 2 and 4–8. The baby then gets asked for a GP, a collection password and going home alone.
   - **Fix:** "Add child → What will they come to?" sets the level per child. Allow adding a child at either level from either type of account.
2. **A long form is thrown away when the email already exists** (§3.4). Imported families who ignore the activation email and click "Create account" lose all 9 sections.
   - **Fix:** follow mockup 3's stepper: ask for the email first and verify it. If the email already has an account, send a neutral email instead: the activation link for imported accounts, sign-in or reset for others. Save each step on the server.
3. **HAF "Not sure".** A "Not sure" answer makes HAF-only activities unbookable, with no next step. Today's process is to text "HAF Apply" and wait for staff to confirm eligibility (`holiday-club.html:36`).
   - **Fix:**
     - "Not sure" becomes "Request a HAF place": `pending_approval` plus a `haf_verify` in-tray item.
     - Staff get a "mark HAF verified" action, one at a time or in bulk from BCP's list.
     - A declined HAF request offers paid Summer Club days instead.
4. **Siblings on the waiting list.** Offers are made per child, so a family waiting with two children can be offered one place.
   - **Fix:** add a `waitlist_group` (per checkout). Offer only when the whole group fits, or offer explicitly "1 of 2".
5. **Choosing a whole week.** A 5-week club is 25 rows per child.
   - **Fix:** add "Select all days this week" per week group. Define the sticky-bar count as child-places.
6. **Age at the session vs age today.**
   - The chip shows "Maya (8)" today, but eligibility uses age at the first session.
   - Under the first-session basis, a child who turns 6 on 10 Aug is blocked for the whole summer.
   - **Fix:** show "6 from 10 Aug". Default multi-week holiday clubs to the per-session-date basis, and term blocks to the first-session basis.
7. **Signed-out visitors on `/book` are unspecified.** Chooser card 4 sends people to `/book?tab=events`, but Continue goes to the review page, which needs a login.
   - **Fix:** for guest-level sessions, "Book" goes to `/book/<slug>/guest?session=`. For all others, "Sign in or register to book" goes to `/register?for=<level>&next=`.
8. **Guest who later registers** (a12). Also: a registered parent who uses the guest form while signed out should have that booking attached to their account after verification, plus an email saying so.
9. **Imported families.**
   - The DOB challenge fails when the export has no child DOB, or when the account is an 18+ adult. **Fix:** fall back to postcode, then to staff-assisted activation.
   - Reconfirm consents on one screen for the whole family, not one per child.
   - Families who activate before bookings open need a "Bookings open on <date>" state.
10. **Separated parents or two carers.** One email per account and one account per child. Additional carer logins are Phase 2, so one parent loses self-service at launch.
    - **Fix:** check the export. If MB has more than one login per child, bring a minimal `account_users` table into Phase 1.
11. **Staff booking on behalf of incomplete profiles.** "Don't block" plus a register flag leaves drop-off children without first-aid or emergency-treatment consent.
    - **Fix:** require the walk-in minimum (one contact, allergies, first-aid consent recorded as `staff_verbal`).
12. **Teens aged 12–17 at drop-off sessions.** The short level's parent-stays callout is wrong for them, and full level asks for a collection password for a 16-year-old.
    - **Fix:** drive callouts from `activity.parent_must_stay`. Default going-home-alone for ages 11 and over.
13. **New voucher or Tax-Free Childcare families.** Pay-later is hidden until staff flag the family, which is a dead end for them.
    - **Fix:** an "I'll pay by vouchers or Tax-Free Childcare" option that creates a staff-approval in-tray item.
14. **Due date in the past.** A pay-later booking made within 3 days of the session gets `due_date` = first session − 3 days, before the issue date.
    - **Fix:** never set the due date before the issue date.
15. **Collection password at pick-up.** Staff type a password for each of about 40 children at 5pm, and the printed fallback register can't check passwords at all.
    - Get the DSL's sign-off (Q11) before PR 1.12, not at launch.

## 2(d). Phasing mistakes

1. **Governance comes too late.**
   - The DPIA, ROPA, Appropriate Policy Document and processor DPAs are in PR 1.20, and the privacy notice rewrite is in PR 1.17.
   - But special-category data is first collected at PR 1.3 go-live and through the PR 1.4 import, 3–4 weeks before bookings open.
   - **Fix:** make all of these a gate before any production registration or import commit.
2. **The cutover plan is missing** (a4). The runbook needs legacy re-keying and a step to close MB sales.
   - A February half-term launch still lands mid-term for continuous activities: Home Ed, the baby classes and Saturday Club all have sessions sold ahead.
3. **The event-to-activity link bypasses the cutover switch (§5.2).** As soon as an activity is published (needed weeks early for the activation campaign), the What's On "Book now" button jumps to `/book/<slug>`.
   - **Fix:** use the activity link only when `bookingUrl` points inside the site, or behind a `booking_live` flag.
4. **Merging duplicate accounts should move from Phase 2 into PR 1.14.** Walk-ins with only a mobile, guests, imports and self-registrations will create duplicates from day one.
5. **Trials (a7) and the e-shop (a8) are MagicBooking features.** Resolve Q4 and Q5 before switch-off, not after.
6. **The import (PR 1.4) is on the critical path but blocked by the unknown export format (Q15).** Request a sample export now.
7. **Registers (PR 1.12) and incidents (PR 1.13) are legally required on day one**, but they sit in the tail group after both engineers' chains.
   - **Fix:** schedule PR 1.12 before PR 1.11 (guest booking) and before the finance exports in PR 1.10.
8. **Split PR 1.16.**
   - Day one: service messages (session changes and cancellations, waiting-list SMS).
   - Can follow later: marketing campaigns and the newsletter double opt-in migration.
9. **The HAF export for BCP must move into PR 1.12.** It needs to exist before the first HAF period on the new system (Easter 2027 if launching at February half-term), so it can't be allowed to slip with PR 1.18.
10. **Take work out of the critical path** (2(b) items 1, 2 and 8):
    - out of Phase 1: self-service SAR export and long-range retention automation;
    - out of Phase 0: the full CMS port.
    - Together these save about 2 weeks.
11. **Additional carer logins may be launch-critical** (c10). Decide once the export arrives.

**Questions to add to §10:**
- What does the MB in-tray hold today?
- How many funded days does HAF allow per child, and do HAF and paid places share one room capacity?
- Does "0 to 1" mean under 2?
- Which registration level should 12–17 drop-off sessions use?
- Can MB export future bookings and outstanding credits?
- Does MB have more than one login per family?
- Is an optional name on guest bookings acceptable?

### Critical Files for Implementation
- server.py
- public/js/main.js (lines 31 and 217–219: gate the event-to-activity link on cutover)
- public/admin/admin.js (lines 229, 238, 785)
- public/holiday-club.html (lines 33, 36, 41: HAF daily places, the "HAF Apply" process, voucher lead time)
- data/content.json (lines 164–259: activity ages and prices, the mixed-age youth groups, cinema parties)

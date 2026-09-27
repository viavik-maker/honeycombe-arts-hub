# Data Protection Impact Assessment — online booking system (DRAFT)

> Draft for review by *[data protection lead]* and sign-off by the trustees. Based on the ICO's DPIA template
> structure. Not legal advice.

| | |
|---|---|
| Controller | Honeycombe Arts Hub (registered charity 1127371), Units 4 & 5, The Sovereign Shopping Centre, 600 Christchurch Road, Boscombe, Bournemouth BH1 4SX |
| Processing | In-house online booking and registration system replacing MagicBooking, built into honeycombeartshub.org.uk |
| DPIA owner | *[name, role]* |
| Version / date | 0.2 draft (adds the phase-2 features in §5a) — *[date]* |
| Review | Before launch; then yearly, and whenever a new kind of data or a new processor is added |

## 1. Why a DPIA is needed

The processing involves **special-category data about children**: health, SEND, SEMH, religion, and
safeguarding information. Some of that may be **criminal-offence data**, for example court orders or people
who must not collect a child. The processing is systematic, it involves vulnerable data subjects, and it uses
a new technology (a system built from scratch). Each of these is on the ICO's list of processing likely to be
high risk, so a DPIA is mandatory.

## 2. Description of the processing

**Nature.** Parents and carers create accounts and register their children. The level of detail depends on
the kind of activity:
- **full** for holiday clubs and drop-off sessions
- **short** for baby and toddler classes, where a parent stays
- **adult** for young adults aged 18 and over booking for themselves

Families book and pay. Staff take registers, log incidents and injuries, send service messages and, with
consent, marketing, and run reports. Families attending one-off events can book with an email address and
phone number only. Staff can also book for families and take payment at reception.

**Scope.** Data is collected at each registration level as follows.

| Data | Full | Short | Adult | Guest |
|---|---|---|---|---|
| Parent / carer: name, email, mobile, address, postcode | ✓ | ✓ (address optional) | own details | email + phone |
| Child: name, date of birth, gender (optional), school / home-educated, HAF status | ✓ | name + DOB | — | — |
| Emergency contacts (name, relationship, phone, can collect) | 2 | 1 (optional when a parent stays) | 1 | — |
| GP / doctor | ✓ | — | — | — |
| Health: allergies, anaphylaxis, medical conditions, medication, dietary, SEND, SEMH, religious requirements | ✓ | allergies + medical | optional | — |
| Family information relevant to safeguarding (DSL only); people who must not collect | ✓ | — | — | — |
| Permissions: photography, first aid, plasters, emergency treatment, going home alone | ✓ | photography | photography | — |
| Collection password (stored as a one-way hash, never displayed) | ✓ | — | — | — |
| Bookings, attendance, sign-in and sign-out, who collected | ✓ | ✓ | ✓ | party counts |
| Payments, invoices, credit notes (card details are held by Stripe, never by us) | ✓ | ✓ | ✓ | ✓ |
| Incident and injury records; safeguarding concerns (DSL only) | as needed | as needed | as needed | as needed |
| Messages sent (archive), marketing preferences, consent history | ✓ | ✓ | ✓ | ✓ |
| SEND support requests: needs, EHCP status, uploaded plans (EHCPs, care or behaviour plans), the SEND lead's notes and agreed support plan | as needed | — | — | — |
| Extra carers (up to 3 per account): name, email, sign-in | optional | optional | optional | — |
| Shop orders: items, amounts, collection or posting address | optional | optional | optional | — |
| Staff accounts: name, email, roles, sign-in history, audit log | — | — | — | — |

The system is expected to hold data on about *[number]* families and *[number]* children. Sessions and
bookings run throughout the year.

**Context.**
- Data subjects are children aged 0–17 (under-18s are always registered by a parent or carer), their parents
  and carers, emergency contacts, young adults aged 18–25, and staff.
- Some families receive free school meals (HAF eligibility), and some children have SEND or safeguarding
  concerns.
- Families will expect this information to be used to keep their child safe, and not shared beyond what is
  necessary.

**Purposes.**
1. Running safe activities: registration, bookings, registers, collection and emergency contact.
2. Meeting our duties under the Ofsted Childcare Register and safeguarding guidance.
3. Taking and recording payments.
4. HAF administration with BCP Council.
5. Service communications.
6. Marketing, with consent only.
7. Reporting to funders, anonymised unless the family consents.
8. Security and accountability (audit).

## 3. Consultation

- *[Record who was consulted: DSL, SEND lead, session staff, trustees, a sample of parents (e.g. via the Youth
  Advisory Board or a parent survey), the hosting and development contact.]*
- The system design was reviewed independently for requirement coverage and for security and data
  protection. See `docs/booking/reviews/`.

## 4. Necessity and proportionality

- **Lawful bases.** See [ropa.md](ropa.md).
  - A child's data is **not** processed on the basis of "contract", because the contract is with the parent.
  - For Ofsted-registered provision the basis is **legal obligation**.
  - Otherwise it is **legitimate interests**, with a documented LIA.
  - Special-category data uses DPA 2018 Sch 1 para 18 (safeguarding of children), backed by the
    [Appropriate Policy Document](appropriate-policy-document.md). The optional health fields for adults
    use explicit consent.
  - HAF sharing is never presented as consent.
- **Minimisation.** Each registration level asks only for what that kind of activity needs:
  - baby and toddler classes (a parent stays) don't ask for a GP, collection password or going-home-alone
    permission;
  - one-off events need only an email address and phone number;
  - the MagicBooking import brings identity and contact details only, and families re-enter health details
    when they activate their account.
- **Accuracy.** Parents can update their details at any time. Imported records are flagged "needs review"
  until the parent confirms them. Health changes close to a booked session alert staff.
- **Retention.** See [retention-schedule.md](retention-schedule.md).
- **Rights.**
  - Parents can update their details, unsubscribe and delete their account themselves.
  - Access requests are handled by staff from the parent's record, with the exemptions set out in the APD.
  - Rights to object and restrict are handled via the data protection lead.
- **Processors and transfers.** See [processors.md](processors.md). Hosting is in Render's Frankfurt (EU)
  region, email goes through Brevo (EU) and off-site backups sit in Backblaze B2's Amsterdam region. Stripe and
  Twilio are covered by DPAs with the UK Addendum.

## 5. Risks

Likelihood (L) and severity (S) are each rated 1–3. Residual risk is the level left after the mitigations.

| # | Risk to individuals | L | S | Mitigations built into the system | Residual |
|---|---|---|---|---|---|
| 1 | Unauthorised staff access to a child's health or safeguarding details | 2 | 3 | Individual accounts with two-step sign-in; roles and least privilege; safeguarding information visible to DSLs only; session staff see health flags only for their sessions, and only through the register; every view of health or safeguarding data is audited; alert on unusual volumes of views | Low |
| 2 | Account takeover of a parent account, exposing a child's record or changing who may collect | 2 | 3 | Strong password rules; throttled sign-in; email verified before any child data is entered; re-authentication, plus an email and SMS alert, before collection details change; staff alerted if the child is booked within 7 days | Low–medium |
| 3 | Mis-typed email address sends a child's details to a stranger | 2 | 3 | Email verified by code before child sections are collected; emails never contain health or safeguarding details; activation emails never name children; activation requires a child's date of birth | Low |
| 4 | A child released to the wrong adult | 1 | 3 | Collection password checked on a tablet at the door (hashed, never shown or printed); after 5 wrong tries staff must use the phone check; "must not collect" alert shown on the register; account holders and named collectors only; going home alone only from age 11 and with the parent's permission; audit of every check | Low |
| 5 | Breach of the hosting server or its backups | 1 | 3 | HTTPS everywhere; security headers and CSP; no secrets in the repository; backups encrypted to a key the server doesn't hold; hosting-account MFA; Render states its disks are encrypted at rest (confirm on Render's security page) | Low–medium (accepted: no field-level encryption, see §6) |
| 6 | Loss of data (disk failure, deletion, ransomware) | 1 | 3 | Nightly backups kept 7 days on the server; encrypted off-site copies in Backblaze B2 locked for 35 days (Object Lock), uploaded with a key that can't delete; Render disk snapshots; restore drill before launch | Low |
| 7 | Over-retention, especially of children's records | 2 | 2 | Retention schedule enforced by nightly jobs; legal holds for incidents and safeguarding; deletion reviewed by the DSL where a hold applies | Low |
| 8 | Marketing without valid consent (PECR) | 2 | 1 | Separate marketing opt-ins with the wording version recorded; one-click unsubscribe; service messages can't be sent as marketing | Low |
| 9 | Health or incident details in SMS or email passing through providers | 2 | 2 | Messages say only "we've added a note — sign in or call"; templates reviewed; staff guidance | Low |
| 10 | Sharing identifiable data with funders without a basis | 1 | 2 | Funder exports include identifiable data only where the family said yes to the separate funder-sharing question, and only for our named funders *[list funders]*; anonymised statistics suppress counts under 5; HAF sharing with BCP Council is never based on consent | Low |
| 11 | International transfers (US-owned processors) | 2 | 2 | Hosting (Render) in Frankfurt and backups (Backblaze B2) in Amsterdam, so data is stored in the EU; email via Brevo (EU); Twilio and Stripe under DPAs with the UK Addendum; texts and Stripe never carry health or children's details; see processors.md | Low |
| 12 | Children's rights and understanding (Children's Code) | 1 | 2 | No analytics or tracking; high-privacy defaults; child-friendly explanation in the privacy notice; parent registers under-18s | Low |

## 5a. Phase-2 features

Same scoring as §5. Numbering continues from the table above.

### SEND support requests and uploaded plans

A signed-in parent tells our SEND lead about their child's needs and can upload plans (EHCPs, care or behaviour
plans: PDF, Word, JPEG or PNG, up to 10 MB). The SEND lead gets in touch, then records an agreed support plan; a
short summary appears on registers so session staff know what helps.

- Uploads go to a **private file store** outside the public website, under random names. There is no web
  address that serves them directly: they come back only through routes that check who is asking.
- Only the family who uploaded them and staff with **send.view** (owner, admin, manager, DSL, SEND lead) can
  open them. Downloads are always saved as files, never shown in the browser, and **every staff download is
  audited**.
- File types are checked from the contents, not the name; location and camera details are removed from photos.
- **The files are not encrypted at rest on the server beyond the host's disk encryption** (Render's). They are
  in the nightly backups, which are encrypted before they leave the server.

| # | Risk to individuals | L | S | Mitigations built into the system | Residual |
|---|---|---|---|---|---|
| 13 | Unauthorised staff access to an EHCP or other plan (diagnoses, family circumstances, professionals' reports) | 1 | 3 | send.view only; two-step sign-in; every view and download audited; the SEND lead is told a request has arrived without any details | Low |
| 14 | Server or disk compromise exposes uploaded plans in readable form | 1 | 3 | Private folder outside the web root; host disk encryption at rest; backups encrypted to the trustees' key; hosting-account MFA. No file-level encryption: same accepted risk as §6 | Low–medium (accepted) |
| 15 | Parents upload more than is needed (whole reports, other family members' details) | 2 | 2 | The form asks only for plans that help us understand the child's needs; families can delete their uploads; uploads never attached to a request are deleted automatically; everything goes when the account is erased | Low |
| 16 | A harmful file is uploaded and opened by staff | 1 | 2 | Contents checked against the allowed types; size limit; upload rate limit; always downloaded, never opened in the browser | Low |

### Extra carer logins

The account holder can invite up to three extra carers (for example a grandparent or partner), each with
their own email and password. Carers can see the family's children and bookings, and can **book, pay, cancel**,
report absences and read incident notes. They **can't** see the parent's confidential safeguarding notes, and
can't change family or child details, consents, collection arrangements, news preferences or carers, or ask for
the family's data or delete the account.

| # | Risk to individuals | L | S | Mitigations built into the system | Residual |
|---|---|---|---|---|---|
| 17 | A carer sees information the parent gave in confidence | 1 | 3 | Safeguarding notes are never shown to carers; only the holder can invite a carer, and the holder sees and can remove them | Low |
| 18 | A carer's login is taken over, or a carer misuses access (e.g. after a separation) | 1 | 2 | Carers can't change who may collect, collection passwords or contact details; removing a carer signs them out at once; refunds go back to the card that paid; audit of every action | Low |

### Turning 18: handing the record to the young person

On a child's 18th birthday the parent is told by email, and staff see it in their in-tray. The parent can hand
the record over by giving the young person's own email address; the young person sets up their own account
from a link that lasts 14 days, and **the record moves to their account**. The parent's consents stop counting
and the young person gives their own; the collection password and going-home-alone answer are cleared. **The
parent's confidential notes, including any "must not collect" note, stay with the DSL and don't move.** The
parent is told when the handover is done. If nothing happens, the child leaves the parent's account after 90
days (once anything already booked has happened).

| # | Risk to individuals | L | S | Mitigations built into the system | Residual |
|---|---|---|---|---|---|
| 19 | The young person reads what their parent told us in confidence | 1 | 3 | Confidential notes stay DSL-only and are never shown on the young person's account | Low |
| 20 | The record goes to the wrong person (a mistyped or someone else's email) | 1 | 3 | The parent can't use their own address; the link expires after 14 days and works once; the young person must set a password; the parent is told when it's done | Low |
| 21 | A parent keeps seeing an adult's details indefinitely | 2 | 1 | The record leaves the parent's account 90 days after the birthday (setting) | Low |

### Offline registers on tablets

So registers keep working if the connection drops, staff can download **today's registers only** onto a tablet
while online.

- The copy holds what the printed register shows: names, health flags, emergency numbers, and the **collection
  alerts and "check with the DSL" flags**, which are included because staff need them at the door.
- It holds **no passwords** (collection passwords can't be checked offline: staff use the phone check) and **no
  safeguarding notes**.
- It is encrypted with **AES-GCM**, under a key made from a **staff PIN of at least 8 digits**. The key is held
  only in memory and the screen locks after 15 minutes.
- The copy is **wiped the next day** (once any offline changes have been sent) and **after 5 wrong PINs**.

| # | Risk to individuals | L | S | Mitigations built into the system | Residual |
|---|---|---|---|---|---|
| 22 | A tablet is lost or stolen and the PIN is guessed, exposing that day's children's health flags, emergency numbers and collection alerts | 1 | 3 | Only one day's registers; no passwords or safeguarding notes; AES-GCM with a slow key derivation; 5 wrong PINs wipe it; wiped the next day. **Also needed (staff practice):** a device passcode and the tablet's own encryption switched on; remote wipe (Find My / Find My Device) set up; tablets locked away after sessions; PINs never written down or shared; a longer PIN (10+ digits) encouraged; a lost tablet reported at once under the breach runbook. Someone who copies the tablet's storage could try PINs without the 5-try limit, which is why the device passcode and one-day lifespan matter | Low–medium |
| 23 | The offline copy is out of date (e.g. a new collection alert added after download) | 1 | 3 | Staff download again before each session; changes sync as soon as there's a connection; the DSL is told of urgent alerts by phone | Low |

### The shop

Families can buy items (T-shirts, art packs, gift vouchers). It is off until switched on in Booking settings.
Every order is an invoice, handled like any other in Finance, and kept for 6 years after the end of the
financial year. **No special-category data** is involved. Stripe receives only the order reference, amount
and the payer's email.

| # | Risk to individuals | L | S | Mitigations built into the system | Residual |
|---|---|---|---|---|---|
| 24 | Order details used or kept beyond need | 1 | 1 | Orders use the finance retention and access rules; no children's details or health data | Low |

## 6. Decisions and accepted risks

- **Field-level encryption is not implemented** (this includes uploaded SEND plans). The Python standard
  library has no AES, and hand-written cryptography would add risk. We rely instead on:
  - the hosting provider's encryption of disks at rest (Render states this; confirm on Render's security page);
  - role-based access and audit;
  - backups encrypted to the trustees' offline key;
  - keeping special-category data out of emails, texts and Stripe.

  Recommended: accept. The trustees record their decision in §7.
- **Hosting region.** Adopted: the Render service is re-created in the **Frankfurt (EU)** region, on the
  paid plan, before launch, so data is stored in the EU. Render's DPA (with the UK Addendum) is accepted
  in the dashboard.
- **Collection password.** It is stored hashed and checked on a tablet at the door; it is never shown or
  printed. After 5 wrong tries, or if the tablet is offline, staff use the phone check instead.
  *[DSL to confirm.]*
- **Going home alone** is allowed from age 11, and only with the parent's permission.
- **Two-step sign-in** is required for every staff member.

## 7. Sign-off

| | Name | Date | Decision |
|---|---|---|---|
| Measures approved by | *[DP lead]* | | |
| Residual risks approved by | *[trustees]* | | |
| DPO / DP lead advice | | | |
| Review date | | | |

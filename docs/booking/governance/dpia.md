# Data Protection Impact Assessment — online booking system (DRAFT)

> Draft for review by *[data protection lead]* and sign-off by the trustees. Based on the ICO's DPIA template
> structure. Not legal advice.

| | |
|---|---|
| Controller | Honeycombe Arts Hub (registered charity 1127371), Units 4 & 5, The Sovereign Shopping Centre, 600 Christchurch Road, Boscombe, Bournemouth BH1 4SX |
| Processing | In-house online booking and registration system replacing MagicBooking, built into honeycombeartshub.org.uk |
| DPIA owner | *[name, role]* |
| Version / date | 0.1 draft — *[date]* |
| Review | Before launch; then yearly, and whenever a new kind of data or a new processor is added (e.g. the phase-2 SEND intake with document uploads **requires this DPIA to be updated first**) |

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
- **Processors and transfers.** See [processors.md](processors.md).

## 5. Risks

Likelihood (L) and severity (S) are each rated 1–3. Residual risk is the level left after the mitigations.

| # | Risk to individuals | L | S | Mitigations built into the system | Residual |
|---|---|---|---|---|---|
| 1 | Unauthorised staff access to a child's health or safeguarding details | 2 | 3 | Individual accounts with two-step sign-in; roles and least privilege; safeguarding information visible to DSLs only; session staff see health flags only for their sessions, and only through the register; every view of health or safeguarding data is audited; alert on unusual volumes of views | Low |
| 2 | Account takeover of a parent account, exposing a child's record or changing who may collect | 2 | 3 | Strong password rules; throttled sign-in; email verified before any child data is entered; re-authentication, plus an email and SMS alert, before collection details change; staff alerted if the child is booked within 7 days | Low–medium |
| 3 | Mis-typed email address sends a child's details to a stranger | 2 | 3 | Email verified by code before child sections are collected; emails never contain health or safeguarding details; activation emails never name children; activation requires a child's date of birth | Low |
| 4 | A child released to the wrong adult | 1 | 3 | Collection password checked at the door (hashed, never shown or printed); "must not collect" alert shown on the register; account holders and named collectors only; audit of every check | Low |
| 5 | Breach of the hosting server or its backups | 1 | 3 | HTTPS everywhere; security headers and CSP; no secrets in the repository; backups encrypted to a key the server doesn't hold; hosting-account MFA; disk encryption at rest (to confirm with Render) | Low–medium (accepted: no field-level encryption, see §6) |
| 6 | Loss of data (disk failure, deletion, ransomware) | 1 | 3 | Nightly off-site encrypted backups with object lock; Render disk snapshots; restore drill before launch | Low |
| 7 | Over-retention, especially of children's records | 2 | 2 | Retention schedule enforced by nightly jobs; legal holds for incidents and safeguarding; deletion reviewed by the DSL where a hold applies | Low |
| 8 | Marketing without valid consent (PECR) | 2 | 1 | Separate marketing opt-ins with the wording version recorded; one-click unsubscribe; service messages can't be sent as marketing | Low |
| 9 | Health or incident details in SMS or email passing through providers | 2 | 2 | Messages say only "we've added a note — sign in or call"; templates reviewed; staff guidance | Low |
| 10 | Sharing identifiable data with funders without a basis | 1 | 2 | Funder exports include identifiable data only where consent was given (asked per funder); anonymised statistics suppress counts under 5 | Low |
| 11 | International transfers (US-owned processors) | 2 | 2 | UK or EU hosting region where available; UK Addendum or UK–US data bridge in DPAs; see processors.md | Low–medium |
| 12 | Children's rights and understanding (Children's Code) | 1 | 2 | No analytics or tracking; high-privacy defaults; child-friendly explanation in the privacy notice; parent registers under-18s | Low |

## 6. Decisions and accepted risks

- **Field-level encryption is not implemented.** The Python standard library has no AES, and hand-written
  cryptography would add risk. We rely instead on:
  - the hosting provider's encryption of disks at rest *[confirm with Render]*;
  - role-based access and audit;
  - backups encrypted to the trustees' offline key;
  - keeping special-category data out of emails, texts and Stripe.

  *[Trustees to accept or reject.]*
- **Hosting region.** *[Decide: recreate the Render service in the EU (Frankfurt) region before launch
  (recommended), or keep it in the US under the UK Addendum or UK–US data bridge.]*
- **Collection password.** It is stored hashed and checked at the door. If the tablet is offline, staff fall
  back to the phone-verification procedure. *[DSL to confirm.]*

## 7. Sign-off

| | Name | Date | Decision |
|---|---|---|---|
| Measures approved by | *[DP lead]* | | |
| Residual risks approved by | *[trustees]* | | |
| DPO / DP lead advice | | | |
| Review date | | | |

# Record of Processing Activities — booking system (DRAFT)

> UK GDPR Article 30. Draft for *[data protection lead]* to confirm. Not legal advice.

**Controller:** Honeycombe Arts Hub, registered charity 1127371. **Contact:** *[data protection lead]*,
info@honeycombeartshub.org.uk.

| Activity | Data subjects | Personal data | Purpose | Lawful basis (Art 6) | Special category / criminal data condition | Recipients | Retention |
|---|---|---|---|---|---|---|---|
| Family accounts and registration | Parents and carers, children, emergency contacts, young adults | Identity, contact, DOB, school, emergency contacts, GP | Running activities safely; contacting families | Legal obligation (Ofsted Childcare Register) for registered provision; otherwise legitimate interests (LIA-1). The parent's own account details: contract | — | Staff (by role); Render (hosting) | See retention schedule |
| Health, SEND, SEMH, dietary and religious information | Children; young adults | Allergies, medical, medication, SEND, SEMH, dietary, religious | Keeping children safe; meeting their needs | As above | Children: Art 9(2)(g) + DPA 2018 Sch 1 para 18 (safeguarding of children and individuals at risk), APD. Adults (optional fields): Art 9(2)(a) explicit consent | Staff by role (session staff: register only) | See retention schedule |
| Safeguarding family information; "must not collect" alerts | Children, parents, third parties | Family circumstances; court orders | Safeguarding | Legal obligation / legitimate interests (LIA-2) | Art 9(2)(g) + Sch 1 para 18; Art 10 + Sch 1 Part 3 para 36 (APD) | DSLs only (alerts shown to register staff) | Until at least the child's 25th birthday, reviewed by the DSL |
| Bookings, registers and attendance | Children, parents, collectors | Bookings, sign-in and sign-out times, who collected | Running sessions; Ofsted record-keeping | Legal obligation / legitimate interests | — (a HAF flag is not health data) | Staff | Registers 3 years; HAF per grant |
| Incidents, injuries and concerns | Children, staff, witnesses | Incident details, first aid, notifications | Duty of care; Ofsted and RIDDOR; defence of claims | Legal obligation; legitimate interests | Art 9(2)(g)/(f) + Sch 1 paras 18, 6; APD | Staff by role; DSL; Ofsted, RIDDOR and insurers where required | Accidents: child's DOB + 25 years; concerns: DSL review |
| Payments and finance | Parents, guests | Invoices, payments, refunds (card data held by Stripe only) | Charging for activities; charity accounts | Contract; legal obligation (Charities Act, HMRC) | — | Stripe (processor); treasurer and accountant | 6 years after the end of the financial year |
| HAF administration | Eligible children and parents | Eligibility, attendance, SEND count, age band | Holiday Activities and Food programme | *[Settle with BCP Council: the charity's basis is contract (grant) or public task; HAF sharing is never consent]* | Art 9 only if health data is shared: *[confirm what BCP requires]* | BCP Council | *[Per grant agreement]* |
| Funder reporting | Children and families | Anonymised statistics; identifiable data only with consent | Grant reporting | Legitimate interests (anonymised); consent (identifiable, per funder) | — | Named funders *[list]* | Per funder agreement |
| Service messages | Parents, guests, staff | Contact details, message content | Booking confirmations, changes, reminders | Contract / legitimate interests | — | Email provider; Twilio | Message bodies 24 months |
| Marketing (newsletter, events) | Subscribers who opted in | Email, mobile, name, preferences | Promoting activities | Consent (PECR reg 22) | — | Email provider; Twilio | Until the person unsubscribes, plus a suppression record |
| Photography | Children | Images | Publicity, portfolios | Consent (the parent, via the merged photography question) | — | Website, social media, press (per the option chosen) | Until consent is withdrawn |
| Staff accounts and audit | Staff | Name, email, roles, sign-ins, audit entries | Security; accountability | Legitimate interests (LIA-3) | — | Render | Audit 6 years; accounts while employed + *[x]* |
| Contact form | Enquirers | Name, email, phone, message | Answering enquiries | Legitimate interests | — | Email provider | *[24 months]* |
| Backups | All of the above | All of the above | Recovery | As for the source data | As for the source data | Backup storage provider (encrypted; the provider can't read it) | 7 days on the server; *[35 days]* off-site |

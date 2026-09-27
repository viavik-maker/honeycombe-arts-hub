# Data protection pack for the booking system (DRAFT)

> **Status: draft for Honeycombe Arts Hub to review and adopt.** These were prepared alongside the software
> so the charity's data protection lead and trustees have a solid starting point. They are **not legal advice**.
> Where there's a sensible default (providers, hosting region, retention periods), our recommendation is
> written in as adopted, and the charity only needs to change it if it disagrees. Anything left in
> *[square brackets]* is something only the charity can supply: names, dates, signatures, numbers, the ICO
> registration, HAF grant details, named funders and the DSL's confirmation. Where these drafts and the
> charity's own policies (the Policy Handbook) disagree, the policies win and these should be updated.

## The gate: nothing real goes in until these are done

The booking system will hold children's health, SEND, SEMH, religious and safeguarding information. It is
special-category data, and some of it may be criminal-offence data (court orders, people who must not
collect a child). UK GDPR requires the charity to assess and document this **before** it starts. Only
then should real families register, or the MagicBooking export be imported.

| # | Item | Document | Who | Done |
|---|---|---|---|---|
| 1 | Data Protection Impact Assessment completed, risks accepted by trustees | [dpia.md](dpia.md) | DP lead + trustees | ☐ |
| 2 | Record of Processing Activities adopted | [ropa.md](ropa.md) | DP lead | ☐ |
| 3 | Appropriate Policy Document (special-category & criminal-offence data) adopted | [appropriate-policy-document.md](appropriate-policy-document.md) | DP lead + trustees | ☐ |
| 4 | Legitimate interests assessments signed off | [legitimate-interests.md](legitimate-interests.md) | DP lead | ☐ |
| 5 | Retention schedule agreed (incl. HAF grant conditions) | [retention-schedule.md](retention-schedule.md) | DP lead + DSL + treasurer | ☐ |
| 6 | Processor agreements in place (Render in Frankfurt, Stripe, Brevo, Twilio, Backblaze B2 in Amsterdam) and transfer safeguards checked | [processors.md](processors.md) | DP lead | ☐ |
| 7 | Breach procedure and contact tree agreed; staff briefed | [breach-runbook.md](breach-runbook.md) | DP lead + DSL | ☐ |
| 8 | Privacy notice rewritten and published (the software change ships with the site) | public/privacy.html | DP lead | ☐ |
| 9 | Off-site encrypted backups configured and a restore practised | README → Backups | whoever manages hosting | ☐ |
| 10 | Two-step sign-in for every staff account; staff accounts reviewed (leavers removed) | admin → Staff | owner | ☐ |
| 11 | MFA and least privilege on Render, GitHub, Stripe, Twilio, Brevo and Backblaze accounts | [processors.md](processors.md) | owner | ☐ |
| 12 | Staff briefing: what may go in which field, never putting health details in free-text notes or messages, incident logging | — | DP lead + DSL | ☐ |
| 13 | ICO registration (data protection fee) up to date: registration number *[number]* | ico.org.uk | treasurer | ☐ |

Named roles to fill in: data protection lead *[name]*; Designated Safeguarding Lead *[name]*, deputies
*[names]*; SEND lead *[name]*; senior information risk owner (a trustee) *[name]*.

## How the software supports these documents

| Requirement | Where it lives in the system |
|---|---|
| Access limited by role | Staff roles and permissions (admin → Staff). Safeguarding information is visible to DSLs only, and session staff see health flags only for their sessions. |
| Accountability | Append-only audit log (admin → Audit log). It records who viewed or changed what and when, logs field names only (never values), and keeps entries for 6 years. |
| Security | Individual staff accounts with two-step sign-in (required for every staff member); security headers and a Content-Security-Policy; rate limits; logs that never contain one-time links. |
| Data minimisation | Each registration level asks only for what that kind of activity needs. The MagicBooking import brings across identity and contact details only. |
| Integrity and availability | SQLite transactions; nightly backups kept 7 days on the server, and encrypted off-site copies kept 35 days in Backblaze B2 (the server can't decrypt them); a restore procedure. |
| Retention and erasure | Account deletion with legal-hold exceptions, and nightly retention jobs (see the retention schedule). |
| Consent records | Versioned, time-stamped, append-only consent history per child. |
| Marketing (PECR) | Service and marketing messages are separate, marketing needs an opt-in, and every marketing email has a one-click unsubscribe. |

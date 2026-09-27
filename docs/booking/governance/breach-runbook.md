# Personal data breach runbook (DRAFT)

> What to do if personal data may have been lost, stolen, seen by the wrong person, changed or destroyed.
> Draft for *[data protection lead]* and the DSL. Keep a printed copy with the safeguarding folder.

## 0. Contact tree

| Role | Name | Phone |
|---|---|---|
| Data protection lead | *[ ]* | *[ ]* |
| Designated Safeguarding Lead | *[ ]* | *[ ]* |
| Trustee (senior information risk owner) | *[ ]* | *[ ]* |
| Whoever manages the hosting / code | *[ ]* | *[ ]* |

## 1. Contain (straight away)

- **A staff account may be compromised:**
  - admin → Staff → *Disable*, which signs them out immediately;
  - then *Reset password & 2FA* once it's safe.
- **Several accounts, or you're unsure:** ask the hosting contact to **sign out everyone and rotate secrets**.
  1. Delete all rows from `staff_sessions` (and later the parent sessions table).
  2. Rotate `HAH_SECRET_KEY`, `HAH_PEPPER`, the Stripe keys, the Twilio token, the Brevo SMTP key and the
     Backblaze application key in Render → Environment.
  3. Redeploy.
- **A lost or stolen device** with the admin open or printed registers:
  - disable that person's account and reset it;
  - list what the printed registers contained (flags and emergency numbers, never passwords or safeguarding
    detail).
- **A lost or stolen tablet holding offline registers:**
  - the copy is today's registers only, encrypted with the staff member's PIN (8+ digits), and wiped after 5
    wrong PINs or the next day;
  - disable the staff member's account and reset it;
  - list the children on that day's registers: their health flags, emergency numbers, collection alerts and
    "check with the DSL" flags were on it. Tell the DSL about any collection alerts at once;
  - treat it as a breach to assess (step 2), even though the data is encrypted.
- **An email sent to the wrong person:** ask them to delete it and confirm in writing.
- **Suspected server breach:** keep evidence. Don't wipe the server. Take a copy of the logs first. Contact
  Render support. The off-site backups in Backblaze B2 are locked for 35 days and can't be changed or deleted
  from the server, so they are a clean copy to restore from.

## 2. Assess (within 24 hours)

Use the audit log (admin → Audit log) to find out what was viewed or changed, by which account, and when.
Then decide:
- Whose data is affected? How many people? Is it children's data? Special-category data?
- What harm could result? (Safeguarding risk, discrimination, distress, financial loss.)

## 3. Notify

- **The ICO within 72 hours** of becoming aware, unless the breach is *unlikely to result in a risk* to
  people. Report on ico.org.uk or call 0303 123 1113. If some facts are still unknown, report what you know
  and follow up.
- **The people affected, without undue delay**, if there's a *high risk* to them. Children's safeguarding
  data usually is high risk: involve the DSL in deciding how to tell families.
- **Others as needed:**
  - Ofsted, if it affects registered provision or safeguarding;
  - the police, if a crime is involved;
  - the insurer;
  - BCP Council, if HAF data is involved;
  - funders, if their contract requires it;
  - the processors involved (Render, Stripe, Brevo, Twilio or Backblaze: see [processors.md](processors.md)).

## 4. Record (always, even if not reported)

Record every breach and near miss in the breach log (UK GDPR Art 33(5)):

| Date found | What happened | Data / people affected | Harm assessment | Actions taken | Reported to ICO? (date / why not) | People told? | Lessons |
|---|---|---|---|---|---|---|---|
| | | | | | | | |

## 5. Learn

Review what happened with the trustees. Update this runbook, the DPIA and staff training.

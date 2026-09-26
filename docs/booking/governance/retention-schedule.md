# Retention schedule — booking system (DRAFT)

> Defaults proposed for the charity to confirm. They follow the IRMS schools toolkit, HMRC and Charity Commission
> record-keeping requirements, and common practice for childcare providers. *[Check HAF grant conditions and the
> charity's insurer's requirements.]* Not legal advice.

| Record | Keep for | Then | Enforced by |
|---|---|---|---|
| Parent / young adult account (inactive) | 3 years after last booking or sign-in | Warning email, then anonymised after 30 days | Nightly job (phase 2 automation; manual review until then) |
| Account closed by the family ("delete my account") | 14-day cooling-off period | Erased, except records under a hold below | Nightly job |
| Unverified registrations | 7 days | Deleted | Nightly job |
| Imported MagicBooking accounts never activated | 12 months | Deleted | Nightly job |
| Guest (one-off event) contact details | 12 months after their last event | Deleted | Nightly job |
| Child's health, GP, consents and collection details | While the account is open | Deleted with the account, unless part of an incident record | With account erasure |
| Registers and attendance | 3 years after the session *[HAF: per grant, possibly 6+ years]* | Link to the named child removed; anonymised counts kept for reports | Nightly job |
| Accident and injury records (children) | Until the child's date of birth + 25 years | Deleted after review | Legal hold on the child's minimal record |
| Safeguarding concerns and family information | At least until the child's 25th birthday | DSL review; **never deleted automatically** | Legal hold; DSL in-tray |
| Invoices, payments, credit notes, refunds | 6 years after the end of the financial year | Deleted (bill-to name snapshots kept until then) | Nightly job |
| Message archive (bodies) | 24 months | Body removed; delivery record kept | Nightly job |
| Marketing unsubscribes | Indefinitely (the email address only, as a suppression list) | — | — |
| Consent history | While the account exists, plus 6 years | Deleted | With account erasure |
| Audit log | 6 years | Deleted (the database blocks earlier deletion) | Database trigger + nightly job |
| Staff accounts | While employed + *[1 year]* | Disabled at leaving; deleted later | Manual (admin → Staff) |
| Contact-form messages | *[24 months]* | Deleted | *[Nightly job — to add]* |
| Raw import rows (MagicBooking file) | 30 days after import | Deleted; also delete any copies on staff computers | Nightly job |
| Local server backups | 7 days | Deleted | Nightly backup job |
| Off-site encrypted backups | *[35 days]* (object lock) | Expire | Storage bucket lifecycle rule |

**Erasure requests.** The rest of the family's data is erased as above. Records under a legal hold are reduced
to the minimum (for example the child's name, date of birth and the incident itself) and kept only for the
hold period. The family is told what is kept and why.

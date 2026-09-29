# Retention schedule — booking system (DRAFT)

> Defaults proposed for the charity to confirm. They follow the IRMS schools toolkit, HMRC and Charity Commission
> record-keeping requirements, and common practice for childcare providers. *[Check HAF grant conditions and the
> charity's insurer's requirements.]* Not legal advice.

| Record | Keep for | Then | Enforced by |
|---|---|---|---|
| Parent / young adult account (inactive) | 3 years after last booking or sign-in | Warning email, then closed after 30 days and erased | Nightly job (`retention_long`) |
| Account closed by the family ("delete my account") | 14-day cooling-off period | Erased, except records under a hold below | Nightly job |
| Unverified registrations | 7 days | Deleted (an 18+ sign-up's own record with it) | Nightly job |
| A child who turns 18 in a parent's account | Parent told on the birthday | Handed to their own account if the parent asks; otherwise archived from the parent's account after 90 days (setting) | Daily job (`turning_18`) |
| Imported MagicBooking accounts never activated | 12 months, unless something is booked in the last year or to come | Closed and erased | Nightly job |
| Guest (one-off event) contact details | 12 months after their last event | Deleted | Nightly job |
| Child's health, GP and collection details | While the account is open | Deleted with the account (a child kept for an incident record keeps only name, date of birth and the incident) | With account erasure |
| Registers and attendance | 3 years after the session *[HAF: per grant, possibly 6+ years]* | Link to the named child removed; anonymised counts kept for reports | Nightly job |
| Accident and injury records | Until the child's date of birth + 25 years, and never less than 7 years after the incident (the recommended default for insurers; this is what applies to adults) | Details cleared | Nightly job; legal hold on the person's minimal record (name, date of birth, the incident) |
| Safeguarding concerns and family information | At least until the child's 25th birthday | DSL review; **never deleted automatically** | Legal hold; DSL in-tray |
| Invoices, payments, credit notes, refunds | 6 years after the end of the financial year (the reporting year in Booking settings) | Anonymised once the family's account has been erased: bill-to name, email, address and children's names removed; numbers and amounts kept for the accounts | Nightly job |
| Message archive (bodies) | 24 months (setting) | Body removed; delivery record kept | Nightly job |
| Marketing unsubscribes | Indefinitely (the email address and unsubscribe dates only, as a suppression list) | — | Kept on erasure |
| Consent history | While the account exists, plus 6 years | Deleted | Kept on erasure; nightly job deletes it 6 years later |
| Audit log | 6 years | Deleted (the database blocks earlier deletion) | Database trigger + nightly job |
| Staff accounts | While employed + 1 year | Disabled at leaving; deleted later | Manual (admin → Staff) |
| Contact-form messages | 24 months (the same setting as the message archive) | Deleted | Nightly job |
| Raw import rows (MagicBooking file) | 30 days after import | Deleted; also delete any copies on staff computers | Nightly job |
| Local server backups | 7 days | Deleted | Nightly backup job |
| Off-site encrypted backups | 35 days (object lock) | Expire | Storage bucket lifecycle rule |

**Erasure requests.** The rest of the family's data is erased as above. Records under a legal hold are reduced
to the minimum (for example the child's name, date of birth and the incident itself) and kept only for the
hold period. The family is told what is kept and why.

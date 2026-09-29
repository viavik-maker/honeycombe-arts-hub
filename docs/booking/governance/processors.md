# Processors and international transfers (DRAFT)

> Every service that handles personal data for us needs a contract with data processing terms (UK GDPR
> Art 28), and a transfer safeguard where data leaves the UK. The providers below are our recommended
> defaults; the charity can change any of them. Draft for *[data protection lead]* to check against each
> provider's current terms. Not legal advice.

| Provider | What it processes | Location / transfer | Contract | Account security |
|---|---|---|---|---|
| **Render** (Render Services, Inc., US) — hosting, database, local backups | Everything in the system | **Frankfurt (EU) region.** The service is re-created there before launch, so the data is stored in the EU. Render is a US company, so its DPA with the UK Addendum (IDTA) covers any access from the US. Render states that disks are encrypted at rest: confirm this on Render's security page | Render DPA, accepted in the Render dashboard | MFA for every team member; minimum team; record who has shell access |
| **Stripe** (Stripe Payments UK Ltd) — card payments | Booking or order references, amounts and the payer's email. **Never children's names or health details** | UK / EU, with onward transfers to the US covered by Stripe's UK Addendum and Data Privacy Framework certification | Stripe DPA (part of the Stripe services agreement) | MFA; restricted API keys; account in the charity's name |
| **Brevo** (Sendinblue SAS, France) — sending email by SMTP relay from a sending address such as bookings@honeycombeartshub.org.uk | Recipients' names and email addresses; message content (never health or safeguarding details) | **EU.** Data stays in the EU, which the UK recognises as adequate | Brevo DPA (part of its terms) | MFA; a dedicated SMTP key; SPF, DKIM and DMARC set up on the domain. The charity's normal inbox (info@…) stays where it is |
| **Twilio** (Twilio Inc., US) — text messages | Mobile numbers and short message text (never health or safeguarding details) | US. Twilio's DPA includes the UK Addendum, and Twilio has binding corporate rules | Twilio DPA | MFA; sub-account; spending limit. Texts come from the alphanumeric sender "Honeycombe" (UK, one-way: replies aren't possible) |
| **Backblaze B2** (Backblaze, Inc., US) — off-site backups | Encrypted backup files only. They're encrypted on our server with the trustees' public certificate before upload, so Backblaze can't read them | **EU Central (Amsterdam) region.** Backblaze's DPA with the UK Addendum covers any access from the US | Backblaze DPA | An application key that can only upload to the backup bucket; Object Lock (file retention) of 35 days |
| **GitHub** (source code) | No personal data. Code and design documents only | US | GitHub terms | MFA for everyone with access; branch protection |
| **Google Maps** (contact page map) | The visitor's IP, only after they click "Show the map" | US | Google terms | — |

**No longer used.**
- Google Fonts: now self-hosted.
- MagicBooking / Creative Kids booking portal: after cutover, ask them for **written confirmation that
  Honeycombe's data has been deleted** (a deletion certificate).

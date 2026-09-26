# Processors and international transfers (DRAFT)

> Every service that handles personal data for us needs a contract with data processing terms (UK GDPR
> Art 28), and a transfer safeguard where data leaves the UK. Draft for *[data protection lead]* to check
> against each provider's current terms.

| Provider | What it processes | Location / transfer | Contract | Account security |
|---|---|---|---|---|
| **Render** (hosting, database, local backups) | Everything in the system | *[Region: Oregon (US) by default, or Frankfurt (EU) if the service is re-created there.]* Render Inc is US-based: check the DPA includes the UK Addendum, or rely on the UK–US data bridge if Render is certified | Render DPA *[accept in dashboard]* | MFA for every team member; minimum team; record who has shell access |
| **Stripe** (card payments) | Payer name, email, amounts, booking references. **No child names or health data.** | US / EU. Stripe offers the UK Addendum and is DPF-certified | Stripe DSA (part of their terms) | MFA; restricted API keys; account in the charity's name |
| **Email provider** *[e.g. Google Workspace / Microsoft 365 / a transactional service]* | Recipients, message content (no health or safeguarding details) | *[Check]* | *[Check DPA]* | MFA; a dedicated sending mailbox |
| **Twilio** (SMS) | Mobile numbers, short message text (no health details) | US. Twilio has a DPA with the UK Addendum and binding corporate rules | Twilio DPA | MFA; sub-account; spending limit |
| **Backup storage** *[provider, UK/EU region]* | Encrypted backup files (the provider can't read them) | UK/EU | *[Check DPA]* | Upload-only access key; object lock |
| **GitHub** (source code) | No personal data. Code and design documents only | US | GitHub terms | MFA for everyone with access; branch protection |
| **Google Maps** (contact page map) | The visitor's IP, only after they click "Show the map" | US | Google terms | — |

**No longer used.**
- Google Fonts: now self-hosted.
- MagicBooking / Creative Kids booking portal: after cutover, ask them for **written confirmation that
  Honeycombe's data has been deleted** (a deletion certificate).

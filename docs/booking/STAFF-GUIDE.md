# Staff guide: the booking system

Sign in at `/admin` with your email, your password and the 6-digit code from your authenticator app (the first time,
scan the QR code on screen with the app). You'll only
see the areas your role uses.

## Every day

- **In-tray.** Things waiting for someone:
  - bookings to approve, HAF claims to check, voucher payments;
  - absences and cancellations;
  - health or collection changes for children booked this week;
  - safeguarding items (DSL only);
  - website messages.

  Press **Take it** so colleagues know you're on it, **Done ✓** when finished, or **Snooze** it for later.
- **Registers.** Pick the day and open the session.
  - **Sign in** each child as they arrive, or mark them **Absent**.
  - Allergies and needs are shown on each row. **ANAPHYLAXIS** is in red.
  - **Sign out…**: choose how they're being collected.
    - If it's by collection password, type what the adult says. You never see the password; the system checks it.
    - After 5 wrong tries, use the phone check instead: call the parent on the number in *Details*, then choose
      *Known adult, checked by phone*.
    - A **COLLECTION ALERT** means see a manager before the child leaves.
  - **Print** gives a paper copy (no passwords, no safeguarding details) for when the tablet isn't available.
    Shred it after use.
- **Log incident** (on a register row or under Incidents). Choose how the parent will be told:
  - **Now**: they get an email saying there's a note to read. The email never contains details.
  - **At collection**: the register won't let the child be signed out until you tick that you've talked it
    through.
  - **Not at all**: you must give a reason.

  For an injury, click the body outline (front or back) to mark where it is, and add a few words for each mark.
  The parent sees the same picture in their note.

  Safeguarding concerns go only to the DSL, and parents aren't told automatically.

## Bookings

- **A family phones to book.** Search in **People**, open the family, then **Book sessions…**.
  - Choose the children and sessions, and whether they've paid, will pay later, or it's free of charge.
  - If their child's details aren't complete, the booking still goes through. It's flagged *details incomplete*
    and the family is emailed to finish them.
  - Booking outside the rules (too young, a full session) needs the override permission and a reason.
- **Walk-in at reception.** Use **Walk-in**: parent, child, one emergency contact, allergies, and the photo and
  first-aid answers. Then the session and the payment. It books, takes the payment and signs the child in in one
  go, and emails the parent to finish their details online.
- **Approvals.** Bookings → **Under approval**.
  - **Approve**: paid places get an invoice; the family is emailed.
  - **Decline**: give a reason; the place goes to the waiting list.
  - For HAF, tick the claims and use **Verify HAF…**. The child is then marked eligible, so their next HAF days
    confirm straight away.
- **Cancelling.** Open the booking → **Cancel…**, then choose what happens to the money:
  - account credit;
  - a refund to their card (online payments);
  - refunded another way;
  - nothing back.

  Invoices always get a credit note, so the family never owes for a cancelled place.
- **Moving a child to another day** at the same price: open the booking → **Move…**.
- **Cancelling a whole session.** Activities → the activity → **Cancel…** on the session. Everyone is cancelled
  and refunded (card payments to the card, everything else as credit) and told by email and text.
- **Waiting list.**
  - When a place frees up, the next family is offered it automatically (email and text) and it's held for them for
    a day, or 2 hours if the session is soon. Brothers and sisters are offered together.
  - Bookings → **Waiting list** lets you offer a place yourself or change someone's priority.

## Searching and bulk actions

- In **People**, search for children or families. Filters include age, needs, form level and HAF.
- **Save this search…** keeps the words and filters, not the results, so it finds whoever matches each time you run
  it. Tick *Share with colleagues* to make it appear in their list too.
- Tick rows (or **Select all**) to act on them together:
  - **Message their families** opens Messages with those families chosen;
  - **Export selected** downloads just those rows;
  - **Add to a waiting list…** puts the children on a session's waiting list.
    - Brothers and sisters are kept together.
    - Anyone already booked or outside the rules is skipped, and you're told why. Override only with permission and
      a reason.
    - If the session has room, the place is offered straight away.

## Money

- **Finance** shows the day's takings by method, unpaid and overdue invoices, and refunds to hand back.
- **Record payment** when vouchers, Tax-Free Childcare, a bank transfer or cash arrive. Quote the invoice number.
  **Never write card numbers anywhere.**
- **Send reminder** emails the family a link to pay. Unpaid invoices are never cancelled automatically, so follow
  them up here.
- **Allow pay later** for a family from their record in People (finance role).
- **Discounts** are set in Booking settings → Discounts. Both are 0 (off) until you choose a percentage.
  - *Sibling*: off the second and later child of a family on the same session, including when a brother or sister
    booked earlier.
  - *Multi-day*: off every session when one child is booked on at least N sessions of an activity at once.
  - A place gets the bigger discount, never both. Trials, HAF and free places aren't discounted.
  - Invoices show the full price and the discount.
- **Aged debt** (Finance → *Show aged debt*) lists who owes what, grouped by how long it's overdue: not due, 1–30,
  31–60, 61–90 and over 90 days. There's a CSV too.
- **Accounting export** (next to the takings dates) is one spreadsheet for your accounts software: invoice lines by
  category (with discounts), credit notes, payments by method and refunds. It contains no children's names.
- **Stripe payouts.** *Show recent payouts* → **Match** lists each card payment and refund in a payout, with
  Stripe's fee.
  - Each line is matched to the family and invoice here. It warns if something isn't matched or the total doesn't
    add up.

## Activities

- New activities start as **drafts** that only staff can see. **Add a run of sessions** makes a term's worth at
  once: preview it first. Then **Publish now**, or **Schedule…** a time.
- The system won't publish an activity with no upcoming places, or a paid one with no way to pay. It tells you
  what's missing.
- **Duplicate for next term** copies an activity and its sessions onto the same weekdays.
- **Trial sessions.** Tick *Offer a trial session* under Price, and set a trial price if you like: leave it empty for
  the normal price, or enter 0 for free.
  - Families then see "Trial session" on the Book page, and can tick it for a child's first session when they review
    their booking.
  - Each child gets one trial per activity, and only if they haven't been booked on it before.
  - To book a trial for a family, tick *trial* when you book on their behalf. To flag an existing booking, open it and
    choose **Mark as trial**; this doesn't change the price.
  - Reports → *Trial sessions* shows how many children came and how many were booked again within 90 days. Bookings →
    **Trials** lists them.

## Messages

- **About a booking** (service messages) go to everyone booked on a session, an activity or a day, or to chosen
  families, whatever their marketing choices. Use these for changes, reminders and cancellations.
- **News** goes only to people who opted in. Every news email has an unsubscribe link.
- Always **Preview** first. It shows how many people will get it.
- **By age.** *Everyone booked (upcoming) for a child of a certain age* reaches families with a booking for a child in
  that age range. For news, the age boxes narrow the list to opted-in families with a child that age.
- **Texts cost money.** The preview shows how many texts each person gets and roughly what it will cost. Set the price
  per text in Booking settings → Messages.
  - One curly apostrophe (’) or emoji makes every text shorter (70 characters instead of 160), and so dearer. The
    preview warns you.
- **Schedule for later** sends at the time you choose (UK time, up to 90 days ahead).
  - Who gets it is worked out when it goes, so families who book in the meantime are included.
  - Scheduled messages wait at the top of **Sent**, where you can **Cancel** them.
  - If a message can't go (for example, its session was deleted), the in-tray tells you.

## Reports

- **Attendance** comes from registers, for a day, week, month, quarter or reporting year. The *Daily breakdown CSV*
  splits it by type of session.
- **Year on year** plots each month of the last three reporting years.
- **MagicBooking history.** To include years before the switch-over, paste monthly totals exported from MagicBooking
  as CSV lines: `month,category,attendances,children`, e.g. `2025-08,Holiday club,412,96`.
  - **Check** first, then **Save**.
  - Saving a month and category again replaces the old figure.
  - Only the owner and admins can change these totals. They're numbers only, with no names.

## Families' data

- Families change their own details, contacts, health information, permissions and collection password in their
  account. Changes to collection details need their password again and send them a confirmation.
- **Their own copy.** Families can download their data themselves from *Your data & messages*, after re-entering
  their password. They get a readable page or a JSON file, and an email tells them it happened. Incidents they
  haven't been told about, and ones where their child was only a witness, are left out.
- **Request my data**: if they'd rather we send it, the in-tray tells you. Open the family → **Download their data**,
  check it, and email it to them within a month.
- **Delete my account**: the account closes at once and is erased after 14 days. The owner or DSL can
  **Restore** it before then.
- Only the DSL sees family safeguarding information. Viewing health details is logged.
- **Turning 18.** On a child's 18th birthday the parent is emailed and you get an in-tray item.
  - The parent can choose *Give them their own account* on their family page. The young person gets an email, sets a
    password, and their record moves into their own account; the parent's answers for them are cleared for them to
    give their own.
  - If nothing happens within 90 days (Booking settings → Data retention), they're archived from the parent's account.
- **What's cleared automatically** (the nightly retention job, with periods in Booking settings → Data retention):
  - unused accounts: warned, then deleted 30 days later;
  - accident records after the child's 25th birthday (safeguarding records go to the DSL instead);
  - names on old registers, keeping the counts;
  - old message text;
  - the audit log after 6 years;
  - imported families who never activated after a year;
  - one-off guests' details a year after their event.
- **Other carers.** In *My details*, an account holder can invite up to 3 carers (a partner or grandparent, say) to
  sign in with their own email and password.
  - Carers can see the children's details, book, pay, cancel and report absences.
  - They can't change any details, consents, collection arrangements or the account.
  - Being a carer is not permission to collect: that's still set in Emergency contacts.
  - You'll see carers on the family's record in People. Use **Remove** if the account holder asks (for example
    after a separation); it signs them out at once.

## SEND support requests

- Families with a child who has SEND or additional needs can send a **support request** before booking. It's
  linked from the registration page and from their account.
- Each request tells us:
  - how they'd like to talk (a call, a visit or email);
  - their child's needs;
  - what helps, what overwhelms, how the child communicates, and the support they get now;
  - whether they have an EHCP, with any plans uploaded.
- **Who sees them:** requests arrive in the **In-tray** and under **SEND support**. Only staff with SEND access see
  them: the owner, admin, manager, DSL and SEND lead. Opening a request or downloading a document is logged.
  - Downloads save to your computer: don't keep copies on shared drives or email them on.
  - Set who gets an email alert (without details) in Booking settings → *SEND lead email(s)*.
- **Work each request through:**
  - **Assign to me**;
  - add **notes** after each call;
  - **Mark as contacted** or **Visit booked…**;
  - finally **Support plan agreed…** with a short, practical summary.

  The summary appears on registers (a *Support plan* chip, with the text under *Details*) and on the child's
  record. The family is emailed that they can book.
- Documents uploaded but never sent with a request are deleted after 2 days. When a family's account is erased,
  their requests and documents are deleted too.

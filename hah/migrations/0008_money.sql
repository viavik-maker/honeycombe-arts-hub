-- Invoices, payments, credit notes and refunds. Invoices are never deleted;
-- numbers are gap-free (allocated from counters inside the same transaction).
--   invoice balance = total - paid - credited
--   account credit  = credit refunds (succeeded) - credit spent (payments by account_credit)

CREATE TABLE invoices(
  id               INTEGER PRIMARY KEY,
  number           TEXT NOT NULL UNIQUE,
  account_id       INTEGER REFERENCES accounts(id),
  guest_contact_id INTEGER REFERENCES guest_contacts(id),
  checkout_id      INTEGER REFERENCES checkouts(id),
  bill_to_name     TEXT NOT NULL,         -- snapshots: survive the family being erased
  bill_to_email    TEXT,
  bill_to_address  TEXT,
  issue_date       TEXT NOT NULL,
  due_date         TEXT NOT NULL,
  status           TEXT NOT NULL CHECK (status IN ('issued', 'part_paid', 'paid', 'void', 'credited')),
  total_pence      INTEGER NOT NULL CHECK (total_pence >= 0),
  paid_pence       INTEGER NOT NULL DEFAULT 0,
  credited_pence   INTEGER NOT NULL DEFAULT 0,
  notes            TEXT,
  created_by_staff INTEGER REFERENCES staff_users(id),
  sent_at          TEXT,
  voided_at        TEXT,
  void_reason      TEXT,
  created_at       TEXT NOT NULL,
  CHECK (due_date >= issue_date)
);
CREATE INDEX ix_invoices_account ON invoices(account_id);
CREATE INDEX ix_invoices_status ON invoices(status, due_date);

CREATE TABLE invoice_lines(
  id               INTEGER PRIMARY KEY,
  invoice_id       INTEGER NOT NULL REFERENCES invoices(id),
  booking_id       INTEGER REFERENCES bookings(id),
  description      TEXT NOT NULL,
  service_date     TEXT,
  participant_name TEXT,
  quantity         INTEGER NOT NULL DEFAULT 1,
  unit_pence       INTEGER NOT NULL,
  amount_pence     INTEGER NOT NULL,
  funding_note     TEXT
);
CREATE INDEX ix_invoice_lines_invoice ON invoice_lines(invoice_id);
CREATE INDEX ix_invoice_lines_booking ON invoice_lines(booking_id);

CREATE TABLE payments(
  id                         INTEGER PRIMARY KEY,
  ref                        TEXT NOT NULL UNIQUE,
  account_id                 INTEGER REFERENCES accounts(id),
  guest_contact_id           INTEGER REFERENCES guest_contacts(id),
  amount_pence               INTEGER NOT NULL CHECK (amount_pence > 0),
  method                     TEXT NOT NULL CHECK (method IN ('stripe_card', 'cash', 'card_terminal', 'bank_transfer',
                                                             'childcare_voucher', 'tax_free_childcare',
                                                             'account_credit', 'other')),
  voucher_provider           TEXT,
  reference                  TEXT,
  status                     TEXT NOT NULL CHECK (status IN ('pending', 'succeeded', 'failed')),
  stripe_payment_intent_id   TEXT UNIQUE,
  stripe_checkout_session_id TEXT,
  received_at                TEXT NOT NULL,
  recorded_by_staff          INTEGER REFERENCES staff_users(id),
  notes                      TEXT,
  created_at                 TEXT NOT NULL
);
CREATE INDEX ix_payments_account ON payments(account_id);
CREATE INDEX ix_payments_received ON payments(received_at);

CREATE TABLE payment_allocations(
  payment_id   INTEGER NOT NULL REFERENCES payments(id),
  invoice_id   INTEGER NOT NULL REFERENCES invoices(id),
  amount_pence INTEGER NOT NULL CHECK (amount_pence > 0),
  PRIMARY KEY (payment_id, invoice_id)
);

CREATE TABLE credit_notes(
  id                 INTEGER PRIMARY KEY,
  number             TEXT NOT NULL UNIQUE,
  invoice_id         INTEGER NOT NULL REFERENCES invoices(id),
  account_id         INTEGER REFERENCES accounts(id),
  reason             TEXT NOT NULL,
  total_pence        INTEGER NOT NULL CHECK (total_pence > 0),
  created_by_staff   INTEGER REFERENCES staff_users(id),
  created_by_account INTEGER REFERENCES accounts(id),
  created_at         TEXT NOT NULL
);

CREATE TABLE credit_note_lines(
  id              INTEGER PRIMARY KEY,
  credit_note_id  INTEGER NOT NULL REFERENCES credit_notes(id),
  booking_id      INTEGER REFERENCES bookings(id),
  invoice_line_id INTEGER REFERENCES invoice_lines(id),
  description     TEXT NOT NULL,
  amount_pence    INTEGER NOT NULL
);

CREATE TABLE refunds(
  id               INTEGER PRIMARY KEY,
  credit_note_id   INTEGER REFERENCES credit_notes(id),
  payment_id       INTEGER REFERENCES payments(id),    -- NULL when it only becomes account credit
  account_id       INTEGER REFERENCES accounts(id),
  amount_pence     INTEGER NOT NULL CHECK (amount_pence > 0),
  method           TEXT NOT NULL CHECK (method IN ('stripe', 'cash', 'bank_transfer', 'voucher_provider',
                                                   'account_credit')),
  status           TEXT NOT NULL CHECK (status IN ('pending', 'succeeded', 'failed')),
  stripe_refund_id TEXT UNIQUE,
  created_by_staff INTEGER REFERENCES staff_users(id),
  created_at       TEXT NOT NULL,
  processed_at     TEXT,
  failure_reason   TEXT
);
CREATE INDEX ix_refunds_account ON refunds(account_id);

CREATE TABLE stripe_events(
  id           TEXT PRIMARY KEY,
  type         TEXT,
  received_at  TEXT NOT NULL,
  processed_at TEXT,
  status       TEXT,
  error        TEXT
);

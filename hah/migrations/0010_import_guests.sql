-- Importing families from MagicBooking, and one-off guest bookings.

CREATE TABLE import_batches(
  id              INTEGER PRIMARY KEY,
  filename        TEXT,
  status          TEXT NOT NULL CHECK (status IN ('committed', 'rolled_back')),
  mapping         TEXT NOT NULL,                 -- JSON: our field -> their column
  stats           TEXT NOT NULL,                 -- JSON counts
  created_by      INTEGER REFERENCES staff_users(id),
  created_at      TEXT NOT NULL,
  rolled_back_at  TEXT,
  activation_sent INTEGER NOT NULL DEFAULT 0
);

-- One row per line of the file (the raw line is purged after 30 days).
CREATE TABLE import_rows(
  id             INTEGER PRIMARY KEY,
  batch_id       INTEGER NOT NULL REFERENCES import_batches(id) ON DELETE CASCADE,
  row_no         INTEGER NOT NULL,
  raw            TEXT,
  result         TEXT NOT NULL CHECK (result IN ('created', 'added_child', 'skipped', 'error')),
  message        TEXT,
  account_id     INTEGER REFERENCES accounts(id),
  participant_id INTEGER REFERENCES participants(id),
  created_at     TEXT NOT NULL
);
CREATE INDEX ix_import_rows_batch ON import_rows(batch_id);

-- Emailed links for guests: confirm a free booking, or confirm a newsletter opt-in.
CREATE TABLE guest_tokens(
  id               INTEGER PRIMARY KEY,
  purpose          TEXT NOT NULL CHECK (purpose IN ('confirm_booking', 'marketing_opt_in')),
  token_hash       TEXT NOT NULL UNIQUE,
  guest_contact_id INTEGER NOT NULL REFERENCES guest_contacts(id),
  booking_id       INTEGER REFERENCES bookings(id),
  expires_at       TEXT NOT NULL,
  used_at          TEXT,
  created_at       TEXT NOT NULL
);

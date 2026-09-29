-- Bookings, checkouts, the waiting list (bookings with status 'waitlisted' /
-- 'offered') and attendance (the register).
--
-- A place is taken by bookings in these states: pending_payment (held while
-- paying by card), pending_approval (reserved while staff decide), offered
-- (reserved for a waiting-list offer) and confirmed.

CREATE TABLE checkouts(
  id                INTEGER PRIMARY KEY,
  ref               TEXT NOT NULL UNIQUE,
  account_id        INTEGER REFERENCES accounts(id),
  guest_contact_id  INTEGER REFERENCES guest_contacts(id),
  invoice_id        INTEGER,                      -- paying an existing invoice later
  idempotency_key   TEXT NOT NULL,
  amount_pence      INTEGER NOT NULL CHECK (amount_pence >= 0),   -- charged by card (after credit)
  credit_pence      INTEGER NOT NULL DEFAULT 0,                  -- account credit used
  credit_payment_id INTEGER,                                     -- the account-credit payment reserving it
  pay_mode          TEXT NOT NULL CHECK (pay_mode IN ('stripe', 'pay_later', 'voucher', 'free', 'credit',
                                                  'staff_offline', 'staff_link')),
  status            TEXT NOT NULL CHECK (status IN ('creating', 'awaiting_payment', 'completed', 'expired',
                                                    'failed', 'cancelled')),
  stripe_session_id TEXT UNIQUE,
  stripe_url        TEXT,
  expires_at        TEXT,
  completed_at      TEXT,
  created_by_staff  INTEGER REFERENCES staff_users(id),
  created_at        TEXT NOT NULL,
  UNIQUE (account_id, idempotency_key),
  UNIQUE (guest_contact_id, idempotency_key)
);
CREATE INDEX ix_checkouts_status ON checkouts(status, expires_at);

CREATE TABLE bookings(
  id                   INTEGER PRIMARY KEY,
  ref                  TEXT NOT NULL UNIQUE,
  checkout_id          INTEGER REFERENCES checkouts(id),
  session_id           INTEGER NOT NULL REFERENCES activity_sessions(id),
  activity_id          INTEGER NOT NULL REFERENCES activities(id),
  kind                 TEXT NOT NULL CHECK (kind IN ('participant', 'party')),
  participant_id       INTEGER REFERENCES participants(id),
  account_id           INTEGER REFERENCES accounts(id),
  guest_contact_id     INTEGER REFERENCES guest_contacts(id),
  party_adults         INTEGER NOT NULL DEFAULT 0 CHECK (party_adults >= 0),
  party_children       INTEGER NOT NULL DEFAULT 0 CHECK (party_children >= 0),
  party_name           TEXT,                      -- "Name for the booking" (guests)
  places               INTEGER NOT NULL CHECK (places > 0),
  status               TEXT NOT NULL CHECK (status IN ('pending_payment', 'pending_approval', 'confirmed',
                                                       'waitlisted', 'offered', 'cancelled', 'expired',
                                                       'pending_confirmation')),
  approval_reason      TEXT CHECK (approval_reason IN ('activity', 'haf_claim', 'voucher')),
  funding              TEXT NOT NULL DEFAULT 'paid'
                       CHECK (funding IN ('paid', 'haf', 'free', 'staff_comp', 'prepaid_legacy')),
  price_pence          INTEGER NOT NULL DEFAULT 0 CHECK (price_pence >= 0),
  pay_later            INTEGER NOT NULL DEFAULT 0 CHECK (pay_later IN (0, 1)),
  is_trial             INTEGER NOT NULL DEFAULT 0 CHECK (is_trial IN (0, 1)),
  profile_incomplete   INTEGER NOT NULL DEFAULT 0 CHECK (profile_incomplete IN (0, 1)),
  hold_expires_at      TEXT,
  offer_expires_at     TEXT,
  waitlist_priority    INTEGER NOT NULL DEFAULT 0,
  waitlist_group       TEXT,                      -- siblings waitlisted in one checkout are offered together
  approved_at          TEXT,
  approved_by          INTEGER REFERENCES staff_users(id),
  cancelled_at         TEXT,
  cancelled_by_staff   INTEGER REFERENCES staff_users(id),
  cancelled_by_account INTEGER REFERENCES accounts(id),
  cancel_reason        TEXT,
  created_via          TEXT NOT NULL CHECK (created_via IN ('online', 'staff', 'walkin', 'guest')),
  created_by_staff     INTEGER REFERENCES staff_users(id),
  notes                TEXT,
  created_at           TEXT NOT NULL,
  updated_at           TEXT NOT NULL,
  CHECK ((kind = 'participant' AND participant_id IS NOT NULL AND places = 1) OR
         (kind = 'party' AND participant_id IS NULL)),
  CHECK (account_id IS NOT NULL OR guest_contact_id IS NOT NULL)
);
CREATE UNIQUE INDEX ux_booking_child_session ON bookings(session_id, participant_id)
  WHERE participant_id IS NOT NULL AND status NOT IN ('cancelled', 'expired');
CREATE INDEX ix_bookings_session ON bookings(session_id, status);
CREATE INDEX ix_bookings_account ON bookings(account_id);
CREATE INDEX ix_bookings_participant ON bookings(participant_id);
CREATE INDEX ix_bookings_hold ON bookings(status, hold_expires_at);
CREATE INDEX ix_bookings_offer ON bookings(status, offer_expires_at);
CREATE INDEX ix_bookings_checkout ON bookings(checkout_id);

-- named children on a signed-in family's party booking (so their flags reach the register)
CREATE TABLE booking_party_children(
  booking_id     INTEGER NOT NULL REFERENCES bookings(id) ON DELETE CASCADE,
  participant_id INTEGER NOT NULL REFERENCES participants(id),
  PRIMARY KEY (booking_id, participant_id)
);

CREATE TABLE attendance(
  id                        INTEGER PRIMARY KEY,
  booking_id                INTEGER NOT NULL UNIQUE REFERENCES bookings(id),
  session_id                INTEGER NOT NULL REFERENCES activity_sessions(id),
  participant_id            INTEGER REFERENCES participants(id),
  status                    TEXT NOT NULL DEFAULT 'expected'
                            CHECK (status IN ('expected', 'present', 'absent', 'absent_notified')),
  arrived_count             INTEGER,
  late                      INTEGER NOT NULL DEFAULT 0,
  signed_in_at              TEXT,
  signed_in_by              INTEGER REFERENCES staff_users(id),
  signed_out_at             TEXT,
  signed_out_by             INTEGER REFERENCES staff_users(id),
  release_method            TEXT CHECK (release_method IN ('password', 'went_home_alone', 'parent_stayed',
                                                           'known_adult_verified', 'other')),
  collected_by_name         TEXT,
  collected_by_relationship TEXT,
  incident_discussed        INTEGER,
  notes                     TEXT,
  snap_age_months           INTEGER,   -- snapshots, so reports survive a family being erased
  snap_haf                  INTEGER,
  snap_send                 INTEGER,
  snap_category             TEXT,
  updated_at                TEXT
);
CREATE INDEX ix_attendance_session ON attendance(session_id);

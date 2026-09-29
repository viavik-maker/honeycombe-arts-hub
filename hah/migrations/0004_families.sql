-- Families: parent/carer and 18+ accounts, the people who attend
-- (children or the young adult themselves), their health, safeguarding,
-- contacts, GP, consents, marketing preferences, and guest bookers.

CREATE TABLE accounts(
  id                INTEGER PRIMARY KEY,
  ref               TEXT NOT NULL UNIQUE,
  kind              TEXT NOT NULL CHECK (kind IN ('family', 'adult')),
  email             TEXT COLLATE NOCASE,          -- NULL only for walk-ins staff created without one
  email_verified_at TEXT,
  password_hash     TEXT,                         -- NULL until they choose one (imported, walk-in)
  status            TEXT NOT NULL CHECK (status IN ('pending_verification', 'pending_activation', 'active',
                                                    'closed', 'anonymised')),
  first_name        TEXT NOT NULL,
  last_name         TEXT NOT NULL,
  mobile            TEXT,                         -- +447…
  address_line1     TEXT,
  address_line2     TEXT,
  town              TEXT,
  postcode          TEXT,
  source            TEXT NOT NULL CHECK (source IN ('self', 'import', 'staff', 'walkin')),
  import_batch_id   INTEGER,
  legacy_ref        TEXT,                         -- MagicBooking id, for matching the export
  pay_later_allowed INTEGER NOT NULL DEFAULT 0 CHECK (pay_later_allowed IN (0, 1)),
  staff_notes       TEXT,
  created_at        TEXT NOT NULL,
  updated_at        TEXT NOT NULL,
  last_login_at     TEXT,
  activated_at      TEXT,
  reconfirmed_at    TEXT,                         -- imported families confirm their details once
  closed_at         TEXT,
  erase_after       TEXT,
  anonymised_at     TEXT
);
CREATE UNIQUE INDEX ux_accounts_email ON accounts(email) WHERE email IS NOT NULL AND status <> 'anonymised';
CREATE INDEX ix_accounts_name ON accounts(last_name, first_name);

CREATE TABLE account_sessions(
  id              INTEGER PRIMARY KEY,
  token_hash      TEXT NOT NULL UNIQUE,
  account_id      INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  csrf_token      TEXT NOT NULL,
  created_at      TEXT NOT NULL,
  last_seen_at    TEXT NOT NULL,
  idle_expires_at TEXT NOT NULL,
  expires_at      TEXT NOT NULL,
  reauth_at       TEXT,                           -- last time they re-entered their password
  ip              TEXT,
  user_agent      TEXT
);
CREATE INDEX ix_account_sessions_account ON account_sessions(account_id);

-- Emailed one-time codes and links for parents. Only hashes are stored.
CREATE TABLE account_tokens(
  id         INTEGER PRIMARY KEY,
  purpose    TEXT NOT NULL CHECK (purpose IN ('verify_email', 'activate', 'reset_password', 'email_change')),
  token_hash TEXT NOT NULL UNIQUE,
  account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  payload    TEXT,
  attempts   INTEGER NOT NULL DEFAULT 0,
  expires_at TEXT NOT NULL,
  used_at    TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX ix_account_tokens_account ON account_tokens(account_id, purpose);

CREATE TABLE participants(
  id                  INTEGER PRIMARY KEY,
  ref                 TEXT NOT NULL UNIQUE,
  account_id          INTEGER NOT NULL REFERENCES accounts(id),
  is_account_holder   INTEGER NOT NULL DEFAULT 0 CHECK (is_account_holder IN (0, 1)),  -- the 18+ person themself
  first_name          TEXT NOT NULL,
  last_name           TEXT NOT NULL,
  dob                 TEXT NOT NULL,              -- YYYY-MM-DD
  gender              TEXT,
  education           TEXT CHECK (education IN ('school', 'home_educated', 'not_applicable')),
  school_name         TEXT,
  haf_status          TEXT NOT NULL DEFAULT 'unknown'
                      CHECK (haf_status IN ('unknown', 'claimed_eligible', 'not_eligible', 'not_sure', 'verified')),
  haf_verified_at     TEXT,
  haf_verified_by     INTEGER REFERENCES staff_users(id),
  target_level        TEXT NOT NULL DEFAULT 'full' CHECK (target_level IN ('short', 'full', 'adult')),
  level               TEXT NOT NULL DEFAULT 'none' CHECK (level IN ('none', 'short', 'full', 'adult')),
  -- flags kept in step with the health/consent sections, for registers and search
  f_allergy           INTEGER NOT NULL DEFAULT 0,
  f_anaphylaxis       INTEGER NOT NULL DEFAULT 0,
  f_medical           INTEGER NOT NULL DEFAULT 0,
  f_dietary           INTEGER NOT NULL DEFAULT 0,
  f_send              INTEGER NOT NULL DEFAULT 0,
  f_semh              INTEGER NOT NULL DEFAULT 0,
  f_religious         INTEGER NOT NULL DEFAULT 0,
  f_safeguarding      INTEGER NOT NULL DEFAULT 0,  -- set by a DSL after review, never from parent text
  photo_consent       TEXT CHECK (photo_consent IN ('online', 'internal', 'none')),
  go_home_alone       INTEGER,
  collection_alert    TEXT,                        -- "must not collect" — shown to register staff
  collection_pw_hash  TEXT,                        -- peppered PBKDF2; never shown
  collection_pw_set_at TEXT,
  needs_review        INTEGER NOT NULL DEFAULT 0,  -- imported or staff-entered; parent must confirm
  status              TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived', 'retention_hold', 'anonymised')),
  created_at          TEXT NOT NULL,
  updated_at          TEXT NOT NULL,
  anonymised_at       TEXT
);
CREATE INDEX ix_participants_account ON participants(account_id);
CREATE INDEX ix_participants_name ON participants(last_name, first_name);

CREATE TABLE participant_health(
  participant_id         INTEGER PRIMARY KEY REFERENCES participants(id) ON DELETE CASCADE,
  allergies              TEXT,
  anaphylaxis            INTEGER,
  adrenaline_pen         INTEGER,
  medical_conditions     TEXT,
  medication             TEXT,
  dietary                TEXT,
  send_needs             TEXT,
  semh_needs             TEXT,
  religious_requirements TEXT,
  access_needs           TEXT,
  updated_at             TEXT NOT NULL,
  updated_by_account     INTEGER,
  updated_by_staff       INTEGER
);

-- Visible to Designated Safeguarding Leads only.
CREATE TABLE participant_safeguarding(
  participant_id     INTEGER PRIMARY KEY REFERENCES participants(id) ON DELETE CASCADE,
  family_info        TEXT,
  updated_at         TEXT NOT NULL,
  updated_by_account INTEGER,
  updated_by_staff   INTEGER,
  dsl_reviewed_at    TEXT,
  dsl_reviewed_by    INTEGER REFERENCES staff_users(id)
);

CREATE TABLE participant_gp(
  participant_id   INTEGER PRIMARY KEY REFERENCES participants(id) ON DELETE CASCADE,
  surgery_name     TEXT,
  doctor_name      TEXT,
  surgery_phone    TEXT,
  surgery_postcode TEXT,
  updated_at       TEXT NOT NULL
);

-- Family-level: shared by siblings.
CREATE TABLE emergency_contacts(
  id           INTEGER PRIMARY KEY,
  account_id   INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  full_name    TEXT NOT NULL,
  relationship TEXT NOT NULL,
  phone        TEXT NOT NULL,
  can_collect  INTEGER NOT NULL DEFAULT 0 CHECK (can_collect IN (0, 1)),
  priority     INTEGER NOT NULL,
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL
);
CREATE INDEX ix_contacts_account ON emergency_contacts(account_id);

-- Consent and permission questions, versioned: changing the wording makes a new version.
CREATE TABLE consent_types(
  id          INTEGER PRIMARY KEY,
  key         TEXT NOT NULL,
  version     INTEGER NOT NULL,
  label       TEXT NOT NULL,
  help_text   TEXT,
  options     TEXT NOT NULL,                     -- JSON [[value, label], …]
  kind        TEXT NOT NULL CHECK (kind IN ('consent', 'permission', 'acknowledgement')),
  active      INTEGER NOT NULL DEFAULT 1,
  created_at  TEXT NOT NULL,
  UNIQUE (key, version)
);

-- Append-only: a new answer supersedes the old one.
CREATE TABLE consents(
  id                   INTEGER PRIMARY KEY,
  consent_type_id      INTEGER NOT NULL REFERENCES consent_types(id),
  account_id           INTEGER NOT NULL REFERENCES accounts(id),
  participant_id       INTEGER REFERENCES participants(id),
  value                TEXT NOT NULL,
  source               TEXT NOT NULL CHECK (source IN ('registration', 'profile', 'reconfirm', 'import',
                                                       'staff_paper', 'staff_verbal')),
  recorded_by_staff_id INTEGER REFERENCES staff_users(id),
  ip                   TEXT,
  created_at           TEXT NOT NULL,
  superseded_at        TEXT
);
CREATE INDEX ix_consents_current ON consents(participant_id, consent_type_id) WHERE superseded_at IS NULL;
CREATE INDEX ix_consents_account ON consents(account_id);

CREATE TABLE guest_contacts(
  id              INTEGER PRIMARY KEY,
  email           TEXT NOT NULL COLLATE NOCASE,
  phone           TEXT,
  name            TEXT,
  created_at      TEXT NOT NULL,
  last_booking_at TEXT,
  account_id      INTEGER REFERENCES accounts(id),   -- linked once they create an account
  anonymised_at   TEXT
);
CREATE UNIQUE INDEX ux_guest_email ON guest_contacts(email) WHERE anonymised_at IS NULL;

-- Marketing opt-ins (PECR): separate from consents because newsletter
-- sign-ups and guests have no account.
CREATE TABLE marketing_preferences(
  id                   INTEGER PRIMARY KEY,
  email                TEXT NOT NULL UNIQUE COLLATE NOCASE,
  phone                TEXT,
  name                 TEXT,
  account_id           INTEGER REFERENCES accounts(id),
  guest_contact_id     INTEGER REFERENCES guest_contacts(id),
  email_opt_in         INTEGER NOT NULL DEFAULT 0,
  email_opt_in_at      TEXT,
  sms_opt_in           INTEGER NOT NULL DEFAULT 0,
  sms_opt_in_at        TEXT,
  wording_version      TEXT NOT NULL,
  source               TEXT NOT NULL CHECK (source IN ('newsletter_form', 'registration', 'guest_booking',
                                                       'account_settings', 'legacy_newsletter', 'staff')),
  unsubscribed_email_at TEXT,
  unsubscribed_sms_at  TEXT,
  created_at           TEXT NOT NULL,
  updated_at           TEXT NOT NULL
);

-- The questions as agreed with the charity. Photography merges the two
-- overlapping questions from the old form into one.
INSERT INTO consent_types(key, version, label, help_text, options, kind, created_at) VALUES
 ('photo', 1, 'Photography and filming', 'Covers photos and video taken during sessions. We never publish a child''s name with their photo.',
  '[["online","Yes, including online and social media"],["internal","Yes, for internal use only (not online)"],["none","No photos please"]]',
  'consent', strftime('%Y-%m-%dT%H:%M:%SZ','now')),
 ('first_aid', 1, 'First aid', 'May we give basic first aid if needed?', '[["yes","Yes"],["no","No"]]', 'permission', strftime('%Y-%m-%dT%H:%M:%SZ','now')),
 ('plasters', 1, 'Plasters', 'May we use plasters?', '[["yes","Yes"],["no","No"]]', 'permission', strftime('%Y-%m-%dT%H:%M:%SZ','now')),
 ('emergency_treatment', 1, 'Emergency medical treatment', 'If we can''t reach you, may staff agree to emergency treatment advised by a medical professional?', '[["yes","Yes"],["no","No"]]', 'permission', strftime('%Y-%m-%dT%H:%M:%SZ','now')),
 ('go_home_alone', 1, 'Going home alone', 'May your child leave on their own at the end of the day?', '[["yes","Yes"],["no","No"]]', 'permission', strftime('%Y-%m-%dT%H:%M:%SZ','now')),
 ('funder_share', 1, 'Sharing with our funders', 'Some funders ask to see who took part. We only ever share anonymous numbers unless you say yes here. You can change this at any time.', '[["yes","Yes, you may share my child''s name and age with funders"],["no","No, anonymous numbers only"]]', 'consent', strftime('%Y-%m-%dT%H:%M:%SZ','now')),
 ('info_correct', 1, 'The information is correct', 'I confirm the information above is correct and I''ll let you know if anything changes.', '[["yes","Confirmed"]]', 'acknowledgement', strftime('%Y-%m-%dT%H:%M:%SZ','now')),
 ('privacy_ack', 1, 'Privacy notice and safeguarding policy', 'I have read the privacy notice and safeguarding policy.', '[["yes","Read"]]', 'acknowledgement', strftime('%Y-%m-%dT%H:%M:%SZ','now'));

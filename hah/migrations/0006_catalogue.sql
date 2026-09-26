-- The catalogue: centres, categories, activities and their sessions.
-- Ages are whole months with inclusive bounds ("0 to 1" = 0-23 months).
-- "Past" isn't stored: an activity is past once its last session has gone.

CREATE TABLE centres(
  id       INTEGER PRIMARY KEY,
  name     TEXT NOT NULL UNIQUE,
  address  TEXT,
  postcode TEXT,
  active   INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1))
);

CREATE TABLE activity_categories(
  id           INTEGER PRIMARY KEY,
  key          TEXT NOT NULL UNIQUE,
  name         TEXT NOT NULL,
  report_group TEXT NOT NULL,       -- the line it's counted under in the daily breakdown
  colour       TEXT,
  sort         INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE activities(
  id                   INTEGER PRIMARY KEY,
  slug                 TEXT NOT NULL UNIQUE,
  title                TEXT NOT NULL,
  category_id          INTEGER NOT NULL REFERENCES activity_categories(id),
  centre_id            INTEGER NOT NULL REFERENCES centres(id),
  summary              TEXT,
  description          TEXT,
  image                TEXT,
  event_id             TEXT,        -- the What's On event this activity books (content.json id)
  status               TEXT NOT NULL DEFAULT 'draft'
                       CHECK (status IN ('draft', 'scheduled', 'published', 'unpublished', 'archived')),
  publish_at           TEXT,        -- when a scheduled activity goes live (UTC)
  booking_opens_at     TEXT,        -- NULL = as soon as it's published (UTC)
  booking_closes_hours INTEGER NOT NULL DEFAULT 0 CHECK (booking_closes_hours >= 0),
  min_age_months       INTEGER NOT NULL DEFAULT 0,
  max_age_months       INTEGER NOT NULL DEFAULT 1200,
  age_basis            TEXT NOT NULL DEFAULT 'first_session' CHECK (age_basis IN ('session_date', 'first_session')),
  registration_level   TEXT NOT NULL CHECK (registration_level IN ('guest', 'short', 'full', 'adult')),
  requires_approval    INTEGER NOT NULL DEFAULT 0 CHECK (requires_approval IN (0, 1)),
  parent_must_stay     INTEGER NOT NULL DEFAULT 0 CHECK (parent_must_stay IN (0, 1)),
  haf_only             INTEGER NOT NULL DEFAULT 0 CHECK (haf_only IN (0, 1)),
  haf_allowance_days   INTEGER,     -- funded days per child (HAF activities)
  price_pence          INTEGER NOT NULL DEFAULT 0 CHECK (price_pence >= 0),
  adult_price_pence    INTEGER NOT NULL DEFAULT 0 CHECK (adult_price_pence >= 0),
  capacity_default     INTEGER NOT NULL DEFAULT 20 CHECK (capacity_default >= 0),
  capacity_counts      TEXT NOT NULL DEFAULT 'children' CHECK (capacity_counts IN ('children', 'all_people')),
  max_party_size       INTEGER NOT NULL DEFAULT 8 CHECK (max_party_size > 0),
  waitlist_enabled     INTEGER NOT NULL DEFAULT 1 CHECK (waitlist_enabled IN (0, 1)),
  waitlist_mode        TEXT NOT NULL DEFAULT 'auto_offer' CHECK (waitlist_mode IN ('auto_offer', 'manual')),
  allow_pay_later      INTEGER NOT NULL DEFAULT 0 CHECK (allow_pay_later IN (0, 1)),
  allow_trial          INTEGER NOT NULL DEFAULT 0 CHECK (allow_trial IN (0, 1)),
  cancel_policy        TEXT,        -- JSON override of the booking settings' cancellation policy
  created_by           INTEGER REFERENCES staff_users(id),
  created_at           TEXT NOT NULL,
  updated_at           TEXT NOT NULL,
  archived_at          TEXT,
  CHECK (min_age_months <= max_age_months),
  CHECK (registration_level <> 'guest' OR parent_must_stay = 1)
);
CREATE INDEX ix_activities_status ON activities(status);

CREATE TABLE activity_sessions(
  id              INTEGER PRIMARY KEY,
  activity_id     INTEGER NOT NULL REFERENCES activities(id),
  date            TEXT NOT NULL,     -- YYYY-MM-DD (UK local)
  start_time      TEXT NOT NULL,     -- HH:MM (UK local)
  end_time        TEXT NOT NULL,
  theme           TEXT,
  centre_id       INTEGER REFERENCES centres(id),   -- overrides the activity's centre
  capacity        INTEGER NOT NULL CHECK (capacity >= 0),
  price_pence     INTEGER CHECK (price_pence >= 0), -- overrides the activity's price
  status          TEXT NOT NULL DEFAULT 'scheduled' CHECK (status IN ('scheduled', 'cancelled')),
  cancelled_reason TEXT,
  staff_notes     TEXT,
  created_at      TEXT NOT NULL,
  UNIQUE (activity_id, date, start_time),
  CHECK (start_time < end_time)
);
CREATE INDEX ix_sessions_date ON activity_sessions(date);

INSERT INTO centres(name, address, postcode) VALUES
  ('Honeycombe Arts Hub, Boscombe', 'Boscombe, Bournemouth', NULL);

INSERT INTO activity_categories(key, name, report_group, colour, sort) VALUES
  ('haf', 'HAF', 'HAF', '#2e7d32', 1),
  ('holiday_club', 'Holiday club', 'Holiday club', '#f57c00', 2),
  ('saturday_club', 'Saturday club', 'Saturday club', '#6a1b9a', 3),
  ('home_ed', 'Home Ed', 'Home Ed', '#1565c0', 4),
  ('baby_toddler', 'Baby & toddler', 'Baby & toddler', '#ad1457', 5),
  ('events', 'Events', 'Events', '#00838f', 6),
  ('young_adults', 'Young adults', 'Young adults', '#4e342e', 7);

-- The activities from the charity's email, as drafts for staff to finish
-- (sessions, prices and capacities are theirs to set before publishing).
INSERT INTO activities(slug, title, category_id, centre_id, summary, min_age_months, max_age_months,
                       registration_level, parent_must_stay, requires_approval, haf_only, price_pence,
                       capacity_default, age_basis, created_at, updated_at)
SELECT v.slug, v.title, (SELECT id FROM activity_categories WHERE key = v.cat), 1, v.summary, v.min_m, v.max_m,
       v.level, v.stay, 0, v.haf, v.price, v.cap, v.basis, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
       strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
FROM (SELECT 'giggles-and-wriggles-babies' AS slug, 'Giggles and Wriggles Babies' AS title, 'baby_toddler' AS cat,
             'Messy play and music for babies, with their grown-up.' AS summary, 0 AS min_m, 23 AS max_m,
             'short' AS level, 1 AS stay, 0 AS haf, 0 AS price, 12 AS cap, 'session_date' AS basis
      UNION ALL SELECT 'giggles-and-wriggles-toddlers', 'Giggles and Wriggles Toddlers', 'baby_toddler',
             'Creative play for toddlers, with their grown-up.', 24, 59, 'short', 1, 0, 0, 12, 'session_date'
      UNION ALL SELECT 'home-ed-programme-term-5-2026', 'Home Ed Programme Term 5 2026', 'home_ed',
             'Weekly creative sessions for home-educated children.', 60, 131, 'full', 0, 0, 0, 20, 'first_session'
      UNION ALL SELECT 'summer-club-2026', 'Summer Club 2026', 'holiday_club',
             'Arts-packed holiday club days.', 72, 155, 'full', 0, 0, 3000, 30, 'session_date'
      UNION ALL SELECT 'summer-haf', 'Summer HAF', 'haf',
             'Free holiday activities and food for children eligible for benefits-related free school meals.',
             72, 155, 'full', 0, 1, 0, 30, 'session_date') AS v;
UPDATE activities SET haf_allowance_days = 16 WHERE slug = 'summer-haf';

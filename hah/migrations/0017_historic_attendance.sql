-- Monthly attendance totals carried over from MagicBooking, so year-on-year
-- charts reach back before the switch-over. Totals only: no names.

CREATE TABLE historic_attendance(
  id           INTEGER PRIMARY KEY,
  month        TEXT NOT NULL CHECK (month GLOB '[12][0-9][0-9][0-9]-[01][0-9]'),
  category     TEXT NOT NULL,           -- a report group, e.g. "Holiday club"
  attendances  INTEGER NOT NULL CHECK (attendances >= 0),
  children     INTEGER CHECK (children IS NULL OR children >= 0),   -- different children, if known
  source       TEXT NOT NULL DEFAULT 'MagicBooking',
  created_by   INTEGER REFERENCES staff_users(id),
  created_at   TEXT NOT NULL,
  UNIQUE (month, category)
);

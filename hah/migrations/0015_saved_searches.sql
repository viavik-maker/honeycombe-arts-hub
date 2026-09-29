-- Searches staff save in Admin → People, for themselves or shared with colleagues.

CREATE TABLE saved_searches(
  id          INTEGER PRIMARY KEY,
  staff_id    INTEGER NOT NULL REFERENCES staff_users(id),
  name        TEXT NOT NULL,
  scope       TEXT NOT NULL CHECK (scope IN ('children', 'families')),
  q           TEXT NOT NULL DEFAULT '',
  filters     TEXT NOT NULL DEFAULT '{}',   -- JSON
  shared      INTEGER NOT NULL DEFAULT 0 CHECK (shared IN (0, 1)),
  created_at  TEXT NOT NULL
);
CREATE INDEX ix_saved_searches_staff ON saved_searches(staff_id);

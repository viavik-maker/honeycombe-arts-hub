-- Messages staff send to many families at once (Admin → Messages). Each
-- recipient's copy is a row in message_deliveries with this campaign_id.

CREATE TABLE campaigns(
  id          INTEGER PRIMARY KEY,
  kind        TEXT NOT NULL CHECK (kind IN ('service', 'marketing')),
  channel     TEXT NOT NULL CHECK (channel IN ('email', 'sms')),
  subject     TEXT,
  body        TEXT NOT NULL,
  audience    TEXT NOT NULL,              -- JSON: who it went to (the rule, not the list)
  audience_label TEXT NOT NULL,
  recipients  INTEGER NOT NULL DEFAULT 0,
  created_by  INTEGER REFERENCES staff_users(id),
  created_at  TEXT NOT NULL
);
CREATE INDEX ix_deliveries_campaign ON message_deliveries(campaign_id);

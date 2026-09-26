-- Twilio delivery reports and replies, and the injury body map.

-- what Twilio last told us about a text (queued/sent/delivered/undelivered/failed)
ALTER TABLE message_deliveries ADD COLUMN delivery_status TEXT;
ALTER TABLE message_deliveries ADD COLUMN delivered_at TEXT;
CREATE INDEX ix_deliveries_provider ON message_deliveries(provider_id);

-- numbers that replied STOP: no texts of any kind until they reply START
CREATE TABLE sms_blocks(
  phone       TEXT PRIMARY KEY,          -- +447…
  blocked_at  TEXT NOT NULL,
  source      TEXT NOT NULL CHECK (source IN ('reply_stop', 'provider_opt_out', 'staff'))
);

-- where on the body an injury is: JSON [{"view":"front"|"back","x":0-100,"y":0-100,"note":"…"}]
ALTER TABLE incidents ADD COLUMN body_map TEXT;

-- The shop (replacing MagicBooking's e-shop): things families can buy, such
-- as T-shirts, art packs or gift vouchers. Orders are paid through the same
-- invoices and payments as bookings, so Finance sees everything in one place.

CREATE TABLE shop_products(
  id              INTEGER PRIMARY KEY,
  slug            TEXT NOT NULL UNIQUE,
  title           TEXT NOT NULL,
  description     TEXT,
  image           TEXT,
  price_pence     INTEGER NOT NULL CHECK (price_pence BETWEEN 0 AND 100000),
  stock           INTEGER CHECK (stock IS NULL OR stock >= 0),     -- NULL = no limit
  max_per_order   INTEGER NOT NULL DEFAULT 10 CHECK (max_per_order BETWEEN 1 AND 100),
  status          TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'live', 'archived')),
  sort            INTEGER NOT NULL DEFAULT 0,
  created_at      TEXT NOT NULL,
  updated_at      TEXT NOT NULL
);

CREATE TABLE shop_orders(
  id              INTEGER PRIMARY KEY,
  ref             TEXT NOT NULL UNIQUE,
  account_id      INTEGER NOT NULL REFERENCES accounts(id),
  invoice_id      INTEGER REFERENCES invoices(id),
  idempotency_key TEXT NOT NULL,
  pay_mode        TEXT NOT NULL CHECK (pay_mode IN ('card', 'on_collection')),
  status          TEXT NOT NULL DEFAULT 'new'
                  CHECK (status IN ('new', 'ready', 'collected', 'posted', 'cancelled')),
  total_pence     INTEGER NOT NULL,
  notes           TEXT,                     -- from the family, e.g. sizes
  staff_notes     TEXT,
  created_at      TEXT NOT NULL,
  updated_at      TEXT NOT NULL,
  closed_at       TEXT,
  UNIQUE (account_id, idempotency_key)
);
CREATE INDEX ix_shop_orders_status ON shop_orders(status);
CREATE INDEX ix_shop_orders_account ON shop_orders(account_id);

CREATE TABLE shop_order_lines(
  id              INTEGER PRIMARY KEY,
  order_id        INTEGER NOT NULL REFERENCES shop_orders(id),
  product_id      INTEGER NOT NULL REFERENCES shop_products(id),
  title           TEXT NOT NULL,
  unit_pence      INTEGER NOT NULL,
  quantity        INTEGER NOT NULL CHECK (quantity > 0),
  amount_pence    INTEGER NOT NULL
);

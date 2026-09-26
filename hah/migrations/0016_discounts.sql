-- Sibling and multi-day discounts (set in Booking settings; off until a percentage is set).
-- price_pence stays what the family pays; discount_pence is how much was taken off.

ALTER TABLE bookings ADD COLUMN discount_pence INTEGER NOT NULL DEFAULT 0 CHECK (discount_pence >= 0);
ALTER TABLE bookings ADD COLUMN discount_reason TEXT;

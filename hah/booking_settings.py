"""Booking settings staff can change (admin → Booking settings).

Stored as JSON in the settings table, one row per key; anything not saved
yet uses DEFAULTS. Secrets (Stripe, SMTP, Twilio keys) are never settings:
they live in the host's environment."""
import json

from . import audit, db, validate
from .validate import Invalid
from .web import route

DEFAULTS = {
    # switching over from MagicBooking: Book Now links go to /book once this is on
    "booking_live": False,
    "bookings_open_message": "",           # shown on /book before booking_live, e.g. "Bookings open on 1 February"
    # policies
    "hold_minutes": 35,                    # a place is held this long while someone pays by card
    "cancel_cutoff_hours": 48,             # parents can cancel up to this long before a session
    "refund_days": 7,                      # cancelled at least this many days before → refund to card
    "waitlist_offer_hours": 24,            # how long a waiting-list offer stays open
    "waitlist_offer_hours_soon": 2,        # ... when the session is within 48 hours
    "manual_waitlist_release_hours": 48,   # manual mode: direct booking resumes if nobody acts
    "pay_later_for_all": False,            # otherwise only families staff have allowed
    "payment_terms_days": 14,
    "go_home_alone_min_age": 11,
    "bank_holidays": [],                   # skipped by the session generator
    "session_reminder_sms": False,
    "reporting_year_start_month": 1,       # 1 = calendar year, 4 = April, 9 = September
    # invoices
    "issuer_name": "Honeycombe Arts Hub",
    "issuer_address": "Boscombe, Bournemouth",
    "charity_number": "",
    "ofsted_urn": "EY496668",
    "bank_name": "",
    "bank_sort_code": "",
    "bank_account_number": "",
    "invoice_footer": "Thank you for booking with Honeycombe Arts Hub.",
    "invoice_prefix": "HAH",
    "credit_note_prefix": "CN",
    # who hears about what
    "dsl_notify_emails": [],
    "send_notify_emails": [],              # told (without details) when a SEND support request arrives
    "send_response_days": 5,               # "our SEND lead gets in touch within N working days"
    "finance_notify_emails": [],
    # discounts (0 = off). One discount per place: the bigger one.
    "sibling_discount_percent": 0,         # 2nd and later child of a family on the same session
    "multi_day_discount_percent": 0,       # the same child booked on several sessions of one activity at once
    "multi_day_min_sessions": 5,
    # data retention (families are told these in the privacy notice)
    "retention_inactive_years": 3,         # unused accounts: warned, then deleted 30 days later
    "retention_register_years": 3,         # who collected whom is cleared from registers after this
    "retention_message_years": 2,          # the message archive
    "retention_audit_years": 6,            # the audit log (never less than 6)
    "turned_18_days": 90,                  # a child who turns 18 leaves the parent's account after this
    # messages
    "sms_segment_pence": 4,                # what one text (160 characters) costs, for estimates
}

LIMITS = {"hold_minutes": (31, 120), "cancel_cutoff_hours": (0, 720), "refund_days": (0, 90),
          "waitlist_offer_hours": (1, 168), "waitlist_offer_hours_soon": (1, 48),
          "manual_waitlist_release_hours": (1, 336), "payment_terms_days": (0, 90), "go_home_alone_min_age": (8, 18),
          "reporting_year_start_month": (1, 12), "send_response_days": (1, 30),
          "sms_segment_pence": (0, 100), "sibling_discount_percent": (0, 75), "multi_day_discount_percent": (0, 75),
          "multi_day_min_sessions": (2, 60), "retention_inactive_years": (1, 10), "retention_register_years": (1, 10),
          "retention_message_years": (1, 10), "retention_audit_years": (6, 20), "turned_18_days": (14, 365)}


def get_all(c=None):
    def load(c):
        stored = {r["key"]: json.loads(r["value"]) for r in c.execute("SELECT key, value FROM settings")}
        return {k: stored.get(k, v) for k, v in DEFAULTS.items()}
    if c is not None:
        return load(c)
    with db.read() as c:
        return load(c)


def get(key, c=None):
    return get_all(c)[key]


def _clean(key, value):
    default = DEFAULTS[key]
    if isinstance(default, bool):
        return bool(value)
    if isinstance(default, int):
        try:
            n = int(value)
        except (TypeError, ValueError):
            raise Invalid({key: "Enter a whole number."})
        lo, hi = LIMITS.get(key, (0, 10 ** 6))
        if not lo <= n <= hi:
            raise Invalid({key: "Enter a number from %d to %d." % (lo, hi)})
        return n
    if isinstance(default, list):
        items = value if isinstance(value, list) else str(value or "").replace(",", "\n").split("\n")
        items = [validate.text(x, 200) for x in items if validate.text(x, 200)]
        if key == "bank_holidays":
            bad = [x for x in items if not validate.date(x)]
            if bad:
                raise Invalid({key: "Dates must look like 2026-12-25 (%s isn't)." % bad[0]})
            items = sorted(set(items))
        if key.endswith("_emails"):
            bad = [x for x in items if not validate.email(x)]
            if bad:
                raise Invalid({key: "%s isn't an email address." % bad[0]})
            items = [validate.email(x) for x in items]
        return items
    limit = 2000 if key in ("invoice_footer", "issuer_address", "bookings_open_message") else 200
    return validate.long_text(value, limit) if limit > 200 else validate.text(value, limit)


def save(c, h, changes):
    unknown = set(changes) - set(DEFAULTS)
    if unknown:
        raise ValueError("unknown setting: %s" % sorted(unknown)[0])
    cleaned = {k: _clean(k, v) for k, v in changes.items()}
    switching_on = cleaned.get("booking_live") and not get("booking_live", c)
    staff = h.staff() if h is not None else None
    for k, v in cleaned.items():
        c.execute("INSERT INTO settings(key, value, updated_at, updated_by_staff_id) VALUES (?,?,?,?)"
                  " ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at,"
                  " updated_by_staff_id=excluded.updated_by_staff_id",
                  (k, json.dumps(v), db.now(), staff["id"] if staff else None))
    if cleaned and h is not None:
        audit.record(c, h, "settings.booking", details={"changed": sorted(cleaned)})
    if switching_on:
        from .content import patch_membership_copy
        patched = patch_membership_copy()
        if h is not None:
            audit.record(c, h, "site.membership_copy_updated", details={"fields": patched})
    return cleaned


@route("GET", "/api/staff/settings/booking", auth="staff", perm="activities.view")
def settings_get(h):
    from . import payments_stripe
    return h.json({"settings": get_all(), "stripe": payments_stripe.status()})


@route("POST", "/api/staff/settings/booking", auth="staff", perm="settings.manage")
def settings_post(h):
    d = h.json_body() or {}
    with db.tx() as c:
        save(c, h, d.get("settings") or {})
    return h.json({"ok": True, "settings": get_all()})

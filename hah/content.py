"""The editable website content (data/content.json) and its bundled defaults."""
import json
import os
import shutil

from . import config
from .storage import load_json, path

_seed_cache = {}


def seed_content():
    """The bundled defaults (seed/content.json), read once."""
    if not _seed_cache:
        try:
            with open(os.path.join(config.SEED, "content.json"), encoding="utf-8") as f:
                _seed_cache.update(json.load(f))
        except (OSError, ValueError):
            _seed_cache["pages"] = {}
    return _seed_cache


def with_page_defaults(c):
    """Fill in any page copy the stored content.json doesn't have yet.

    Sites that went live before the Contact / Get Involved pages became
    editable keep their content.json on a persistent disk, so it has no
    "pages" section — fall back to the bundled copy until staff save."""
    pages = c.get("pages")
    pages = dict(pages) if isinstance(pages, dict) else {}
    for key, default in (seed_content().get("pages") or {}).items():
        if not isinstance(pages.get(key), dict):
            pages[key] = default
    c["pages"] = pages
    return c


def site_content():
    """content.json as the site and the admin see it (page defaults filled in)."""
    return with_page_defaults(dict(load_json("content.json", {})))


def public_content():
    """content.json with sensitive settings (SMTP credentials) stripped, for public use."""
    c = site_content()
    s = dict(c.get("settings", {}))
    s.pop("smtp", None)
    s.update(_booking_flags())
    c["settings"] = s
    return c


def _booking_flags():
    """Whether online booking is live, and which What's On events book which
    activity — so Book Now links can switch over without a deploy."""
    try:
        from . import booking_settings, db
        with db.read() as c:
            live = bool(booking_settings.get("booking_live", c))
            links = {r["event_id"]: r["slug"] for r in c.execute(
                "SELECT event_id, slug FROM activities WHERE event_id IS NOT NULL AND status='published'")}
    except Exception:  # database not ready (first start-up)
        return {"bookingLive": False, "eventActivities": {}}
    return {"bookingLive": live, "eventActivities": links}


def bootstrap_seed():
    """On a fresh persistent disk the committed data/ files are shadowed by the
    mount, so copy bundled defaults from seed/ into DATA when they're missing."""
    for name in ("content.json",):
        dest, src = path(name), os.path.join(config.SEED, name)
        if not os.path.exists(dest) and os.path.exists(src):
            shutil.copy(src, dest)


# The original membership wording, and what replaces it once online booking
# is live. Only text still exactly as first written is changed, so anything
# staff have reworded in the admin is left alone.
MEMBERSHIP_COPY = [
    (("pages", "getInvolved", "metaDescription"), "or become a member — become part", "— become part"),
    (("pages", "getInvolved", "sections", 4, "eyebrow"), "Already a member?", "Already booked with us?"),
    (("pages", "getInvolved", "sections", 4, "heading"), "Members area", "Your account"),
    (("pages", "getInvolved", "sections", 4, "body"),
     "Log in to our members area to book sessions and pay for activities, see your account history, update personal"
     " and medical information for your child, and download invoices.",
     "Sign in to book sessions and pay for activities, see your bookings, update your child's details and download"
     " invoices. There's no membership fee."),
    (("pages", "getInvolved", "sections", 4, "buttonLabel"), "Log in to members area", "Sign in to your account"),
    (("events", 0, "description"),
     "You must be a member to join: £15 per child per year, with free membership for families eligible for Free School"
     " Meals.",
     "There's no membership fee — book online, and families eligible for Free School Meals can request a free HAF"
     " place."),
    (("events", 5, "description"),
     "Annual membership (£15 per child) gives families access to all our arts programmes and events throughout the"
     " year.", "There's no membership fee."),
    (("events", 5, "price"), "Members · materials included", "Materials included"),
]


def patch_membership_copy():
    """Run once when online booking is switched on. Returns what changed."""
    from .storage import update_json
    changed = []

    def patch(c):
        for keys, old, new in MEMBERSHIP_COPY:
            node = c
            try:
                for k in keys[:-1]:
                    node = node[k]
                value = node[keys[-1]]
            except (KeyError, IndexError, TypeError):
                continue
            if isinstance(value, str) and old in value:
                node[keys[-1]] = value.replace(old, new)
                changed.append("/".join(str(k) for k in keys))
        return c

    update_json("content.json", {}, patch)
    return changed

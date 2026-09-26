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
    c["settings"] = s
    return c


def bootstrap_seed():
    """On a fresh persistent disk the committed data/ files are shadowed by the
    mount, so copy bundled defaults from seed/ into DATA when they're missing."""
    for name in ("content.json",):
        dest, src = path(name), os.path.join(config.SEED, name)
        if not os.path.exists(dest) and os.path.exists(src):
            shutil.copy(src, dest)

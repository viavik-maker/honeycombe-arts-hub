"""The family portal's pages (register, sign in, account, family, booking).

They're one page shell (public/portal/shell.html) that the portal script
fills in; no family data is ever put into the page itself — the script
fetches it from /api/account/*. None of these pages are indexed."""
from .markup import attr
from .pages import page
from .web import route

PAGES = {
    "/register": ("register", "Create an account"),
    "/register/details": ("register-details", "Your details"),
    "/login": ("login", "Sign in"),
    "/forgot-password": ("forgot", "Forgotten password"),
    "/reset-password": ("reset", "Choose a new password"),
    "/activate": ("activate", "Activate your account"),
    "/account": ("account", "My account"),
    "/account/family/new": ("child-new", "Add a child"),
    "/account/details": ("details", "Your details"),
    "/account/bookings": ("bookings", "My bookings"),
    "/book": ("book", "Book activities"),
    "/book/review": ("book-review", "Your booking"),
    "/book/done": ("book-done", "Booking complete"),
    "/book/guest/confirm": ("guest-confirm", "Confirm your booking"),
}


def _register(path, name, title):
    route("GET", path)(lambda h: shell(h, name, title))


def shell(h, name, title, **extra):
    subs = {"portal_page": attr(name), "portal_title": attr(title)}
    subs.update({k: attr(v) for k, v in extra.items()})
    return page(h, "portal/shell.html", private=True, subs=subs)


for _path, (_name, _title) in PAGES.items():
    _register(_path, _name, _title)


@route("GET", "/account/family/<ref>")
def child_page(h, ref):
    return shell(h, "child", "Your child's details")


@route("GET", "/book/<slug>/guest")
def guest_page(h, slug):
    return shell(h, "guest", "Book a place")

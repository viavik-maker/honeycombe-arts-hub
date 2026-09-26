"""Email and SMS templates (hah/templates/).

Templates are plain text with {{name}} placeholders and the same light
markup as the website's editable copy (blank line = new paragraph,
**bold**, [label](link)). The HTML version is made from the same text, so
the two can't drift apart, and every value is escaped in the HTML.

An email template's first line is its subject:  "Subject: Your booking"."""
import os
import re

from .markup import html as esc_html
from .markup import rich

TEMPLATES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
_VAR_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")
SECRET = "{{SECRET}}"  # stands in for a one-time link until the moment of sending


def _read(channel, name):
    """A template file; NAME without an extension means NAME.txt."""
    fn = name if "." in name else name + ".txt"
    with open(os.path.join(TEMPLATES, channel, fn), encoding="utf-8") as f:
        return f.read()


def fill(text, context):
    """Replace {{name}} with context values; {{SECRET}} is left for the sender."""
    def sub(m):
        if m.group(1) == "SECRET":
            return m.group(0)
        v = context.get(m.group(1), "")
        return "" if v is None else str(v)
    return _VAR_RE.sub(sub, text)


def render_email(key, context):
    """(subject, text body, html body)."""
    raw = _read("email", key)
    first, _, body = raw.partition("\n")
    if not first.startswith("Subject:"):
        raise ValueError("email template %s must start with 'Subject:'" % key)
    subject = " ".join(fill(first[len("Subject:"):], context).split())
    body = body.strip("\n")
    text = fill(body, context)
    return subject, plain_link(text) + "\n", html_layout(subject, _html_body(body, context))


def _html_body(body, context):
    """Markup is applied to the template only; values are then slotted in as
    escaped plain text, so a value can never add a link or formatting (a
    contact-form message containing [click](https://…) stays plain text).
    Values named *_url are system-generated links and may be used in [label](…)."""
    urls = {k: v for k, v in context.items() if k.endswith("_url")}
    markers = {}

    def marker(m):
        name = m.group(1)
        if name == "SECRET":
            return m.group(0)
        if name in urls:
            return str(urls[name])
        markers[name] = "\u0001%d\u0002" % len(markers) if name not in markers else markers[name]
        return markers[name]

    html_out = rich(_VAR_RE.sub(marker, body))
    for name, mk in markers.items():
        v = context.get(name, "")
        html_out = html_out.replace(mk, esc_html("" if v is None else str(v)).replace("\n", "<br>"))
    return html_out


def render_sms(key, context):
    return " ".join(fill(_read("sms", key), context).split())


def html_layout(title, content_html):
    return _read("email", "_layout.html").replace("{{title}}", esc_html(title)).replace("{{content}}", content_html)


def plain_link(text):
    """[label](url) -> 'label: url' for the plain-text version of an email."""
    return re.sub(r"\[([^\]\n]+)\]\(([^)\s]+)\)", r"\1: \2", text)

"""Escaping and the small, safe markup subset staff can use in page copy."""
import re


def attr(s):
    return (s or "").replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")


html = attr  # same escaping; separate name for readability at call sites

BOLD_RE = re.compile(r"\*\*(.+?)\*\*", re.S)
LINK_RE = re.compile(r"\[([^\]\n]+)\]\(([^)\s]+)\)")


def safe_url(url):
    """Only let staff copy link to places a link can sensibly go."""
    u = (url or "").strip()
    return u if u.startswith(("https://", "http://", "mailto:", "tel:", "/", "#")) else ""


def ext(url):
    return ' target="_blank" rel="noopener"' if url.startswith(("http://", "https://")) else ""


def rich(text):
    """Staff copy -> HTML paragraphs.

    Everything is escaped first; only a tiny, safe subset is then allowed:
    a blank line starts a new paragraph, **bold**, and [label](link)."""
    out = []
    for para in re.split(r"\n\s*\n", (text or "").strip()):
        if not para.strip():
            continue
        body = html(para.strip()).replace("\n", "<br>")
        body = BOLD_RE.sub(lambda m: "<strong>%s</strong>" % m.group(1), body)

        def link(m):
            url = safe_url(m.group(2))
            # a link we can't allow is left exactly as typed, so staff can see why
            return '<a href="%s"%s>%s</a>' % (url, ext(url), m.group(1)) if url else m.group(0)

        out.append("<p>%s</p>" % LINK_RE.sub(link, body))
    return "\n".join(out)

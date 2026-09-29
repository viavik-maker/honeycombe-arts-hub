"""Server-side page rendering: partials, editable page blocks, SEO tags and
the public content payload (window.HAH)."""
import json
import os
import re
import urllib.parse

from . import config
from .content import public_content, seed_content
from .markup import attr, ext, html, rich, safe_url

PRETTY = {
    "/": "index.html",
    "/whats-on": "whats-on.html",
    "/past-events": "past-events.html",
    "/about": "about.html",
    "/gallery": "gallery.html",
    "/get-involved": "get-involved.html",
    "/holiday-club": "holiday-club.html",
    "/arts-award": "arts-award.html",
    "/emerging-artists": "emerging-artists.html",
    "/testimonials": "testimonials.html",
    "/contact": "contact.html",
    "/policies": "policies.html",
    "/privacy": "privacy.html",
    "/safeguarding": "safeguarding.html",
    "/admin": "admin/index.html",
    "/admin/offline-register": "admin/offline.html",
}

INCLUDE_RE = re.compile(r"<!--#include\s+([\w.-]+)\s*-->")
# <!--#block name--> markers are filled from the editable page copy in content.json
BLOCK_RE = re.compile(r"<!--#block\s+([\w-]+)\s*-->")


SITE = "https://honeycombeartshub.org.uk"
SHARE_IMG = SITE + "/img/og-image.jpg"
TITLE_RE = re.compile(r"<title>(.*?)</title>", re.S)
DESC_RE = re.compile(r'<meta\s+name="description"\s+content="(.*?)"', re.S | re.I)


def page_copy(content, name):
    p = (content.get("pages") or {}).get(name)
    return p if isinstance(p, dict) else (seed_content().get("pages") or {}).get(name, {})


def _meta_block(name, fallback_title):
    """<title> + description for a page whose copy staff can edit."""
    def build(content):
        p = page_copy(content, name)
        return '<title>%s</title>\n  <meta name="description" content="%s">' % (
            html(p.get("metaTitle") or fallback_title), attr(p.get("metaDescription") or ""))
    return build


def _hero(eyebrow, heading, intro, intro_style=""):
    style = ' style="%s"' % intro_style if intro_style else ""
    return "\n        ".join(bit for bit in (
        '<span class="eyebrow">%s</span>' % html(eyebrow) if eyebrow else "",
        "<h1>%s</h1>" % html(heading) if heading else "",
        "<p%s>%s</p>" % (style, html(intro)) if intro else "",
    ) if bit)


# ---------------- contact page

def _contact_link(card, settings):
    """The email / phone / address a contact card points at — taken from
    Settings so the cards can never drift out of step with the real details."""
    kind = card.get("link") or "none"
    if kind in ("emailGeneral", "emailTrustees", "emailSupport"):
        v = settings.get(kind, "")
        return '<a href="mailto:%s">%s</a>' % (attr(v), html(v)) if v else ""
    if kind == "phone":
        v = settings.get("phone", "")
        return '<a href="tel:%s">%s</a>' % (attr(v.replace(" ", "")), html(v)) if v else ""
    if kind == "address":
        return html(settings.get("address", ""))
    if kind == "custom":
        url = safe_url(card.get("linkUrl"))
        if url:
            return '<a href="%s"%s>%s</a>' % (attr(url), ext(url), html(card.get("linkLabel") or url))
    return ""


def _b_contact_hero(content):
    p = page_copy(content, "contact")
    return _hero(p.get("eyebrow"), p.get("heading"), p.get("intro"),
                 "max-width:36em; color:var(--muted); font-size:1.12rem")


def _b_contact_cards(content):
    settings = content.get("settings", {})
    cards = []
    for card in page_copy(content, "contact").get("cards", []):
        body = "<br>".join(bit for bit in (html(card.get("text", "")),
                                           _contact_link(card, settings)) if bit)
        cards.append("""<div class="contact-card reveal">
                <span class="ico">%s</span>
                <div>
                  <h2>%s</h2>
                  <p>%s</p>
                </div>
              </div>""" % (html(card.get("icon", "")), html(card.get("title", "")), body))
    return "\n              ".join(cards)


def _b_contact_opening(content):
    p = page_copy(content, "contact")
    heading = p.get("openingHeading") or ""
    rows = "".join(
        '<div class="event-side__row"><span>%s</span><strong>%s</strong></div>'
        % (html(o.get("label", "")), html(o.get("value", "")))
        for o in content.get("settings", {}).get("openingTimes", []))
    if not heading and not rows:
        return ""
    return """<div class="event-side__card reveal" style="margin-top:1.1rem">
              <div class="event-side__body">
                %s
                <div id="openingTimes">%s</div>
              </div>
            </div>""" % ('<h3 style="margin-bottom:.6em">%s</h3>' % html(heading) if heading else "", rows)


def _b_contact_form_head(content):
    p = page_copy(content, "contact")
    return "\n              ".join(bit for bit in (
        '<h2 style="font-size:1.6rem">%s</h2>' % html(p.get("formHeading")) if p.get("formHeading") else "",
        '<p class="form-note">%s</p>' % html(p.get("formNote")) if p.get("formNote") else "",
    ) if bit)


def _b_contact_map(content):
    p = page_copy(content, "contact")
    settings = content.get("settings", {})
    address = settings.get("address") or ""
    if not p.get("showMap", True) or not address:
        return ""
    # Google Maps only loads when the visitor asks for it: no third-party
    # cookies or requests until then (see the privacy notice).
    q = urllib.parse.quote(address, safe="")
    return """<div class="map-frame map-frame--consent reveal" data-map-src="https://www.google.com/maps?q=%s&amp;output=embed"
             data-map-title="Map showing where to find %s">
          <p>The map is provided by Google, which may set cookies when it loads.</p>
          <p><button type="button" class="btn btn--navy btn--sm" data-map-load>Show the map</button>
             <a class="btn btn--ghost btn--sm" href="https://www.google.com/maps/search/?api=1&amp;query=%s" target="_blank" rel="noopener">Open in Google Maps</a></p>
        </div>""" % (q, html(settings.get("siteName") or "us"), q)


# ---------------- get involved page

SETTING_LINKS = ("bookingUrl", "donateUrl", "volunteerUrl", "seesawUrl",
                 "facebook", "instagram", "twitter", "youtube")


def _section_href(section, settings):
    kind = section.get("buttonLink") or ""
    if kind == "custom":
        return safe_url(section.get("buttonUrl"))
    if kind == "contact":
        return "/contact"
    if kind == "bookingUrl" and settings.get("bookingLive"):
        return "/account"
    if kind in SETTING_LINKS:
        return safe_url(settings.get(kind))
    return ""


def _b_get_involved_hero(content):
    p = page_copy(content, "getInvolved")
    img = safe_url(p.get("heroImage"))
    style = ' style="--ph-img:url(\'%s\')"' % attr(img) if img else ""
    return """<section class="page-hero%s"%s>
      <div class="container">
        %s
      </div>
    </section>""" % (" page-hero--img" if img else "", style,
                     _hero(p.get("eyebrow"), p.get("heading"), p.get("intro")))


def _b_get_involved_sections(content):
    settings = content.get("settings", {})
    out = []
    for sec in page_copy(content, "getInvolved").get("sections", []):
        anchor = re.sub(r"[^a-z0-9-]+", "-", (sec.get("id") or "").lower()).strip("-")
        img = safe_url(sec.get("image"))
        href = _section_href(sec, settings)
        label = sec.get("buttonLabel") or ""
        style = sec.get("buttonStyle") or "orange"
        if style not in ("orange", "honey", "navy", "ghost"):
            style = "orange"
        body = "\n            ".join(bit for bit in (
            '<span class="eyebrow">%s</span>' % html(sec.get("eyebrow")) if sec.get("eyebrow") else "",
            "<h2>%s</h2>" % html(sec.get("heading")) if sec.get("heading") else "",
            rich(sec.get("body")),
            '<a class="btn btn--%s" href="%s"%s>%s</a>' % (style, attr(href), ext(href), html(label))
            if href and label else "",
        ) if bit)
        out.append("""<div class="feature-row reveal"%s>
          <div class="feature-row__media"><img src="%s" alt="%s"></div>
          <div>
            %s
          </div>
        </div>""" % (' id="%s"' % anchor if anchor else "", attr(img),
                     attr(sec.get("imageAlt", "")), body))
    return "\n\n        ".join(out)


BLOCKS = {
    "contact-meta": _meta_block("contact", "Contact Us — Honeycombe Arts Hub"),
    "contact-hero": _b_contact_hero,
    "contact-cards": _b_contact_cards,
    "contact-opening": _b_contact_opening,
    "contact-form-head": _b_contact_form_head,
    "contact-map": _b_contact_map,
    "get-involved-meta": _meta_block("getInvolved", "Get Involved — Honeycombe Arts Hub"),
    "get-involved-hero": _b_get_involved_hero,
    "get-involved-sections": _b_get_involved_sections,
}


def org_jsonld(s):
    data = {
        "@context": "https://schema.org", "@type": ["NGO", "LocalBusiness"],
        "name": "Honeycombe Arts Hub", "url": SITE,
        "logo": SITE + "/img/logo.png", "image": SHARE_IMG,
        "description": "A youth-focused community arts centre in Boscombe, Bournemouth — art, drama, music, film and friendship for children and young people aged 5–25.",
        "email": s.get("emailGeneral", "info@honeycombeartshub.org.uk"),
        "telephone": "+447932772905",
        "address": {"@type": "PostalAddress",
                    "streetAddress": "Units 4 & 5, The Sovereign Shopping Centre, 600 Christchurch Road",
                    "addressLocality": "Boscombe", "addressRegion": "Bournemouth",
                    "postalCode": "BH1 4SX", "addressCountry": "GB"},
        "sameAs": [s[k] for k in ("facebook", "instagram", "twitter", "youtube") if s.get(k)],
        "identifier": {"@type": "PropertyValue", "propertyID": "UK Registered Charity Number",
                       "value": s.get("charityNumber", "1127371")},
    }
    return json.dumps(data, ensure_ascii=False).replace("</", "<\\/")


def seo_head(html, canonical, settings):
    tm = TITLE_RE.search(html); title = tm.group(1).strip() if tm else "Honeycombe Arts Hub"
    dm = DESC_RE.search(html); desc = dm.group(1).strip() if dm else ""
    url = attr(SITE + (canonical or "/"))
    t, d = attr(title), attr(desc)
    return "\n".join([
        f'<link rel="canonical" href="{url}">',
        '<meta property="og:type" content="website">',
        '<meta property="og:site_name" content="Honeycombe Arts Hub">',
        f'<meta property="og:title" content="{t}">',
        f'<meta property="og:description" content="{d}">',
        f'<meta property="og:url" content="{url}">',
        f'<meta property="og:image" content="{SHARE_IMG}">',
        '<meta property="og:image:width" content="1200">',
        '<meta property="og:image:height" content="630">',
        '<meta property="og:image:alt" content="Honeycombe Arts Hub — supporting young and emerging creatives">',
        '<meta name="twitter:card" content="summary_large_image">',
        f'<meta name="twitter:title" content="{t}">',
        f'<meta name="twitter:description" content="{d}">',
        f'<meta name="twitter:image" content="{SHARE_IMG}">',
        f'<script type="application/ld+json">{org_jsonld(settings)}</script>',
    ])


def render_page(filename, canonical=None, nonce=None, seo=True, subs=None):
    path = os.path.join(config.PUBLIC, filename)
    with open(path, encoding="utf-8") as f:
        html = f.read()

    def inc(m):
        p = os.path.join(config.PARTIALS, m.group(1))
        try:
            with open(p, encoding="utf-8") as fh:
                return fh.read()
        except OSError:
            return ""

    html = INCLUDE_RE.sub(inc, html)
    content = public_content()
    if "<!--#block" in html:
        html = BLOCK_RE.sub(lambda m: BLOCKS.get(m.group(1), lambda _c: "")(content), html)
    for key, value in (subs or {}).items():  # {{name}} values for page shells (already escaped)
        html = html.replace("{{%s}}" % key, value)
    if seo and "</head>" in html:
        html = html.replace("</head>", seo_head(html, canonical, content.get("settings", {})) + "\n</head>", 1)
    if "<!--#data-->" in html:
        payload = json.dumps(content, ensure_ascii=False).replace("</", "<\\/")
        tag = '<script nonce="%s">' % nonce if nonce else "<script>"
        html = html.replace("<!--#data-->", f"{tag}window.HAH={payload}</script>")
    return html.encode("utf-8")

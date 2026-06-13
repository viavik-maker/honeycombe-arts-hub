/* Honeycombe Arts Hub — front-end behaviour
   Content is injected server-side as window.HAH               */
(function () {
  "use strict";
  const C = window.HAH || {};
  const S = C.settings || {};
  const $ = (sel, el) => (el || document).querySelector(sel);
  const $$ = (sel, el) => Array.from((el || document).querySelectorAll(sel));
  const esc = (s) => String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

  /* ---------- header / shared chrome ---------- */
  const header = $("#siteHeader");
  if (header) {
    addEventListener("scroll", () => header.classList.toggle("scrolled", scrollY > 12), { passive: true });
  }
  const burger = $("#burger"), nav = $("#mainNav");
  if (burger && nav) {
    burger.addEventListener("click", () => {
      const open = nav.classList.toggle("open");
      burger.classList.toggle("open", open);
      burger.setAttribute("aria-expanded", open);
    });
  }
  // active nav link
  const seg = location.pathname.split("/")[1] || "";
  $$(".nav a[data-nav]").forEach(a => {
    if (a.dataset.nav === seg) a.classList.add("active");
  });
  // dynamic links
  $$("[data-booking]").forEach(a => a.href = S.bookingUrl || "#");
  $$("[data-donate]").forEach(a => a.href = S.donateUrl || "#");
  $$("[data-charity]").forEach(el => el.textContent = S.charityNumber || "");
  $$("[data-ofsted]").forEach(el => el.textContent = S.ofstedNumber || "");
  const yr = $("#year"); if (yr) yr.textContent = new Date().getFullYear();

  // announcement bar
  const ann = $("#announce");
  if (ann && S.announcementOn && S.announcement && !sessionStorage.getItem("hah-ann-hide")) {
    $("#announceText").textContent = S.announcement;
    ann.hidden = false;
    $("#announceClose").addEventListener("click", () => {
      ann.hidden = true; sessionStorage.setItem("hah-ann-hide", "1");
    });
  }

  // footer contact + socials
  const fa = $("#footerAddress"); if (fa) fa.textContent = S.address || "";
  const fp = $("#footerPhone"); if (fp) { fp.textContent = S.phone || ""; fp.href = "tel:" + String(S.phone || "").replace(/\s/g, ""); }
  const fe = $("#footerEmail"); if (fe) { fe.textContent = S.emailGeneral || ""; fe.href = "mailto:" + (S.emailGeneral || ""); }
  const icons = {
    facebook: '<svg viewBox="0 0 24 24"><path d="M13.5 21v-7.4h2.5l.4-2.9h-2.9V8.8c0-.84.23-1.41 1.44-1.41h1.54V4.8c-.27-.04-1.18-.11-2.24-.11-2.22 0-3.74 1.35-3.74 3.84v2.14H8v2.9h2.5V21h3z"/></svg>',
    instagram: '<svg viewBox="0 0 24 24"><path d="M12 4.3c2.5 0 2.8 0 3.8.06 2.55.12 3.74 1.33 3.86 3.86.05 1 .06 1.3.06 3.78s-.01 2.79-.06 3.78c-.12 2.53-1.3 3.74-3.86 3.86-1 .05-1.3.06-3.8.06-2.5 0-2.8-.01-3.78-.06-2.56-.12-3.74-1.34-3.86-3.86-.05-1-.06-1.3-.06-3.78s.01-2.78.06-3.78C4.48 5.7 5.66 4.48 8.22 4.36c1-.05 1.3-.06 3.78-.06zM12 2.2c-2.54 0-2.86.01-3.86.06-3.4.16-5.3 2.05-5.46 5.46-.05 1-.06 1.32-.06 3.86s.01 2.87.06 3.87c.16 3.4 2.05 5.3 5.46 5.46 1 .04 1.32.05 3.86.05s2.87-.01 3.87-.05c3.4-.16 5.3-2.06 5.45-5.46.05-1 .06-1.33.06-3.87s-.01-2.86-.06-3.86c-.15-3.4-2.05-5.3-5.45-5.46-1-.05-1.33-.06-3.87-.06zm0 4.56a5.24 5.24 0 100 10.48 5.24 5.24 0 000-10.48zm0 8.64a3.4 3.4 0 110-6.8 3.4 3.4 0 010 6.8zm5.44-9.99a1.22 1.22 0 100 2.45 1.22 1.22 0 000-2.45z"/></svg>',
    twitter: '<svg viewBox="0 0 24 24"><path d="M18.9 2H22l-6.8 7.8L23.2 22h-6.3l-4.9-6.4L6.4 22H3.3l7.3-8.3L2.5 2h6.4l4.4 5.9L18.9 2zm-1.1 18.1h1.7L7.9 3.8H6.1l11.7 16.3z"/></svg>',
    youtube: '<svg viewBox="0 0 24 24"><path d="M21.6 7.2a2.5 2.5 0 00-1.76-1.77C18.25 5 12 5 12 5s-6.25 0-7.84.43A2.5 2.5 0 002.4 7.2 26 26 0 002 12a26 26 0 00.4 4.8 2.5 2.5 0 001.76 1.77C5.75 19 12 19 12 19s6.25 0 7.84-.43a2.5 2.5 0 001.76-1.77A26 26 0 0022 12a26 26 0 00-.4-4.8zM10 15.2V8.8l5.2 3.2-5.2 3.2z"/></svg>'
  };
  const fs = $("#footerSocial");
  if (fs) {
    [["facebook", S.facebook], ["instagram", S.instagram], ["twitter", S.twitter], ["youtube", S.youtube]]
      .filter(x => x[1])
      .forEach(([k, url]) => {
        const a = document.createElement("a");
        a.href = url; a.target = "_blank"; a.rel = "noopener";
        a.setAttribute("aria-label", k);
        a.innerHTML = icons[k];
        fs.appendChild(a);
      });
  }

  // newsletter
  const nf = $("#newsletterForm");
  if (nf) nf.addEventListener("submit", async (e) => {
    e.preventDefault();
    const email = nf.email.value.trim();
    try {
      const r = await fetch("/api/newsletter", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, website: nf.website.value })
      });
      if (r.ok) { $(".footer__newsrow", nf).style.display = "none"; $(".footer__newsdone", nf).hidden = false; }
    } catch (_) { /* offline */ }
  });

  /* ---------- scroll reveal ---------- */
  const io = new IntersectionObserver((entries) => {
    entries.forEach(en => { if (en.isIntersecting) { en.target.classList.add("in"); io.unobserve(en.target); } });
  }, { threshold: 0.12 });
  $$(".reveal").forEach(el => io.observe(el));

  /* ---------- helpers for renderers ---------- */
  function eventCard(ev) {
    return `<a class="event-card reveal" href="/whats-on/${esc(ev.id)}">
      <div class="event-card__media">
        <img src="${esc(ev.image)}" alt="${esc(ev.title)}" loading="lazy">
        ${ev.tag ? `<span class="event-card__tag">${esc(ev.tag)}</span>` : ""}
        ${ev.dates ? `<span class="event-card__date">${esc(ev.dates)}</span>` : ""}
      </div>
      <div class="event-card__body">
        <h3>${esc(ev.title)}</h3>
        <p>${esc(ev.summary)}</p>
        <div class="event-card__meta">
          ${ev.ages ? `<span class="chip">Ages ${esc(ev.ages)}</span>` : ""}
          ${ev.price ? `<span class="chip chip--price">${esc(ev.price)}</span>` : ""}
          ${ev.schedule ? `<span class="chip">${esc(ev.schedule)}</span>` : ""}
        </div>
        <span class="event-card__go">Find out more <span>→</span></span>
      </div>
    </a>`;
  }

  function observeNew(root) { $$(".reveal", root).forEach(el => io.observe(el)); }

  /* ---------- page renderers ---------- */
  const page = document.body.dataset.page;

  if (page === "home") {
    // rotating hero word
    const words = ["imagine", "create", "explore", "perform", "belong"];
    const swap = $(".swap");
    if (swap) {
      let i = 0;
      setInterval(() => {
        i = (i + 1) % words.length;
        swap.innerHTML = `<span class="word">${words[i]}</span>`;
      }, 2600);
    }
    // featured events
    const evWrap = $("#homeEvents");
    if (evWrap) {
      const featured = (C.events || []).filter(e => e.featured).slice(0, 4);
      const list = featured.length ? featured : (C.events || []).slice(0, 4);
      evWrap.innerHTML = list.map(eventCard).join("");
      observeNew(evWrap);
    }
    // impact
    const imWrap = $("#homeImpact");
    if (imWrap) {
      imWrap.innerHTML = (C.impact || []).map(s => `
        <div class="impact-card reveal"><strong>${esc(s.number)}</strong><span>${esc(s.label)}</span><p>${esc(s.detail)}</p></div>`).join("");
      observeNew(imWrap);
    }
    // gallery preview
    const gp = $("#homeGallery");
    if (gp) {
      const pics = (C.gallery || []).slice(0, 8);
      gp.innerHTML = pics.map((g, i) => `
        <figure class="reveal" data-i="${i}"><img src="${esc(g.src)}" alt="${esc(g.caption)}" loading="lazy"><figcaption>${esc(g.caption)}</figcaption></figure>`).join("");
      observeNew(gp);
      bindLightbox(gp, pics);
    }
    // testimonials slider
    const ts = $("#tslider");
    if (ts) {
      const qs = (C.testimonials || []).slice(0, 5);
      ts.innerHTML = qs.map((q, i) => `
        <div class="tslide${i === 0 ? " active" : ""}">
          <blockquote>“${esc(q.quote)}”</blockquote>
          <cite>${esc(q.author)}<small>${esc(q.role)}</small></cite>
        </div>`).join("");
      const dots = $("#tdots");
      dots.innerHTML = qs.map((_, i) => `<button${i === 0 ? ' class="active"' : ""} aria-label="Testimonial ${i + 1}"></button>`).join("");
      let cur = 0, timer = setInterval(() => go(cur + 1), 6000);
      function go(n) {
        cur = (n + qs.length) % qs.length;
        $$(".tslide", ts).forEach((s, i) => s.classList.toggle("active", i === cur));
        $$("button", dots).forEach((d, i) => d.classList.toggle("active", i === cur));
      }
      $$("button", dots).forEach((d, i) => d.addEventListener("click", () => { clearInterval(timer); go(i); }));
    }
  }

  if (page === "whats-on") {
    const events = C.events || [];
    const feat = events.find(e => e.featured) || events[0];
    const fWrap = $("#featuredEvent");
    if (fWrap && feat) {
      fWrap.innerHTML = `
        <img src="${esc(feat.image)}" alt="${esc(feat.title)}">
        <div class="featured-strip__body">
          <span class="eyebrow">Don’t miss</span>
          <h2>${esc(feat.title)}</h2>
          <p>${esc(feat.summary)}</p>
          <div class="event-card__meta" style="margin-bottom:1.4em">
            ${feat.dates ? `<span class="chip">${esc(feat.dates)}</span>` : ""}
            ${feat.ages ? `<span class="chip">Ages ${esc(feat.ages)}</span>` : ""}
            ${feat.price ? `<span class="chip">${esc(feat.price)}</span>` : ""}
          </div>
          <div><a class="btn btn--honey" href="/whats-on/${esc(feat.id)}">Find out more</a></div>
        </div>`;
    }
    const grid = $("#eventsGrid");
    if (grid) {
      grid.innerHTML = events.filter(e => e !== feat).map(eventCard).join("") || "<p>No events listed right now — check back soon!</p>";
      observeNew(grid);
    }
  }

  if (page === "event") {
    const slug = decodeURIComponent(location.pathname.split("/")[2] || "");
    const ev = (C.events || []).find(e => e.id === slug);
    const wrap = $("#eventDetail");
    if (!ev) {
      wrap.innerHTML = `<div class="center" style="padding:4rem 0">
        <h1>Event not found</h1><p>This event may have finished — see what else is on!</p>
        <a class="btn btn--honey" href="/whats-on">Back to What’s On</a></div>`;
    } else {
      document.title = ev.title + " — Honeycombe Arts Hub";
      $("#evTitle").textContent = ev.title;
      $("#evTag").textContent = ev.tag || "Event";
      $("#evSummary").textContent = ev.summary;
      $("#evBody").textContent = ev.description;
      $("#evImg").src = ev.image; $("#evImg").alt = ev.title;
      const rows = [["Dates", ev.dates], ["Schedule", ev.schedule], ["Ages", ev.ages], ["Price", ev.price]]
        .filter(r => r[1])
        .map(r => `<div class="event-side__row"><span>${r[0]}</span><strong>${esc(r[1])}</strong></div>`).join("");
      $("#evRows").innerHTML = rows;
      const btn = $("#evBook");
      if (ev.bookable) { btn.textContent = "Book via our booking portal"; btn.href = S.bookingUrl; btn.target = "_blank"; }
      else { btn.textContent = "Enquire about this event"; btn.href = "/contact"; }
    }
  }

  if (page === "past-events") {
    const wrap = $("#timeline");
    const items = (C.pastEvents || []).slice().sort((a, b) => String(b.date).localeCompare(String(a.date)));
    wrap.innerHTML = items.map(ev => `
      <article class="tl-item reveal">
        <div class="tl-media"><img src="${esc(ev.image)}" alt="${esc(ev.title)}" loading="lazy"></div>
        <div class="tl-body">
          <span class="tl-date">${esc(ev.dateLabel)}</span>
          <h3>${esc(ev.title)}</h3>
          <p>${esc(ev.description)}</p>
        </div>
      </article>`).join("");
    observeNew(wrap);
  }

  if (page === "gallery") {
    const all = C.gallery || [];
    const cats = ["All", ...new Set(all.map(g => g.category).filter(Boolean))];
    const fWrap = $("#galleryFilters"), gWrap = $("#galleryGrid");
    let current = "All", visible = all;
    fWrap.innerHTML = cats.map((c, i) => `<button${i === 0 ? ' class="active"' : ""} data-cat="${esc(c)}">${esc(c)}</button>`).join("");
    function draw() {
      visible = current === "All" ? all : all.filter(g => g.category === current);
      gWrap.innerHTML = visible.map((g, i) => `
        <figure data-i="${i}" class="reveal in"><img src="${esc(g.src)}" alt="${esc(g.caption)}" loading="lazy"><figcaption>${esc(g.caption)}</figcaption></figure>`).join("");
      bindLightbox(gWrap, visible);
    }
    fWrap.addEventListener("click", (e) => {
      const b = e.target.closest("button"); if (!b) return;
      current = b.dataset.cat;
      $$("button", fWrap).forEach(x => x.classList.toggle("active", x === b));
      draw();
    });
    draw();
  }

  if (page === "testimonials") {
    const wrap = $("#quoteWall");
    wrap.innerHTML = (C.testimonials || []).map(q => `
      <div class="quote-card reveal in">
        <p>${esc(q.quote)}</p>
        <cite>${esc(q.author)}<small>${esc(q.role)}</small></cite>
      </div>`).join("");
  }

  if (page === "about") {
    const vWrap = $("#valuesGrid");
    if (vWrap) {
      const icons = { orange: "🧡", honey: "✨", sage: "🌱", navy: "📣" };
      vWrap.innerHTML = (C.values || []).map(v => `
        <div class="value-card value-card--${esc(v.color)} reveal">
          <span class="value-card__icon">${icons[v.color] || "★"}</span>
          <h3>${esc(v.title)}</h3><p>${esc(v.text)}</p>
        </div>`).join("");
      observeNew(vWrap);
    }
    const iWrap = $("#aboutImpact");
    if (iWrap) {
      iWrap.innerHTML = (C.impact || []).map(s => `
        <div class="impact-card reveal"><strong>${esc(s.number)}</strong><span>${esc(s.label)}</span><p>${esc(s.detail)}</p></div>`).join("");
      observeNew(iWrap);
    }
  }

  if (page === "contact") {
    const form = $("#contactForm");
    const status = $("#contactStatus");
    if (form) form.addEventListener("submit", async (e) => {
      e.preventDefault();
      status.className = "form-status";
      const btn = $("button[type=submit]", form);
      btn.disabled = true; btn.textContent = "Sending…";
      try {
        const r = await fetch("/api/contact", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            name: form.name.value, email: form.email.value,
            phone: form.phone.value, message: form.message.value,
            website: form.website.value
          })
        });
        const d = await r.json();
        if (r.ok) {
          status.textContent = "Thank you! Your message is on its way to the team — we'll get back to you soon.";
          status.classList.add("ok"); form.reset();
        } else {
          status.textContent = d.error || "Something went wrong — please try again.";
          status.classList.add("err");
        }
      } catch (_) {
        status.textContent = "Couldn't reach the server — please try again, or email us directly.";
        status.classList.add("err");
      }
      btn.disabled = false; btn.textContent = "Send message";
    });
    // contact info
    const ce = $("#cEmail"); if (ce) { ce.textContent = S.emailGeneral; ce.href = "mailto:" + S.emailGeneral; }
    const ct = $("#cTrustees"); if (ct) { ct.textContent = S.emailTrustees; ct.href = "mailto:" + S.emailTrustees; }
    const cp = $("#cPhone"); if (cp) { cp.textContent = S.phone; cp.href = "tel:" + String(S.phone || "").replace(/\s/g, ""); }
    const ca = $("#cAddress"); if (ca) ca.textContent = S.address;
    const ot = $("#openingTimes");
    if (ot) ot.innerHTML = (S.openingTimes || []).map(o =>
      `<div class="event-side__row"><span>${esc(o.label)}</span><strong>${esc(o.value)}</strong></div>`).join("");
    const map = $("#mapFrame");
    if (map) map.src = "https://www.google.com/maps?q=" + encodeURIComponent(S.address || "Sovereign Shopping Centre Boscombe") + "&output=embed";
  }

  if (page === "get-involved" || page === "arts-award" || page === "holiday-club") {
    const sw = $("[data-seesaw]"); if (sw) sw.href = S.seesawUrl || "#";
    const vol = $$("[data-volunteer]"); vol.forEach(a => a.href = S.volunteerUrl || "/contact");
    const fb = $$("[data-facebook]"); fb.forEach(a => a.href = S.facebook || "#");
  }

  /* ---------- lightbox ----------
     NB: `var` (not let) — bindLightbox is invoked by the page renderers
     above, before execution reaches these declarations. */
  var lb, lbImg, lbCap, lbList = [], lbIdx = 0;
  function ensureLb() {
    if (lb) return;
    lb = document.createElement("div");
    lb.className = "lightbox";
    lb.innerHTML = `<button class="lb-close" aria-label="Close">×</button>
      <button class="lb-prev" aria-label="Previous">‹</button>
      <img alt=""><figcaption></figcaption>
      <button class="lb-next" aria-label="Next">›</button>`;
    document.body.appendChild(lb);
    lbImg = $("img", lb); lbCap = $("figcaption", lb);
    $(".lb-close", lb).addEventListener("click", () => lb.classList.remove("open"));
    $(".lb-prev", lb).addEventListener("click", () => show(lbIdx - 1));
    $(".lb-next", lb).addEventListener("click", () => show(lbIdx + 1));
    lb.addEventListener("click", (e) => { if (e.target === lb) lb.classList.remove("open"); });
    addEventListener("keydown", (e) => {
      if (!lb.classList.contains("open")) return;
      if (e.key === "Escape") lb.classList.remove("open");
      if (e.key === "ArrowLeft") show(lbIdx - 1);
      if (e.key === "ArrowRight") show(lbIdx + 1);
    });
  }
  function show(i) {
    lbIdx = (i + lbList.length) % lbList.length;
    lbImg.src = lbList[lbIdx].src; lbImg.alt = lbList[lbIdx].caption || "";
    lbCap.textContent = lbList[lbIdx].caption || "";
  }
  function bindLightbox(rootEl, list) {
    ensureLb();
    rootEl.addEventListener("click", (e) => {
      const f = e.target.closest("figure"); if (!f) return;
      lbList = list; show(Number(f.dataset.i) || 0);
      lb.classList.add("open");
    });
  }
})();

/* Website editing (CMS): Page text: the Contact and Get Involved pages.
   Edits change the shared copy (cms.content); nothing goes live until Save & publish (admin.js). */
import { $, $$, api, esc, post, toast } from "../ui.js";

const A = window.HAHAdmin;
const cms = A.cms;

/* ================================================================
   PAGE TEXT — the Contact and Get Involved pages
================================================================ */
const CARD_LINKS = [
  ["none", "Nothing extra"],
  ["emailGeneral", "General email (from Settings)"],
  ["emailTrustees", "Trustees email (from Settings)"],
  ["emailSupport", "Support email (from Settings)"],
  ["phone", "Phone number (from Settings)"],
  ["address", "Address (from Settings)"],
  ["custom", "A link I type below"],
];
const BUTTON_LINKS = [
  ["", "No button"],
  ["bookingUrl", "Booking portal (from Settings)"],
  ["donateUrl", "Donations page (from Settings)"],
  ["volunteerUrl", "Volunteer form (from Settings)"],
  ["seesawUrl", "Seesaw login (from Settings)"],
  ["facebook", "Facebook (from Settings)"],
  ["instagram", "Instagram (from Settings)"],
  ["twitter", "X / Twitter (from Settings)"],
  ["youtube", "YouTube (from Settings)"],
  ["contact", "Our Contact page"],
  ["custom", "A link I type below"],
];
const BUTTON_STYLES = [["orange", "Orange"], ["honey", "Yellow"], ["navy", "Navy"], ["ghost", "Outline"]];
const COPY_HINT = "Leave a blank line between paragraphs. **words in stars** come out bold, " +
  "and [words in brackets](https://example.com) become a link.";

const options = (opts, value) => opts.map(([v, label]) =>
  `<option value="${esc(v)}"${v === value ? " selected" : ""}>${esc(label)}</option>`).join("");

function reorder(list, i, act) {
  if (act === "up" && i > 0) [list[i - 1], list[i]] = [list[i], list[i - 1]];
  if (act === "down" && i < list.length - 1) [list[i + 1], list[i]] = [list[i], list[i + 1]];
}

const rowTop = (name) => `
    <div class="prow__top">
      <span class="prow__name">${esc(name)}</span>
      <span class="item__actions">
        <button class="icon-btn" data-act="up" title="Move up">↑</button>
        <button class="icon-btn" data-act="down" title="Move down">↓</button>
        <button class="icon-btn icon-btn--danger" data-act="del" title="Delete">🗑️</button>
      </span>
    </div>`;

const seoFields = (attr, p) => `
    <h3>How the page looks in Google</h3>
    <div class="fgroup"><label>Page title</label>
      <input type="text" data-${attr}="metaTitle" value="${esc(p.metaTitle)}">
      <div class="fhint">Shown on the browser tab, in search results and when the page is shared.</div></div>
    <div class="fgroup"><label>Search description</label>
      <textarea data-${attr}="metaDescription">${esc(p.metaDescription)}</textarea>
      <div class="fhint">One or two sentences — about 25 words.</div></div>`;

function renderPages() {
  const root = $("#tab-pages");
  const pages = cms.content.pages || (cms.content.pages = {});
  const c = pages.contact || (pages.contact = {});
  const g = pages.getInvolved || (pages.getInvolved = {});
  if (!Array.isArray(c.cards)) c.cards = [];
  if (!Array.isArray(g.sections)) g.sections = [];

  root.innerHTML = `
    <h1>Page text</h1>
    <p class="sub">Everything on the Contact and Get Involved pages. Your email addresses, phone number,
      address, opening times and the booking/donate/social links all stay in <strong>Settings</strong> —
      here you choose which of them each card or button shows.</p>

    <div class="acard" id="contactCard">
      <h2>Contact page <a class="viewlink" href="/contact" target="_blank" rel="noopener">↗ view page</a></h2>
      <div class="frow">
        <div class="fgroup"><label>Small heading above the title</label>
          <input type="text" data-c="eyebrow" value="${esc(c.eyebrow)}"></div>
        <div class="fgroup"><label>Page title</label>
          <input type="text" data-c="heading" value="${esc(c.heading)}"></div>
      </div>
      <div class="fgroup"><label>Introduction</label><textarea data-c="intro">${esc(c.intro)}</textarea></div>

      <h3>Contact cards</h3>
      <div id="cCards"></div>
      <button class="abtn abtn--honey abtn--sm" id="addCard" style="margin-top:1rem">＋ Add a card</button>

      <h3>Opening times, message form &amp; map</h3>
      <div class="frow">
        <div class="fgroup"><label>Opening times heading</label>
          <input type="text" data-c="openingHeading" value="${esc(c.openingHeading)}">
          <div class="fhint">The times themselves are in Settings. Clear this and remove the times to hide the box.</div></div>
        <div class="fgroup"><label>Message form heading</label>
          <input type="text" data-c="formHeading" value="${esc(c.formHeading)}"></div>
      </div>
      <div class="fgroup"><label>Note under the form heading</label>
        <input type="text" data-c="formNote" value="${esc(c.formNote)}"></div>
      <label class="fcheck"><input type="checkbox" data-c="showMap" ${c.showMap === false ? "" : "checked"}>
        Show the map at the bottom of the page</label>
      ${seoFields("c", c)}
    </div>

    <div class="acard" id="giCard">
      <h2>Get Involved page <a class="viewlink" href="/get-involved" target="_blank" rel="noopener">↗ view page</a></h2>
      <div class="frow">
        <div class="fgroup"><label>Small heading above the title</label>
          <input type="text" data-g="eyebrow" value="${esc(g.eyebrow)}"></div>
        <div class="fgroup"><label>Page title</label>
          <input type="text" data-g="heading" value="${esc(g.heading)}"></div>
      </div>
      <div class="fgroup"><label>Introduction</label><textarea data-g="intro">${esc(g.intro)}</textarea></div>
      <div class="fgroup"><label>Banner photo</label><div id="giHero"></div></div>

      <h3>Ways to get involved</h3>
      <div id="giSections"></div>
      <button class="abtn abtn--honey abtn--sm" id="addSection" style="margin-top:1rem">＋ Add a way to get involved</button>
      ${seoFields("g", g)}
    </div>`;

  /* -------- simple fields on each page -------- */
  const bindFields = (cardId, attr, obj) => $(cardId, root).addEventListener("input", (e) => {
    const k = e.target.dataset[attr]; if (!k) return;
    obj[k] = e.target.type === "checkbox" ? e.target.checked : e.target.value;
    cms.dirty();
  });
  bindFields("#contactCard", "c", c);
  bindFields("#giCard", "g", g);

  /* -------- contact cards -------- */
  function drawCards() {
    $("#cCards", root).innerHTML = c.cards.map((card, i) => `
      <div class="prow" data-i="${i}">${rowTop(card.title || "Card " + (i + 1))}
        <div class="frow frow--3">
          <div class="fgroup"><label>Icon</label><input type="text" data-k="icon" value="${esc(card.icon)}">
            <div class="fhint">One character or emoji</div></div>
          <div class="fgroup"><label>Heading</label><input type="text" data-k="title" value="${esc(card.title)}"></div>
          <div class="fgroup"><label>Also show</label>
            <select data-k="link">${options(CARD_LINKS, card.link || "none")}</select></div>
        </div>
        <div class="fgroup"><label>Description</label><input type="text" data-k="text" value="${esc(card.text)}"></div>
        ${card.link === "custom" ? `<div class="frow">
          <div class="fgroup"><label>Link text</label><input type="text" data-k="linkLabel" value="${esc(card.linkLabel)}"></div>
          <div class="fgroup"><label>Link address</label><input type="text" data-k="linkUrl" value="${esc(card.linkUrl)}">
            <div class="fhint">https://… , mailto:… , tel:… or /a-page-on-this-site</div></div>
        </div>` : ""}
      </div>`).join("") || '<div class="empty">No contact cards yet — add your first one below.</div>';
  }
  drawCards();

  $("#cCards", root).addEventListener("input", (e) => {
    const row = e.target.closest("[data-i]"), k = e.target.dataset.k;
    if (!row || !k) return;
    c.cards[+row.dataset.i][k] = e.target.value;
    cms.dirty();
    if (k === "link") drawCards();   // reveals/hides the typed-link fields
  });
  $("#cCards", root).addEventListener("click", (e) => {
    const b = e.target.closest("[data-act]"); if (!b) return;
    const i = +b.closest("[data-i]").dataset.i;
    if (b.dataset.act === "del" && !confirm("Delete this contact card?")) return;
    if (b.dataset.act === "del") c.cards.splice(i, 1); else reorder(c.cards, i, b.dataset.act);
    drawCards(); cms.dirty();
  });
  $("#addCard", root).addEventListener("click", () => {
    c.cards.push({ icon: "★", title: "New card", text: "", link: "emailGeneral", linkLabel: "", linkUrl: "" });
    drawCards(); cms.dirty();
  });

  /* -------- get involved sections -------- */
  $("#giHero", root).appendChild(cms.imgPicker(g.heroImage, (url) => { g.heroImage = url; cms.dirty(); }));

  function drawSections() {
    const wrap = $("#giSections", root);
    wrap.innerHTML = g.sections.map((s, i) => `
      <div class="prow" data-i="${i}">${rowTop(s.heading || "Section " + (i + 1))}
        <div class="frow">
          <div class="fgroup"><label>Small heading above the title</label>
            <input type="text" data-k="eyebrow" value="${esc(s.eyebrow)}"></div>
          <div class="fgroup"><label>Title</label><input type="text" data-k="heading" value="${esc(s.heading)}"></div>
        </div>
        <div class="fgroup"><label>Text</label><textarea data-k="body">${esc(s.body)}</textarea>
          <div class="fhint">${esc(COPY_HINT)}</div></div>
        <div class="fgroup"><label>Photo</label><div data-img></div></div>
        <div class="fgroup"><label>Photo description (for screen readers)</label>
          <input type="text" data-k="imageAlt" value="${esc(s.imageAlt)}"></div>
        <div class="frow frow--3">
          <div class="fgroup"><label>Button text</label><input type="text" data-k="buttonLabel" value="${esc(s.buttonLabel)}"></div>
          <div class="fgroup"><label>Button goes to</label>
            <select data-k="buttonLink">${options(BUTTON_LINKS, s.buttonLink || "")}</select></div>
          <div class="fgroup"><label>Button colour</label>
            <select data-k="buttonStyle">${options(BUTTON_STYLES, s.buttonStyle || "orange")}</select></div>
        </div>
        ${s.buttonLink === "custom" ? `<div class="fgroup"><label>Button link address</label>
          <input type="text" data-k="buttonUrl" value="${esc(s.buttonUrl)}">
          <div class="fhint">https://… or /a-page-on-this-site</div></div>` : ""}
        <div class="fgroup"><label>Section name for links</label><input type="text" data-k="id" value="${esc(s.id)}">
          <div class="fhint">Letters and dashes only — other pages can link straight here with
            /get-involved#${esc(s.id || "name")}</div></div>
      </div>`).join("") || '<div class="empty">Nothing here yet — add your first section below.</div>';
    $$("[data-img]", wrap).forEach((slot, i) =>
      slot.appendChild(cms.imgPicker(g.sections[i].image, (url) => { g.sections[i].image = url; cms.dirty(); })));
  }
  drawSections();

  $("#giSections", root).addEventListener("input", (e) => {
    const row = e.target.closest("[data-i]"), k = e.target.dataset.k;
    if (!row || !k) return;
    g.sections[+row.dataset.i][k] = e.target.value;
    cms.dirty();
    if (k === "buttonLink") drawSections();   // reveals/hides the typed-link field
  });
  $("#giSections", root).addEventListener("click", (e) => {
    const b = e.target.closest("[data-act]"); if (!b) return;
    const i = +b.closest("[data-i]").dataset.i;
    if (b.dataset.act === "del" && !confirm(`Delete “${g.sections[i].heading || "this section"}”?`)) return;
    if (b.dataset.act === "del") g.sections.splice(i, 1); else reorder(g.sections, i, b.dataset.act);
    drawSections(); cms.dirty();
  });
  $("#addSection", root).addEventListener("click", () => {
    g.sections.push({
      id: "new-section", eyebrow: "", heading: "New section", body: "",
      image: "/img/photos/painted-star.jpg", imageAlt: "",
      buttonLabel: "", buttonLink: "", buttonUrl: "", buttonStyle: "orange",
    });
    drawSections(); cms.dirty();
  });
}

cms.register("pages", renderPages);

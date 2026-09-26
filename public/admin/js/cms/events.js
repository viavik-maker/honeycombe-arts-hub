/* Website editing (CMS): What's On and Past Events (the scrapbook timeline).
   Edits change the shared copy (cms.content); nothing goes live until Save & publish (admin.js). */
import { $, $$, api, esc, post, toast } from "../ui.js";

const A = window.HAHAdmin;
const cms = A.cms;

/* ================================================================
   EVENTS (What's On) — also used for Past Events
================================================================ */
const slugify = (s) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "event";
const uniqueEventId = (base, ev) => {
  let id = base, n = 1;
  while ((cms.content.events || []).some(x => x !== ev && x.id === id)) id = base + "-" + (++n);
  return id;
};

function renderEvents() {
  const root = $("#tab-events");
  const list = cms.content.events;
  root.innerHTML = `
    <h1>What’s On</h1>
    <p class="sub">The events shown on the What’s On page (aim for 8–10). The first “featured” event becomes the big banner.</p>
    <div style="margin-bottom:1.2rem"><button class="abtn abtn--primary" id="addEvent">＋ Add a new event</button></div>
    <div id="eventEditor"></div>
    <div class="item-list" id="eventList"></div>`;

  const listEl = $("#eventList", root);
  listEl.innerHTML = list.map((ev, i) => `
    <div class="item" data-i="${i}">
      <img class="item__thumb" src="${esc(ev.image)}" alt="">
      <div>
        <div class="item__title">${esc(ev.title)}</div>
        <div class="item__meta">
          ${ev.featured ? '<span class="pill pill--feat">★ featured</span>' : ""}
          <span class="pill">${esc(ev.tag || "")}</span> ${esc(ev.dates || "")} · ${esc(ev.price || "")}
        </div>
      </div>
      <div class="item__actions">
        <button class="icon-btn" data-act="up" title="Move up">↑</button>
        <button class="icon-btn" data-act="down" title="Move down">↓</button>
        <button class="icon-btn" data-act="edit" title="Edit">✏️</button>
        <button class="icon-btn icon-btn--danger" data-act="del" title="Delete">🗑️</button>
      </div>
    </div>`).join("") || '<div class="empty">No events yet — add your first one!</div>';

  listEl.addEventListener("click", (e) => {
    const b = e.target.closest("[data-act]"); if (!b) return;
    const i = +b.closest(".item").dataset.i;
    const act = b.dataset.act;
    if (act === "up" && i > 0) { [list[i - 1], list[i]] = [list[i], list[i - 1]]; renderEvents(); }
    if (act === "down" && i < list.length - 1) { [list[i + 1], list[i]] = [list[i], list[i + 1]]; renderEvents(); }
    if (act === "del" && confirm(`Delete “${list[i].title}”?`)) { list.splice(i, 1); renderEvents(); }
    if (act === "edit") { cms.editing.events = i; renderEvents(); }
    cms.dirty();
  });

  $("#addEvent", root).addEventListener("click", () => {
    const ev = {
      id: "new-event-" + Math.random().toString(36).slice(2, 7),
      title: "New event", summary: "", description: "", image: "/img/photos/painted-star.jpg",
      dates: "", schedule: "", ages: "", price: "", tag: "Event", featured: false, bookable: true
    };
    cms.freshEvents.add(ev);
    list.unshift(ev);
    cms.editing.events = 0; renderEvents(); cms.dirty();
  });

  if (cms.editing.events != null && list[cms.editing.events]) {
    $("#eventEditor", root).appendChild(eventForm(list[cms.editing.events], () => {
      cms.editing.events = null; renderEvents(); cms.dirty();
    }));
  }
}

function eventForm(ev, onClose) {
  const card = document.createElement("div");
  card.className = "acard editor";
  card.innerHTML = `
    <h2>Editing: ${esc(ev.title)}</h2>
    <div class="fgroup"><label>Title</label><input type="text" data-k="title" value="${esc(ev.title)}"></div>
    <div class="fgroup"><label>Short summary (shown on cards)</label><textarea data-k="summary">${esc(ev.summary)}</textarea></div>
    <div class="fgroup"><label>Full description (shown on the event page — blank line = new paragraph)</label>
      <textarea class="tall" data-k="description">${esc(ev.description)}</textarea></div>
    <div class="frow">
      <div class="fgroup"><label>Dates (e.g. “27 July – 28 Aug 2026”)</label><input type="text" data-k="dates" value="${esc(ev.dates)}"></div>
      <div class="fgroup"><label>Schedule (e.g. “Thursdays 5–6:30pm”)</label><input type="text" data-k="schedule" value="${esc(ev.schedule)}"></div>
    </div>
    <div class="frow--3 frow">
      <div class="fgroup"><label>Ages</label><input type="text" data-k="ages" value="${esc(ev.ages)}"></div>
      <div class="fgroup"><label>Price</label><input type="text" data-k="price" value="${esc(ev.price)}"></div>
      <div class="fgroup"><label>Tag (e.g. Holiday Club)</label><input type="text" data-k="tag" value="${esc(ev.tag)}"></div>
    </div>
    <div class="fgroup"><label>Photo</label><div data-img></div></div>
    <div class="frow">
      <label class="fcheck"><input type="checkbox" data-k="featured" ${ev.featured ? "checked" : ""}> Featured (top of What’s On + homepage)</label>
      <label class="fcheck"><input type="checkbox" data-k="bookable" ${ev.bookable ? "checked" : ""}> “Book now” goes to the booking portal</label>
    </div>
    <div style="display:flex; gap:.6rem; margin-top:1rem">
      <button class="abtn abtn--primary" data-done>Done</button>
    </div>`;
  card.querySelector("[data-img]").appendChild(cms.imgPicker(ev.image, (url) => { ev.image = url; cms.dirty(); }));
  card.addEventListener("input", (e) => {
    const k = e.target.dataset.k; if (!k) return;
    ev[k] = e.target.type === "checkbox" ? e.target.checked : e.target.value;
    // an event's address (/whats-on/<id>) is fixed once it has been published,
    // so links to it — and its booking link — keep working if the title changes
    if (k === "title" && cms.freshEvents.has(ev)) ev.id = uniqueEventId(slugify(ev.title), ev);
    cms.dirty();
  });
  card.querySelector("[data-done]").addEventListener("click", onClose);
  return card;
}

/* ================================================================
   PAST EVENTS
================================================================ */
function renderPast() {
  const root = $("#tab-past");
  const list = cms.content.pastEvents;
  root.innerHTML = `
    <h1>Past Events</h1>
    <p class="sub">The scrapbook timeline. Newest first — the date field (YYYY-MM) controls the order.</p>
    <div style="margin-bottom:1.2rem"><button class="abtn abtn--primary" id="addPast">＋ Add a past event</button></div>
    <div id="pastEditor"></div>
    <div class="item-list" id="pastList"></div>`;

  $("#pastList", root).innerHTML = list.map((ev, i) => `
    <div class="item" data-i="${i}">
      <img class="item__thumb" src="${esc(ev.image)}" alt="">
      <div>
        <div class="item__title">${esc(ev.title)}</div>
        <div class="item__meta"><span class="pill">${esc(ev.dateLabel)}</span>${esc(ev.description).slice(0, 80)}…</div>
      </div>
      <div class="item__actions">
        <button class="icon-btn" data-act="edit" title="Edit">✏️</button>
        <button class="icon-btn icon-btn--danger" data-act="del" title="Delete">🗑️</button>
      </div>
    </div>`).join("") || '<div class="empty">Nothing here yet.</div>';

  $("#pastList", root).addEventListener("click", (e) => {
    const b = e.target.closest("[data-act]"); if (!b) return;
    const i = +b.closest(".item").dataset.i;
    if (b.dataset.act === "del" && confirm(`Delete “${list[i].title}”?`)) { list.splice(i, 1); renderPast(); }
    if (b.dataset.act === "edit") { cms.editing.past = i; renderPast(); }
    cms.dirty();
  });

  $("#addPast", root).addEventListener("click", () => {
    const now = new Date();
    list.unshift({
      id: "past-" + Math.random().toString(36).slice(2, 7),
      title: "New past event",
      date: now.toISOString().slice(0, 7),
      dateLabel: now.toLocaleString("en-GB", { month: "long", year: "numeric" }),
      description: "", image: "/img/photos/painted-star.jpg"
    });
    cms.editing.past = 0; renderPast(); cms.dirty();
  });

  if (cms.editing.past != null && list[cms.editing.past]) {
    const ev = list[cms.editing.past];
    const card = document.createElement("div");
    card.className = "acard editor";
    card.innerHTML = `
      <h2>Editing: ${esc(ev.title)}</h2>
      <div class="fgroup"><label>Title</label><input type="text" data-k="title" value="${esc(ev.title)}"></div>
      <div class="frow">
        <div class="fgroup"><label>Sort date (YYYY-MM)</label><input type="text" data-k="date" value="${esc(ev.date)}"><div class="fhint">Used for ordering, e.g. 2026-04</div></div>
        <div class="fgroup"><label>Displayed date (e.g. “Easter 2026”)</label><input type="text" data-k="dateLabel" value="${esc(ev.dateLabel)}"></div>
      </div>
      <div class="fgroup"><label>Description</label><textarea data-k="description">${esc(ev.description)}</textarea></div>
      <div class="fgroup"><label>Photo</label><div data-img></div></div>
      <button class="abtn abtn--primary" data-done>Done</button>`;
    card.querySelector("[data-img]").appendChild(cms.imgPicker(ev.image, (url) => { ev.image = url; cms.dirty(); }));
    card.addEventListener("input", (e) => { const k = e.target.dataset.k; if (k) { ev[k] = e.target.value; cms.dirty(); } });
    card.querySelector("[data-done]").addEventListener("click", () => { cms.editing.past = null; renderPast(); cms.dirty(); });
    $("#pastEditor", root).appendChild(card);
  }
}

cms.register("events", renderEvents);
cms.register("past", renderPast);

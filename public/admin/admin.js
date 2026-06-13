/* Honeycombe Arts Hub — staff admin app */
(function () {
  "use strict";
  const $ = (s, el) => (el || document).querySelector(s);
  const $$ = (s, el) => Array.from((el || document).querySelectorAll(s));
  const esc = (s) => String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

  let content = null;          // editable copy
  let saved = null;            // last-saved snapshot (JSON string)
  let messages = [], subscribers = [];
  let editing = { events: null, past: null, testimonials: null };

  /* ---------------- api ---------------- */
  async function api(path, opts) {
    const r = await fetch(path, opts);
    const d = await r.json().catch(() => ({}));
    if (r.status === 401) { showLogin(); throw new Error("unauthorised"); }
    if (!r.ok) throw new Error(d.error || "Request failed");
    return d;
  }
  const post = (path, body) => api(path, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body)
  });

  function toast(msg, err) {
    const t = $("#toast");
    t.textContent = msg; t.className = "toast" + (err ? " err" : ""); t.hidden = false;
    clearTimeout(t._h); t._h = setTimeout(() => t.hidden = true, 2600);
  }

  /* ---------------- auth flow ---------------- */
  function showLogin() { $("#loginView").hidden = false; $("#appView").hidden = true; }
  function showApp() { $("#loginView").hidden = true; $("#appView").hidden = false; }

  $("#loginForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    $("#loginErr").hidden = true;
    try {
      await post("/api/admin/login", { password: $("#loginPass").value });
      $("#loginPass").value = "";
      await boot();
    } catch (err) {
      $("#loginErr").textContent = err.message; $("#loginErr").hidden = false;
    }
  });
  $("#logoutBtn").addEventListener("click", async () => {
    await post("/api/admin/logout", {}); showLogin();
  });

  async function boot() {
    const d = await api("/api/admin/overview");
    content = d.content; messages = d.messages; subscribers = d.subscribers;
    saved = JSON.stringify(content);
    showApp(); renderAll();
  }

  /* ---------------- dirty tracking ---------------- */
  function dirty() {
    const isDirty = JSON.stringify(content) !== saved;
    $("#saveBar").hidden = !isDirty;
    return isDirty;
  }
  $("#saveBtn").addEventListener("click", async () => {
    try {
      await post("/api/admin/content", content);
      saved = JSON.stringify(content); dirty();
      toast("Published! The live site is up to date ✨");
    } catch (e) { toast(e.message, true); }
  });
  $("#discardBtn").addEventListener("click", () => {
    content = JSON.parse(saved);
    editing = { events: null, past: null, testimonials: null };
    renderAll(); dirty();
  });
  addEventListener("beforeunload", (e) => { if (content && dirty()) { e.preventDefault(); e.returnValue = ""; } });

  /* ---------------- tabs ---------------- */
  $("#sideNav").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    $$("#sideNav button").forEach(x => x.classList.toggle("active", x === b));
    $$(".tab").forEach(t => t.hidden = t.id !== "tab-" + b.dataset.tab);
  });

  /* ---------------- upload helper ---------------- */
  function uploadButton(onDone) {
    const inp = document.createElement("input");
    inp.type = "file"; inp.accept = "image/*";
    inp.addEventListener("change", async () => {
      if (!inp.files[0]) return;
      const fd = new FormData(); fd.append("file", inp.files[0]);
      try {
        const r = await fetch("/api/admin/upload", { method: "POST", body: fd });
        const d = await r.json();
        if (!r.ok) throw new Error(d.error || "Upload failed");
        onDone(d.url); toast("Image uploaded 📷");
      } catch (e) { toast(e.message, true); }
    });
    inp.click();
  }

  function imgPicker(current, onChange) {
    const wrap = document.createElement("div");
    wrap.className = "imgpick";
    wrap.innerHTML = `<img class="imgpick__preview" src="${esc(current || "")}" alt="">
      <div class="imgpick__btns">
        <button type="button" class="abtn abtn--honey abtn--sm">Upload new image</button>
        <span class="fhint">${esc(current || "No image yet")}</span>
      </div>`;
    $("button", wrap).addEventListener("click", () =>
      uploadButton((url) => { $("img", wrap).src = url; $(".fhint", wrap).textContent = url; onChange(url); }));
    return wrap;
  }

  /* ================================================================
     DASHBOARD
  ================================================================ */
  function renderDashboard() {
    const unread = messages.filter(m => !m.read).length;
    $("#tab-dashboard").innerHTML = `
      <h1>Hello, Hub team! 👋</h1>
      <p class="sub">Here's how the website is looking today.</p>
      <div class="statgrid">
        <div class="stat"><strong>${content.events.length}</strong><span>events on What's On</span></div>
        <div class="stat"><strong>${unread}</strong><span>unread message${unread === 1 ? "" : "s"}</span></div>
        <div class="stat"><strong>${subscribers.length}</strong><span>newsletter subscribers</span></div>
        <div class="stat"><strong>${content.gallery.length}</strong><span>photos in the gallery</span></div>
      </div>
      <div class="acard">
        <h2>Quick actions</h2>
        <div class="quicklinks">
          <button class="abtn abtn--primary" data-go="events">＋ Add an event</button>
          <button class="abtn abtn--honey" data-go="gallery">＋ Add photos</button>
          <button class="abtn abtn--ghost" data-go="messages">Read messages</button>
          <button class="abtn abtn--ghost" data-go="settings">Edit announcement bar</button>
        </div>
      </div>
      <div class="acard">
        <h2>How it works</h2>
        <p>Make your changes in any tab — nothing goes live until you press <strong>Save &amp; publish</strong> in the bar at the bottom. The live site updates instantly. If you make a mistake, press <strong>Discard</strong> to go back to the last published version.</p>
      </div>`;
    $$("#tab-dashboard [data-go]").forEach(b => b.addEventListener("click", () => {
      $(`#sideNav button[data-tab=${b.dataset.go}]`).click();
    }));
  }

  /* ================================================================
     EVENTS (What's On) — also used for Past Events
  ================================================================ */
  const slugify = (s) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "event";

  function renderEvents() {
    const root = $("#tab-events");
    const list = content.events;
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
      if (act === "edit") { editing.events = i; renderEvents(); }
      dirty();
    });

    $("#addEvent", root).addEventListener("click", () => {
      list.unshift({
        id: "new-event-" + Math.random().toString(36).slice(2, 7),
        title: "New event", summary: "", description: "", image: "/img/photos/painted-star.jpg",
        dates: "", schedule: "", ages: "", price: "", tag: "Event", featured: false, bookable: true
      });
      editing.events = 0; renderEvents(); dirty();
    });

    if (editing.events != null && list[editing.events]) {
      $("#eventEditor", root).appendChild(eventForm(list[editing.events], () => {
        editing.events = null; renderEvents(); dirty();
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
    card.querySelector("[data-img]").appendChild(imgPicker(ev.image, (url) => { ev.image = url; dirty(); }));
    card.addEventListener("input", (e) => {
      const k = e.target.dataset.k; if (!k) return;
      ev[k] = e.target.type === "checkbox" ? e.target.checked : e.target.value;
      if (k === "title") ev.id = slugify(ev.title);
      dirty();
    });
    card.querySelector("[data-done]").addEventListener("click", onClose);
    return card;
  }

  /* ================================================================
     PAST EVENTS
  ================================================================ */
  function renderPast() {
    const root = $("#tab-past");
    const list = content.pastEvents;
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
      if (b.dataset.act === "edit") { editing.past = i; renderPast(); }
      dirty();
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
      editing.past = 0; renderPast(); dirty();
    });

    if (editing.past != null && list[editing.past]) {
      const ev = list[editing.past];
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
      card.querySelector("[data-img]").appendChild(imgPicker(ev.image, (url) => { ev.image = url; dirty(); }));
      card.addEventListener("input", (e) => { const k = e.target.dataset.k; if (k) { ev[k] = e.target.value; dirty(); } });
      card.querySelector("[data-done]").addEventListener("click", () => { editing.past = null; renderPast(); dirty(); });
      $("#pastEditor", root).appendChild(card);
    }
  }

  /* ================================================================
     GALLERY
  ================================================================ */
  function renderGallery() {
    const root = $("#tab-gallery");
    const list = content.gallery;
    const cats = [...new Set(list.map(g => g.category).filter(Boolean))];
    root.innerHTML = `
      <h1>Gallery</h1>
      <p class="sub">Click a caption or category to edit it. New photos appear at the front of the gallery.</p>
      <div style="margin-bottom:1.2rem"><button class="abtn abtn--primary" id="addPhoto">＋ Upload a photo</button></div>
      <div class="ggrid" id="ggrid"></div>`;

    $("#ggrid", root).innerHTML = list.map((g, i) => `
      <div class="gcell" data-i="${i}">
        <img src="${esc(g.src)}" alt="" loading="lazy">
        <div class="gcell__body">
          <input type="text" data-k="caption" value="${esc(g.caption)}" placeholder="Caption">
          <input type="text" data-k="category" value="${esc(g.category)}" placeholder="Category" list="catList">
          <div class="gcell__row">
            <span class="fhint">#${i + 1}</span>
            <span>
              <button class="icon-btn" data-act="left" title="Move earlier">←</button>
              <button class="icon-btn" data-act="right" title="Move later">→</button>
              <button class="icon-btn icon-btn--danger" data-act="del" title="Remove">🗑️</button>
            </span>
          </div>
        </div>
      </div>`).join("") || '<div class="empty">No photos yet — upload your first!</div>';
    root.insertAdjacentHTML("beforeend",
      `<datalist id="catList">${cats.map(c => `<option value="${esc(c)}">`).join("")}</datalist>`);

    $("#ggrid", root).addEventListener("input", (e) => {
      const cell = e.target.closest(".gcell"); if (!cell) return;
      const g = list[+cell.dataset.i];
      if (e.target.dataset.k) { g[e.target.dataset.k] = e.target.value; dirty(); }
    });
    $("#ggrid", root).addEventListener("click", (e) => {
      const b = e.target.closest("[data-act]"); if (!b) return;
      const i = +b.closest(".gcell").dataset.i;
      if (b.dataset.act === "left" && i > 0) { [list[i - 1], list[i]] = [list[i], list[i - 1]]; renderGallery(); }
      if (b.dataset.act === "right" && i < list.length - 1) { [list[i + 1], list[i]] = [list[i], list[i + 1]]; renderGallery(); }
      if (b.dataset.act === "del" && confirm("Remove this photo from the gallery?")) { list.splice(i, 1); renderGallery(); }
      dirty();
    });
    $("#addPhoto", root).addEventListener("click", () => uploadButton((url) => {
      list.unshift({ src: url, caption: "New photo", category: "Arts Club" });
      renderGallery(); dirty();
    }));
  }

  /* ================================================================
     TESTIMONIALS
  ================================================================ */
  function renderTestimonials() {
    const root = $("#tab-testimonials");
    const list = content.testimonials;
    root.innerHTML = `
      <h1>Testimonials</h1>
      <p class="sub">The first five appear in the homepage slider; all of them appear on the Testimonials page.</p>
      <div style="margin-bottom:1.2rem"><button class="abtn abtn--primary" id="addQuote">＋ Add a testimonial</button></div>
      <div class="item-list" id="qlist"></div>`;
    $("#qlist", root).innerHTML = list.map((q, i) => `
      <div class="acard" data-i="${i}" style="margin:0">
        <div class="fgroup"><label>Quote</label><textarea data-k="quote">${esc(q.quote)}</textarea></div>
        <div class="frow">
          <div class="fgroup"><label>Name</label><input type="text" data-k="author" value="${esc(q.author)}"></div>
          <div class="fgroup"><label>Role (e.g. Parent)</label><input type="text" data-k="role" value="${esc(q.role)}"></div>
        </div>
        <div style="display:flex; gap:.4rem">
          <button class="icon-btn" data-act="up" title="Move up">↑</button>
          <button class="icon-btn" data-act="down" title="Move down">↓</button>
          <button class="icon-btn icon-btn--danger" data-act="del" title="Delete">🗑️</button>
        </div>
      </div>`).join("");
    $("#qlist", root).addEventListener("input", (e) => {
      const c = e.target.closest("[data-i]"); if (!c || !e.target.dataset.k) return;
      list[+c.dataset.i][e.target.dataset.k] = e.target.value; dirty();
    });
    $("#qlist", root).addEventListener("click", (e) => {
      const b = e.target.closest("[data-act]"); if (!b) return;
      const i = +b.closest("[data-i]").dataset.i;
      if (b.dataset.act === "up" && i > 0) { [list[i - 1], list[i]] = [list[i], list[i - 1]]; renderTestimonials(); }
      if (b.dataset.act === "down" && i < list.length - 1) { [list[i + 1], list[i]] = [list[i], list[i + 1]]; renderTestimonials(); }
      if (b.dataset.act === "del" && confirm("Delete this testimonial?")) { list.splice(i, 1); renderTestimonials(); }
      dirty();
    });
    $("#addQuote", root).addEventListener("click", () => {
      list.unshift({ quote: "", author: "", role: "Parent" }); renderTestimonials(); dirty();
    });
  }

  /* ================================================================
     MISSION — impact stats & values
  ================================================================ */
  function renderMission() {
    const root = $("#tab-mission");
    root.innerHTML = `
      <h1>Impact &amp; Values</h1>
      <p class="sub">Shown on the homepage and the Mission, Values &amp; Impact page.</p>
      <div class="acard"><h2>Impact statistics</h2><div id="impList"></div>
        <button class="abtn abtn--honey abtn--sm" id="addImp">＋ Add a statistic</button></div>
      <div class="acard"><h2>Values</h2><div id="valList"></div></div>`;

    $("#impList", root).innerHTML = content.impact.map((s, i) => `
      <div data-i="${i}" style="border-bottom:1.5px dashed var(--line); padding:1em 0">
        <div class="frow">
          <div class="fgroup"><label>Number (e.g. 3,624)</label><input type="text" data-k="number" value="${esc(s.number)}"></div>
          <div class="fgroup"><label>Headline</label><input type="text" data-k="label" value="${esc(s.label)}"></div>
        </div>
        <div class="fgroup"><label>Detail</label><textarea data-k="detail">${esc(s.detail)}</textarea></div>
        <button class="abtn abtn--danger abtn--sm" data-act="del">Remove</button>
      </div>`).join("");
    $("#impList", root).addEventListener("input", (e) => {
      const c = e.target.closest("[data-i]"); if (!c || !e.target.dataset.k) return;
      content.impact[+c.dataset.i][e.target.dataset.k] = e.target.value; dirty();
    });
    $("#impList", root).addEventListener("click", (e) => {
      const b = e.target.closest("[data-act=del]"); if (!b) return;
      content.impact.splice(+b.closest("[data-i]").dataset.i, 1); renderMission(); dirty();
    });
    $("#addImp", root).addEventListener("click", () => {
      content.impact.push({ number: "0", label: "", detail: "" }); renderMission(); dirty();
    });

    $("#valList", root).innerHTML = content.values.map((v, i) => `
      <div data-i="${i}" style="border-bottom:1.5px dashed var(--line); padding:1em 0">
        <div class="frow">
          <div class="fgroup"><label>Title</label><input type="text" data-k="title" value="${esc(v.title)}"></div>
          <div class="fgroup"><label>Colour</label>
            <select data-k="color">
              ${["orange", "honey", "sage", "navy"].map(c => `<option ${v.color === c ? "selected" : ""}>${c}</option>`).join("")}
            </select></div>
        </div>
        <div class="fgroup"><label>Text</label><textarea data-k="text">${esc(v.text)}</textarea></div>
      </div>`).join("");
    $("#valList", root).addEventListener("input", (e) => {
      const c = e.target.closest("[data-i]"); if (!c || !e.target.dataset.k) return;
      content.values[+c.dataset.i][e.target.dataset.k] = e.target.value; dirty();
    });
  }

  /* ================================================================
     MESSAGES
  ================================================================ */
  function renderMessages() {
    const root = $("#tab-messages");
    const unread = messages.filter(m => !m.read).length;
    $("#msgBadge").hidden = unread === 0;
    $("#msgBadge").textContent = unread;
    root.innerHTML = `
      <h1>Inbox</h1>
      <p class="sub">Messages sent from the contact form on the website.</p>
      ${messages.length ? "" : '<div class="empty">No messages yet — when families use the contact form, they’ll appear here.</div>'}
      ${messages.map(m => `
        <div class="msg ${m.read ? "" : "unread"}" data-id="${esc(m.id)}">
          <div class="msg__head">
            <strong>${esc(m.name)}</strong>
            <a href="mailto:${esc(m.email)}">${esc(m.email)}</a>
            ${m.phone ? `<span>${esc(m.phone)}</span>` : ""}
            <time>${esc(m.date)}</time>
          </div>
          <p>${esc(m.message)}</p>
          <div class="msg__actions">
            <a class="abtn abtn--honey abtn--sm" href="mailto:${esc(m.email)}?subject=Re:%20your%20message%20to%20Honeycombe%20Arts%20Hub">Reply by email</a>
            <button class="abtn abtn--ghost abtn--sm" data-act="toggle">${m.read ? "Mark unread" : "Mark read"}</button>
            <button class="abtn abtn--danger abtn--sm" data-act="del">Delete</button>
          </div>
        </div>`).join("")}`;
    root.onclick = async (e) => {
      const b = e.target.closest("[data-act]"); if (!b) return;
      const id = b.closest(".msg").dataset.id;
      const m = messages.find(x => x.id === id);
      try {
        if (b.dataset.act === "toggle") {
          const d = await post("/api/admin/messages", { action: "read", id, read: !m.read });
          messages = d.messages;
        }
        if (b.dataset.act === "del" && confirm("Delete this message?")) {
          const d = await post("/api/admin/messages", { action: "delete", id });
          messages = d.messages;
        }
        renderMessages();
      } catch (err) { toast(err.message, true); }
    };
  }

  /* ================================================================
     SUBSCRIBERS
  ================================================================ */
  function renderSubscribers() {
    const root = $("#tab-subscribers");
    root.innerHTML = `
      <h1>Newsletter</h1>
      <p class="sub">People who signed up through the website footer.</p>
      <div style="display:flex; gap:.6rem; margin-bottom:1.2rem">
        <a class="abtn abtn--primary" href="/api/admin/subscribers.csv">⬇ Download as CSV</a>
        <button class="abtn abtn--ghost" id="copyEmails">Copy all emails</button>
      </div>
      ${subscribers.length ? `
      <table class="table">
        <tr><th>Email</th><th>Signed up</th><th></th></tr>
        ${subscribers.map(s => `
          <tr><td>${esc(s.email)}</td><td>${esc(s.date)}</td>
          <td><button class="icon-btn icon-btn--danger" data-email="${esc(s.email)}" title="Remove">🗑️</button></td></tr>`).join("")}
      </table>` : '<div class="empty">No subscribers yet.</div>'}`;
    $("#copyEmails", root).addEventListener("click", () => {
      navigator.clipboard.writeText(subscribers.map(s => s.email).join(", "))
        .then(() => toast("Email list copied 📋"));
    });
    root.onclick = async (e) => {
      const b = e.target.closest("[data-email]"); if (!b) return;
      if (!confirm(`Remove ${b.dataset.email} from the list?`)) return;
      try {
        const d = await post("/api/admin/subscribers", { action: "delete", email: b.dataset.email });
        subscribers = d.subscribers; renderSubscribers();
      } catch (err) { toast(err.message, true); }
    };
  }

  /* ================================================================
     SETTINGS
  ================================================================ */
  function renderSettings() {
    const s = content.settings;
    const root = $("#tab-settings");
    const f = (label, key, hint, type) => `
      <div class="fgroup"><label>${label}</label>
        <input type="${type || "text"}" data-k="${key}" value="${esc(s[key])}">
        ${hint ? `<div class="fhint">${hint}</div>` : ""}</div>`;
    root.innerHTML = `
      <h1>Settings</h1>
      <p class="sub">Site-wide details. Remember to Save &amp; publish.</p>

      <div class="acard"><h2>Announcement bar</h2>
        <label class="fcheck" style="margin-bottom:1em"><input type="checkbox" data-k="announcementOn" ${s.announcementOn ? "checked" : ""}> Show the announcement bar</label>
        <div class="fgroup"><label>Announcement text</label><input type="text" data-k="announcement" value="${esc(s.announcement)}"></div>
      </div>

      <div class="acard"><h2>Contact details</h2>
        ${f("Phone", "phone")}
        ${f("General email", "emailGeneral")}
        ${f("Trustees email", "emailTrustees")}
        ${f("Student & financial support email", "emailSupport")}
        ${f("Address", "address")}
      </div>

      <div class="acard"><h2>Links</h2>
        ${f("Booking portal (CRM)", "bookingUrl", "Where “Book Now” buttons go")}
        ${f("Donations page", "donateUrl")}
        ${f("Volunteer application form", "volunteerUrl")}
        ${f("Seesaw login", "seesawUrl")}
        ${f("Facebook", "facebook")}
        ${f("Instagram", "instagram")}
        ${f("X / Twitter", "twitter")}
        ${f("YouTube", "youtube")}
      </div>

      <div class="acard"><h2>Charity details</h2>
        ${f("Registered charity number", "charityNumber")}
        ${f("Ofsted register number", "ofstedNumber")}
      </div>

      <div class="acard"><h2>Email notifications (optional)</h2>
        <p class="fhint" style="margin-bottom:1em">If your email provider gives you SMTP details, the website can forward contact-form messages straight to your email inbox. Leave blank to just use the Inbox tab here.</p>
        <div class="frow">
          <div class="fgroup"><label>SMTP host</label><input type="text" data-smtp="host" value="${esc(s.smtp.host)}"></div>
          <div class="fgroup"><label>Port</label><input type="number" data-smtp="port" value="${esc(s.smtp.port)}"></div>
        </div>
        <div class="frow">
          <div class="fgroup"><label>Username</label><input type="text" data-smtp="user" value="${esc(s.smtp.user)}"></div>
          <div class="fgroup"><label>Password</label><input type="password" data-smtp="password" value="${esc(s.smtp.password)}"></div>
        </div>
        <div class="fgroup"><label>Send notifications to</label><input type="email" data-smtp="notifyTo" value="${esc(s.smtp.notifyTo)}"></div>
      </div>

      <div class="acard"><h2>Change admin password</h2>
        <div class="frow">
          <div class="fgroup"><label>Current password</label><input type="password" id="pwCur" autocomplete="current-password"></div>
          <div class="fgroup"><label>New password (8+ characters)</label><input type="password" id="pwNew" autocomplete="new-password"></div>
        </div>
        <button class="abtn abtn--primary" id="pwBtn">Change password</button>
      </div>`;

    root.addEventListener("input", (e) => {
      if (e.target.dataset.k) {
        s[e.target.dataset.k] = e.target.type === "checkbox" ? e.target.checked : e.target.value;
        dirty();
      }
      if (e.target.dataset.smtp) {
        s.smtp[e.target.dataset.smtp] = e.target.type === "number" ? +e.target.value : e.target.value;
        dirty();
      }
    });
    $("#pwBtn", root).addEventListener("click", async () => {
      try {
        await post("/api/admin/password", { current: $("#pwCur").value, new: $("#pwNew").value });
        $("#pwCur").value = ""; $("#pwNew").value = "";
        toast("Password changed 🔒");
      } catch (e) { toast(e.message, true); }
    });
  }

  /* ---------------- render all ---------------- */
  function renderAll() {
    renderDashboard(); renderEvents(); renderPast(); renderGallery();
    renderTestimonials(); renderMission(); renderMessages(); renderSubscribers(); renderSettings();
  }

  /* ---------------- start ---------------- */
  (async function start() {
    try { await boot(); }
    catch (_) { showLogin(); }
  })();
})();

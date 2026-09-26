/* Honeycombe Arts Hub — staff admin app */
(function () {
  "use strict";
  const $ = (s, el) => (el || document).querySelector(s);
  const $$ = (s, el) => Array.from((el || document).querySelectorAll(s));
  const esc = (s) => String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

  let me = null;               // signed-in staff member: {staff, perms, csrf, ...}
  let perms = new Set();
  let content = null;          // editable copy
  let saved = null;            // last-saved snapshot (JSON string)
  let messages = [], subscribers = [];
  let editing = { events: null, past: null, testimonials: null };

  /* ---------------- api ---------------- */
  async function api(path, opts) {
    opts = opts || {};
    if (opts.method && opts.method !== "GET" && me) {
      opts.headers = Object.assign({ "X-CSRF-Token": me.csrf }, opts.headers || {});
    }
    const r = await fetch(path, opts);
    const d = await r.json().catch(() => ({}));
    if (r.status === 401 && !opts.quiet401) { showLogin(); throw new Error("Please sign in again"); }
    if (!r.ok) { const e = new Error(d.error || "Request failed"); e.status = r.status; e.data = d; throw e; }
    return d;
  }
  const post = (path, body, extra) => api(path, Object.assign({
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body)
  }, extra || {}));

  function toast(msg, err) {
    const t = $("#toast");
    t.textContent = msg; t.className = "toast" + (err ? " err" : ""); t.hidden = false;
    clearTimeout(t._h); t._h = setTimeout(() => t.hidden = true, 2600);
  }

  /* ---------------- sign-in (step by step) ----------------
     setup (first owner) · password · 2FA code · 2FA set-up · recovery codes · invite links */
  function showLogin() {
    me = null; perms = new Set();
    $("#loginView").hidden = false; $("#appView").hidden = true;
    const m = location.hash.match(/^#(invite|reset)=([\w-]+)$/);
    if (m) return stepInvite(m[2]);
    api("/api/staff/setup", { quiet401: true })
      .then(st => st.needed ? stepSetup(st.possible) : stepPassword())
      .catch(() => stepPassword());
  }
  function showApp() { $("#loginView").hidden = true; $("#appView").hidden = false; }

  function stepView(html, onSubmit) {
    const box = $("#loginStep");
    box.innerHTML = html + `<p class="login__err" hidden></p>`;
    const form = $("form", box);
    if (form) form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const err = $(".login__err", box); err.hidden = true;
      const btn = $("button[type=submit]", form); btn.disabled = true;
      try { await onSubmit(form); }
      catch (x) { err.textContent = x.message; err.hidden = false; }
      finally { btn.disabled = false; }
    });
    const first = $("input", box); if (first) first.focus();
  }
  // after any step the server says what comes next
  async function next(d) {
    if (d.csrf) me = Object.assign(me || {}, { csrf: d.csrf });
    if (d.step === "totp") return stepCode();
    if (d.step === "enrol") return stepEnrol();
    if (d.recovery_codes) return stepRecovery(d.recovery_codes);
    if (d.recovery_codes_left != null) toast(`Recovery code used — ${d.recovery_codes_left} left`);
    history.replaceState(null, "", location.pathname);
    return boot();
  }

  function stepPassword() {
    stepView(`<h1>Staff sign in</h1>
      <p>Sign in with your own email and password.</p>
      <form><input type="email" name="email" placeholder="Email" autocomplete="username" required>
        <input type="password" name="password" placeholder="Password" autocomplete="current-password" required>
        <button class="abtn abtn--primary" type="submit">Sign in</button></form>`,
      async (f) => next(await post("/api/staff/login", { email: f.email.value, password: f.password.value }, { quiet401: true })));
  }

  function stepCode() {
    stepView(`<h1>Enter your code</h1>
      <p>Open your authenticator app and type the 6-digit code for Honeycombe Arts Hub. Lost your phone? Use one of your recovery codes.</p>
      <form><input name="code" inputmode="numeric" autocomplete="one-time-code" placeholder="123 456" required>
        <button class="abtn abtn--primary" type="submit">Continue</button></form>`,
      async (f) => next(await post("/api/staff/totp/verify", { code: f.code.value }, { quiet401: true })));
  }

  async function stepEnrol() {
    const e = await api("/api/staff/totp/enrol", { quiet401: true });
    const grouped = e.secret.replace(/(.{4})/g, "$1 ").trim();
    stepView(`<h1>Set up two-step sign-in</h1>
      <p>Because the booking system holds children's details, every staff account uses a code from an
        authenticator app (Google or Microsoft Authenticator, 1Password…) as well as a password.</p>
      <ol class="login__steps">
        <li>In the app, choose <em>Add account → Enter a setup key</em>${/Android|iPhone|iPad/.test(navigator.userAgent) ? ` (or <a href="${esc(e.uri)}">tap here</a>)` : ""}.</li>
        <li>Account: <strong>Honeycombe Arts Hub</strong>. Key: <code class="login__key">${esc(grouped)}</code> (time-based).</li>
        <li>Type the 6-digit code the app shows:</li>
      </ol>
      <form><input name="code" inputmode="numeric" autocomplete="one-time-code" placeholder="123 456" required>
        <button class="abtn abtn--primary" type="submit">Turn on two-step sign-in</button></form>`,
      async (f) => next(await post("/api/staff/totp/enrol", { code: f.code.value }, { quiet401: true })));
  }

  function stepRecovery(codes) {
    stepView(`<h1>Save your recovery codes</h1>
      <p>If you lose your phone, each of these lets you sign in once. Print them or save them somewhere safe
        (not on the same phone). You won't see them again.</p>
      <pre class="login__codes">${codes.map(esc).join("\n")}</pre>
      <form><button class="abtn abtn--primary" type="submit">I've saved them — continue</button></form>`,
      async () => next({}));
  }

  function stepSetup(possible) {
    if (!possible) {
      return stepView(`<h1>Set up the admin</h1><p>No staff accounts exist yet. Ask whoever manages the hosting to set
        the <code>ADMIN_PASSWORD</code> environment variable, then reload this page.</p>`);
    }
    stepView(`<h1>Create the owner account</h1>
      <p>Staff now sign in with their own accounts. Create yours first — you'll need the current team password.
        After this, the shared team password stops working.</p>
      <form><input type="password" name="team" placeholder="Current team password" autocomplete="off" required>
        <input name="name" placeholder="Your name" autocomplete="name" required>
        <input type="email" name="email" placeholder="Your email" autocomplete="username" required>
        <input type="password" name="password" placeholder="New password (10+ characters)" autocomplete="new-password" required minlength="10">
        <button class="abtn abtn--primary" type="submit">Create owner account</button></form>`,
      async (f) => next(await post("/api/staff/setup", { team_password: f.team.value, name: f.name.value,
        email: f.email.value, password: f.password.value }, { quiet401: true })));
  }

  async function stepInvite(token) {
    let who;
    try { who = await post("/api/staff/invite/check", { token }, { quiet401: true }); }
    catch (e) {
      history.replaceState(null, "", location.pathname);
      stepView(`<h1>Link not valid</h1><p>${esc(e.message)}</p>`);
      return;
    }
    stepView(`<h1>${who.purpose === "staff_reset" ? "Choose a new password" : "Welcome, " + esc(who.name) + "!"}</h1>
      <p>Choose a password for <strong>${esc(who.email)}</strong> — at least 10 characters (three random words works well).</p>
      <form><input type="password" name="password" placeholder="New password" autocomplete="new-password" required minlength="10">
        <button class="abtn abtn--primary" type="submit">Continue</button></form>`,
      async (f) => next(await post("/api/staff/invite/accept", { token, password: f.password.value }, { quiet401: true })));
  }

  $("#logoutBtn").addEventListener("click", async () => {
    try { await post("/api/staff/logout", {}); } catch (_) { /* signed out either way */ }
    showLogin();
  });

  async function boot() {
    me = await api("/api/staff/me", { quiet401: true });
    if (!me.mfa_passed) throw new Error("two-factor check needed");
    perms = new Set(me.perms);
    $("#whoAmI").textContent = me.staff.name;
    $$("#sideNav button[data-perm]").forEach(b => b.hidden = !perms.has(b.dataset.perm));
    if (perms.has("site.content")) {
      const d = await api("/api/admin/overview");
      content = d.content; messages = d.messages; subscribers = d.subscribers;
      saved = JSON.stringify(content);
    }
    showApp();
    renderAll();
    document.dispatchEvent(new CustomEvent("hah:ready", { detail: me }));
  }

  /* ---------------- bridge for the ES-module areas (public/admin/js/*.js) ---------------- */
  const moduleTabs = {};
  window.HAHAdmin = {
    api, post, toast, esc, $, $$,
    me: () => me,
    can: (p) => perms.has(p),
    /* addTab({id, label, icon, perm, render(root)}) — a new sidebar area, rendered when opened */
    addTab(t) {
      moduleTabs[t.id] = t;
      const b = document.createElement("button");
      b.dataset.tab = t.id;
      if (t.perm) { b.dataset.perm = t.perm; b.hidden = !perms.has(t.perm); }
      b.innerHTML = `<span>${t.icon}</span> ${esc(t.label)}`;
      $("#sideNav").appendChild(b);
      const panel = document.createElement("div");
      panel.id = "tab-" + t.id; panel.className = "tab"; panel.hidden = true;
      $("main.panel").appendChild(panel);
    },
  };

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
    const mt = moduleTabs[b.dataset.tab];
    if (mt) Promise.resolve(mt.render($("#tab-" + mt.id))).catch(err => toast(err.message, true));
  });

  /* ---------------- upload helper ---------------- */
  function uploadButton(onDone) {
    const inp = document.createElement("input");
    // photos for the public website only (never forms or documents about
    // children); the server strips location/camera details from JPEGs
    inp.type = "file"; inp.accept = "image/jpeg,image/png,image/gif,image/webp";
    inp.addEventListener("change", async () => {
      if (!inp.files[0]) return;
      const fd = new FormData(); fd.append("file", inp.files[0]);
      try {
        const r = await fetch("/api/admin/upload", { method: "POST", body: fd, headers: { "X-CSRF-Token": me.csrf } });
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
        <button type="button" class="abtn abtn--honey abtn--sm" title="Public website photos only — JPEG, PNG, GIF or WebP">Upload new image</button>
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
    if (!content) {
      $("#tab-dashboard").innerHTML = `<h1>Hello, ${esc(me.staff.name)}! 👋</h1>
        <p class="sub">Choose an area from the menu on the left.</p>`;
      return;
    }
    const unread = messages.filter(m => !m.read).length;
    $("#tab-dashboard").innerHTML = `
      <h1>Hello, ${esc(me.staff.name)}! 👋</h1>
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
    const pages = content.pages || (content.pages = {});
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
      dirty();
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
      dirty();
      if (k === "link") drawCards();   // reveals/hides the typed-link fields
    });
    $("#cCards", root).addEventListener("click", (e) => {
      const b = e.target.closest("[data-act]"); if (!b) return;
      const i = +b.closest("[data-i]").dataset.i;
      if (b.dataset.act === "del" && !confirm("Delete this contact card?")) return;
      if (b.dataset.act === "del") c.cards.splice(i, 1); else reorder(c.cards, i, b.dataset.act);
      drawCards(); dirty();
    });
    $("#addCard", root).addEventListener("click", () => {
      c.cards.push({ icon: "★", title: "New card", text: "", link: "emailGeneral", linkLabel: "", linkUrl: "" });
      drawCards(); dirty();
    });

    /* -------- get involved sections -------- */
    $("#giHero", root).appendChild(imgPicker(g.heroImage, (url) => { g.heroImage = url; dirty(); }));

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
        slot.appendChild(imgPicker(g.sections[i].image, (url) => { g.sections[i].image = url; dirty(); })));
    }
    drawSections();

    $("#giSections", root).addEventListener("input", (e) => {
      const row = e.target.closest("[data-i]"), k = e.target.dataset.k;
      if (!row || !k) return;
      g.sections[+row.dataset.i][k] = e.target.value;
      dirty();
      if (k === "buttonLink") drawSections();   // reveals/hides the typed-link field
    });
    $("#giSections", root).addEventListener("click", (e) => {
      const b = e.target.closest("[data-act]"); if (!b) return;
      const i = +b.closest("[data-i]").dataset.i;
      if (b.dataset.act === "del" && !confirm(`Delete “${g.sections[i].heading || "this section"}”?`)) return;
      if (b.dataset.act === "del") g.sections.splice(i, 1); else reorder(g.sections, i, b.dataset.act);
      drawSections(); dirty();
    });
    $("#addSection", root).addEventListener("click", () => {
      g.sections.push({
        id: "new-section", eyebrow: "", heading: "New section", body: "",
        image: "/img/photos/painted-star.jpg", imageAlt: "",
        buttonLabel: "", buttonLink: "", buttonUrl: "", buttonStyle: "orange",
      });
      drawSections(); dirty();
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

      <div class="acard"><h2>System &amp; backups</h2>
        <div id="sysBody"><p class="fhint">Checking…</p></div>
        <button class="abtn abtn--ghost abtn--sm" id="backupBtn" style="margin-top:.8em">Back up now</button>
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
    $("#backupBtn", root).addEventListener("click", async () => {
      try {
        await post("/api/admin/backup-now", {});
        toast("Backup started — it takes a minute or two");
        setTimeout(loadSystem, 4000);
      } catch (e) { toast(e.message, true); }
    });
    loadSystem();
  }

  /* System & backups card (Settings): read-only status from the server */
  async function loadSystem() {
    const body = $("#sysBody");
    if (!body) return;
    const when = (iso) => iso ? new Date(iso).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" }) : "never";
    try {
      const st = await api("/api/admin/system-status");
      const backupJob = st.jobs.find(j => j.name === "nightly_backup") || {};
      const warn = (text) => `<p class="fhint" style="color:var(--red);font-weight:800">⚠️ ${esc(text)}</p>`;
      body.innerHTML = [
        st.database_ok ? "" : warn("The booking database isn't answering."),
        st.backup_stale ? warn("No successful backup in the last 26 hours.") : "",
        st.offsite_backup_configured ? "" :
          warn("Off-site backup isn't set up yet — required before real family data is stored (see README → Backups)."),
        `<table class="table"><tbody>
          <tr><td>Last backup</td><td>${esc(when(backupJob.last_finished_at))} — ${esc(backupJob.last_status || "not run yet")}</td></tr>
          <tr><td>Result</td><td>${esc(backupJob.last_error || backupJob.last_detail || "—")}</td></tr>
          <tr><td>Copies on the server</td><td>${esc(st.local_backups.length)} (kept for a week)</td></tr>
          <tr><td>Database size</td><td>${esc(st.database_kb)} KB</td></tr>
          <tr><td>Background jobs</td><td>${st.worker_running ? "running" : "not running"}</td></tr>
          <tr><td>Email</td><td>${st.email.configured ? "set up" + (st.email.source === "admin settings" ? " (using the SMTP box below — please move it to the server settings)" : "") : "not set up"}</td></tr>
          <tr><td>Text messages</td><td>${st.sms.configured ? "set up (" + esc(st.sms.provider) + ")" : "not set up"}</td></tr>
          <tr><td>Waiting to send</td><td>${esc(((st.outbox.counts.email || {}).queued || 0) + ((st.outbox.counts.sms || {}).queued || 0))} · failed: ${esc(((st.outbox.counts.email || {}).failed || 0) + ((st.outbox.counts.sms || {}).failed || 0))}</td></tr>
        </tbody></table>
        ${st.outbox.recent_failures.length ? `<p class="fhint">Recent failures: ${st.outbox.recent_failures.map(f => esc(f.channel + " " + (f.template_key || "") + ": " + (f.error || ""))).join(" · ")}</p>` : ""}
        <p><button class="abtn abtn--ghost abtn--sm" id="testEmailBtn">Send me a test email</button>
           <button class="abtn abtn--ghost abtn--sm" id="testSmsBtn">Send a test text</button></p>`
      ].join("");
      $("#testEmailBtn").onclick = async () => {
        try { const d = await post("/api/admin/test-email", {}); toast("Test email queued to " + d.to); setTimeout(loadSystem, 12000); }
        catch (e) { toast(e.message, true); }
      };
      $("#testSmsBtn").onclick = async () => {
        const to = prompt("UK mobile number to text:");
        if (!to) return;
        try { await post("/api/admin/test-sms", { to }); toast("Test text queued"); setTimeout(loadSystem, 12000); }
        catch (e) { toast(e.message, true); }
      };
    } catch (e) {
      body.innerHTML = `<p class="fhint">Couldn't load system status: ${esc(e.message)}</p>`;
    }
  }

  /* ---------------- render all ---------------- */
  function renderAll() {
    renderDashboard();
    if (!content) return;  // no website-editing permission: the CMS tabs are hidden
    renderEvents(); renderPast(); renderGallery();
    renderTestimonials(); renderMission(); renderPages();
    renderMessages(); renderSubscribers(); renderSettings();
  }

  /* ---------------- start ---------------- */
  (async function start() {
    try { await boot(); }
    catch (_) { showLogin(); }
  })();
})();

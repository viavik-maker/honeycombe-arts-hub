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
  let freshEvents = new WeakSet();  // events added since the last save: their id still follows the title

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

  /* Accessibility: tie each ".fgroup" label to its field (screen readers read the label; clicking it focuses the
     field), and let keyboard users scroll wide tables. Runs whenever a tab re-renders. */
  let labelSeq = 0;
  function tidyA11y() {
    $$(".fgroup > label:not([for])").forEach((l) => {
      if (l.querySelector("input, select, textarea")) return;
      const f = l.parentElement.querySelector("input:not([type=hidden]), select, textarea");
      if (!f) return;
      if (!f.id) f.id = "fld" + (++labelSeq);
      l.htmlFor = f.id;
    });
    $$(".table-wrap:not([tabindex])").forEach((w) => {
      if (w.scrollWidth > w.clientWidth) { w.tabIndex = 0; w.setAttribute("role", "region"); w.setAttribute("aria-label", "Table (scrolls sideways)"); }
    });
  }
  new MutationObserver(tidyA11y).observe(document.body, { childList: true, subtree: true });

  /* #toast sits inside a live region (index.html) so screen readers hear it: errors in the role="alert" one,
     everything else in the polite role="status" one */
  function toast(msg, err) {
    const t = $("#toast");
    const live = $(err ? "#toastAlert" : "#toastLive");
    if (live && t.parentNode !== live) live.appendChild(t);
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

  /* a real label for each sign-in box (a placeholder alone vanishes as you type and isn't read reliably) */
  let loginSeq = 0;
  const field = (label, input) => {
    const id = "loginF" + (++loginSeq);
    return `<label for="${id}" style="display:block;font-weight:800;font-size:.9rem;margin:0 0 .3em">${esc(label)}</label>` +
      input.replace("<input", `<input id="${id}"`);
  };

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
      <form>${field("Email", `<input type="email" name="email" autocomplete="username" required>`)}
        ${field("Password", `<input type="password" name="password" autocomplete="current-password" required>`)}
        <button class="abtn abtn--primary" type="submit">Sign in</button></form>`,
      async (f) => next(await post("/api/staff/login", { email: f.email.value, password: f.password.value }, { quiet401: true })));
  }

  function stepCode() {
    stepView(`<h1>Enter your code</h1>
      <p>Open your authenticator app and type the 6-digit code for Honeycombe Arts Hub. Lost your phone? Use one of your recovery codes.</p>
      <form>${field("6-digit code or recovery code", `<input name="code" inputmode="numeric" autocomplete="one-time-code" placeholder="123 456" required>`)}
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
        <li>In the app, choose <em>Add account → Scan a QR code</em> and scan this:
          <div class="login__qr">${e.qr_svg || ""}</div>
          Can't scan it? Choose <em>Enter a setup key</em>${/Android|iPhone|iPad/.test(navigator.userAgent) ? ` (or <a href="${esc(e.uri)}">tap here</a>)` : ""}:
          account <strong>Honeycombe Arts Hub</strong>, key <code class="login__key">${esc(grouped)}</code> (time-based).</li>
        <li>Type the 6-digit code the app shows:</li>
      </ol>
      <form>${field("6-digit code", `<input name="code" inputmode="numeric" autocomplete="one-time-code" placeholder="123 456" required>`)}
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
      <form>${field("Current team password", `<input type="password" name="team" autocomplete="off" required>`)}
        ${field("Your name", `<input name="name" autocomplete="name" required>`)}
        ${field("Your email", `<input type="email" name="email" autocomplete="username" required>`)}
        ${field("New password", `<input type="password" name="password" placeholder="10+ characters" autocomplete="new-password" required minlength="10">`)}
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
      <form>${field("New password", `<input type="password" name="password" placeholder="10+ characters" autocomplete="new-password" required minlength="10">`)}
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
    booted = true;
    renderAll();
    document.dispatchEvent(new CustomEvent("hah:ready", { detail: me }));
  }

  /* ---------------- bridge for the ES-module areas (public/admin/js/*.js) ---------------- */
  const moduleTabs = {};
  window.HAHAdmin = {
    api, post, toast, esc, $, $$,
    me: () => me,
    can: (p) => perms.has(p),
    /* the editable website copy, shared with the CMS tabs (public/admin/js/cms/) */
    cms: {
      get content() { return content; },
      get messages() { return messages; }, set messages(v) { messages = v; },
      get subscribers() { return subscribers; }, set subscribers(v) { subscribers = v; },
      get editing() { return editing; },
      get freshEvents() { return freshEvents; },
      dirty: () => dirty(),
      imgPicker: (current, onChange) => imgPicker(current, onChange),
      uploadButton: (onDone) => uploadButton(onDone),
      /* register("events", render) — render now if the admin has already started */
      register(id, render) {
        cmsTabs[id] = render;
        if (booted && (id === "dashboard" || content)) render();
      },
    },
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
    /* openTab("bookings", {session: 12}) — switch area, handing it options to start from */
    openTab(id, opts) {
      const mt = moduleTabs[id];
      if (mt) mt.options = opts || null;
      const b = $(`#sideNav button[data-tab="${id}"]`);
      if (b) b.click();
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
      freshEvents = new WeakSet();  // published: addresses are now fixed
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

  /* The website-editing tabs (Dashboard, What's On, Past Events, Gallery, Testimonials, Impact & Values, Page
     text, Inbox, Newsletter, Settings) live in public/admin/js/cms/*.js and register themselves here. */
  const cmsTabs = {};
  const CMS_ORDER = ["dashboard", "events", "past", "gallery", "testimonials", "mission", "pages", "messages",
    "subscribers", "settings"];
  let booted = false;

  /* ---------------- render all ---------------- */
  function renderAll() {
    CMS_ORDER.forEach(id => {
      if (cmsTabs[id] && (id === "dashboard" || content)) cmsTabs[id]();  // no website-editing permission: CMS tabs hidden
    });
  }

  /* ---------------- start ---------------- */
  (async function start() {
    try { await boot(); }
    catch (_) { showLogin(); }
  })();
})();

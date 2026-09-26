/* Activities (catalogue, sessions, publishing) and Booking settings. */
import { $, $$, api, can, chip, confirmBox, day, esc, modal, money, post, qs, table, toast, when } from "./ui.js";

const A = window.HAHAdmin;
const STATUS_CHIP = { draft: ["Draft", "muted"], scheduled: ["Scheduled", "info"], published: ["Published", "ok"],
  unpublished: ["Unpublished", "warn"], archived: ["Archived", "muted"], past: ["Past", "muted"] };
const TABS = [["current", "Current"], ["draft", "Drafts"], ["scheduled", "Scheduled"], ["published", "Published"],
  ["unpublished", "Unpublished"], ["past", "Past"], ["archived", "Archived"], ["all", "All"]];
const statusChip = (s) => chip(...(STATUS_CHIP[s] || [s, "muted"]));
const pounds = (p) => p == null ? "" : (p / 100).toFixed(2);
const toPence = (v) => v === "" || v == null ? null : Math.round(parseFloat(String(v).replace(/[£,\s]/g, "")) * 100);
const ym = (m) => ({ y: Math.floor(m / 12), m: m % 12 });
let META = null;

async function meta() { if (!META) META = await api("/api/staff/activities/meta"); return META; }

/* errors from a 422: show next to the boxes */
function fieldErrors(root, x) {
  $$(".ferr", root).forEach(e => e.remove());
  const errs = (x.data && x.data.errors) || {};
  for (const [k, m] of Object.entries(errs)) {
    const el = $(`[name="${k}"]`, root);
    if (el) el.closest(".fgroup").insertAdjacentHTML("beforeend", `<p class="ferr">${esc(m)}</p>`);
  }
  toast(x.message, true);
}

A.addTab({
  id: "activities", label: "Activities", icon: "🗓️", perm: "activities.view",
  state: { tab: "current", q: "" },
  async render(root) {
    await meta();
    const st = this.state;
    const d = await api("/api/staff/activities?" + qs({ tab: st.tab, q: st.q }));
    root.innerHTML = `<h1>Activities</h1>
      <p class="sub">Everything families can book. Drafts are only visible here; publish an activity to put it on the Book page.</p>
      <div class="toolbar">
        <div class="segtabs">${TABS.map(([k, l]) => `<button class="abtn abtn--sm ${k === st.tab ? "abtn--honey" : "abtn--ghost"}" data-tab="${k}">${l}</button>`).join("")}</div>
        <input type="search" id="actQ" placeholder="Search" value="${esc(st.q)}">
        ${can("activities.manage") ? `<button class="abtn abtn--primary" id="newAct">＋ New activity</button>` : ""}
        <a class="abtn abtn--ghost abtn--sm" href="/api/staff/activities.csv">Export CSV</a>
      </div>
      ${META.card_payments ? "" : `<p class="fhint">Card payments aren't set up yet, so paid activities need “Allow pay later” until Stripe is connected.</p>`}
      ${table([
        { label: "Activity", get: a => `<a href="#" data-open="${a.id}"><strong>${esc(a.title)}</strong></a><br><span class="fhint">${esc(a.category)} · ages ${esc(a.age_text)}</span>` },
        { label: "Next session", get: a => a.next_date ? esc(day(a.next_date)) : "—" },
        { label: "Sessions", cls: "nowrap", get: a => a.sessions },
        { label: "Booked", cls: "nowrap", get: a => `${a.booked} / ${a.capacity}${a.waiting ? `<br><span class="fhint">${a.waiting} waiting</span>` : ""}` },
        { label: "Status", get: a => statusChip(a.derived_status) + (a.derived_status === "scheduled" ? `<br><span class="fhint">${esc(when(a.publish_at))}</span>` : "") },
      ], d.activities, { empty: "No activities here." })}`;
    $$("[data-tab]", root).forEach(b => b.onclick = () => { st.tab = b.dataset.tab; this.render(root); });
    const q = $("#actQ", root);
    q.onkeydown = (e) => { if (e.key === "Enter") { st.q = q.value; this.render(root); } };
    const nb = $("#newAct", root);
    if (nb) nb.onclick = () => editor(root, null, () => this.render(root));
    $$("[data-open]", root).forEach(a => a.onclick = async (e) => {
      e.preventDefault();
      const r = await api("/api/staff/activities/" + a.dataset.open);
      editor(root, r.activity, () => this.render(root));
    });
  },
});

/* ---------------- the activity editor ---------------- */
function editor(root, act, back) {
  const m = META;
  const a = act || { title: "", category_id: m.categories[0].id, centre_id: m.centres[0].id, registration_level: "full",
    min_age_months: 60, max_age_months: 155, age_basis: "first_session", capacity_default: 20, price_pence: 0,
    adult_price_pence: 0, max_party_size: 8, waitlist_enabled: 1, waitlist_mode: "auto_offer", booking_closes_hours: 0,
    capacity_counts: "children", status: "draft", sessions: [] };
  const manage = can("activities.manage") && a.status !== "archived";
  const lo = ym(a.min_age_months), hi = ym(a.max_age_months);
  const opt = (list, val, key = "id", label = "name") => list.map(x => `<option value="${esc(x[key])}"${String(x[key]) === String(val) ? " selected" : ""}>${esc(x[label])}</option>`).join("");
  const chk = (name, label, hint) => `<label class="fcheck"><input type="checkbox" name="${name}"${a[name] ? " checked" : ""}${manage ? "" : " disabled"}> ${label}</label>${hint ? `<p class="fhint">${hint}</p>` : ""}`;
  root.innerHTML = `<p><button class="abtn abtn--ghost abtn--sm" id="backBtn">← All activities</button></p>
    <h1>${act ? esc(a.title) : "New activity"} ${act ? statusChip(a.derived_status) : ""}</h1>
    ${act ? `<p class="sub">Link: <a href="/book?activity=${esc(a.slug)}" target="_blank" rel="noopener">/book?activity=${esc(a.slug)}</a></p>` : `<p class="sub">Start as a draft — add sessions, then publish.</p>`}
    ${act ? statusActions(a) : ""}
    <form id="actForm" autocomplete="off">
    <div class="acard"><h2>Details</h2>
      <div class="fgroup"><label>Title</label><input type="text" name="title" value="${esc(a.title)}" maxlength="120" required></div>
      <div class="frow">
        <div class="fgroup"><label>Category</label><select name="category_id">${opt(m.categories, a.category_id)}</select></div>
        <div class="fgroup"><label>Centre</label><select name="centre_id">${opt(m.centres, a.centre_id)}</select></div>
      </div>
      <div class="fgroup"><label>Short summary (shown on the Book page)</label><input type="text" name="summary" value="${esc(a.summary || "")}" maxlength="300"></div>
      <div class="fgroup"><label>Description</label><textarea name="description" maxlength="5000">${esc(a.description || "")}</textarea></div>
      <div class="frow"><div class="fgroup"><label>Image (optional)</label><input type="text" name="image" value="${esc(a.image || "")}" placeholder="/uploads/…"><p class="fhint">Copy an image address from the Gallery or What's On.</p></div>
        <div class="fgroup"><label>What's On event id (optional)</label><input type="text" name="event_id" value="${esc(a.event_id || "")}"><p class="fhint">Makes that event's Book button open this activity.</p></div></div>
    </div>
    <div class="acard"><h2>Who and how</h2>
      <div class="frow frow--3">
        <div class="fgroup"><label>Youngest age</label><span class="agepair"><input type="number" name="min_y" min="0" max="99" value="${lo.y}"> y <input type="number" name="min_m" min="0" max="11" value="${lo.m}"> m</span></div>
        <div class="fgroup"><label>Oldest age</label><span class="agepair"><input type="number" name="max_y" min="0" max="99" value="${hi.y}"> y <input type="number" name="max_m" min="0" max="11" value="${hi.m}"> m</span><p class="fhint">Inclusive: “6–12” is 6y 0m to 12y 11m.</p></div>
        <div class="fgroup"><label>Age checked on</label><select name="age_basis">
          <option value="first_session"${a.age_basis === "first_session" ? " selected" : ""}>The first session</option>
          <option value="session_date"${a.age_basis === "session_date" ? " selected" : ""}>Each session's date</option></select></div>
      </div>
      <input type="hidden" name="min_age_months"><input type="hidden" name="max_age_months">
      <div class="fgroup"><label>Registration form families complete</label><select name="registration_level">${Object.entries(m.levels).map(([k, v]) => `<option value="${k}"${k === a.registration_level ? " selected" : ""}>${esc(v)}</option>`).join("")}</select></div>
      ${chk("parent_must_stay", "A parent or carer stays for the session")}
      ${chk("requires_approval", "Staff approve each booking", "Places are held while you decide.")}
      ${chk("haf_only", "HAF only (free, for children eligible for benefits-related free school meals)", "Unverified claims wait for your approval.")}
      <div class="frow"><div class="fgroup"><label>HAF funded days per child</label><input type="number" name="haf_allowance_days" min="1" max="60" value="${a.haf_allowance_days || ""}"></div>
        <div class="fgroup"><label>Places count</label><select name="capacity_counts"><option value="children"${a.capacity_counts === "children" ? " selected" : ""}>Children only</option><option value="all_people"${a.capacity_counts === "all_people" ? " selected" : ""}>Children and adults</option></select></div></div>
    </div>
    <div class="acard"><h2>Booking</h2>
      <div class="frow"><div class="fgroup"><label>Booking opens (optional)</label><input type="datetime-local" name="booking_opens_at_local" value="${esc(a.booking_opens_at_local || "")}"><p class="fhint">Empty = as soon as it's published.</p></div>
        <div class="fgroup"><label>Booking closes (hours before each session)</label><input type="number" name="booking_closes_hours" min="0" max="720" value="${a.booking_closes_hours}"></div></div>
      <div class="frow"><div class="fgroup"><label>Places per session (default)</label><input type="number" name="capacity_default" min="0" max="500" value="${a.capacity_default}"></div>
        <div class="fgroup"><label>Largest group for one-off events</label><input type="number" name="max_party_size" min="1" max="30" value="${a.max_party_size}"></div></div>
      ${chk("waitlist_enabled", "Waiting list when full")}
      <div class="fgroup"><label>When a place frees up</label><select name="waitlist_mode"><option value="auto_offer"${a.waitlist_mode === "auto_offer" ? " selected" : ""}>Offer it to the next family automatically</option><option value="manual"${a.waitlist_mode === "manual" ? " selected" : ""}>Tell staff (in-tray) and let them choose</option></select></div>
      ${chk("allow_pay_later", "Allow pay later (for families you've allowed, or everyone if set in Booking settings)")}
    </div>
    <div class="acard"><h2>Price</h2>
      <div class="frow"><div class="fgroup"><label>Price per session (£)</label><input type="text" inputmode="decimal" name="price" value="${pounds(a.price_pence)}"></div>
        <div class="fgroup"><label>Adult price for one-off events (£)</label><input type="text" inputmode="decimal" name="adult_price" value="${pounds(a.adult_price_pence)}"></div></div>
    </div>
    ${manage ? `<p><button class="abtn abtn--primary" type="submit">${act ? "Save changes" : "Create draft"}</button></p>` : ""}
    </form>
    ${act ? sessionsCard(a) : ""}`;
  if (!manage) $$("#actForm input, #actForm select, #actForm textarea", root).forEach(i => i.disabled = true);
  $("#backBtn", root).onclick = back;
  $("#actForm", root).onsubmit = async (e) => {
    e.preventDefault();
    const f = e.target;
    const body = {
      title: f.title.value, category_id: f.category_id.value, centre_id: f.centre_id.value, summary: f.summary.value,
      description: f.description.value, image: f.image.value, event_id: f.event_id.value,
      min_age_months: (+f.min_y.value || 0) * 12 + (+f.min_m.value || 0), max_age_months: (+f.max_y.value || 0) * 12 + (+f.max_m.value || 0),
      age_basis: f.age_basis.value, registration_level: f.registration_level.value, haf_allowance_days: f.haf_allowance_days.value,
      capacity_counts: f.capacity_counts.value, booking_opens_at_local: f.booking_opens_at_local.value,
      booking_closes_hours: f.booking_closes_hours.value, capacity_default: f.capacity_default.value,
      max_party_size: f.max_party_size.value, waitlist_mode: f.waitlist_mode.value,
      price_pence: toPence(f.price.value) || 0, adult_price_pence: toPence(f.adult_price.value) || 0,
    };
    for (const k of ["parent_must_stay", "requires_approval", "haf_only", "waitlist_enabled", "allow_pay_later"]) body[k] = f[k].checked;
    try {
      const r = await post(act ? `/api/staff/activities/${a.id}/update` : "/api/staff/activities", body);
      toast(act ? "Saved" : "Draft created");
      editor(root, r.activity, back);
    } catch (x) { fieldErrors(root, x); }
  };
  if (act) wireActivity(root, a, back);
}

function statusActions(a) {
  if (!can("activities.manage")) return "";
  const b = (to, label, kind) => `<button class="abtn abtn--sm ${kind || "abtn--ghost"}" data-status="${to}">${label}</button>`;
  const acts = {
    draft: [b("published", "Publish now", "abtn--primary"), b("scheduled", "Schedule…"), b("archived", "Archive")],
    scheduled: [b("published", "Publish now", "abtn--primary"), b("draft", "Back to draft"), b("archived", "Archive")],
    published: [b("unpublished", "Unpublish (hide from Book page)"), b("archived", "Archive")],
    unpublished: [b("published", "Publish again", "abtn--primary"), b("draft", "Back to draft"), b("archived", "Archive")],
    archived: [b("unpublished", "Restore")],
  }[a.status] || [];
  return `<div class="acard"><h2>Status</h2><p class="fhint">${{
    draft: "Only staff can see drafts.", scheduled: "Goes live automatically at " + esc(when(a.publish_at)) + ".",
    published: "Families can see and book it.", unpublished: "Hidden from the Book page; existing bookings stand.",
    archived: "Read-only. No new bookings." }[a.status]}</p><div class="quicklinks">${acts.join("")}
    <button class="abtn abtn--sm abtn--ghost" data-dup>Duplicate for next term…</button>
    <a class="abtn abtn--sm abtn--ghost" href="/api/staff/activities/${a.id}/export.csv">Sessions CSV</a></div>
    <div id="pubProblems"></div></div>`;
}

function sessionsCard(a) {
  const manage = can("activities.manage") && a.status !== "archived";
  const rows = a.sessions;
  return `<div class="acard" id="sessCard"><h2>Sessions (${rows.length})</h2>
    ${manage ? `<div class="quicklinks"><button class="abtn abtn--sm abtn--primary" data-gen>＋ Add a run of sessions…</button>
      <button class="abtn abtn--sm abtn--ghost" data-add>＋ Add one session</button></div>` : ""}
    ${table([
      { label: "Date", get: s => `${esc(day(s.date))}${s.past ? `<br><span class="fhint">past</span>` : ""}` },
      { label: "Time", cls: "nowrap", get: s => `${esc(s.start_time)}–${esc(s.end_time)}` },
      { label: "Theme", get: s => esc(s.theme || "") },
      { label: "Places", cls: "nowrap", get: s => `${s.taken} / ${s.capacity}${s.waiting ? `<br><span class="fhint">${s.waiting} waiting</span>` : ""}` },
      { label: "Price", cls: "nowrap", get: s => esc(money(s.effective_price_pence)) + (s.price_pence != null ? " *" : "") },
      { label: "", get: s => s.status === "cancelled" ? chip("Cancelled", "bad") + (s.cancelled_reason ? `<br><span class="fhint">${esc(s.cancelled_reason)}</span>` : "") : "" },
      { label: "", cls: "nowrap", get: s => manage && s.status !== "cancelled" ? `
        <button class="abtn abtn--ghost abtn--sm" data-edit="${s.id}">Edit</button>
        ${s.waiting ? `<button class="abtn abtn--ghost abtn--sm" data-wl="${s.id}">Waiting list</button>` : ""}
        ${s.ever_booked ? `<button class="abtn abtn--danger abtn--sm" data-cancel="${s.id}">Cancel…</button>` : `<button class="abtn abtn--danger abtn--sm" data-del="${s.id}">Delete</button>`}` : "" },
    ], rows, { empty: "No sessions yet." })}
    ${rows.some(s => s.price_pence != null) ? `<p class="fhint">* price set for that session only</p>` : ""}</div>`;
}

function sessionFields(s, a) {
  return `<div class="frow"><div class="fgroup"><label>Date</label><input type="date" name="date" value="${esc(s.date || "")}" required></div>
    <div class="fgroup"><label>Theme (optional)</label><input type="text" name="theme" value="${esc(s.theme || "")}"></div></div>
    <div class="frow"><div class="fgroup"><label>Starts</label><input type="time" name="start_time" value="${esc(s.start_time || "10:00")}"></div>
    <div class="fgroup"><label>Ends</label><input type="time" name="end_time" value="${esc(s.end_time || "15:00")}"></div></div>
    <div class="frow"><div class="fgroup"><label>Places</label><input type="number" name="capacity" min="0" max="500" value="${s.capacity ?? a.capacity_default}"></div>
    <div class="fgroup"><label>Price for this session (£, optional)</label><input type="text" name="price" value="${s.price_pence != null ? pounds(s.price_pence) : ""}" placeholder="${pounds(a.price_pence)}"></div></div>
    <div class="fgroup"><label>Staff notes</label><input type="text" name="staff_notes" value="${esc(s.staff_notes || "")}"></div>`;
}
const sessionBody = (f) => ({ date: f.date.value, theme: f.theme.value, start_time: f.start_time.value, end_time: f.end_time.value,
  capacity: f.capacity.value, price_pence: toPence(f.price.value), staff_notes: f.staff_notes.value });

function wireActivity(root, a, back) {
  const reload = async () => { const r = await api("/api/staff/activities/" + a.id); editor(root, r.activity, back); };
  $$("[data-status]", root).forEach(b => b.onclick = async () => {
    const to = b.dataset.status;
    let body = { to };
    if (to === "scheduled") {
      const r = await modal("Schedule publishing", `<div class="fgroup"><label>Go live at</label><input type="datetime-local" name="at" required></div>`,
        async (f) => ({ to, publish_at_local: f.at.value }), "Schedule");
      if (!r) return;
      body = r;
    } else if (to === "archived" && !await confirmBox(`Archive “${a.title}”? It can't be booked or edited until restored.`, "Archive")) return;
    try { await post(`/api/staff/activities/${a.id}/status`, body); toast("Updated"); reload(); }
    catch (x) {
      const probs = (x.data && x.data.problems) || [];
      $("#pubProblems", root).innerHTML = probs.length ? `<ul class="problems">${probs.map(p => `<li>${esc(p)}</li>`).join("")}</ul>` : "";
      toast(x.message, true);
    }
  });
  const dup = $("[data-dup]", root);
  if (dup) dup.onclick = async () => {
    const r = await modal("Duplicate for next term", `<div class="fgroup"><label>New title</label><input type="text" name="title" value="${esc(a.title)}"></div>
      <div class="fgroup"><label>First day of the new run</label><input type="date" name="start"></div>
      <p class="fhint">Every session moves by the same number of whole weeks, so weekdays stay the same. The copy starts as a draft.</p>`,
      (f) => post(`/api/staff/activities/${a.id}/duplicate`, { title: f.title.value, new_start_date: f.start.value }), "Duplicate");
    if (r) { toast("Copied as a draft"); editor(root, r.activity, back); }
  };
  const gen = $("[data-gen]", root);
  if (gen) gen.onclick = () => generator(a, reload);
  const add = $("[data-add]", root);
  if (add) add.onclick = async () => {
    const r = await modal("Add a session", sessionFields({}, a), (f) => post(`/api/staff/activities/${a.id}/sessions`, sessionBody(f)), "Add");
    if (r) reload();
  };
  $("#sessCard", root).onclick = async (e) => {
    const t = e.target.closest("[data-edit],[data-cancel],[data-del],[data-wl]");
    if (!t) return;
    const sid = +(t.dataset.edit || t.dataset.cancel || t.dataset.del || t.dataset.wl);
    const s = a.sessions.find(x => x.id === sid);
    try {
      if (t.dataset.edit) {
        const r = await modal("Edit session", sessionFields(s, a), (f) => post(`/api/staff/sessions/${sid}/update`, sessionBody(f)));
        if (r) reload();
      } else if (t.dataset.del) {
        if (await confirmBox(`Delete the session on ${day(s.date)}?`, "Delete")) { await post(`/api/staff/sessions/${sid}/delete`, {}); reload(); }
      } else if (t.dataset.cancel) {
        const r = await modal(`Cancel ${day(s.date)}`, `<p>${s.taken} booked. Every booking is cancelled and families are told by email and text.
          Card payments are refunded to the card; everything else becomes account credit.</p>
          <div class="fgroup"><label>Reason (included in the message)</label><input type="text" name="reason" required></div>
          <div class="fgroup"><label>Money</label><select name="refund"><option value="auto">Refund cards, credit the rest</option><option value="credit">Account credit for everyone</option></select></div>
          <label class="fcheck"><input type="checkbox" name="notify" checked> Email and text families</label>`,
          (f) => post(`/api/staff/sessions/${sid}/cancel`, { reason: f.reason.value, refund: f.refund.value, notify: f.notify.checked }), "Cancel session");
        if (r) { toast(`Session cancelled (${r.cancelled} booking${r.cancelled === 1 ? "" : "s"})`); reload(); }
      } else if (t.dataset.wl) {
        A.openTab("bookings", { session: sid, quick: "waitlist" });
      }
    } catch (x) { toast(x.message, true); }
  };
}

async function generator(a, done) {
  const days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  const html = `<div class="frow"><div class="fgroup"><label>From</label><input type="date" name="from" required></div>
    <div class="fgroup"><label>To</label><input type="date" name="to" required></div></div>
    <div class="fgroup"><label>Days</label><div class="quicklinks">${days.map((d, i) => `<label class="fcheck"><input type="checkbox" name="wd" value="${i}"${i < 5 ? " checked" : ""}> ${d}</label>`).join("")}</div></div>
    <div class="frow"><div class="fgroup"><label>Starts</label><input type="time" name="start_time" value="10:00"></div>
    <div class="fgroup"><label>Ends</label><input type="time" name="end_time" value="15:00"></div></div>
    <div class="frow"><div class="fgroup"><label>Places</label><input type="number" name="capacity" value="${a.capacity_default}"></div>
    <div class="fgroup"><label>Price (£, optional)</label><input type="text" name="price" placeholder="${pounds(a.price_pence)}"></div></div>
    <div class="fgroup"><label>Skip these dates (one per line)</label><textarea name="skip" placeholder="2026-12-25"></textarea></div>
    <label class="fcheck"><input type="checkbox" name="bh" checked> Skip bank holidays (from Booking settings)</label>
    <div class="fgroup"><label>Themes, in order (one per line, optional)</label><textarea name="themes" placeholder="Puppetry&#10;Clay"></textarea></div>
    <div id="genPreview"></div>`;
  let previewed = false;
  const body = (f, dry) => ({ from: f.from.value, to: f.to.value, weekdays: $$("input[name=wd]:checked", f).map(i => +i.value),
    start_time: f.start_time.value, end_time: f.end_time.value, capacity: f.capacity.value, price_pence: toPence(f.price.value),
    skip_dates: f.skip.value.split(/\s+/).filter(Boolean), skip_bank_holidays: f.bh.checked,
    themes: f.themes.value.split("\n").map(s => s.trim()).filter(Boolean), dry_run: dry });
  const r = await modal("Add a run of sessions", html, async (f) => {
    if (!previewed) {
      const p = await post(`/api/staff/activities/${a.id}/sessions/generate`, body(f, true));
      $("#genPreview", f).innerHTML = `<p><strong>${p.sessions.filter(s => !s.exists).length} new sessions</strong>${p.sessions.some(s => s.exists) ? " (some already exist and will be skipped)" : ""}:</p>
        <p class="fhint">${p.sessions.map(s => esc(day(s.date)) + (s.theme ? " · " + esc(s.theme) : "") + (s.exists ? " (exists)" : "")).join("<br>")}</p>
        ${p.skipped.length ? `<p class="fhint">Skipped: ${p.skipped.map(esc).join(", ")}</p>` : ""}`;
      previewed = true;
      $("button[type=submit]", f.closest("dialog")).textContent = "Create sessions";
      f.oninput = () => { previewed = false; $("button[type=submit]", f.closest("dialog")).textContent = "Preview"; };
      throw new Error("Check the list below, then create.");
    }
    return post(`/api/staff/activities/${a.id}/sessions/generate`, body(f, false));
  }, "Preview");
  if (r) { toast(`${r.created} session${r.created === 1 ? "" : "s"} created`); done(); }
}

/* ---------------- Booking settings ---------------- */
const GROUPS = [
  ["Switching on", [["booking_live", "bool", "Online booking is live (Book Now links go to /book)"],
    ["bookings_open_message", "text", "Message on the Book page before it's live"]]],
  ["Policies", [["hold_minutes", "int", "Minutes a place is held while paying by card"], ["cancel_cutoff_hours", "int", "Families can cancel up to (hours before)"],
    ["refund_days", "int", "Refund to card if cancelled at least (days before) — otherwise account credit"],
    ["waitlist_offer_hours", "int", "Waiting-list offers last (hours)"], ["waitlist_offer_hours_soon", "int", "… if the session is within 48 hours"],
    ["pay_later_for_all", "bool", "Everyone may choose pay later (otherwise only families you allow)"],
    ["payment_terms_days", "int", "Invoice payment terms (days)"], ["go_home_alone_min_age", "int", "Youngest age to go home alone"],
    ["bank_holidays", "list", "Bank holidays skipped by the session generator (one per line, YYYY-MM-DD)"],
    ["reporting_year_start_month", "int", "Reporting year starts in month (1 = January, 4 = April, 9 = September)"]]],
  ["Invoices", [["issuer_name", "text", "Name on invoices"], ["issuer_address", "area", "Address"], ["charity_number", "text", "Charity number"],
    ["ofsted_urn", "text", "Ofsted URN"], ["bank_name", "text", "Bank name"], ["bank_sort_code", "text", "Sort code"],
    ["bank_account_number", "text", "Account number"], ["invoice_footer", "area", "Footer"], ["invoice_prefix", "text", "Invoice number prefix"],
    ["credit_note_prefix", "text", "Credit note prefix"]]],
  ["Notifications", [["dsl_notify_emails", "list", "Safeguarding lead email(s)"], ["finance_notify_emails", "list", "Finance email(s)"],
    ["send_notify_emails", "list", "SEND lead email(s) — told when a support request arrives (no details in the email)"],
    ["send_response_days", "int", "SEND lead gets in touch within (working days)"]]],
];

A.addTab({
  id: "booking-settings", label: "Booking settings", icon: "🧾", perm: "settings.manage",
  async render(root) {
    const d = await api("/api/staff/settings/booking");
    const s = d.settings;
    const input = ([k, type, label]) => type === "bool"
      ? `<label class="fcheck"><input type="checkbox" name="${k}"${s[k] ? " checked" : ""}> ${esc(label)}</label>`
      : `<div class="fgroup"><label>${esc(label)}</label>${type === "area" || type === "list"
        ? `<textarea name="${k}">${esc(type === "list" ? s[k].join("\n") : s[k])}</textarea>`
        : `<input type="${type === "int" ? "number" : "text"}" name="${k}" value="${esc(s[k])}">`}</div>`;
    root.innerHTML = `<h1>Booking settings</h1>
      <p class="sub">How booking works for families. Changes apply straight away and are recorded in the audit log.</p>
      <div class="acard"><h2>Card payments</h2><p>${d.stripe.configured ? chip(d.stripe.mode === "live" ? "Stripe: live" : "Stripe: test mode", d.stripe.mode === "live" ? "ok" : "warn") +
        (d.stripe.webhook_secret ? " " + chip("Webhook ✓", "ok") : " " + chip("Webhook secret missing", "bad"))
        : chip("Not set up", "muted") + ` <span class="fhint">Your web developer adds STRIPE_SECRET_KEY and STRIPE_WEBHOOK_SECRET on the server.</span>`}</p></div>
      <form id="bsForm">${GROUPS.map(([title, fields]) => `<div class="acard"><h2>${esc(title)}</h2>${fields.map(input).join("")}</div>`).join("")}
      <p><button class="abtn abtn--primary" type="submit">Save settings</button></p></form>`;
    $("#bsForm", root).onsubmit = async (e) => {
      e.preventDefault();
      const f = e.target, out = {};
      for (const [, fields] of GROUPS) for (const [k, type] of fields) {
        out[k] = type === "bool" ? f[k].checked : type === "list" ? f[k].value.split("\n").map(x => x.trim()).filter(Boolean) : f[k].value;
      }
      if (out.booking_live && !s.booking_live && !await confirmBox("Switch online booking on? Book Now links across the site will go to the new booking page.", "Switch on")) return;
      try { await post("/api/staff/settings/booking", { settings: out }); toast("Saved"); this.render(root); }
      catch (x) { fieldErrors(root, x); }
    };
  },
});

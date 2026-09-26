/* People (search, family and child records, book for a family, merge) and the walk-in screen. */
import { $, $$, api, can, chip, confirmBox, day, esc, modal, money, post, table, toast, when } from "./ui.js";

const A = window.HAHAdmin;
const FLAGS = ["allergy", "anaphylaxis", "medical", "dietary", "send", "semh"];
const LEVEL = { none: ["Incomplete", "warn"], short: ["Baby & toddler", "info"], full: ["Full", "ok"], adult: ["Adult", "ok"] };
const levelChip = (l) => chip(...(LEVEL[l] || [l, "muted"]));

async function download(path, body, name) {
  const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": A.me().csrf }, body: JSON.stringify(body) });
  if (!r.ok) { toast("Export failed", true); return; }
  const url = URL.createObjectURL(await r.blob());
  const a = document.createElement("a"); a.href = url; a.download = name; a.click(); URL.revokeObjectURL(url);
}

A.addTab({
  id: "people", label: "People", icon: "🔎", perm: "people.view_basic",
  state: { q: "", scope: "children", filters: {}, results: null, open: null },
  async render(root) {
    const st = this.state;
    if (this.options) { Object.assign(st, this.options); this.options = null; }
    if (st.open) return st.open.type === "account" ? familyRecord(root, st.open.ref, () => { st.open = null; this.render(root); }, (o) => { st.open = o; this.render(root); })
      : childRecord(root, st.open.ref, () => { st.open = null; this.render(root); }, (o) => { st.open = o; this.render(root); });
    const health = can("people.view_health");
    root.innerHTML = `<h1>People</h1>
      <p class="sub">Find families and children. Opening someone's record is logged.</p>
      <form id="pForm" class="acard">
        <div class="toolbar"><div class="segtabs">${[["children", "Children"], ["families", "Families"], ...(can("staff.manage") ? [["staff", "Staff"]] : [])].map(([k, l]) =>
          `<button type="button" class="abtn abtn--sm ${k === st.scope ? "abtn--honey" : "abtn--ghost"}" data-scope="${k}">${l}</button>`).join("")}</div>
          <input type="search" name="q" placeholder="Name, email, phone, postcode or ref" value="${esc(st.q)}" style="flex:1">
          <button class="abtn abtn--primary">Search</button></div>
        ${st.scope === "children" ? `<div class="quicklinks">
          ${health ? FLAGS.map(f => `<label class="fcheck"><input type="checkbox" name="f_${f}"${st.filters[f] ? " checked" : ""}> ${f.toUpperCase()}</label>`).join("") : ""}
          <label class="fcheck"><input type="checkbox" name="needs_review"${st.filters.needs_review ? " checked" : ""}> Needs checking</label>
          <select name="level"><option value="">Any form level</option>${Object.entries(LEVEL).map(([k, [l]]) => `<option value="${k}"${st.filters.level === k ? " selected" : ""}>${l}</option>`).join("")}</select>
          ${can("bookings.manage") ? `<select name="haf"><option value="">Any HAF status</option>${["claimed_eligible", "not_sure", "verified", "not_eligible"].map(k => `<option value="${k}"${st.filters.haf === k ? " selected" : ""}>${k.replace("_", " ")}</option>`).join("")}</select>` : ""}
          <label>Age <input type="number" name="min_age" min="0" max="25" style="width:4em" value="${esc(st.filters.min_age || "")}"> to <input type="number" name="max_age" min="0" max="25" style="width:4em" value="${esc(st.filters.max_age || "")}"></label></div>` : ""}
      </form>
      <div id="pResults">${st.results ? results(st) : ""}</div>
      ${can("people.edit") ? `<p><button class="abtn abtn--ghost abtn--sm" id="dupBtn">Possible duplicates</button></p>` : ""}`;
    $$("[data-scope]", root).forEach(b => b.onclick = () => { st.scope = b.dataset.scope; st.results = null; this.render(root); });
    $("#pForm", root).onsubmit = async (e) => {
      e.preventDefault();
      const f = e.target;
      st.q = f.q.value;
      st.filters = {};
      if (st.scope === "children") {
        FLAGS.forEach(k => { if (f["f_" + k] && f["f_" + k].checked) st.filters[k] = true; });
        if (f.needs_review.checked) st.filters.needs_review = true;
        if (f.level.value) st.filters.level = f.level.value;
        if (f.haf && f.haf.value) st.filters.haf = f.haf.value;
        if (f.min_age.value) st.filters.min_age = f.min_age.value;
        if (f.max_age.value) st.filters.max_age = f.max_age.value;
      }
      try { st.results = await post("/api/staff/search", { q: st.q, scope: st.scope, filters: st.filters }); this.render(root); }
      catch (x) { toast(x.message, true); }
    };
    const ex = $("#pExport", root);
    if (ex) ex.onclick = () => download("/api/staff/search/export", { q: st.q, scope: st.scope, filters: st.filters }, st.scope + ".csv");
    $$("[data-child]", root).forEach(a => a.onclick = (e) => { e.preventDefault(); st.open = { type: "child", ref: a.dataset.child }; this.render(root); });
    $$("[data-family]", root).forEach(a => a.onclick = (e) => { e.preventDefault(); st.open = { type: "account", ref: a.dataset.family }; this.render(root); });
    const dup = $("#dupBtn", root);
    if (dup) dup.onclick = async () => {
      const d = await api("/api/staff/people/duplicates");
      modal("Possible duplicates", `<h3>Same child name and date of birth</h3>${d.children.length ? `<ul>${d.children.map(x => `<li>${esc(x.name)} (${esc(x.dob)}) — ${esc(x.families)}</li>`).join("")}</ul>` : "<p>None.</p>"}
        <h3>Same mobile number</h3>${d.mobiles.length ? `<ul>${d.mobiles.map(x => `<li>${esc(x.mobile)} — ${esc(x.families)}</li>`).join("")}</ul>` : "<p>None.</p>"}
        <p class="fhint">Open one of the families and choose “Merge into…” if they're the same family.</p>`, async () => true, "Close");
    };
  },
});

function results(st) {
  const r = st.results.results;
  const head = `<p>${r.length}${r.length >= 200 ? "+" : ""} found · <button class="abtn abtn--ghost abtn--sm" id="pExport">Export CSV</button></p>`;
  if (st.results.scope === "families") return head + table([
    { label: "Family", get: x => `<a href="#" data-family="${esc(x.ref)}"><strong>${esc(x.name)}</strong></a><br><span class="fhint">${esc(x.email || "")} ${esc(x.mobile || "")}</span>` },
    { label: "Children", get: x => esc(x.children.join(", ")) },
    { label: "Status", get: x => chip(x.status.replace("_", " "), x.status === "active" ? "ok" : "muted") + (x.source !== "self" ? " " + chip(x.source, "info") : "") },
  ], r);
  if (st.results.scope === "staff") return head + table([{ label: "Name", get: x => esc(x.name) }, { label: "Email", get: x => esc(x.email) }, { label: "Status", get: x => esc(x.status) }], r);
  return head + table([
    { label: "Child", get: x => `<a href="#" data-child="${esc(x.ref)}"><strong>${esc(x.name)}</strong></a> <span class="fhint">(${x.age})</span>` },
    { label: "Needs", get: x => x.flags.map(f => chip(f.toUpperCase(), f === "anaphylaxis" ? "bad" : "warn")).join(" ") + (x.needs_review ? " " + chip("needs checking", "warn") : "") },
    { label: "Form", get: x => levelChip(x.level) + (x.haf && x.haf !== "unknown" ? " " + chip("HAF: " + x.haf.replace("_", " "), "info") : "") },
    { label: "Family", get: x => `<a href="#" data-family="${esc(x.family.ref)}">${esc(x.family.name)}</a><br><span class="fhint">${esc(x.family.mobile || "")}</span>` },
  ], r);
}

/* ---------------- family record ---------------- */
async function familyRecord(root, ref, back, open) {
  const d = await api("/api/staff/people/accounts/" + encodeURIComponent(ref));
  const a = d.account;
  root.innerHTML = `<p><button class="abtn abtn--ghost abtn--sm" id="bk">← Search</button></p>
    <h1>${esc(a.first_name)} ${esc(a.last_name)} ${chip(a.status.replace("_", " "), a.status === "active" ? "ok" : "warn")}</h1>
    <p class="sub">${esc(a.email || "no email")} · ${esc(a.mobile || "no mobile")} · ${esc([a.address_line1, a.town, a.postcode].filter(Boolean).join(", "))}
      · joined ${esc(when(a.created_at))} (${esc(a.source)})${a.last_login_at ? " · last signed in " + esc(when(a.last_login_at)) : ""}</p>
    <div class="quicklinks">
      ${can("bookings.manage") ? `<button class="abtn abtn--primary abtn--sm" id="bookFor">Book sessions…</button>` : ""}
      ${can("messaging.service") ? `<button class="abtn abtn--ghost abtn--sm" id="msgFam">Message</button>` : ""}
      ${can("gdpr.manage") ? `<a class="abtn abtn--ghost abtn--sm" href="/api/staff/people/accounts/${esc(a.ref)}/export">Download their data (SAR)</a>` : ""}
      ${can("gdpr.manage") && a.status === "closed" ? `<button class="abtn abtn--ghost abtn--sm" id="restoreFam">Restore account</button>` : ""}
      ${can("people.edit") ? `<button class="abtn abtn--ghost abtn--sm" id="editFam">Edit</button>` : ""}
      ${can("people.edit") && a.status === "pending_activation" && a.email ? `<button class="abtn abtn--ghost abtn--sm" id="actFam">Resend activation email</button>` : ""}
      ${can("finance.manage") ? `<button class="abtn abtn--ghost abtn--sm" id="plFam">${a.pay_later_allowed ? "Stop" : "Allow"} pay later</button>` : ""}
      ${can("people.edit") && can("bookings.manage") ? `<button class="abtn abtn--ghost abtn--sm" id="mergeFam">Merge into…</button>` : ""}
    </div>
    ${a.staff_notes ? `<div class="acard"><h2>Staff notes</h2><p style="white-space:pre-line">${esc(a.staff_notes)}</p></div>` : ""}
    <div class="acard"><h2>Children</h2>${table([
      { label: "Name", get: p => `<a href="#" data-child="${esc(p.ref)}"><strong>${esc(p.first_name)} ${esc(p.last_name)}</strong></a> <span class="fhint">(${p.age})</span>` },
      { label: "Form", get: p => levelChip(p.level) + (p.missing.length ? `<br><span class="fhint">missing: ${esc(p.missing.join(", "))}</span>` : "") },
      { label: "Needs", get: p => Object.entries(p.flags || {}).filter(([, v]) => v).map(([k]) => chip(k.toUpperCase(), "warn")).join(" ") },
    ], d.participants)}</div>
    <div class="acard"><h2>Emergency contacts</h2>${table([
      { label: "Name", get: c => esc(c.full_name) }, { label: "Relationship", get: c => esc(c.relationship) },
      { label: "Phone", get: c => esc(c.phone) }, { label: "Can collect", get: c => c.can_collect ? "Yes" : "" }], d.contacts, { empty: "None yet." })}</div>
    ${d.bookings ? `<div class="acard"><h2>Bookings (${d.upcoming} coming up)${d.credit_pence ? ` · ${esc(money(d.credit_pence))} credit` : ""}</h2>${table([
      { label: "Date", get: b => esc(day(b.date)) }, { label: "Activity", get: b => esc(b.activity) },
      { label: "Who", get: b => esc(b.who ? b.who.first_name : b.places + " place(s)") }, { label: "Status", get: b => esc(b.status_text) },
      { label: "", get: b => `<button class="abtn abtn--ghost abtn--sm" data-bk="${b.id}">Open</button>` }], d.bookings.slice(0, 40), { empty: "No bookings." })}
      ${d.invoices.length ? `<h3>Invoices</h3>${table([
        { label: "Number", get: i => `<a href="/admin/invoices/${esc(i.number)}" target="_blank" rel="noopener">${esc(i.number)}</a>` },
        { label: "Issued", get: i => esc(i.issue_date) }, { label: "Total", get: i => esc(money(i.total_pence)) },
        { label: "Balance", get: i => i.balance_pence > 0 ? `<strong>${esc(money(i.balance_pence))}</strong>${i.overdue ? " " + chip("overdue", "bad") : ""}` : chip(i.status, "ok") }], d.invoices)}` : ""}</div>` : ""}
    ${d.messages.length ? `<div class="acard"><h2>Messages sent</h2>${table([{ label: "When", get: m => esc(when(m.created_at)) },
      { label: "", get: m => esc(m.channel) }, { label: "Subject", get: m => esc(m.subject || m.template_key) }, { label: "Status", get: m => esc(m.status) }], d.messages)}</div>` : ""}`;
  $("#bk", root).onclick = back;
  $$("[data-child]", root).forEach(x => x.onclick = (e) => { e.preventDefault(); open({ type: "child", ref: x.dataset.child }); });
  $$("[data-bk]", root).forEach(x => x.onclick = () => A.openTab("bookings", { q: "", quick: "", openId: +x.dataset.bk }));
  const reload = () => familyRecord(root, ref, back, open);
  const on = (id, fn) => { const el = $(id, root); if (el) el.onclick = () => fn().catch(x => toast(x.message, true)); };
  on("#bookFor", () => bookForFamily(a.ref, d.participants).then(r => r && reload()));
  on("#msgFam", async () => A.openTab("messages2", { audience: { type: "accounts", refs: [a.ref] } }));
  on("#restoreFam", async () => { await post(`/api/staff/people/accounts/${a.ref}/restore`, {}); toast("Restored"); reload(); });
  on("#editFam", async () => {
    const r = await modal("Edit family", `<div class="frow"><div class="fgroup"><label>First name</label><input type="text" name="first_name" value="${esc(a.first_name)}"></div>
      <div class="fgroup"><label>Last name</label><input type="text" name="last_name" value="${esc(a.last_name)}"></div></div>
      <div class="frow"><div class="fgroup"><label>Mobile</label><input type="text" name="mobile" value="${esc(a.mobile || "")}"></div>
      <div class="fgroup"><label>Postcode</label><input type="text" name="postcode" value="${esc(a.postcode || "")}"></div></div>
      <div class="fgroup"><label>Address</label><input type="text" name="address_line1" value="${esc(a.address_line1 || "")}"></div>
      <div class="fgroup"><label>Town</label><input type="text" name="town" value="${esc(a.town || "")}"></div>
      <div class="fgroup"><label>Staff notes (not shown to the family)</label><textarea name="staff_notes">${esc(a.staff_notes || "")}</textarea></div>`,
      (f) => post(`/api/staff/people/accounts/${a.ref}/update`, { first_name: f.first_name.value, last_name: f.last_name.value,
        mobile: f.mobile.value, postcode: f.postcode.value, address_line1: f.address_line1.value, town: f.town.value, staff_notes: f.staff_notes.value }));
    if (r) reload();
  });
  on("#actFam", async () => { await post(`/api/staff/people/accounts/${a.ref}/activation`, {}); toast("Activation email queued"); });
  on("#plFam", async () => { await post(`/api/staff/accounts/${a.ref}/pay-later`, { allowed: !a.pay_later_allowed }); reload(); });
  on("#mergeFam", async () => {
    const r = await modal("Merge this family into another", `<p>Everything (children, bookings, invoices, contacts) moves to the other account, and this one is closed. This can't be undone.</p>
      <div class="fgroup"><label>The other family's ref (e.g. A-XXXXXXXX)</label><input type="text" name="into" required></div>`,
      async (f) => { if (!await confirmBox("Merge and close this account?", "Merge")) return null; return post(`/api/staff/people/accounts/${a.ref}/merge`, { into_ref: f.into.value.trim() }); }, "Merge");
    if (r) { toast("Merged"); back(); }
  });
}

/* ---------------- child record ---------------- */
async function childRecord(root, ref, back, open, tab) {
  tab = tab || "basic";
  let d;
  try { d = await api(`/api/staff/people/participants/${encodeURIComponent(ref)}?tab=${tab}`); }
  catch (x) { toast(x.message, true); return; }
  const p = d.participant;
  const hl = d.health || {};
  const body = tab === "health" ? `<table class="table"><tbody>${Object.entries({ Allergies: hl.allergies, Anaphylaxis: hl.anaphylaxis ? "Yes" : "No",
      "Adrenaline pen": hl.adrenaline_pen ? "Yes" : "No", Medical: hl.medical_conditions, Medication: hl.medication, Diet: hl.dietary,
      SEND: hl.send_needs, SEMH: hl.semh_needs, "Religious / cultural": hl.religious_requirements, Access: hl.access_needs,
      GP: d.gp ? [d.gp.surgery_name, d.gp.doctor_name, d.gp.surgery_phone].filter(Boolean).join(" · ") : "" })
      .map(([k, v]) => `<tr><th>${esc(k)}</th><td>${esc(v || "—")}</td></tr>`).join("")}</tbody></table>`
    : tab === "safeguarding" ? `<div class="acard"><p style="white-space:pre-line">${esc((d.safeguarding && d.safeguarding.family_info) || "Nothing given.")}</p>
      <p class="fhint">${d.safeguarding && d.safeguarding.dsl_reviewed_at ? "Reviewed " + esc(when(d.safeguarding.dsl_reviewed_at)) : "Not reviewed yet."}</p>
      <label class="fcheck"><input type="checkbox" id="sgFlag"${d.flag_safeguarding ? " checked" : ""}> Show a safeguarding flag to managers (details stay private)</label>
      <button class="abtn abtn--primary abtn--sm" id="sgSave">Mark reviewed</button></div>`
    : `<table class="table"><tbody>
      <tr><th>Date of birth</th><td>${esc(p.dob)} (${p.age})</td></tr>
      <tr><th>School</th><td>${esc(p.education === "home_educated" ? "Home educated" : p.school_name || "—")}</td></tr>
      <tr><th>Photos</th><td>${esc(p.photo || "not answered")}</td></tr>
      <tr><th>Permissions</th><td>${esc(Object.entries(d.consents).map(([k, v]) => k.replace("_", " ") + ": " + v).join(" · ") || "—")}</td></tr>
      <tr><th>Collection</th><td>${p.has_collection_password ? "Password set" : "No password"}${p.collection_alert ? `<br><strong class="bad">Alert:</strong> ${esc(p.collection_alert)}` : ""}</td></tr>
      ${p.support_plan ? `<tr><th>Support plan</th><td style="white-space:pre-line">${esc(p.support_plan)}</td></tr>` : ""}
      ${p.haf ? `<tr><th>HAF</th><td>${esc(p.haf.replace("_", " "))}</td></tr>` : ""}
      ${d.attended != null ? `<tr><th>Sessions attended</th><td>${d.attended}</td></tr>` : ""}</tbody></table>
      ${d.bookings ? `<h3>Bookings</h3>${table([{ label: "Date", get: b => esc(day(b.date)) }, { label: "Activity", get: b => esc(b.activity) }, { label: "Status", get: b => esc(b.status_text) }], d.bookings.slice(0, 30), { empty: "None." })}` : ""}
      ${d.incidents && d.incidents.length ? `<h3>Incidents</h3>${table([{ label: "When", get: i => esc(when(i.occurred_at)) }, { label: "What", get: i => esc(i.kind_text) }], d.incidents)}` : ""}`;
  root.innerHTML = `<p><button class="abtn abtn--ghost abtn--sm" id="bk">← Back</button></p>
    <h1>${esc(p.first_name)} ${esc(p.last_name)} ${levelChip(p.level)}</h1>
    <p class="sub">Family: <a href="#" id="famLink">${esc(d.family.name)}</a>${p.missing.length ? " · missing: " + esc(p.missing.join(", ")) : ""}</p>
    <div class="segtabs">${d.tabs.map(t => `<button class="abtn abtn--sm ${t === tab ? "abtn--honey" : "abtn--ghost"}" data-tab2="${t}">${{ basic: "Details", health: "Health", safeguarding: "Safeguarding (DSL)" }[t]}</button>`).join("")}
      ${can("people.edit") ? `<button class="abtn abtn--sm abtn--ghost" id="editChild">Edit</button>` : ""}
      ${can("bookings.manage") ? `<button class="abtn abtn--sm abtn--ghost" id="legacyBtn">Add pre-sold places (MagicBooking)</button>` : ""}</div>
    <div class="acard" style="margin-top:1rem">${body}</div>`;
  $("#bk", root).onclick = back;
  $("#famLink", root).onclick = (e) => { e.preventDefault(); open({ type: "account", ref: d.family.ref }); };
  $$("[data-tab2]", root).forEach(b => b.onclick = () => childRecord(root, ref, back, open, b.dataset.tab2));
  const sg = $("#sgSave", root);
  if (sg) sg.onclick = async () => { await post(`/api/staff/people/participants/${ref}/update`, { f_safeguarding: $("#sgFlag", root).checked }); toast("Saved"); };
  const lg = $("#legacyBtn", root);
  if (lg) lg.onclick = async () => {
    const { sessions } = await api("/api/staff/sessions/upcoming");
    const r = await modal(`Pre-sold places for ${p.first_name}`, `<p class="fhint">For places already paid for in MagicBooking. They count towards capacity and appear on registers, but no invoice is raised.</p>
      <div class="fgroup"><label>Sessions</label><select name="s" multiple size="10">${sessions.map(x => `<option value="${x.id}">${esc(day(x.date))} ${esc(x.start_time)} · ${esc(x.title)} · ${x.free} free</option>`).join("")}</select></div>
      <label class="fcheck"><input type="checkbox" name="ov"> Add even if a session is full</label>`,
      (f) => post("/api/staff/bookings/legacy", { participant_ref: ref, session_ids: Array.from(f.s.selectedOptions).map(o => +o.value), override: f.ov.checked }), "Add places");
    if (r) { toast(`${r.created.length} added` + (r.problems.length ? " · " + r.problems.join("; ") : "")); childRecord(root, ref, back, open, tab); }
  };
  const ed = $("#editChild", root);
  if (ed) ed.onclick = async () => {
    const r = await modal(`Edit ${p.first_name}`, `<div class="frow"><div class="fgroup"><label>First name</label><input type="text" name="first_name" value="${esc(p.first_name)}"></div>
      <div class="fgroup"><label>Last name</label><input type="text" name="last_name" value="${esc(p.last_name)}"></div></div>
      <div class="fgroup"><label>Date of birth</label><input type="date" name="dob" value="${esc(p.dob)}"></div>
      ${can("bookings.manage") ? `<div class="fgroup"><label>HAF status</label><select name="haf">${["unknown", "claimed_eligible", "not_sure", "verified", "not_eligible"].map(k => `<option value="${k}"${k === p.haf ? " selected" : ""}>${k.replace("_", " ")}</option>`).join("")}</select></div>` : ""}
      <div class="fgroup"><label>Collection alert (shown on registers — e.g. someone who must not collect)</label><textarea name="alert">${esc(p.collection_alert || "")}</textarea></div>
      <label class="fcheck"><input type="checkbox" name="review"${p.needs_review ? " checked" : ""}> Ask the family to check these details</label>`,
      (f) => post(`/api/staff/people/participants/${ref}/update`, Object.assign({ first_name: f.first_name.value, last_name: f.last_name.value,
        dob: f.dob.value, collection_alert: f.alert.value, needs_review: f.review.checked }, f.haf ? { haf_status: f.haf.value } : {})));
    if (r) childRecord(root, ref, back, open, tab);
  };
}

/* ---------------- book sessions for a family ---------------- */
async function bookForFamily(accountRef, people) {
  const { sessions } = await api("/api/staff/sessions/upcoming");
  const kids = people.filter(p => p.status === "active");
  return modal("Book sessions", `
    <div class="fgroup"><label>Who</label><div class="quicklinks">${kids.map(p => `<label class="fcheck"><input type="checkbox" name="who" value="${esc(p.ref)}"> ${esc(p.first_name)}</label>`).join("")}</div></div>
    <div class="fgroup"><label>Sessions</label><select name="sessions" multiple size="8">${sessions.map(s =>
      `<option value="${s.id}">${esc(day(s.date))} ${esc(s.start_time)} · ${esc(s.title)} · ${s.price_pence ? esc(money(s.price_pence)) : "free"} · ${s.free} free</option>`).join("")}</select>
      <p class="fhint">Hold Ctrl (or ⌘) to choose several.</p></div>
    <div class="fgroup"><label>Payment</label><select name="mode"><option value="unpaid">Leave unpaid (invoice, pay later)</option>
      <option value="record">Paid now</option><option value="link">Email the invoice to pay online</option><option value="comp">No charge (complimentary)</option></select></div>
    <div class="frow"><div class="fgroup"><label>Paid by</label><select name="method"><option value="cash">Cash</option><option value="card_terminal">Card machine</option>
      <option value="bank_transfer">Bank transfer</option><option value="childcare_voucher">Childcare vouchers</option><option value="tax_free_childcare">Tax-Free Childcare</option><option value="other">Other</option></select></div>
      <div class="fgroup"><label>Reference (never a card number)</label><input type="text" name="reference"></div></div>
    ${can("bookings.override") ? `<label class="fcheck"><input type="checkbox" name="override"> Override rules (age, full, HAF…)</label>
      <div class="fgroup"><label>Reason for overriding</label><input type="text" name="reason"></div>` : ""}
    <label class="fcheck"><input type="checkbox" name="trial"> Trial session (their first go at this activity; the trial price applies)</label>
    <label class="fcheck"><input type="checkbox" name="notify" checked> Email the family</label>`,
    async (f) => {
      const who = $$("input[name=who]:checked", f).map(i => i.value);
      const sids = Array.from(f.sessions.selectedOptions).map(o => +o.value);
      if (!who.length || !sids.length) throw new Error("Choose who and at least one session.");
      const items = sids.flatMap(sid => who.map(ref => ({ session_id: sid, participant: ref })));
      try {
        const r = await post("/api/staff/bookings/create", { account_ref: accountRef, items, notify: f.notify.checked, is_trial: f.trial.checked,
          override: f.override ? f.override.checked : false, reason: f.reason ? f.reason.value : "",
          payment: { mode: f.mode.value, method: f.method.value, reference: f.reference.value } });
        toast(`${r.bookings.length} booked`);
        return r;
      } catch (x) {
        if (x.data && x.data.problems) throw new Error(x.data.problems.join(" · "));
        throw x;
      }
    }, "Book");
}

/* ---------------- walk-in ---------------- */
A.addTab({
  id: "walkin", label: "Walk-in", icon: "🚪", perm: "bookings.manage",
  async render(root) {
    const { sessions } = await api("/api/staff/sessions/upcoming?range=today");
    root.innerHTML = `<h1>Walk-in: book and pay on the spot</h1>
      <p class="sub">For a family who turns up without booking. We'll email them to finish their details online.</p>
      ${sessions.length ? "" : `<p class="empty">No sessions today.</p>`}
      <form id="wiForm" class="acard" autocomplete="off">
        <div class="fgroup"><label>Session</label><select name="session_id">${sessions.map(s => `<option value="${s.id}">${esc(s.start_time)} · ${esc(s.title)} · ${s.price_pence ? esc(money(s.price_pence)) : "free"} · ${s.free} free</option>`).join("")}</select></div>
        <h3>Parent or carer</h3>
        <div class="frow"><div class="fgroup"><label>First name</label><input type="text" name="p_first"></div><div class="fgroup"><label>Last name</label><input type="text" name="p_last"></div></div>
        <div class="frow"><div class="fgroup"><label>Mobile</label><input type="tel" name="p_mobile"></div><div class="fgroup"><label>Email (optional — for their account)</label><input type="email" name="p_email"></div></div>
        <h3>Child</h3>
        <div class="frow frow--3"><div class="fgroup"><label>First name</label><input type="text" name="c_first"></div><div class="fgroup"><label>Last name</label><input type="text" name="c_last"></div>
          <div class="fgroup"><label>Date of birth</label><input type="date" name="c_dob"></div></div>
        <div class="fgroup"><label>Allergies or medical needs (write “none” if none)</label><input type="text" name="allergies"></div>
        <h3>Emergency contact (not the parent)</h3>
        <div class="frow frow--3"><div class="fgroup"><label>Name</label><input type="text" name="e_name"></div><div class="fgroup"><label>Relationship</label><input type="text" name="e_rel"></div>
          <div class="fgroup"><label>Phone</label><input type="tel" name="e_phone"></div></div>
        <h3>Permissions (asked in person)</h3>
        <div class="frow"><div class="fgroup"><label>Photos</label><select name="photo"><option value="">— ask —</option><option value="online">Yes, including online</option><option value="internal">Internal use only</option><option value="none">No photos</option></select></div>
          <div class="fgroup"><label>First aid</label><select name="first_aid"><option value="">— ask —</option><option value="yes">Yes</option><option value="no">No</option></select></div></div>
        <label class="fcheck"><input type="checkbox" name="paper"> Given on a paper form (otherwise recorded as verbal)</label>
        <h3>Payment</h3>
        <div class="frow"><div class="fgroup"><label>Paid by</label><select name="method"><option value="cash">Cash</option><option value="card_terminal">Card machine</option>
          <option value="childcare_voucher">Childcare vouchers</option><option value="tax_free_childcare">Tax-Free Childcare</option><option value="">Not paid yet</option></select></div>
          <div class="fgroup"><label>Reference (never a card number)</label><input type="text" name="reference"></div></div>
        <label class="fcheck"><input type="checkbox" name="sign_in" checked> Sign them in now</label>
        <div id="wiErr"></div>
        <p><button class="abtn abtn--primary"${sessions.length ? "" : " disabled"}>Book${" "}and sign in</button></p></form>`;
    $("#wiForm", root).onsubmit = async (e) => {
      e.preventDefault();
      const f = e.target;
      const body = { session_id: f.session_id.value, parent: { first_name: f.p_first.value, last_name: f.p_last.value, mobile: f.p_mobile.value, email: f.p_email.value },
        child: { first_name: f.c_first.value, last_name: f.c_last.value, dob: f.c_dob.value }, allergies: f.allergies.value,
        contact: { full_name: f.e_name.value, relationship: f.e_rel.value, phone: f.e_phone.value },
        photo: f.photo.value, first_aid: f.first_aid.value, consent_source: f.paper.checked ? "staff_paper" : "staff_verbal",
        payment: f.method.value ? { mode: "record", method: f.method.value, reference: f.reference.value } : { mode: "unpaid" }, sign_in: f.sign_in.checked };
      try {
        const r = await post("/api/staff/walkin", body);
        toast("Booked" + (f.sign_in.checked ? " and signed in" : ""));
        $("#wiErr", root).innerHTML = `<p class="ok">Done — family ${esc(r.account_ref)}${r.invoice ? ", invoice " + esc(r.invoice.number) + (r.invoice.balance_pence ? " (" + esc(money(r.invoice.balance_pence)) + " to pay)" : " (paid)") : ""}.</p>`;
        f.reset();
      } catch (x) {
        const errs = (x.data && x.data.errors) || {};
        $("#wiErr", root).innerHTML = `<ul class="problems">${(Object.values(errs).length ? Object.values(errs) : [x.message]).map(m => `<li>${esc(m)}</li>`).join("")}${x.data && x.data.problems ? x.data.problems.map(m => `<li>${esc(m)}</li>`).join("") : ""}</ul>`;
      }
    };
  },
});

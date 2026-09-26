/* Registers (sign in/out, collection checks, print) and the incident log. */
import { $, $$, api, can, chip, day, esc, modal, post, qs, table, toast, when } from "./ui.js";
import * as bodymap from "/js/bodymap.js";

const A = window.HAHAdmin;
const FLAG_TEXT = { allergy: "Allergy", anaphylaxis: "ANAPHYLAXIS", medical: "Medical", dietary: "Diet", send: "SEND",
  semh: "SEMH", religious: "Religious/cultural" };
const STATUS = { expected: ["Expected", "muted"], present: ["In", "ok"], absent: ["Absent", "bad"], absent_notified: ["Absent (told us)", "warn"] };
const PHOTO = { online: ["Photos OK", "ok"], internal: ["Photos: internal only", "warn"], none: ["NO PHOTOS", "bad"] };
const isoToday = () => new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 10);
const hhmm = (iso) => iso ? new Date(iso).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }) : "";

A.addTab({
  id: "registers", label: "Registers", icon: "✅", perm: "registers.view",
  state: { date: null, centre: "", open: null },
  async render(root) {
    const st = this.state;
    st.date = st.date || isoToday();
    if (st.open) return openRegister(root, st.open, () => { st.open = null; this.render(root); }, (sid) => { st.open = sid; });
    const d = await api("/api/staff/registers?" + qs({ date: st.date, centre: st.centre }));
    root.innerHTML = `<h1>Registers</h1>
      <p class="sub">Sign children in and out. Session staff can open today's and tomorrow's registers.</p>
      <div class="toolbar"><button class="abtn abtn--ghost abtn--sm" data-shift="-1">← Previous day</button>
        <input type="date" id="regDate" value="${esc(st.date)}">
        <button class="abtn abtn--ghost abtn--sm" data-shift="1">Next day →</button>
        ${d.centres.length > 1 ? `<select id="regCentre"><option value="">All centres</option>${d.centres.map(c => `<option value="${c.id}"${String(c.id) === st.centre ? " selected" : ""}>${esc(c.name)}</option>`).join("")}</select>` : ""}
        ${can("reports.view") ? `<button class="abtn abtn--ghost abtn--sm" id="hafExport">HAF export…</button>` : ""}</div>
      ${d.sessions.length ? `<div class="regcards">${d.sessions.map(s => `<button class="regcard" data-sid="${s.id}"${s.allowed ? "" : " disabled"}>
        <strong>${esc(s.start_time)}–${esc(s.end_time)} · ${esc(s.title)}</strong>
        <span>${esc(s.centre)}${s.theme ? " · " + esc(s.theme) : ""}${s.status === "cancelled" ? " · CANCELLED" : ""}</span>
        <span class="regcard__nums">${s.bookings} booked · ${s.present} in · ${s.signed_out} out · ${s.absent} absent</span>
        ${s.allowed ? "" : `<span class="fhint">Not available to session staff yet</span>`}</button>`).join("")}</div>`
        : `<p class="empty">No sessions on ${esc(day(st.date))}.</p>`}`;
    const shift = (n) => { const x = new Date(st.date + "T12:00:00"); x.setDate(x.getDate() + n); st.date = x.toISOString().slice(0, 10); this.render(root); };
    $$("[data-shift]", root).forEach(b => b.onclick = () => shift(+b.dataset.shift));
    $("#regDate", root).onchange = (e) => { st.date = e.target.value || isoToday(); this.render(root); };
    const rc = $("#regCentre", root); if (rc) rc.onchange = () => { st.centre = rc.value; this.render(root); };
    $$("[data-sid]", root).forEach(b => b.onclick = () => { st.open = +b.dataset.sid; this.render(root); });
    const he = $("#hafExport", root);
    if (he) he.onclick = () => modal("HAF export for BCP Council", `<div class="frow"><div class="fgroup"><label>From</label><input type="date" name="from" required></div>
      <div class="fgroup"><label>To</label><input type="date" name="to" required></div></div><p class="fhint">A spreadsheet of HAF children and the days they attended. The download is recorded in the audit log.</p>`,
      async (f) => { location.href = "/api/staff/reports/haf.csv?" + qs({ from: f.from.value, to: f.to.value }); return true; }, "Download");
  },
});

async function openRegister(root, sid, back) {
  let d;
  try { d = await api("/api/staff/registers/session/" + sid); }
  catch (x) { toast(x.message, true); return back(); }
  const s = d.session;
  const draw = () => {
    root.innerHTML = `<p><button class="abtn abtn--ghost abtn--sm" id="regBack">← All registers</button></p>
      <h1>${esc(s.title)}</h1>
      <p class="sub">${esc(day(s.date))} · ${esc(s.start_time)}–${esc(s.end_time)}${s.theme ? " · " + esc(s.theme) : ""} · ${esc(s.centre)}
        · ${s.bookings} booked${d.pending ? ` · ${d.pending} pending` : ""}
        · <a href="/admin/registers/${s.id}/print" target="_blank" rel="noopener">Print</a></p>
      <div class="statgrid"><div class="stat"><strong>${d.rows.filter(r => r.status === "expected").length}</strong><span>expected</span></div>
        <div class="stat"><strong>${d.rows.filter(r => r.status === "present" && !r.signed_out_at).length}</strong><span>here now</span></div>
        <div class="stat"><strong>${d.rows.filter(r => r.signed_out_at).length}</strong><span>gone home</span></div>
        <div class="stat"><strong>${d.rows.filter(r => r.status.startsWith("absent")).length}</strong><span>absent</span></div></div>
      <div class="reglist">${d.rows.map((r, i) => rowHtml(r, i)).join("") || `<p class="empty">Nobody booked.</p>`}</div>`;
    $("#regBack", root).onclick = back;
    $$("[data-act]", root).forEach(b => b.onclick = () => act(d.rows[+b.dataset.i], b.dataset.act));
  };
  const rowHtml = (r, i) => {
    const p = r.person;
    const name = p ? `${esc(p.first_name)} ${esc(p.last_name)} <span class="fhint">(${p.age})</span>`
      : `${esc(r.party.contact.name)} <span class="fhint">group: ${r.party.adults} adult(s), ${r.party.children} child(ren)</span>`;
    const flags = p ? Object.entries(p.flags).filter(([, v]) => v).map(([k]) => chip(FLAG_TEXT[k], k === "anaphylaxis" ? "bad" : "warn")).join(" ") : "";
    const extra = p ? [p.collection_alert ? chip("COLLECTION ALERT", "bad") : "", PHOTO[p.photo] ? chip(...PHOTO[p.photo]) : "",
      p.go_home_alone ? chip("May go home alone", "info") : "", p.support_plan ? chip("Support plan", "info") : "", r.profile_incomplete || p.level === "none" ? chip("Details incomplete", "warn") : "",
      p.haf && p.haf !== "unknown" && r.funding === "haf" ? chip("HAF", "info") : "", r.to_discuss.length ? chip("Incident to discuss", "bad") : ""].join(" ") : "";
    const st = STATUS[r.status] || [r.status, "muted"];
    const inOut = r.signed_in_at ? `in ${hhmm(r.signed_in_at)}${r.late ? " (late)" : ""}` + (r.signed_out_at ? ` · out ${hhmm(r.signed_out_at)}${r.collected_by ? " with " + esc(r.collected_by) : ""}` : "") : "";
    const btns = [];
    if (can("registers.mark")) {
      if (!r.signed_in_at && !r.status.startsWith("absent")) btns.push(["in", "Sign in", "abtn--primary"], ["absent", "Absent"]);
      else if (r.signed_in_at && !r.signed_out_at) btns.push(["out", "Sign out…", "abtn--primary"]);
      if (r.signed_in_at || r.status === "absent") btns.push(["undo", "Undo"]);
    }
    if (can("incidents.log")) btns.push(["incident", "Log incident"]);
    btns.push(["details", "Details"]);
    return `<div class="regrow${r.signed_out_at ? " regrow--done" : ""}"><div class="regrow__main">
      <div><strong>${name}</strong> ${chip(st[0], st[1])} <span class="fhint">${inOut}</span><br>${flags} ${extra}</div>
      <div class="regrow__btns">${btns.map(([k, l, c]) => `<button class="abtn abtn--sm ${c || "abtn--ghost"}" data-act="${k}" data-i="${i}">${l}</button>`).join("")}</div></div>
      ${p && p.health && (p.health.allergies || p.health.medical_conditions) ? `<p class="regrow__health">${p.health.allergies ? `<strong>Allergies:</strong> ${esc(p.health.allergies)}${p.health.adrenaline_pen ? " (adrenaline pen)" : ""} ` : ""}${p.health.medical_conditions ? `<strong>Medical:</strong> ${esc(p.health.medical_conditions)}` : ""}</p>` : ""}
    </div>`;
  };
  const refresh = async () => { d = await api("/api/staff/registers/session/" + sid); draw(); };
  const act = async (r, k) => {
    const url = "/api/staff/attendance/" + r.booking_id;
    try {
      if (k === "in") { await post(url, { action: "in" }); }
      else if (k === "absent") { await post(url, { action: "absent" }); }
      else if (k === "undo") { await post(url, { action: "undo" }); }
      else if (k === "out") { if (!await signOut(r, url)) return; }
      else if (k === "incident") { if (!await logIncident({ session_id: s.id, people: [{ booking_id: r.booking_id, role: "injured" }], label: r.person ? r.person.first_name : r.party.contact.name })) return; }
      else if (k === "details") { return details(r); }
      await refresh();
    } catch (x) { toast(x.message, true); }
  };
  const signOut = (r, url) => {
    const p = r.person;
    const methods = Object.entries(d.release_methods).filter(([k]) =>
      (k !== "password" || (p && p.has_collection_password)) && (k !== "went_home_alone" || (p && p.go_home_alone)) &&
      (k !== "parent_stayed" || s.parent_must_stay || !p));
    const who = p ? [`${esc(p.parent.name)} (parent)`, ...p.collectors.map(c => `${esc(c.full_name)} (${esc(c.relationship)})`)] : [];
    return modal(`Sign out ${p ? p.first_name : r.party.contact.name}`, `
      ${p && p.collection_alert ? `<p class="dlg__warn"><strong>Collection alert:</strong> ${esc(p.collection_alert)}</p>` : ""}
      ${who.length ? `<p class="fhint">Can collect: ${who.join(", ")}</p>` : ""}
      ${r.to_discuss.length ? `<p class="dlg__warn">There's an incident to talk through with the adult collecting (${r.to_discuss.map(x => esc(x.kind)).join(", ")}).</p>
        <label class="fcheck"><input type="checkbox" name="discussed"> We've talked it through</label>` : ""}
      <div class="fgroup"><label>How are they being collected?</label><select name="method">${methods.map(([k, l]) => `<option value="${k}">${esc(l)}</option>`).join("")}</select></div>
      <div class="fgroup" data-pw><label>Collection password (type what they say)</label><input type="password" name="password" autocomplete="off"></div>
      <div class="frow"><div class="fgroup"><label>Collected by</label><input type="text" name="name"></div>
      <div class="fgroup"><label>Relationship</label><input type="text" name="rel"></div></div>`,
      (f) => post(url, { action: "out", method: f.method.value, password: f.password.value, collected_by_name: f.name.value,
        collected_by_relationship: f.rel.value, incident_discussed: f.discussed ? f.discussed.checked : false }), "Sign out");
  };
  const details = (r) => {
    const people = r.person ? [r.person] : r.party.named;
    const h = (p) => p.health ? Object.entries({ Allergies: p.health.allergies, "Anaphylaxis": p.health.anaphylaxis ? "Yes" + (p.health.adrenaline_pen ? " — carries an adrenaline pen" : "") : "",
      Medical: p.health.medical_conditions, Medication: p.health.medication, Diet: p.health.dietary, SEND: p.health.send_needs,
      SEMH: p.health.semh_needs, "Religious / cultural": p.health.religious_requirements, Access: p.health.access_needs })
      .filter(([, v]) => v).map(([k, v]) => `<tr><th>${esc(k)}</th><td>${esc(v)}</td></tr>`).join("") : "";
    modal(r.person ? `${r.person.first_name} ${r.person.last_name}` : r.party.contact.name, people.map(p => `
      ${r.person ? "" : `<h3>${esc(p.first_name)} ${esc(p.last_name)}</h3>`}
      <table class="table"><tbody>${h(p) || `<tr><td>No health needs recorded.</td></tr>`}
      ${p.support_plan ? `<tr><th>Support plan</th><td style="white-space:pre-line">${esc(p.support_plan)}</td></tr>` : ""}
      <tr><th>First aid / plasters</th><td>${esc(p.first_aid || "?")} / ${esc(p.plasters || "?")}</td></tr>
      <tr><th>Parent</th><td>${esc(p.parent.name)} ${esc(p.parent.mobile || "")}</td></tr>
      ${p.contacts.map(c => `<tr><th>${esc(c.relationship)}</th><td>${esc(c.full_name)} ${esc(c.phone)}${c.can_collect ? " · can collect" : ""}</td></tr>`).join("")}
      </tbody></table>`).join("") + (r.party ? `<p>Contact: ${esc(r.party.contact.name)} ${esc(r.party.contact.mobile || "")}</p>` : ""),
      async () => true, "Close");
  };
  draw();
}

/* ---------------- incidents ---------------- */
async function logIncident(pre) {
  const meta = { kinds: { injury: "Injury / accident", illness: "Illness", behaviour: "Behaviour", safeguarding: "Safeguarding concern",
    near_miss: "Near miss", other: "Other" }, notify: { now: "Tell the parent now (email)", at_collection: "Talk it through at collection",
    not_notified: "Don't tell the parent (give a reason)" } };
  const now = new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  let marks = () => [];
  const done = modal("Log an incident" + (pre && pre.label ? " — " + pre.label : ""), `
    <div class="frow"><div class="fgroup"><label>What kind?</label><select name="kind">${Object.entries(meta.kinds).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("")}</select></div>
      <div class="fgroup"><label>When</label><input type="datetime-local" name="when" value="${now}"></div></div>
    ${pre && pre.people ? "" : `<div class="fgroup"><label>Who was involved (name)</label><input type="text" name="person"></div>`}
    <div class="fgroup"><label>What happened</label><textarea name="description" required></textarea></div>
    <div class="fgroup"><label>What we did</label><textarea name="action"></textarea></div>
    <label class="fcheck"><input type="checkbox" name="first_aid"> First aid given</label>
    <div class="fgroup" id="bodyBox"><label>Where on the body (for injuries)</label><div id="bodyMap"></div></div>
    <div class="frow"><div class="fgroup"><label>Where it happened</label><input type="text" name="location"></div>
      <div class="fgroup"><label>Severity</label><select name="severity"><option value="minor">Minor</option><option value="moderate">Moderate</option><option value="serious">Serious</option></select></div></div>
    <div class="fgroup"><label>Telling the parent</label><select name="notify">${Object.entries(meta.notify).map(([k, v]) => `<option value="${k}"${k === "at_collection" ? " selected" : ""}>${esc(v)}</option>`).join("")}</select></div>
    <div class="fgroup"><label>Reason (if not telling them)</label><input type="text" name="reason"></div>
    <p class="fhint">Safeguarding concerns go to the Designated Safeguarding Lead only — parents aren't told automatically.</p>`,
    (f) => post("/api/staff/incidents", {
      kind: f.kind.value, occurred_at_local: f.when.value, session_id: pre && pre.session_id, description: f.description.value,
      action_taken: f.action.value, first_aid_given: f.first_aid.checked, location: f.location.value, severity: f.severity.value,
      notify_mode: f.notify.value, not_notified_reason: f.reason.value, body_map: f.kind.value === "injury" ? marks() : [],
      people: pre && pre.people ? pre.people : [{ person_name: f.person.value, role: "involved" }] }), "Save incident")
    .then(r => { if (r) toast("Incident logged" + (r.notified ? " — parent emailed" : "")); return r; });
  // the dialog is in the page now: add the body map, shown for injuries
  const dlg = [...document.querySelectorAll("dialog[open]")].pop();  // the one just opened
  if (dlg && $("#bodyMap", dlg)) {
    marks = bodymap.editor($("#bodyMap", dlg), []);
    const sync = () => { $("#bodyBox", dlg).hidden = dlg.querySelector("[name=kind]").value !== "injury"; };
    dlg.querySelector("[name=kind]").addEventListener("change", sync); sync();
  }
  return done;
}

A.addTab({
  id: "incidents", label: "Incidents", icon: "🩹", perm: "incidents.view",
  state: { status: "open" },
  async render(root) {
    const st = this.state;
    const d = await api("/api/staff/incidents?" + qs({ status: st.status }));
    root.innerHTML = `<h1>Incidents & injuries</h1>
      <p class="sub">Accidents, illness, behaviour and near misses. ${can("safeguarding.view") ? "Safeguarding concerns are shown to you as DSL." : "Safeguarding concerns are only visible to the DSL."}</p>
      <div class="toolbar"><div class="segtabs">${[["open", "Open"], ["closed", "Closed"], ["", "All"]].map(([k, l]) => `<button class="abtn abtn--sm ${k === st.status ? "abtn--honey" : "abtn--ghost"}" data-st="${k}">${l}</button>`).join("")}</div>
        ${can("incidents.log") ? `<button class="abtn abtn--primary" id="newInc">＋ Log an incident</button>` : ""}</div>
      ${table([
        { label: "When", get: i => esc(when(i.occurred_at)) },
        { label: "What", get: i => `${chip(i.kind_text, i.restricted ? "bad" : i.severity === "serious" ? "bad" : i.severity === "moderate" ? "warn" : "muted")}` },
        { label: "Who", get: i => i.people.map(p => esc(p.name)).join(", ") },
        { label: "Parent", get: i => i.restricted ? "DSL decides" : i.notify_mode === "not_notified" ? "Not told" : i.parent_notified_at || i.discussed_at ? "Told" + (i.people.some(p => p.acknowledged_at) ? " · read ✓" : "") : "At collection" },
        { label: "", get: i => `<button class="abtn abtn--ghost abtn--sm" data-open="${i.id}">Open</button>` },
      ], d.incidents, { empty: "No incidents." })}`;
    $$("[data-st]", root).forEach(b => b.onclick = () => { st.status = b.dataset.st; this.render(root); });
    const ni = $("#newInc", root); if (ni) ni.onclick = async () => { if (await logIncident(null)) this.render(root); };
    $$("[data-open]", root).forEach(b => b.onclick = async () => {
      const { incident: i } = await api("/api/staff/incidents/" + b.dataset.open);
      const manage = can("incidents.manage");
      const r = await modal(`${i.kind_text} · ${when(i.occurred_at)}`, `<table class="table"><tbody>
        <tr><th>Who</th><td>${i.people.map(p => esc(p.name) + ` (${esc(p.role)})`).join(", ")}</td></tr>
        <tr><th>What happened</th><td>${esc(i.description)}</td></tr>
        <tr><th>What we did</th><td>${esc(i.action_taken || "—")}${i.first_aid_given ? " · first aid given" : ""}</td></tr>
        <tr><th>Where</th><td>${esc(i.location || "—")}</td></tr>
        ${i.body_map && i.body_map.length ? `<tr><th>Body map</th><td>${bodymap.picture(i.body_map)}</td></tr>` : ""}
        <tr><th>Parent</th><td>${esc({ now: "Emailed", at_collection: "At collection", not_notified: "Not told: " + (i.not_notified_reason || "") }[i.notify_mode])}${i.discussed_at ? " · discussed " + esc(when(i.discussed_at)) : ""}</td></tr>
        <tr><th>Logged by</th><td>${esc(i.created_by || "")} · ${esc(when(i.created_at))}</td></tr>
        <tr><th>Kept until</th><td>${esc(i.retain_until || "—")}</td></tr></tbody></table>
        ${manage ? `<div class="fgroup"><label>Follow-up notes</label><textarea name="follow_up">${esc(i.follow_up || "")}</textarea></div>
          <label class="fcheck"><input type="checkbox" name="riddor"${i.riddor_reportable ? " checked" : ""}> Reportable under RIDDOR</label>
          ${!i.restricted && !i.parent_notified_at ? `<label class="fcheck"><input type="checkbox" name="notify"> Email the parent now that there's a note</label>` : ""}
          <label class="fcheck"><input type="checkbox" name="close"${i.status === "closed" ? " checked" : ""}> Closed</label>` : ""}`,
        async (f) => manage ? post(`/api/staff/incidents/${i.id}/update`, { follow_up: f.follow_up.value, riddor_reportable: f.riddor.checked,
          notify_now: f.notify ? f.notify.checked : false, status: f.close.checked ? "closed" : "open" }) : true, manage ? "Save" : "Close");
      if (r && manage) { toast("Saved"); this.render(root); }
    });
  },
});

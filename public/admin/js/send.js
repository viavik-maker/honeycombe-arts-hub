/* SEND support requests: the SEND lead's list, each request, notes and the agreed plan. */
import { $, $$, api, chip, esc, modal, post, table, toast, when } from "./ui.js";

const A = window.HAHAdmin;
const KIND = { submitted: "warn", contacted: "info", visit_booked: "info", plan_agreed: "ok", closed: "muted" };
const kb = (n) => n > 1048576 ? (n / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(n / 1024)) + " KB";

A.addTab({
  id: "send", label: "SEND support", icon: "🧩", perm: "send.view",
  state: { view: "open", open: null },
  async render(root) {
    const st = this.state;
    if (this.options) { Object.assign(st, this.options); this.options = null; }
    if (st.open) return openRequest(root, st.open, () => { st.open = null; this.render(root); });
    const d = await api("/api/staff/send?view=" + st.view);
    root.innerHTML = `<h1>SEND support</h1>
      <p class="sub">Families who've asked to plan their child's support before booking. Opening a request is logged.</p>
      <div class="toolbar"><div class="segtabs">${[["open", "To do"], ["agreed", "Plan agreed"], ["closed", "Closed"], ["all", "All"]].map(([k, l]) =>
        `<button class="abtn abtn--sm ${k === st.view ? "abtn--honey" : "abtn--ghost"}" data-v="${k}">${l}</button>`).join("")}</div></div>
      ${table([
        { label: "Child", get: r => `<a href="#" data-open="${esc(r.ref)}"><strong>${esc(r.child)}</strong></a> <span class="fhint">(${r.age})</span><br><span class="fhint">${esc(r.family)}</span>` },
        { label: "Needs", get: r => r.needs.map(n => chip(n, "info")).join(" ") },
        { label: "Contact", get: r => esc(r.contact_method) + `<br><span class="fhint">EHCP: ${esc(r.ehcp)}${r.files ? ` · ${r.files} file(s)` : ""}</span>` },
        { label: "Status", get: r => chip(r.status_text, KIND[r.status]) + `<br><span class="fhint">${esc(when(r.created_at))}</span>` },
      ], d.requests, { empty: "Nothing here." })}`;
    $$("[data-v]", root).forEach(b => b.onclick = () => { st.view = b.dataset.v; this.render(root); });
    $$("[data-open]", root).forEach(a => a.onclick = (e) => { e.preventDefault(); st.open = a.dataset.open; this.render(root); });
  },
});

async function openRequest(root, ref, back) {
  const { request: r, options: o } = await api("/api/staff/send/" + encodeURIComponent(ref));
  const row = (label, value) => value ? `<tr><th>${esc(label)}</th><td style="white-space:pre-line">${esc(value)}</td></tr>` : "";
  root.innerHTML = `<p><button class="abtn abtn--ghost abtn--sm" id="bk">← All requests</button></p>
    <h1>${esc(r.child.first_name)} ${esc(r.child.last_name)} <span class="fhint">(${r.child.age})</span> ${chip(r.status_text, KIND[r.status])}</h1>
    <p class="sub">${esc(r.family.name)} · ${esc(r.family.email || "")} · ${esc(r.family.mobile || "")} · sent ${esc(when(r.created_at))}${r.assigned_to ? " · with " + esc(r.assigned_to) : ""}
      · <a href="#" id="childLink">child's record</a></p>
    <div class="quicklinks">
      <button class="abtn abtn--ghost abtn--sm" data-act="mine">Assign to me</button>
      ${r.status === "submitted" ? `<button class="abtn abtn--ghost abtn--sm" data-act="contacted">Mark as contacted</button>` : ""}
      ${["submitted", "contacted"].includes(r.status) ? `<button class="abtn abtn--ghost abtn--sm" data-act="visit">Visit booked…</button>` : ""}
      ${r.status !== "plan_agreed" && r.status !== "closed" ? `<button class="abtn abtn--primary abtn--sm" data-act="plan">Support plan agreed…</button>` : ""}
      ${r.status === "plan_agreed" ? `<button class="abtn abtn--ghost abtn--sm" data-act="plan">Edit plan summary…</button>` : ""}
      ${r.status !== "closed" ? `<button class="abtn abtn--ghost abtn--sm" data-act="close">Close</button>` : ""}
    </div>
    ${r.plan_summary ? `<div class="acard"><h2>Agreed plan (shown on registers)</h2><p style="white-space:pre-line">${esc(r.plan_summary)}</p>
      <p class="fhint">Agreed ${esc(when(r.plan_agreed_at))}</p></div>` : ""}
    <div class="acard"><h2>What the family told us</h2><table class="table"><tbody>
      ${row("How to talk", o.contact[r.contact_method] + " · " + o.best_time[r.best_time])}
      ${row("Visit", r.visit_at ? when(r.visit_at) : "")}
      ${row("Needs", r.needs.map(n => o.needs[n]).join(", ") + (r.needs_other ? " — " + r.needs_other : ""))}
      ${row("Interested in", r.interests.map(n => o.interests[n]).join(", "))}
      ${row("What helps them have a good day", r.good_day)}
      ${row("What can upset or overwhelm them", r.overwhelm)}
      ${row("How they communicate", r.communication)}
      ${row("Support at school or home", r.current_support)}
      ${row("Needs 1:1 support", o.one_to_one[r.one_to_one])}
      ${row("EHCP", o.ehcp[r.ehcp])}
      ${row("May contact school / professionals", r.consent_professionals ? "Yes" : "No")}</tbody></table></div>
    <div class="acard"><h2>Documents</h2>${r.files.length ? `<ul>${r.files.map(f => `<li><a href="/api/staff/send/files/${esc(f.ref)}">${esc(f.filename)}</a>
      <span class="fhint">${f.kind === "ehcp" ? "EHCP" : "Other plan"} · ${kb(f.size)}</span></li>`).join("")}</ul>
      <p class="fhint">Downloads are logged. Don't save copies to shared drives or email them on.</p>` : "<p>None uploaded.</p>"}</div>
    <div class="acard"><h2>Notes</h2>${r.notes.length ? r.notes.map(n => `<p><strong>${esc(n.staff || "")}</strong> <span class="fhint">${esc(when(n.created_at))}</span><br>
      <span style="white-space:pre-line">${esc(n.body)}</span></p>`).join("") : "<p class=\"fhint\">No notes yet.</p>"}
      <div class="fgroup"><label for="sendNote">Add a note (e.g. a call summary)</label><textarea id="sendNote"></textarea></div>
      <button class="abtn abtn--primary abtn--sm" id="addNote">Add note</button></div>`;
  const reload = () => openRequest(root, ref, back);
  const up = async (body) => { await post(`/api/staff/send/${encodeURIComponent(ref)}/update`, body); toast("Saved"); reload(); };
  $("#bk", root).onclick = back;
  $("#childLink", root).onclick = (e) => { e.preventDefault(); A.openTab("people", { open: { type: "child", ref: r.child.ref } }); };
  $("#addNote", root).onclick = () => { const v = $("#sendNote", root).value.trim(); if (v) up({ note: v }).catch(x => toast(x.message, true)); };
  $$("[data-act]", root).forEach(b => b.onclick = async () => {
    try {
      const a = b.dataset.act;
      if (a === "mine") return up({ assign_to_me: true });
      if (a === "contacted") return up({ status: "contacted" });
      if (a === "close") return up({ status: "closed" });
      if (a === "visit") {
        await modal("Visit booked", `<div class="fgroup"><label>When</label><input type="datetime-local" name="at" required></div>`,
          (f) => up({ status: "visit_booked", visit_at: f.at.value }), "Save");
      } else if (a === "plan") {
        await modal("Support plan agreed", `<p class="fhint">A short summary for the staff running sessions — it shows on registers.
          Keep it practical (what helps, what to avoid, who to call) and leave out anything they don't need.</p>
          <div class="fgroup"><label>Summary</label><textarea name="summary" class="tall">${esc(r.plan_summary || "")}</textarea></div>`,
          (f) => up(r.status === "plan_agreed" ? { plan_summary: f.summary.value } : { status: "plan_agreed", plan_summary: f.summary.value }), "Save");
      }
    } catch (x) { toast(x.message, true); }
  });
}

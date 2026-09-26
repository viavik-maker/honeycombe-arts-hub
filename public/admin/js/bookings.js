/* Bookings: find, approve/decline, cancel (with credit or refund), move,
   and the waiting list for a session. */
import { $, $$, api, can, chip, confirmBox, day, esc, modal, money, post, qs, table, toast, when } from "./ui.js";

const A = window.HAHAdmin;
const QUICK = [["upcoming", "Upcoming"], ["approval", "Under approval"], ["waitlist", "Waiting list"], ["unpaid", "Unpaid"],
  ["cancelled", "Cancelled"], ["", "All"]];
const CHIP = { confirmed: "ok", pending_approval: "warn", pending_payment: "warn", waitlisted: "muted", offered: "info",
  cancelled: "bad", expired: "muted", pending_confirmation: "warn" };
const REASON = { activity: "needs approval", haf_claim: "HAF claim to check", voucher: "paying by vouchers/TFC" };

A.addTab({
  id: "bookings", label: "Bookings", icon: "📋", perm: "bookings.view",
  state: { quick: "approval", q: "", session: "", activity: "", page: 1 },
  async render(root) {
    const st = this.state;
    if (this.options) { Object.assign(st, { quick: "", q: "", session: "", activity: "", page: 1 }, this.options); this.options = null; }
    const d = await api("/api/staff/bookings?" + qs({ quick: st.quick, q: st.q, session: st.session, activity: st.activity, page: st.page }));
    const hafRows = d.bookings.filter(b => b.status === "pending_approval" && b.approval_reason === "haf_claim");
    root.innerHTML = `<h1>Bookings</h1>
      <p class="sub">${d.total} booking${d.total === 1 ? "" : "s"}${st.session ? " on one session · <a href='#' id='clearSession'>show all</a>" : ""}</p>
      <div class="toolbar"><div class="segtabs">${QUICK.map(([k, l]) => `<button class="abtn abtn--sm ${k === st.quick ? "abtn--honey" : "abtn--ghost"}" data-quick="${k}">${l}</button>`).join("")}</div>
        <input type="search" id="bkQ" placeholder="Name, email or booking ref" value="${esc(st.q)}"></div>
      ${hafRows.length && can("bookings.manage") ? `<p><button class="abtn abtn--sm abtn--primary" id="hafBulk">Verify HAF for the ${hafRows.length} ticked…</button> <span class="fhint">Approves them and marks those children as HAF-eligible.</span></p>` : ""}
      ${table([
        { label: "", get: b => b.status === "pending_approval" && b.approval_reason === "haf_claim" ? `<input type="checkbox" class="hafpick" value="${b.id}" checked aria-label="Select">` : "" },
        { label: "Session", get: b => `<strong>${esc(b.activity)}</strong><br><span class="fhint">${esc(day(b.date))} ${esc(b.start_time)}–${esc(b.end_time)}</span>` },
        { label: "Who", get: b => `${b.who ? esc(b.who.first_name + " " + (b.who.last_name || "")) + (b.who.age != null ? ` <span class="fhint">(${b.who.age})</span>` : "") : esc(b.places + " place(s)")}
            <br><span class="fhint">${esc(b.family ? b.family.name : "")}</span>` },
        { label: "Status", get: b => chip(b.status_text, CHIP[b.status]) + (b.approval_reason && b.status === "pending_approval" ? `<br><span class="fhint">${esc(REASON[b.approval_reason])}</span>` : "")
            + (b.position ? `<br><span class="fhint">#${b.position}</span>` : "") + (b.profile_incomplete ? `<br>${chip("profile incomplete", "warn")}` : "") },
        { label: "Money", cls: "nowrap", get: b => b.funding === "haf" ? "HAF" : b.price_pence ? esc(money(b.price_pence)) + (b.invoice ? `<br><span class="fhint">${b.invoice.balance_pence > 0 ? "owes " + esc(money(b.invoice.balance_pence)) : "paid"}</span>` : "") : "Free" },
        { label: "", cls: "nowrap", get: b => `<button class="abtn abtn--ghost abtn--sm" data-open="${b.id}">Open</button>` },
      ], d.bookings, { empty: "No bookings here." })}
      ${d.pages > 1 ? `<p>${st.page > 1 ? `<button class="abtn abtn--ghost abtn--sm" data-page="${st.page - 1}">← Newer</button>` : ""} Page ${st.page} of ${d.pages}
        ${st.page < d.pages ? `<button class="abtn abtn--ghost abtn--sm" data-page="${st.page + 1}">Older →</button>` : ""}</p>` : ""}`;
    $$("[data-quick]", root).forEach(b => b.onclick = () => { st.quick = b.dataset.quick; st.page = 1; this.render(root); });
    $$("[data-page]", root).forEach(b => b.onclick = () => { st.page = +b.dataset.page; this.render(root); });
    const q = $("#bkQ", root);
    q.onkeydown = (e) => { if (e.key === "Enter") { st.q = q.value; st.page = 1; this.render(root); } };
    const cs = $("#clearSession", root);
    if (cs) cs.onclick = (e) => { e.preventDefault(); st.session = ""; this.render(root); };
    const hb = $("#hafBulk", root);
    if (hb) hb.onclick = async () => {
      const ids = $$(".hafpick:checked", root).map(i => +i.value);
      if (!ids.length || !await confirmBox(`Verify HAF eligibility and approve ${ids.length} booking(s)?`, "Verify & approve")) return;
      try { const r = await post("/api/staff/bookings/approve-haf", { booking_ids: ids }); toast(`${r.approved} approved`); this.render(root); }
      catch (x) { toast(x.message, true); }
    };
    $$("[data-open]", root).forEach(b => b.onclick = () => openBooking(+b.dataset.open, () => this.render(root)));
    if (st.openId) { const id = st.openId; st.openId = null; openBooking(id, () => this.render(root)); }
  },
});

async function openBooking(id, refresh) {
  const { booking: b } = await api("/api/staff/bookings/" + id);
  const manage = can("bookings.manage");
  const acts = [];
  if (manage && b.status === "pending_approval") acts.push(["approve", "Approve", "abtn--primary"], ["decline", "Decline", "abtn--danger"]);
  if (manage && b.status === "waitlisted") acts.push(["offer", "Offer a place"], ["priority", "Priority…"], ["decline", "Remove from list", "abtn--danger"]);
  if (manage && ["confirmed", "pending_approval", "offered"].includes(b.status)) acts.push(["cancel", "Cancel…", "abtn--danger"]);
  if (manage && ["confirmed", "pending_approval"].includes(b.status)) acts.push(["move", "Move…"]);
  if (manage && b.family && b.family.email) acts.push(["resend", "Resend email"]);
  const inv = b.invoice_detail;
  const d = document.createElement("dialog");
  d.className = "dlg dlg--form dlg--wide";
  d.innerHTML = `<h2>${esc(b.activity)} · ${esc(day(b.date))}</h2>
    <p>${chip(b.status_text, CHIP[b.status])} <span class="fhint">Ref ${esc(b.ref)} · booked ${esc(when(b.created_at))} (${esc(b.created_via)})</span></p>
    <table class="table"><tbody>
      <tr><th>Who</th><td>${b.who ? esc(b.who.first_name + " " + (b.who.last_name || "")) + (b.who.age != null ? ` (${b.who.age})` : "") + (b.who.haf_status && b.who.haf_status !== "unknown" ? ` · HAF: ${esc(b.who.haf_status.replace("_", " "))}` : "") : esc(b.places + " place(s)")}</td></tr>
      <tr><th>Family</th><td>${b.family ? esc(b.family.name) + `<br><span class="fhint">${esc(b.family.email || "")} ${esc(b.family.mobile || "")}</span>` : "—"}</td></tr>
      <tr><th>Time</th><td>${esc(b.start_time)}–${esc(b.end_time)}${b.theme ? " · " + esc(b.theme) : ""}</td></tr>
      <tr><th>Money</th><td>${b.funding === "haf" ? "HAF funded" : money(b.price_pence)}${inv ? ` · <a href="/admin/invoices/${esc(inv.number)}" target="_blank" rel="noopener">${esc(inv.number)}</a> (${inv.balance_pence > 0 ? "owes " + money(inv.balance_pence) : esc(inv.status)})` : ""}</td></tr>
      ${b.cancel_reason ? `<tr><th>Cancelled</th><td>${esc(b.cancel_reason)}</td></tr>` : ""}
    </tbody></table>
    <div class="fgroup"><label for="bkNotes">Staff notes</label><textarea id="bkNotes"${manage ? "" : " disabled"}>${esc(b.notes || "")}</textarea></div>
    <details><summary>History (${b.history.length})</summary><ul>${b.history.map(h => `<li>${esc(when(h.at))} — ${esc(h.action)} (${esc(h.by)})</li>`).join("")}</ul></details>
    <div class="dlg__btns">${acts.map(([k, l, c]) => `<button type="button" class="abtn abtn--sm ${c || "abtn--ghost"}" data-act="${k}">${l}</button>`).join("")}
      ${manage ? `<button type="button" class="abtn abtn--sm abtn--ghost" data-act="notes">Save notes</button>` : ""}
      <button type="button" class="abtn abtn--sm abtn--ghost" data-act="close">Close</button></div>`;
  document.body.appendChild(d);
  const close = () => { d.close(); d.remove(); };
  d.addEventListener("cancel", () => d.remove());
  d.onclick = async (e) => {
    const k = e.target.closest("[data-act]") && e.target.closest("[data-act]").dataset.act;
    if (!k) return;
    try {
      if (k === "close") return close();
      if (k === "notes") { await post(`/api/staff/bookings/${id}/notes`, { notes: $("#bkNotes", d).value }); toast("Notes saved"); return; }
      close();
      if (k === "approve") { await post(`/api/staff/bookings/${id}/approve`, {}); toast("Approved — the family has been emailed"); }
      else if (k === "decline") {
        const r = await modal("Decline booking", `<div class="fgroup"><label>Reason (sent to the family)</label><input type="text" name="reason"></div>
          ${b.approval_reason === "haf_claim" ? `<label class="fcheck"><input type="checkbox" name="haf"> Not eligible for HAF (stops future HAF requests)</label>` : ""}`,
          (f) => post(`/api/staff/bookings/${id}/decline`, { reason: f.reason.value, haf_not_eligible: f.haf ? f.haf.checked : false }), "Decline");
        if (!r) return;
        toast("Declined");
      } else if (k === "cancel") {
        const t = b.cancel_terms;
        const r = await modal("Cancel booking", `<div class="fgroup"><label>Reason (sent to the family)</label><input type="text" name="reason" required></div>
          ${b.price_pence && inv ? `<div class="fgroup"><label>Money</label><select name="outcome">
            <option value="credit">Account credit</option><option value="refund_card">Refund to card (online payments)</option>
            <option value="refund_offline">Refunded another way (cash / bank)</option><option value="none">Nothing back (only unpaid amount written off)</option></select>
            <p class="fhint">Family policy would give: ${esc(t.message || "—")}</p></div>` : ""}
          <label class="fcheck"><input type="checkbox" name="notify" checked> Email the family</label>`,
          (f) => post(`/api/staff/bookings/${id}/cancel`, { reason: f.reason.value, outcome: f.outcome ? f.outcome.value : "none", notify: f.notify.checked }), "Cancel booking");
        if (!r) return;
        toast(r.refunded_pence ? `Cancelled — ${money(r.refunded_pence)} returned` : "Cancelled");
      } else if (k === "move") {
        const act = await api("/api/staff/activities/" + b.activity_id);
        const options = act.activity.sessions.filter(s => s.status === "scheduled" && !s.past && s.id !== b.session_id);
        const r = await modal("Move to another session", `<div class="fgroup"><label>Session</label><select name="sid">${options.map(s =>
          `<option value="${s.id}">${esc(day(s.date))} ${esc(s.start_time)} — ${s.capacity - s.taken} free</option>`).join("")}</select></div>
          ${can("bookings.override") ? `<label class="fcheck"><input type="checkbox" name="ov"> Allow even if full</label>` : ""}`,
          (f) => post(`/api/staff/bookings/${id}/move`, { session_id: +f.sid.value, override: f.ov ? f.ov.checked : false }), "Move");
        if (!r) return;
        toast("Moved");
      } else if (k === "offer") { await post(`/api/staff/bookings/${id}/offer`, {}); toast("Offer sent by email and text"); }
      else if (k === "priority") {
        const r = await modal("Waiting-list priority", `<div class="fgroup"><label>Priority (higher goes first; 0 = normal)</label><input type="number" name="p" min="-10" max="10" value="${b.waitlist_priority}"></div>`,
          (f) => post(`/api/staff/bookings/${id}/priority`, { priority: +f.p.value }), "Save");
        if (!r) return;
      } else if (k === "resend") { await post(`/api/staff/bookings/${id}/resend`, {}); toast("Email queued"); }
      refresh();
    } catch (x) { toast(x.message, true); }
  };
  d.showModal();
}

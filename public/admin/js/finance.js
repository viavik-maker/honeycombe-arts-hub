/* Finance (invoices, delayed payments, takings, refunds, exports) and the In-tray. */
import { $, $$, api, can, chip, confirmBox, day, esc, modal, money, post, qs, table, toast, when, ukNowLocal, ukToday } from "./ui.js";

const A = window.HAHAdmin;
const isoToday = ukToday;

function paymentForm(meta, balance) {
  return `<div class="frow"><div class="fgroup"><label>Paid by</label><select name="method">${Object.entries(meta.methods)
      .filter(([k]) => !["stripe_card", "account_credit"].includes(k)).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("")}</select></div>
    <div class="fgroup"><label>Amount (£)</label><input type="text" name="amount" value="${(balance / 100).toFixed(2)}"></div></div>
    <div class="frow"><div class="fgroup"><label>Voucher provider</label><select name="provider"><option value="">—</option>${meta.voucher_providers.map(p => `<option>${esc(p)}</option>`).join("")}</select></div>
    <div class="fgroup"><label>Received on</label><input type="date" name="on" value="${isoToday()}"></div></div>
    <div class="fgroup"><label>Reference (never a card number)</label><input type="text" name="reference"></div>
    <label class="fcheck"><input type="checkbox" name="receipt" checked> Email a receipt</label>`;
}
const paymentBody = (f, number) => ({ invoice_number: number, method: f.method.value, amount_pence: Math.round(parseFloat(f.amount.value) * 100),
  voucher_provider: f.provider.value, received_on: f.on.value, reference: f.reference.value, send_receipt: f.receipt.checked });

async function payoutDetail(id) {
  let d;
  try { d = await api("/api/staff/finance/stripe/payouts/" + encodeURIComponent(id)); } catch (x) { return toast(x.message, true); }
  const t = d.totals;
  await modal(`Payout ${money(d.payout.amount_pence)}${d.payout.arrival_date ? " · " + day(d.payout.arrival_date) : ""}`,
    `<p>${d.adds_up ? chip("Adds up", "ok") : chip("Doesn't add up — check in Stripe", "bad")} ${t.unmatched ? chip(t.unmatched + " not matched", "warn") : chip("All matched", "ok")}</p>
     <p>Taken ${esc(money(t.gross))} − Stripe fees ${esc(money(t.fees))} = <strong>${esc(money(t.net))}</strong></p>
     ${table([{ label: "Date", get: r => esc(r.date || "") }, { label: "What", get: r => esc(r.type) + (r.ours ? `<br><span class="fhint">${esc(r.ours.ref)} · ${esc(r.ours.name)} ${esc(r.ours.invoices)}</span>` : r.matched ? "" : `<br>${chip("not found here", "warn")}`) },
       { label: "Gross", cls: "nowrap", get: r => esc(money(r.amount_pence)) }, { label: "Fee", cls: "nowrap", get: r => esc(money(r.fee_pence)) },
       { label: "Net", cls: "nowrap", get: r => esc(money(r.net_pence)) }], d.rows, { empty: "Nothing in this payout." })}
     <p><a class="abtn abtn--ghost abtn--sm" href="/api/staff/finance/stripe/payouts/${encodeURIComponent(id)}?format=csv">Download CSV</a></p>`,
    async () => true, "Close");
}

A.addTab({
  id: "finance", label: "Finance", icon: "💷", perm: "finance.view",
  state: { view: "unpaid", q: "", from: null, to: null },
  async render(root) {
    const st = this.state;
    st.from = st.from || isoToday(); st.to = st.to || isoToday();
    const [s, inv] = await Promise.all([api("/api/staff/finance/summary?" + qs({ from: st.from, to: st.to })),
      api("/api/staff/finance/invoices?" + qs({ view: st.view, q: st.q }))]);
    root.innerHTML = `<h1>Finance</h1>
      <p class="sub">Takings, invoices and delayed payments. Unpaid invoices are never cancelled automatically — follow them up here.</p>
      <div class="statgrid">
        <div class="stat"><strong>${esc(money(s.takings_total_pence))}</strong><span>taken ${st.from === st.to ? "on " + esc(day(st.from)) : "in period"}</span></div>
        <div class="stat"><strong>${esc(money(s.outstanding.pence))}</strong><span>${s.outstanding.count} unpaid invoice(s)</span></div>
        <div class="stat"><strong>${esc(money(s.overdue.pence))}</strong><span>${s.overdue.count} overdue</span></div>
        <div class="stat"><strong>${esc(money(s.credit_held_pence))}</strong><span>account credit held</span></div></div>
      <div class="acard"><h2>Takings</h2>
        <div class="toolbar"><label>From <input type="date" id="fFrom" value="${esc(st.from)}"></label><label>To <input type="date" id="fTo" value="${esc(st.to)}"></label>
          <a class="abtn abtn--ghost abtn--sm" href="/api/staff/finance/payments.csv?${qs({ from: st.from, to: st.to })}">Payments CSV</a>
          <a class="abtn abtn--ghost abtn--sm" href="/api/staff/finance/invoices.csv?${qs({ from: st.from, to: st.to })}">Invoices CSV</a>
          <a class="abtn abtn--ghost abtn--sm" href="/api/staff/finance/accounting.csv?${qs({ from: st.from, to: st.to })}" title="Invoice lines by category, credit notes, payments and refunds — for your accounts software">Accounting export</a></div>
        ${table([{ label: "Method", get: t => esc(t.method) }, { label: "Payments", get: t => t.count }, { label: "Total", get: t => esc(money(t.amount_pence)) }], s.takings, { empty: "Nothing taken in this period." })}
        ${s.refunds_pence ? `<p class="fhint">Refunded in period: ${esc(money(s.refunds_pence))}</p>` : ""}</div>
      ${s.pending_refunds.length ? `<div class="acard"><h2>Refunds to check</h2>${table([
        { label: "Created", get: r => esc(when(r.created_at)) }, { label: "Amount", get: r => esc(money(r.amount_pence)) },
        { label: "How", get: r => esc(r.method.replace("_", " ")) + (r.failure_reason ? `<br><span class="fhint">${esc(r.failure_reason)}</span>` : "") },
        { label: "Status", get: r => chip(r.status, r.status === "failed" ? "bad" : "warn") },
        { label: "", get: r => r.method !== "stripe" && can("finance.manage") ? `<button class="abtn abtn--ghost abtn--sm" data-refund="${r.id}">Mark handed back</button>` : "" }], s.pending_refunds)}</div>` : ""}
      <div class="acard"><h2>Invoices</h2>
        <div class="toolbar"><div class="segtabs">${[["unpaid", "Unpaid"], ["overdue", "Overdue"], ["paid", "Paid"], ["credited", "Credited"], ["void", "Void"], ["all", "All"]].map(([k, l]) =>
          `<button class="abtn abtn--sm ${k === st.view ? "abtn--honey" : "abtn--ghost"}" data-view="${k}">${l}</button>`).join("")}</div>
          <input type="search" id="fQ" placeholder="Number, name or email" value="${esc(st.q)}"></div>
        ${table([
          { label: "Invoice", get: i => `<a href="/admin/invoices/${esc(i.number)}" target="_blank" rel="noopener">${esc(i.number)}</a><br><span class="fhint">${esc(i.bill_to_name)}</span>` },
          { label: "Due", get: i => esc(day(i.due_date)) + (i.overdue ? " " + chip("overdue", "bad") : "") },
          { label: "Total", cls: "nowrap", get: i => esc(money(i.total_pence)) },
          { label: "To pay", cls: "nowrap", get: i => i.balance_pence > 0 ? `<strong>${esc(money(i.balance_pence))}</strong>` : chip(i.status, i.status === "paid" ? "ok" : "muted") },
          { label: "", cls: "nowrap", get: i => i.balance_pence > 0 && i.status !== "void" ? `${can("payments.record") ? `<button class="abtn abtn--primary abtn--sm" data-pay="${esc(i.number)}">Record payment</button>` : ""}
            <button class="abtn abtn--ghost abtn--sm" data-remind="${esc(i.number)}">Send reminder</button>
            ${can("finance.manage") && !i.paid_pence ? `<button class="abtn abtn--danger abtn--sm" data-void="${esc(i.number)}">Void</button>` : ""}` : "" },
        ], inv.invoices, { empty: "No invoices here." })}</div>
      <div class="acard"><h2>Aged debt</h2><p class="fhint">Who owes what, by how long it's been overdue.</p>
        <div id="agedBox"><button class="abtn abtn--ghost abtn--sm" id="agedBtn">Show aged debt</button></div></div>
      ${s.card_payments ? `<div class="acard"><h2>Stripe payouts</h2><p class="fhint">Match each payout that reaches the bank to the card payments and refunds in it (Stripe's fees are shown too).</p>
        <div id="payoutBox"><button class="abtn abtn--ghost abtn--sm" id="payoutBtn">Show recent payouts</button></div></div>` : ""}`;
    $("#agedBtn", root).onclick = async () => {
      const d = await api("/api/staff/finance/aged-debt");
      const t = d.totals;
      $("#agedBox", root).innerHTML = `<div class="statgrid">${d.buckets.map(b => `<div class="stat"><strong>${esc(money(t[b.key]))}</strong><span>${esc(b.label)}</span></div>`).join("")}</div>
        <p><a class="abtn abtn--ghost abtn--sm" href="/api/staff/finance/aged-debt.csv">Aged debt CSV</a></p>
        ${table([{ label: "Bill to", get: r => (r.account_ref ? `<a href="#" data-fam="${esc(r.account_ref)}">${esc(r.name)}</a>` : esc(r.name)) + `<br><span class="fhint">${esc(r.email || "")} ${esc(r.mobile || "")}</span>` },
          { label: "Invoices", get: r => r.invoices.map(i => `<a href="/admin/invoices/${esc(i.number)}" target="_blank" rel="noopener">${esc(i.number)}</a>${i.days_overdue ? ` <span class="fhint">(${i.days_overdue}d)</span>` : ""}`).join("<br>") },
          ...d.buckets.map(b => ({ label: b.label, cls: "nowrap", get: r => r[b.key] ? esc(money(r[b.key])) : "" })),
          { label: "Total", cls: "nowrap", get: r => `<strong>${esc(money(r.total_pence))}</strong>` }], d.rows, { empty: "Nobody owes anything. 🎉" })}`;
      $$("[data-fam]", root).forEach(a => a.onclick = (e) => { e.preventDefault(); A.openTab("people", { open: { type: "account", ref: a.dataset.fam } }); });
    };
    const pb = $("#payoutBtn", root);
    if (pb) pb.onclick = async () => {
      try {
        const d = await api("/api/staff/finance/stripe/payouts");
        $("#payoutBox", root).innerHTML = table([{ label: "Arrives", get: p => esc(p.arrival_date ? day(p.arrival_date) : "—") },
          { label: "Amount", get: p => esc(money(p.amount_pence)) }, { label: "Status", get: p => chip(p.status, p.status === "paid" ? "ok" : "muted") },
          { label: "", get: p => `<button class="abtn abtn--ghost abtn--sm" data-payout="${esc(p.id)}">Match</button>` }], d.payouts, { empty: "No payouts yet." });
        $$("[data-payout]", root).forEach(b => b.onclick = () => payoutDetail(b.dataset.payout));
      } catch (x) { toast(x.message, true); }
    };
    const re = () => this.render(root);
    $("#fFrom", root).onchange = (e) => { st.from = e.target.value; re(); };
    $("#fTo", root).onchange = (e) => { st.to = e.target.value; re(); };
    $$("[data-view]", root).forEach(b => b.onclick = () => { st.view = b.dataset.view; re(); });
    $("#fQ", root).onkeydown = (e) => { if (e.key === "Enter") { st.q = e.target.value; re(); } };
    const act = async (fn) => { try { if (await fn()) re(); } catch (x) { toast(x.message, true); } };
    $$("[data-pay]", root).forEach(b => b.onclick = () => act(async () => {
      const i = inv.invoices.find(x => x.number === b.dataset.pay);
      const r = await modal(`Record a payment — ${i.number}`, paymentForm(s, i.balance_pence), (f) => post("/api/staff/payments", paymentBody(f, i.number)), "Record");
      if (r) toast("Payment recorded"); return r;
    }));
    $$("[data-remind]", root).forEach(b => b.onclick = () => act(async () => { await post(`/api/staff/invoices/${b.dataset.remind}/remind`, {}); toast("Reminder queued"); }));
    $$("[data-void]", root).forEach(b => b.onclick = () => act(async () => modal(`Void ${b.dataset.void}`, `<p>Only for invoices raised in error with nothing paid. To cancel bookings, use Bookings → Cancel instead.</p>
      <div class="fgroup"><label>Reason</label><input type="text" name="reason" required></div>`, (f) => post(`/api/staff/invoices/${b.dataset.void}/void`, { reason: f.reason.value }), "Void")));
    $$("[data-refund]", root).forEach(b => b.onclick = () => act(async () => {
      if (!await confirmBox("Mark this refund as handed back to the family?", "Yes")) return false;
      await post(`/api/staff/refunds/${b.dataset.refund}/done`, {}); return true;
    }));
  },
});

/* ---------------- In-tray ---------------- */
function link(i) {
  if (i.entity_type === "booking") return ["Open booking", () => A.openTab("bookings", { quick: "", openId: i.entity_id })];
  if (i.entity_type === "send_intake") return ["Open request", () => A.openTab("send")];
  if (i.entity_type === "incident") return ["Open incidents", () => A.openTab("incidents")];
  if (i.entity_type === "session") return ["Waiting list", () => A.openTab("bookings", { quick: "waitlist", session: i.entity_id })];
  if (i.entity_type === "activity") return ["Open activities", () => A.openTab("activities")];
  if (i.entity_type === "contact_message") return ["Open inbox", () => $('#sideNav button[data-tab="messages"]').click()];
  if (["haf_verify"].includes(i.type)) return ["HAF approvals", () => A.openTab("bookings", { quick: "approval" })];
  if (i.type === "voucher_payment") return ["Approvals", () => A.openTab("bookings", { quick: "approval" })];
  if (i.participant_ref) return ["Open child", () => A.openTab("people", { open: { type: "child", ref: i.participant_ref } })];
  if (i.account_ref) return ["Open family", () => A.openTab("people", { open: { type: "account", ref: i.account_ref } })];
  if (["payment_problem", "late_payment", "refund_failed", "payment_dispute"].includes(i.type)) return ["Open finance", () => A.openTab("finance")];
  return null;
}

A.addTab({
  id: "intray", label: "In-tray", icon: "📬",
  state: { view: "open", type: "" },
  async render(root) {
    const st = this.state;
    const d = await api("/api/staff/intray?" + qs({ view: st.view, type: st.type }));
    const badge = $('#sideNav button[data-tab="intray"]');
    if (badge && st.view === "open" && !st.type) badge.dataset.count = d.total_open;
    root.innerHTML = `<h1>In-tray</h1>
      <p class="sub">Things waiting for someone to act. You only see items your role deals with.</p>
      <div class="toolbar"><div class="segtabs">${[["open", "Open"], ["mine", "Assigned to me"], ["snoozed", "Snoozed"], ["done", "Done"]].map(([k, l]) =>
        `<button class="abtn abtn--sm ${k === st.view ? "abtn--honey" : "abtn--ghost"}" data-view="${k}">${l}</button>`).join("")}</div>
        <select id="itType"><option value="">All types (${d.total_open} open)</option>${Object.entries(d.types).map(([k, v]) =>
          `<option value="${esc(k)}"${k === st.type ? " selected" : ""}>${esc(v)} (${d.counts[k] || 0})</option>`).join("")}</select></div>
      <div class="item-list">${d.items.map((i, n) => `<div class="item"><div style="flex:1">
          <div class="item__title">${esc(i.title)}</div>
          <div class="item__meta"><span class="pill">${esc(i.type_name)}</span> ${esc(when(i.created_at))}${i.assignee ? " · " + esc(i.assignee) : ""}${i.snooze_until ? " · until " + esc(when(i.snooze_until)) : ""}</div>
          ${i.detail ? `<div class="fhint">${esc(i.detail)}</div>` : ""}</div>
        <div class="item__actions">${link(i) ? `<button class="abtn abtn--ghost abtn--sm" data-go="${n}">${esc(link(i)[0])}</button>` : ""}
          ${i.status === "done" ? `<button class="abtn abtn--ghost abtn--sm" data-a="reopen" data-id="${i.id}">Reopen</button>` : `
          <button class="abtn abtn--ghost abtn--sm" data-a="assign" data-id="${i.id}">Take it</button>
          <button class="abtn abtn--ghost abtn--sm" data-a="snooze" data-id="${i.id}">Snooze</button>
          <button class="abtn abtn--primary abtn--sm" data-a="done" data-id="${i.id}">Done ✓</button>`}</div></div>`).join("") || `<p class="empty">Nothing here 🎉</p>`}</div>`;
    $$("[data-view]", root).forEach(b => b.onclick = () => { st.view = b.dataset.view; this.render(root); });
    $("#itType", root).onchange = (e) => { st.type = e.target.value; this.render(root); };
    $$("[data-go]", root).forEach(b => b.onclick = () => link(d.items[+b.dataset.go])[1]());
    $$("[data-a]", root).forEach(b => b.onclick = async () => {
      const a = b.dataset.a;
      let body = { action: a };
      if (a === "snooze") {
        const r = await modal("Snooze", `<div class="fgroup"><label>For how many days?</label><input type="number" name="days" value="1" min="1" max="60"></div>`, async (f) => ({ action: a, days: +f.days.value }), "Snooze");
        if (!r) return; body = r;
      }
      if (a === "assign") body.staff_id = A.me().staff.id;
      try { await post("/api/staff/intray/" + b.dataset.id, body); this.render(root); } catch (x) { toast(x.message, true); }
    });
  },
});

/* the In-tray sits just under the Dashboard */
{
  const nav = $("#sideNav"), it = $('#sideNav button[data-tab="intray"]'), dash = $('#sideNav button[data-tab="dashboard"]');
  if (nav && it && dash) nav.insertBefore(it, dash.nextSibling);
}

/* keep the In-tray count on the sidebar fresh */
document.addEventListener("hah:ready", async () => {
  try {
    const d = await api("/api/staff/intray");
    const b = $('#sideNav button[data-tab="intray"]');
    if (b && d.total_open) { b.dataset.count = d.total_open; b.insertAdjacentHTML("beforeend", ` <em>${d.total_open}</em>`); }
  } catch (_) { /* not signed in yet */ }
});

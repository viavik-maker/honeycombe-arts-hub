/* Shop: products (price, stock, picture) and orders (ready → collected/posted, or cancelled with a refund). */
import { $, $$, api, can, chip, confirmBox, esc, modal, money, post, table, toast, when } from "./ui.js";

const A = window.HAHAdmin;
const pounds = (p) => p == null ? "" : (p / 100).toFixed(2);
const toPence = (v) => Math.round(parseFloat(String(v).replace(/[£,\s]/g, "")) * 100);
const CHIP = { new: "warn", ready: "info", collected: "ok", posted: "ok", cancelled: "muted" };

async function editProduct(p, done) {
  p = p || { title: "", description: "", image: "", price_pence: 0, stock: "", max_per_order: 10, status: "draft", sort: 0 };
  const r = await modal(p.id ? "Edit product" : "New product", `
    <div class="fgroup"><label>Name</label><input type="text" name="title" value="${esc(p.title)}" required></div>
    <div class="fgroup"><label>Description (optional)</label><textarea name="description">${esc(p.description || "")}</textarea></div>
    <div class="frow"><div class="fgroup"><label>Price (£)</label><input type="text" inputmode="decimal" name="price" value="${pounds(p.price_pence)}"></div>
      <div class="fgroup"><label>In stock</label><input type="number" name="stock" min="0" value="${p.stock ?? ""}"><p class="fhint">Empty = no limit.</p></div>
      <div class="fgroup"><label>Most per order</label><input type="number" name="max" min="1" max="100" value="${p.max_per_order}"></div></div>
    <div class="frow"><div class="fgroup"><label>Picture (optional)</label><input type="text" name="image" value="${esc(p.image || "")}" placeholder="/uploads/…"><p class="fhint">Copy an image address from the Gallery.</p></div>
      <div class="fgroup"><label>Order on the page</label><input type="number" name="sort" value="${p.sort}"></div>
      <div class="fgroup"><label>Status</label><select name="status">${[["draft", "Draft (hidden)"], ["live", "On sale"], ["archived", "Archived"]].map(([k, l]) =>
        `<option value="${k}"${k === p.status ? " selected" : ""}>${l}</option>`).join("")}</select></div></div>`,
    (f) => post(p.id ? `/api/staff/shop/products/${p.id}/update` : "/api/staff/shop/products", {
      title: f.title.value, description: f.description.value, price_pence: toPence(f.price.value || "0"), stock: f.stock.value,
      max_per_order: f.max.value, image: f.image.value, sort: f.sort.value, status: f.status.value }), "Save");
  if (r) { toast("Saved"); done(); }
}

A.addTab({
  id: "shop", label: "Shop", icon: "🛍️", perm: "shop.manage",
  state: { view: "open", q: "" },
  async render(root) {
    const st = this.state;
    const [pr, od] = await Promise.all([api("/api/staff/shop/products"), api("/api/staff/shop/orders?view=" + st.view + "&q=" + encodeURIComponent(st.q))]);
    root.innerHTML = `<h1>Shop</h1>
      <p class="sub">${pr.live ? chip("Open to families", "ok") : chip("Closed", "muted") + " — open it in Booking settings → The shop"} · families order at <a href="/shop" target="_blank" rel="noopener">/shop</a>. Every order is an invoice, so payments show in Finance.</p>
      <div class="acard"><h2>Orders</h2>
        <div class="toolbar"><div class="segtabs">${[["open", "To do"], ["collected", "Collected"], ["posted", "Posted"], ["cancelled", "Cancelled"], ["all", "All"]].map(([k, l]) =>
          `<button class="abtn abtn--sm ${k === st.view ? "abtn--honey" : "abtn--ghost"}" data-view="${k}">${l}</button>`).join("")}</div>
          <input type="search" id="shopQ" placeholder="Order ref, name or email" value="${esc(st.q)}"></div>
        ${table([
          { label: "Order", get: o => `<strong>${esc(o.ref)}</strong><br><span class="fhint">${esc(when(o.created_at))}</span>` },
          { label: "Family", get: o => esc(o.family.name) + `<br><span class="fhint">${esc(o.family.email || "")} ${esc(o.family.mobile || "")}</span>` },
          { label: "Items", cls: "minw", get: o => o.lines.map(l => `${l.quantity} × ${esc(l.title)}`).join("<br>") + (o.notes ? `<br><em>${esc(o.notes)}</em>` : "") },
          { label: "Paid?", cls: "nowrap", get: o => esc(money(o.total_pence)) + "<br>" + (o.invoice ? (o.invoice.balance_pence > 0 ? chip(o.pay_mode === "card" ? "awaiting card" : "pay on collection", "warn") : chip("paid", "ok")) : "") },
          { label: "Status", get: o => chip(o.status_text, CHIP[o.status]) },
          { label: "", cls: "nowrap", get: o => ["new", "ready"].includes(o.status) ? `${o.status === "new" ? `<button class="abtn abtn--primary abtn--sm" data-to="ready" data-ref="${esc(o.ref)}">Ready</button>` : ""}
            <button class="abtn abtn--ghost abtn--sm" data-to="collected" data-ref="${esc(o.ref)}">Collected</button>
            <button class="abtn abtn--ghost abtn--sm" data-to="posted" data-ref="${esc(o.ref)}">Posted</button>
            <button class="abtn abtn--danger abtn--sm" data-to="cancelled" data-ref="${esc(o.ref)}">Cancel</button>` : "" }], od.orders, { empty: "No orders here." })}
        <p class="fhint">Take payment at collection with Finance → Record payment (quote the invoice number). Cancelling refunds anything paid: to the card if it was paid by card, otherwise as account credit.</p></div>
      <div class="acard"><h2>Products</h2><p><button class="abtn abtn--primary abtn--sm" id="newProd">＋ New product</button></p>
        ${table([{ label: "Product", get: p => `<strong>${esc(p.title)}</strong>` }, { label: "Price", get: p => esc(money(p.price_pence)) },
          { label: "Stock", get: p => p.stock == null ? "no limit" : String(p.stock) }, { label: "Sold", get: p => p.sold },
          { label: "Status", get: p => chip({ draft: "Draft", live: "On sale", archived: "Archived" }[p.status], p.status === "live" ? "ok" : "muted") },
          { label: "", get: p => `<button class="abtn abtn--ghost abtn--sm" data-edit="${p.id}">Edit</button>` }], pr.products, { empty: "No products yet." })}</div>`;
    const re = () => this.render(root);
    $$("[data-view]", root).forEach(b => b.onclick = () => { st.view = b.dataset.view; re(); });
    $("#shopQ", root).onkeydown = (e) => { if (e.key === "Enter") { st.q = e.target.value; re(); } };
    $("#newProd", root).onclick = () => editProduct(null, re);
    $$("[data-edit]", root).forEach(b => b.onclick = () => editProduct(pr.products.find(p => p.id === +b.dataset.edit), re));
    $$("[data-to]", root).forEach(b => b.onclick = async () => {
      const to = b.dataset.to, ref = b.dataset.ref;
      if (to === "cancelled") {
        const r = await modal("Cancel order " + ref, `<div class="fgroup"><label>Reason (for the records)</label><input type="text" name="reason"></div>
          <p class="fhint">The items go back into stock and anything paid is refunded.</p>`,
          (f) => post(`/api/staff/shop/orders/${ref}/status`, { to, reason: f.reason.value }), "Cancel order");
        if (r) { toast(r.refunded_pence ? `Cancelled — ${money(r.refunded_pence)} refunded` : "Cancelled"); re(); }
        return;
      }
      try { await post(`/api/staff/shop/orders/${ref}/status`, { to }); toast(to === "ready" ? "Marked ready — the family has been emailed" : "Done"); re(); }
      catch (x) { toast(x.message, true); }
    });
  },
});

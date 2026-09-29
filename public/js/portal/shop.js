/* The shop: products, a basket on the page, and ordering (signed in). Prices and stock come from the server. */
import { $, $$, api, busy, esc, loadMe, notice, renderNav, root, state } from "./core.js";

const money = (p) => "£" + ((p || 0) / 100).toFixed(2).replace(/\.00$/, "");

export async function shop() {
  await loadMe();
  renderNav("shop");
  const d = await api("/api/shop");
  if (!d.live) {
    root().innerHTML = `<h1>Shop</h1><p>The shop is closed at the moment. <a href="/book">Book activities</a></p>`;
    return;
  }
  const qty = {};
  const idem = crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random();
  const draw = () => {
    const chosen = d.products.filter(p => qty[p.id] > 0);
    const total = chosen.reduce((t, p) => t + p.price_pence * qty[p.id], 0);
    root().innerHTML = `<h1>Shop</h1><p class="lead">${esc(d.collection_note)}</p>
      <div class="shop-grid">${d.products.map(p => `<article class="pcard shop-item">
        ${p.image ? `<img src="${esc(p.image)}" alt="" loading="lazy">` : ""}
        <h2>${esc(p.title)}</h2>${p.description ? `<p>${esc(p.description)}</p>` : ""}
        <p class="shop-item__price">${p.price_pence ? money(p.price_pence) : "Free"}${p.left != null && p.in_stock ? ` <span class="hint">· only ${p.left} left</span>` : ""}</p>
        ${p.in_stock ? `<div class="field shop-item__qty"><label for="q${p.id}">How many?</label>
          <input type="number" id="q${p.id}" data-q="${p.id}" min="0" max="${p.left != null ? Math.min(p.left, p.max_per_order) : p.max_per_order}" value="${qty[p.id] || 0}"></div>`
          : `<p class="status status--muted">Sold out</p>`}</article>`).join("")}</div>
      <form id="orderForm" class="pcard" novalidate ${chosen.length ? "" : "hidden"}>
        <h2>Your order</h2>
        <table class="table-plain"><tbody>${chosen.map(p => `<tr><td>${qty[p.id]} × ${esc(p.title)}</td><td class="num">${money(p.price_pence * qty[p.id])}</td></tr>`).join("")}</tbody>
          <tfoot><tr><td>Total</td><td class="num"><strong>${money(total)}</strong></td></tr></tfoot></table>
        <div class="field"><label for="notes">Anything we should know? (optional)</label><input id="notes" name="notes" maxlength="300" placeholder="e.g. T-shirt sizes"></div>
        ${total ? `<fieldset class="field"><legend>How would you like to pay?</legend>
          ${d.card_payments ? `<label class="check"><input type="radio" name="pay" value="card" checked> <span><strong>Card now</strong></span></label>` : ""}
          <label class="check"><input type="radio" name="pay" value="on_collection"${d.card_payments ? "" : " checked"}> <span><strong>When I collect it</strong><br><small>Cash, card or bank transfer — or pay online later from My bookings.</small></span></label></fieldset>` : ""}
        <p class="field__error" id="orderErr" hidden></p>
        <div class="btn-row"><span></span>${state.me ? `<button class="btn btn--orange" type="submit">Place order</button>`
          : `<a class="btn btn--orange" href="/login?next=/shop">Sign in to order</a>`}</div></form>`;
    $$("[data-q]").forEach(inp => inp.onchange = () => {
      const max = +inp.max;
      qty[inp.dataset.q] = Math.max(0, Math.min(max, Math.floor(+inp.value || 0)));
      draw();
      const again = $(`[data-q="${inp.dataset.q}"]`); if (again) again.focus();
    });
    const f = $("#orderForm");
    if (f && state.me) f.onsubmit = async (e) => {
      e.preventDefault();
      const err = $("#orderErr"); err.hidden = true;
      try {
        const r = await busy($("button[type=submit]", f), () => api("/api/shop/order", {
          items: chosen.map(p => ({ product: p.id, quantity: qty[p.id] })), notes: f.notes.value,
          pay_mode: (($("input[name=pay]:checked", f) || {}).value) || null, idempotency_key: idem }));
        if (r.redirect) { location.assign(r.redirect); return; }
        root().innerHTML = `<h1>Thank you!</h1>${notice(`Your order <strong>${esc(r.order.ref)}</strong> is in. We've emailed you the details.${r.warning ? " " + esc(r.warning) : ""}`, "ok")}
          <p>${esc(d.collection_note)}</p><p><a class="btn btn--ghost" href="/account/bookings">My bookings & orders</a></p>`;
      } catch (x) { err.textContent = x.message; err.hidden = false; }
    };
  };
  draw();
}

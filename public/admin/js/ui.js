/* Shared helpers for the admin's ES-module areas (Staff, Bookings, Registers…).
   Everything that renders visitor- or parent-entered text goes through esc(). */
const A = window.HAHAdmin;
export const { api, post, toast, esc, $, $$ } = A;
export const can = (p) => A.can(p);
export const me = () => A.me();

export function when(iso, opts) {
  if (!iso) return "—";
  const d = new Date(iso.length === 10 ? iso + "T12:00:00" : iso);
  return d.toLocaleString("en-GB", opts || { dateStyle: "medium", timeStyle: "short" });
}
/* the charity's date and time (UK), whatever the device's clock is set to */
export const ukToday = () => new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/London" }).format(new Date());
export const ukNowLocal = () => new Intl.DateTimeFormat("sv-SE", { timeZone: "Europe/London", dateStyle: "short", timeStyle: "short" })
  .format(new Date()).replace(" ", "T");
export const day = (iso) => when(iso, { weekday: "short", day: "numeric", month: "short", year: "numeric" });
export const money = (pence) => (pence < 0 ? "−" : "") + "£" + (Math.abs(pence || 0) / 100).toFixed(2);

/* chip("Confirmed", "ok") — kinds: ok, warn, bad, info, muted */
export const chip = (text, kind) => `<span class="chip chip--${kind || "muted"}">${esc(text)}</span>`;

/* table([{label, get: row => html, cls}], rows, {empty}) — cells return HTML, so escape in get() */
export function table(cols, rows, opts) {
  if (!rows.length) return `<p class="empty">${esc((opts && opts.empty) || "Nothing here yet.")}</p>`;
  return `<div class="table-wrap"><table class="table">
    <thead><tr>${cols.map(c => `<th${c.cls ? ` class="${c.cls}"` : ""}>${esc(c.label)}</th>`).join("")}</tr></thead>
    <tbody>${rows.map((r, i) => `<tr data-i="${i}">${cols.map(c =>
      `<td${c.cls ? ` class="${c.cls}"` : ""}>${c.get(r)}</td>`).join("")}</tr>`).join("")}</tbody>
  </table></div>`;
}

/* confirmBox("Disable Sam's account?") -> Promise<boolean>, using <dialog> (not window.confirm) */
export function confirmBox(message, okLabel) {
  return new Promise((resolve) => {
    const d = document.createElement("dialog");
    d.className = "dlg";
    d.innerHTML = `<p>${esc(message)}</p><div class="dlg__btns">
      <button class="abtn abtn--ghost" value="no">Cancel</button>
      <button class="abtn abtn--primary" value="yes">${esc(okLabel || "OK")}</button></div>`;
    document.body.appendChild(d);
    d.addEventListener("click", (e) => {
      const v = e.target.closest("button") && e.target.closest("button").value;
      if (v) { d.close(); d.remove(); resolve(v === "yes"); }
    });
    d.addEventListener("cancel", () => { d.remove(); resolve(false); });
    d.showModal();
  });
}

/* modal(title, html, onSubmit(form)) — a dialog containing a <form>; resolves when closed */
export function modal(title, html, onSubmit, submitLabel) {
  return new Promise((resolve) => {
    const d = document.createElement("dialog");
    d.className = "dlg dlg--form";
    d.innerHTML = `<form method="dialog"><h2>${esc(title)}</h2>${html}
      <p class="dlg__err" hidden></p>
      <div class="dlg__btns"><button type="button" class="abtn abtn--ghost" data-close>Cancel</button>
      <button type="submit" class="abtn abtn--primary">${esc(submitLabel || "Save")}</button></div></form>`;
    document.body.appendChild(d);
    const f = $("form", d);
    const done = (v) => { d.close(); d.remove(); resolve(v); };
    $("[data-close]", d).addEventListener("click", () => done(null));
    d.addEventListener("cancel", () => { d.remove(); resolve(null); });
    f.addEventListener("submit", async (e) => {
      e.preventDefault();
      const err = $(".dlg__err", d); err.hidden = true;
      try { done(await onSubmit(f)); }
      catch (x) { err.textContent = x.message; err.hidden = false; }
    });
    d.showModal();
    const first = $("input, select, textarea", d); if (first) first.focus();
  });
}

/* query string helper for GET APIs */
export const qs = (o) => Object.entries(o).filter(([, v]) => v !== "" && v != null)
  .map(([k, v]) => encodeURIComponent(k) + "=" + encodeURIComponent(v)).join("&");

export function copyText(text) {
  return navigator.clipboard.writeText(text).then(() => toast("Copied"), () => toast("Couldn't copy — select and copy it by hand", true));
}

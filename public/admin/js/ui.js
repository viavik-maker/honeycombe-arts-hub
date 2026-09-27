/* Shared helpers for the admin's ES-module areas (Staff, Bookings, Registers…).
   Everything that renders visitor- or parent-entered text goes through esc(). */
const A = window.HAHAdmin;
export const { api, post, toast, esc, $, $$ } = A;
export const can = (p) => A.can(p);
export const me = () => A.me();

export function when(iso, opts) {
  if (!iso) return "—";
  /* a bare date is read as midday UTC, so it's the same day in the UK wherever the device is (and has no time) */
  const bare = iso.length === 10;
  const d = new Date(bare ? iso + "T12:00:00Z" : iso);
  return d.toLocaleString("en-GB", Object.assign({ timeZone: "Europe/London" },
    opts || (bare ? { dateStyle: "medium" } : { dateStyle: "medium", timeStyle: "short" })));
}
/* the charity's date and time (UK), whatever the device's clock is set to */
export const ukToday = () => new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/London" }).format(new Date());
export const ukNowLocal = () => new Intl.DateTimeFormat("sv-SE", { timeZone: "Europe/London", dateStyle: "short", timeStyle: "short" })
  .format(new Date()).replace(" ", "T");
export const day = (iso) => when(iso, { weekday: "short", day: "numeric", month: "short", year: "numeric" });
export const money = (pence) => (pence < 0 ? "−" : "") + "£" + (Math.abs(pence || 0) / 100).toFixed(2);

/* money typed by staff: "12", "12.5", "£12.50", "1,200" -> pence; empty -> null ("not set");
   anything else -> NaN, so a typo is never sent as 0 or null */
export function parsePence(v) {
  const s = String(v == null ? "" : v).replace(/[£,\s]/g, "");
  if (s === "") return null;
  const m = /^(\d*)(?:\.(\d{0,2}))?$/.exec(s);
  if (!m || (m[1] === "" && !m[2])) return NaN;
  return (+m[1] || 0) * 100 + +((m[2] || "") + "00").slice(0, 2);
}

/* pence(input, {label, required, positive}) -> pence or null (empty and optional). On a bad amount it shows
   the problem under the box and throws, so a modal shows it too and nothing is sent. */
export function pence(input, opts) {
  const o = opts || {};
  const label = o.label || "Amount";
  const box = input.closest(".fgroup") || input.parentElement;
  const old = box.querySelector(".ferr[data-pence]"); if (old) old.remove();
  input.removeAttribute("aria-invalid");
  const p = parsePence(input.value);
  const msg = Number.isNaN(p) ? `${label}: enter pounds and pence, like 12.50.`
    : p === null && o.required ? `${label}: enter an amount${o.positive ? "" : " (0 for free)"}.`
    : p === 0 && o.positive ? `${label}: enter more than £0.` : "";
  if (!msg) return p;
  const id = (input.id || input.name || "amount") + "-err";
  box.insertAdjacentHTML("beforeend", `<p class="ferr" data-pence id="${esc(id)}">${esc(msg)}</p>`);
  input.setAttribute("aria-invalid", "true"); input.setAttribute("aria-describedby", id);
  input.focus();
  throw new Error(msg);
}

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
    const btn = $("button[type=submit]", d);
    f.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (btn.disabled) return;   /* already sending: a double click must not record it twice */
      const err = $(".dlg__err", d); err.hidden = true;
      btn.disabled = true; f.setAttribute("aria-busy", "true");
      try { done(await onSubmit(f)); }
      catch (x) { err.textContent = x.message; err.hidden = false; }
      finally { btn.disabled = false; f.removeAttribute("aria-busy"); }
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

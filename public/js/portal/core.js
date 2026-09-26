/* Family portal: shared plumbing. */
export const $ = (s, el) => (el || document).querySelector(s);
export const $$ = (s, el) => Array.from((el || document).querySelectorAll(s));
export const esc = (s) => String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;")
  .replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");

export const state = { me: null, csrf: null, spec: null };
export const root = () => $("#portal");
export const params = new URLSearchParams(location.search);

/* only follow ?next= to pages on this site */
export function safeNext(fallback) {
  const n = params.get("next") || "";
  return n.startsWith("/") && !n.startsWith("//") ? n : fallback;
}

export class ApiError extends Error {
  constructor(message, status, data) { super(message); this.status = status; this.data = data || {}; }
}

export async function api(path, body, opts) {
  const init = { method: body === undefined ? "GET" : "POST", headers: {} };
  if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
    if (state.csrf) init.headers["X-CSRF-Token"] = state.csrf;
  }
  let r;
  try { r = await fetch(path, init); }
  catch (_) { throw new ApiError("We couldn't reach the website — check your connection and try again.", 0); }
  const d = await r.json().catch(() => ({}));
  if (r.status === 403 && d.reauth && !(opts && opts.noReauth)) {
    await reauthenticate();
    return api(path, body, { noReauth: true });
  }
  if (!r.ok) throw new ApiError(d.error || "Something went wrong — please try again.", r.status, d);
  if (d.csrf) state.csrf = d.csrf;
  return d;
}

/* who's signed in (null if nobody) */
export async function loadMe() {
  try { state.me = await api("/api/account/me"); state.csrf = state.me.csrf; }
  catch (e) { if (e.status === 401) state.me = null; else throw e; }
  return state.me;
}

export async function requireSignIn() {
  if (!(await loadMe())) {
    location.replace("/login?next=" + encodeURIComponent(location.pathname + location.search));
    throw new Error("redirecting");
  }
  return state.me;
}

export async function loadSpec() {
  if (!state.spec) state.spec = await api("/api/account/formspec");
  return state.spec;
}

export function go(url) { location.assign(url); }

/* the signed-in navigation bar */
export function renderNav(active) {
  const nav = $("#portalNav");
  if (!state.me) { nav.hidden = true; return; }
  const link = (href, label, key) => `<a href="${href}"${key === active ? ' aria-current="page"' : ""}>${label}</a>`;
  nav.innerHTML = `<div class="container portal-nav__inner">
      <span class="portal-nav__hi">Hi ${esc(state.me.account.first_name)}</span>
      ${link("/book", "Book activities", "book")}
      ${link("/account/bookings", "My bookings", "bookings")}
      ${link("/account", "My family", "family")}
      ${link("/account/details", "My details", "details")}
      <button type="button" id="signOut">Sign out</button></div>`;
  nav.hidden = false;
  $("#signOut").onclick = async () => {
    try { await api("/api/account/logout", {}); } catch (_) { /* signed out anyway */ }
    go("/login");
  };
}

/* a busy state on a button while a request runs */
export async function busy(btn, fn) {
  const label = btn.textContent;
  btn.disabled = true; btn.textContent = "Please wait…";
  try { return await fn(); }
  finally { btn.disabled = false; btn.textContent = label; }
}

/* ask for the password again before a sensitive change */
function reauthenticate() {
  return new Promise((resolve, reject) => {
    const d = document.createElement("dialog");
    d.className = "pcard";
    d.innerHTML = `<form method="dialog"><h2>Confirm it's you</h2>
      <p>To change who can collect a child, please enter your account password.</p>
      <div class="field"><label for="reauthPw">Password</label>
        <input type="password" id="reauthPw" autocomplete="current-password" required></div>
      <p class="field__error" hidden></p>
      <div class="btn-row"><button type="button" class="btn btn--ghost" value="cancel">Cancel</button>
        <button class="btn btn--orange" type="submit">Confirm</button></div></form>`;
    document.body.appendChild(d);
    const f = $("form", d), err = $(".field__error", d);
    $("[value=cancel]", d).onclick = () => { d.close(); d.remove(); reject(new ApiError("Not changed.", 403)); };
    f.onsubmit = async (e) => {
      e.preventDefault();
      try { await api("/api/account/reauth", { password: $("#reauthPw", d).value }, { noReauth: true }); d.close(); d.remove(); resolve(); }
      catch (x) { err.textContent = x.message; err.hidden = false; }
    };
    d.showModal();
  });
}

export function notice(html, kind) {
  return `<div class="notice${kind ? " notice--" + kind : ""}" role="status">${html}</div>`;
}

export function fail(e) {
  if (e && e.message === "redirecting") return;
  root().innerHTML = notice(esc(e.message || "Something went wrong."), "err") +
    `<p><a href="/account">Back to your account</a></p>`;
}

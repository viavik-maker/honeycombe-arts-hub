/* Offline registers for a staff tablet.

   While online, staff download today's registers (only what the printed register shows: no collection passwords,
   no safeguarding details). The copy, and any changes made while offline, are kept on this tablet encrypted with
   AES-GCM under a key made from a PIN the staff member chooses (PBKDF2); the key is only ever held in memory.
   Changes are sent, with the time they really happened, as soon as there's a connection. Yesterday's copy is
   wiped. Collection passwords can't be checked offline — staff use the phone check instead. */

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const root = () => $("#offlineRoot");
// the charity's date (UK), whatever the tablet's clock is set to
const today = () => new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/London" }).format(new Date());
const stamp = () => new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
const hhmm = (iso) => iso ? new Date(iso).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }) : "";
const LOCK_AFTER_MS = 15 * 60 * 1000;
const MAX_TRIES = 5;

function toast(msg, bad) {
  const t = $("#toast"); t.textContent = msg; t.hidden = false; t.classList.toggle("toast--err", !!bad);
  clearTimeout(toast.timer); toast.timer = setTimeout(() => { t.hidden = true; }, 3500);
}

/* ---------------- storage (IndexedDB, one small key–value store) ---------------- */
function idb() {
  return new Promise((resolve, reject) => {
    const r = indexedDB.open("hah-offline", 1);
    r.onupgradeneeded = () => r.result.createObjectStore("kv");
    r.onsuccess = () => resolve(r.result); r.onerror = () => reject(r.error);
  });
}
async function kv(mode, fn) {
  const db = await idb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction("kv", mode); const store = tx.objectStore("kv");
    const req = fn(store);
    tx.oncomplete = () => resolve(req && req.result); tx.onerror = () => reject(tx.error);
  });
}
const get = (k) => kv("readonly", s => s.get(k));
const put = (k, v) => kv("readwrite", s => s.put(v, k));
const wipe = () => kv("readwrite", s => s.clear());

/* ---------------- encryption ---------------- */
const b64 = (buf) => btoa(String.fromCharCode(...new Uint8Array(buf)));
const unb64 = (s) => Uint8Array.from(atob(s), c => c.charCodeAt(0));
async function keyFrom(pin, salt) {
  const base = await crypto.subtle.importKey("raw", new TextEncoder().encode(pin), "PBKDF2", false, ["deriveKey"]);
  return crypto.subtle.deriveKey({ name: "PBKDF2", salt, iterations: 250000, hash: "SHA-256" }, base,
    { name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]);
}
async function seal(key, obj) {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const data = await crypto.subtle.encrypt({ name: "AES-GCM", iv }, key, new TextEncoder().encode(JSON.stringify(obj)));
  return { iv: b64(iv), data: b64(data) };
}
async function open(key, box) {
  const plain = await crypto.subtle.decrypt({ name: "AES-GCM", iv: unb64(box.iv) }, key, unb64(box.data));
  return JSON.parse(new TextDecoder().decode(plain));
}

/* ---------------- state ---------------- */
let KEY = null;          // only in memory
let S = null;            // { pack, queue: [], problems: [], active }
let lastTouch = Date.now();

async function save() {
  await put("state", await seal(KEY, S));
  await put("meta", { date: S.pack.date, pending: S.queue.length, saved: stamp() });  // nothing personal
  $("#syncBtn").hidden = !S.queue.length;
  $("#syncBtn").textContent = `Send changes (${S.queue.length})`;
}

function showNet() {
  const pending = S ? S.queue.length : 0;
  $("#netState").textContent = (navigator.onLine ? "Online" : "OFFLINE — changes are saved on this tablet") +
    (pending ? ` · ${pending} change${pending === 1 ? "" : "s"} waiting to send` : "");
}

/* ---------------- talking to the server ---------------- */
async function csrf() {
  const r = await fetch("/api/staff/me", { credentials: "same-origin" });
  if (r.status === 401) throw Object.assign(new Error("signin"), { signin: true });
  const d = await r.json();
  if (!d.mfa_passed) throw Object.assign(new Error("signin"), { signin: true });
  return d.csrf;
}

async function sync(quiet) {
  if (!S || !S.queue.length || !navigator.onLine || sync.busy) return;
  sync.busy = true;
  try {
    const token = await csrf();
    while (S.queue.length) {
      const item = S.queue[0];
      let r;
      try {
        r = await fetch("/api/staff/attendance/" + item.bid, { method: "POST", credentials: "same-origin",
          headers: { "Content-Type": "application/json", "X-CSRF-Token": token }, body: JSON.stringify(item.body) });
      } catch (_) { break; }  // connection went again: try later
      if (r.status === 401) throw Object.assign(new Error("signin"), { signin: true });
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        S.problems.push({ who: item.who, what: item.what, at: item.body.at, error: d.error || "Not accepted" });
      }
      S.queue.shift();
      await save();
    }
    if (!quiet) toast(S.queue.length ? "Some changes are still waiting" : "All changes sent ✓");
  } catch (x) {
    if (x.signin) toast("Sign in to the admin (Full admin, then come back) to send the changes.", true);
  } finally {
    sync.busy = false; showNet(); if (S) draw();
  }
}

/* ---------------- screens ---------------- */
function pinForm(title, lead, twice, onPin) {
  root().innerHTML = `<div class="acard offline-pin"><h1>${esc(title)}</h1><p>${lead}</p>
    <form id="pinForm"><div class="fgroup"><label for="pin">PIN (at least 6 digits)</label>
      <input id="pin" type="password" inputmode="numeric" autocomplete="off" minlength="6" required></div>
      ${twice ? `<div class="fgroup"><label for="pin2">The same PIN again</label><input id="pin2" type="password" inputmode="numeric" autocomplete="off" required></div>` : ""}
      <p class="fhint" id="pinErr" hidden></p>
      <button class="abtn abtn--primary" type="submit">${twice ? "Download today's registers" : "Open"}</button></form></div>`;
  $("#pin").focus();
  $("#pinForm").onsubmit = async (e) => {
    e.preventDefault();
    const pin = $("#pin").value.trim(), err = $("#pinErr");
    err.hidden = true;
    if (!/^\d{6,}$/.test(pin)) { err.textContent = "Use at least 6 digits."; err.hidden = false; return; }
    if (twice && pin !== $("#pin2").value.trim()) { err.textContent = "The two PINs don't match."; err.hidden = false; return; }
    try { await onPin(pin); } catch (x) { err.textContent = x.message; err.hidden = false; }
  };
}

async function download(pin) {
  if (!navigator.onLine) throw new Error("You need a connection to download the registers.");
  const r = await fetch("/api/staff/registers/offline-pack", { credentials: "same-origin" });
  if (r.status === 401 || r.status === 403) throw new Error("Sign in to the admin first (Full admin), then come back here.");
  if (!r.ok) throw new Error("Couldn't download the registers — try again.");
  const pack = await r.json();
  const salt = crypto.getRandomValues(new Uint8Array(16));
  await put("salt", b64(salt));
  await put("tries", 0);
  KEY = await keyFrom(pin, salt);
  S = { pack, queue: (S && S.queue) || [], problems: (S && S.problems) || [], active: pack.sessions.length ? pack.sessions[0].id : null };
  await save();
  draw();
}

async function unlock(pin) {
  const box = await get("state"), salt = unb64(await get("salt"));
  const key = await keyFrom(pin, salt);
  try {
    S = await open(key, box);
  } catch (_) {
    const tries = ((await get("tries")) || 0) + 1;
    await put("tries", tries);
    if (tries >= MAX_TRIES) {
      await wipe();
      throw new Error("Too many wrong PINs, so the registers have been cleared from this tablet. Download them again.");
    }
    throw new Error(`That PIN isn't right (${MAX_TRIES - tries} tries left).`);
  }
  await put("tries", 0);
  KEY = key;
  showNet(); draw(); sync(true);
}

function lock() {
  KEY = null; S = null;
  start();
}

function personCell(p) {
  const bad = (n) => /ANAPHYLAXIS|ALLERGY/.test(n);
  return `<strong>${esc(p.first_name)} ${esc(p.last_name)}</strong> <span class="fhint">(${p.age})</span>
    ${p.collection_alert ? `<br><span class="chip chip--bad">COLLECTION ALERT — see a manager</span>` : ""}
    ${p.needs.length ? `<br>${p.needs.map(n => `<span class="chip chip--${bad(n) ? "bad" : "warn"}">${esc(n)}</span>`).join(" ")}` : ""}
    <br><span class="fhint">${esc({ online: "Photos OK", internal: "Photos: internal only", none: "NO PHOTOS" }[p.photo] || "Photos: ?")}
    · Parent ${esc(p.parent.name)} <a href="tel:${esc(p.parent.mobile || "")}">${esc(p.parent.mobile || "")}</a></span>`;
}

function draw() {
  lastTouch = Date.now();
  const sessions = S.pack.sessions;
  const s = sessions.find(x => x.id === S.active) || sessions[0];
  root().innerHTML = `
    ${S.pack.date !== today() ? `<div class="offline-warn">These are the registers for ${esc(S.pack.date)}. Send any changes, then clear this tablet.</div>` : ""}
    ${S.problems.length ? `<div class="acard offline-problems"><h2>Changes that weren't accepted</h2><ul>${S.problems.map(p =>
      `<li><strong>${esc(p.who)}</strong> — ${esc(p.what)} at ${esc(hhmm(p.at))}: ${esc(p.error)}</li>`).join("")}</ul>
      <p class="fhint">Put these right in the full admin, then</p><button class="abtn abtn--ghost abtn--sm" id="clearProblems">Clear this list</button></div>` : ""}
    <div class="toolbar">${sessions.map(x => `<button class="abtn abtn--sm ${x.id === (s && s.id) ? "abtn--honey" : "abtn--ghost"}" data-session="${x.id}">${esc(x.start_time)} ${esc(x.title)}</button>`).join("")}</div>
    ${s ? `<h1>${esc(s.title)} · ${esc(s.start_time)}–${esc(s.end_time)}</h1><p class="sub">${esc(s.centre)}${s.theme ? " · " + esc(s.theme) : ""} ·
      downloaded ${esc(hhmm(S.pack.generated_at))} by ${esc(S.pack.staff)}</p>
      <div class="table-wrap"><table class="table"><thead><tr><th>Who</th><th>Status</th><th></th></tr></thead><tbody>
      ${s.rows.map((r, i) => `<tr>
        <td>${r.person ? personCell(r.person) : `<strong>${esc(r.party.contact.name)}</strong> — ${r.party.places} place(s)
          <br><span class="fhint">${esc(r.party.contact.mobile || "")}</span>${r.party.named.map(k => "<br>" + personCell(k)).join("")}`}
          ${r.to_discuss ? `<br><span class="chip chip--warn">Incident to talk through at collection</span>` : ""}</td>
        <td>${r.signed_out_at ? `Out ${esc(hhmm(r.signed_out_at))}` : r.signed_in_at ? `In ${esc(hhmm(r.signed_in_at))}` : r.status === "absent" ? "Absent" : r.status === "absent_notified" ? "Absent (told us)" : "Expected"}</td>
        <td class="nowrap">${!r.signed_in_at && !r.status.startsWith("absent") ? `<button class="abtn abtn--primary abtn--sm" data-act="in" data-i="${i}">In</button>
            <button class="abtn abtn--ghost abtn--sm" data-act="absent" data-i="${i}">Absent</button>` : ""}
          ${r.signed_in_at && !r.signed_out_at ? `<button class="abtn abtn--primary abtn--sm" data-act="out" data-i="${i}">Out…</button>` : ""}</td></tr>`).join("")}
      </tbody></table></div>` : `<p class="empty">No sessions today.</p>`}
    <p class="offline-foot"><button class="abtn abtn--ghost abtn--sm" id="refresh">Download again</button>
      <button class="abtn abtn--ghost abtn--sm" id="lockBtn">Lock</button>
      <button class="abtn abtn--danger abtn--sm" id="wipeBtn">Clear this tablet</button></p>`;
  $$("[data-session]").forEach(b => b.onclick = () => { S.active = +b.dataset.session; save(); draw(); });
  $$("[data-act]").forEach(b => b.onclick = () => act(s, s.rows[+b.dataset.i], b.dataset.act));
  const cp = $("#clearProblems"); if (cp) cp.onclick = async () => { S.problems = []; await save(); draw(); };
  $("#lockBtn").onclick = lock;
  $("#refresh").onclick = async () => {
    if (S.queue.length) return toast("Send the waiting changes first.", true);
    if (!navigator.onLine) return toast("You're offline.", true);
    pinForm("Download again", "Get the latest registers. Choose a PIN for them (it can be the same one).", true,
      async (pin) => { await download(pin); toast("Up to date ✓"); });
  };
  $("#wipeBtn").onclick = async () => {
    const msg = S.queue.length ? `${S.queue.length} change(s) haven't been sent and will be lost. Clear anyway?` : "Remove the registers from this tablet?";
    if (!confirm(msg)) return;
    await wipe(); KEY = null; S = null; start();
  };
  showNet();
}

function who(r) { return r.person ? `${r.person.first_name} ${r.person.last_name}` : r.party.contact.name; }

async function queue(r, body, what) {
  S.queue.push({ bid: r.booking_id, who: who(r), what, body: Object.assign({ offline: true, at: stamp() }, body) });
  await save(); draw(); sync(true);
}

async function act(s, r, kind) {
  if (kind === "in") { r.signed_in_at = stamp(); r.status = "present"; return queue(r, { action: "in" }, "signed in"); }
  if (kind === "absent") { r.status = "absent"; return queue(r, { action: "absent" }, "absent"); }
  const p = r.person || {};
  const d = document.createElement("dialog"); d.className = "dlg dlg--form";
  const methods = [["known_adult_verified", "Known adult, checked by phone"]];
  if (s.parent_must_stay || r.party) methods.unshift(["parent_stayed", "Parent/carer stayed"]);
  if (p.go_home_alone) methods.push(["went_home_alone", "Went home alone (with permission)"]);
  methods.push(["other", "Other (say who in the name box)"]);
  d.innerHTML = `<form method="dialog"><h2>Sign out ${esc(who(r))}</h2>
    ${p.collection_alert ? `<p class="chip chip--bad">COLLECTION ALERT — a manager must see who is collecting before they go.</p>` : ""}
    <p class="fhint">Offline, collection passwords can't be checked: call the parent on ${esc((p.parent || {}).mobile || "the number in their record")} to confirm who's collecting.</p>
    ${p.collectors && p.collectors.length ? `<p class="fhint">Allowed to collect: ${p.collectors.map(c => esc(`${c.name} (${c.relationship}) ${c.phone}`)).join("; ")}</p>` : ""}
    <div class="fgroup"><label>How</label><select name="method">${methods.map(([k, l]) => `<option value="${k}">${esc(l)}</option>`).join("")}</select></div>
    <div class="frow"><div class="fgroup"><label>Name of the adult collecting</label><input name="name"></div>
      <div class="fgroup"><label>Relationship</label><input name="rel"></div></div>
    ${r.to_discuss ? `<label class="fcheck"><input type="checkbox" name="discussed"> I've talked the incident through with them</label>` : ""}
    <p class="dlg__err" hidden></p>
    <div class="dlg__btns"><button type="button" class="abtn abtn--ghost" data-close>Cancel</button><button class="abtn abtn--primary">Sign out</button></div></form>`;
  document.body.appendChild(d);
  const f = $("form", d), err = $(".dlg__err", d);
  $("[data-close]", d).onclick = () => { d.close(); d.remove(); };
  f.onsubmit = async (e) => {
    e.preventDefault();
    const method = f.method.value, name = f.name.value.trim();
    const needName = ["known_adult_verified", "other"].includes(method);
    if (needName && !name) { err.textContent = "Enter who is collecting."; err.hidden = false; return; }
    if (r.to_discuss && !f.discussed.checked) { err.textContent = "Talk the incident through before they go."; err.hidden = false; return; }
    d.close(); d.remove();
    r.signed_out_at = stamp();
    await queue(r, { action: "out", method, collected_by_name: name, collected_by_relationship: f.rel.value.trim(),
      incident_discussed: !!(f.discussed && f.discussed.checked) }, "signed out");
  };
  d.showModal();
}

/* ---------------- start ---------------- */
async function start() {
  const meta = await get("meta");
  if (meta && meta.date < today() && !meta.pending) { await wipe(); }
  const box = await get("state");
  if (!box) {
    return pinForm("Registers for today", "Download today's registers so they keep working if the connection drops. " +
      "Choose a PIN: you'll need it to open them. They're kept on this tablet encrypted, and cleared tomorrow.", true, download);
  }
  const m = await get("meta");
  pinForm("Enter your PIN", `Registers for ${esc(m ? m.date : "today")} are saved on this tablet${m && m.pending ? ` with ${m.pending} change(s) waiting to send` : ""}.`, false, unlock);
}

if ("serviceWorker" in navigator) navigator.serviceWorker.register("/admin/sw.js").catch(() => {});
addEventListener("online", () => { showNet(); sync(); });
addEventListener("offline", showNet);
["click", "keydown", "touchstart"].forEach(ev => addEventListener(ev, () => { lastTouch = Date.now(); }, { passive: true }));
setInterval(() => { if (KEY && Date.now() - lastTouch > LOCK_AFTER_MS) { lock(); toast("Locked after 15 minutes — enter your PIN"); } }, 30000);
$("#syncBtn").onclick = () => sync();
start().catch(e => { root().innerHTML = `<p>${esc(e.message)}</p>`; });

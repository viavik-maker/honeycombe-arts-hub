/* One-off event booking without an account (mockup 4), and the page the
   emailed confirmation link opens. */
import { $, api, busy, esc, loadMe, notice, params, root, state } from "./core.js";
import { showErrors } from "./forms.js";

const money = (p) => "£" + ((p || 0) / 100).toFixed(2).replace(/\.00$/, "");

export async function guest() {
  await loadMe();
  const slug = location.pathname.split("/")[2];
  const sid = params.get("session");
  let d;
  try { d = await api(`/api/book/guest/${encodeURIComponent(slug)}?session=${encodeURIComponent(sid || "")}`); }
  catch (e) { root().innerHTML = notice(esc(e.status === 404 ? "Sorry, we couldn't find that event." : e.message), "err") + `<p><a href="/book">See what's on</a></p>`; return; }
  const a = d.activity, s = d.session;
  const when = new Date(s.date + "T12:00:00").toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" });
  const opts = (n) => Array.from({ length: a.max_party_size + 1 }, (_, i) => `<option${i === n ? " selected" : ""}>${i}</option>`).join("");
  const full = !["open", "few"].includes(s.state);
  root().innerHTML = `<div class="portal__narrow">
    <h1>Book a place</h1>
    ${state.me ? notice(`You're signed in — <a href="/book?activity=${esc(slug)}">book from your account</a> so your children's details come with you.`, "") :
      `<p class="lead">Have an account? <a href="/login?next=${encodeURIComponent("/book?activity=" + slug)}">Sign in to book faster</a>.</p>`}
    <div class="pcard"><h2>You're booking</h2>
      <p><strong>${esc(a.title)}</strong><br>${esc(when)}, ${esc(s.start_time)}–${esc(s.end_time)}${s.theme ? " · " + esc(s.theme) : ""}<br>${esc(a.centre)}</p>
      <ul class="chips"><li>Ages ${esc(a.age_text)}</li><li>Children ${a.child_price_pence ? money(a.child_price_pence) : "free"}</li>
        ${a.adult_price_pence ? `<li>Adults ${money(a.adult_price_pence)}</li>` : ""}<li>Grown-ups stay</li></ul></div>
    ${full ? notice("Sorry, this session is full or no longer taking bookings.", "warn") : `
    <form id="gForm" class="pcard" novalidate>
      <input type="text" name="website" tabindex="-1" autocomplete="off" class="hp" aria-hidden="true">
      <h2>1. Your contact details</h2>
      <div class="form-grid">
        <div class="field"><label for="g-email">Email</label><span class="hint">We'll send your confirmation here.</span><input type="email" id="g-email" name="email" autocomplete="email"></div>
        <div class="field"><label for="g-phone">Phone</label><span class="hint">In case we need to reach you on the day.</span><input type="tel" id="g-phone" name="phone" autocomplete="tel"></div>
        <div class="field field--wide"><label for="g-name">Name for the booking (optional)</label><input type="text" id="g-name" name="name" autocomplete="name"></div>
      </div>
      <h2>2. How many places?</h2>
      <div class="form-grid">
        <div class="field"><label for="g-adults">Adults</label><select id="g-adults" name="adults">${opts(1)}</select></div>
        <div class="field"><label for="g-children">Children</label><select id="g-children" name="children">${opts(1)}</select></div>
      </div>
      <p id="gTotal" class="lead"></p>
      <label class="check"><input type="checkbox" id="g-ages_ok" name="ages_ok"> <span>The children coming are aged ${esc(a.age_text)}.</span></label>
      <label class="check"><input type="checkbox" id="g-adult_18" name="adult_18"> <span>I'm 18 or over and I'll stay with the children I'm booking for.</span></label>
      <label class="check"><input type="checkbox" name="marketing"> <span>Keep me updated about future events (optional — you can unsubscribe any time).</span></label>
      <p class="hint">We use your email and phone number only for this booking (see our <a href="/privacy">privacy notice</a>).</p>
      <div class="btn-row"><a class="btn btn--ghost" href="/book">Back</a><button class="btn btn--orange" type="submit">Book</button></div>
    </form>`}</div>`;
  if (full) return;
  const f = $("#gForm");
  const total = () => {
    const t = (+f.adults.value) * a.adult_price_pence + (+f.children.value) * a.child_price_pence;
    $("#gTotal").textContent = t ? `Total: ${money(t)} — you'll pay by card on the next page.` : "Free — we'll email you a link to confirm your places.";
  };
  f.onchange = total; total();
  const idem = crypto.randomUUID ? crypto.randomUUID() : String(Date.now());
  f.onsubmit = async (e) => {
    e.preventDefault();
    const body = { session_id: +sid, email: f.email.value, phone: f.phone.value, name: f.name.value, adults: +f.adults.value,
      children: +f.children.value, ages_ok: f.ages_ok.checked, adult_18: f.adult_18.checked, marketing: f.marketing.checked,
      website: f.website.value, idempotency_key: idem };
    try {
      const r = await busy($("button[type=submit]", f), () => api("/api/book/guest", body));
      if (r.redirect) return location.assign(r.redirect);
      root().innerHTML = `<div class="portal__narrow"><h1>Check your email</h1>
        <p class="lead">We've sent a link to <strong>${esc(body.email)}</strong>. Click it within ${r.minutes || 30} minutes to confirm your places — until then they're held for you.</p>
        <p>Can't see it? Check your spam folder.</p></div>`;
    } catch (x) { showErrors(f, x.data.errors || {}, "g", x.message); }
  };
}

export async function guestConfirm() {
  const hash = new URLSearchParams(location.hash.slice(1));
  const token = hash.get("t") || hash.get("m");
  history.replaceState(null, "", location.pathname);  // the link works once; don't keep it in history
  if (!token) { root().innerHTML = notice("This link is incomplete — please use the whole link from your email.", "err"); return; }
  root().innerHTML = `<div class="portal__narrow"><h1>${hash.get("m") ? "Confirm your subscription" : "Confirm your booking"}</h1>
    <p class="lead">Press the button to confirm.</p><button class="btn btn--orange" id="go">Confirm</button></div>`;
  $("#go").onclick = async () => {
    try {
      const r = await busy($("#go"), () => api("/api/book/guest/confirm", { token }));
      root().innerHTML = `<div class="portal__narrow"><h1>Thank you!</h1>${notice(esc(r.message), "ok")}<p><a href="/whats-on">See what else is on</a></p></div>`;
    } catch (x) { root().innerHTML = `<div class="portal__narrow">${notice(esc(x.message), "err")}<p><a href="/book">Book again</a></p></div>`; }
  };
}

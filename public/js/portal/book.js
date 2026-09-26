/* Booking pages: Book activities (mockup 2), review & pay, the "done" page,
   and My bookings. Prices, places and eligibility always come from the
   server; the basket in sessionStorage is only a list of choices. */
import { $, $$, api, busy, esc, go, loadMe, notice, params, renderNav, requireSignIn, root, state } from "./core.js";

const BASKET = "hah_basket";
const money = (p) => "£" + ((p || 0) / 100).toFixed(2).replace(/\.00$/, "");
const dayParts = (iso) => {
  const d = new Date(iso + "T12:00:00");
  return { wd: d.toLocaleDateString("en-GB", { weekday: "short" }), day: d.getDate(),
    mon: d.toLocaleDateString("en-GB", { month: "short" }) };
};
const niceDate = (iso) => new Date(iso + "T12:00:00").toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" });
const LEVEL_FOR = { full: "full", short: "short", adult: "adult", guest: "guest" };

function loadBasket() {
  try { return JSON.parse(sessionStorage.getItem(BASKET) || "[]"); } catch (_) { return []; }
}
function saveBasket(b) {
  try { sessionStorage.setItem(BASKET, JSON.stringify(b)); } catch (_) { /* private mode: basket lives on this page only */ }
}
const itemKey = (it) => it.participant ? `${it.session_id}:${it.participant}` : `${it.session_id}:party`;

/* ---------------- Book activities ---------------- */
export async function book() {
  await loadMe();
  renderNav("book");
  let data;
  const load = async () => { data = await api("/api/book/catalogue"); };
  await load();
  if (!data.live && !data.activities.length) {
    root().innerHTML = `<h1>Book activities</h1>${notice(esc(data.message || "Online booking opens soon. Keep an eye on our What's On page."), "")}
      <p><a class="btn btn--ghost" href="/whats-on">See What's On</a></p>`;
    return;
  }
  let basket = loadBasket();
  const kids = data.people.filter(p => !p.is_account_holder);
  const holder = data.people.find(p => p.is_account_holder);
  let chosen = new Set(data.people.map(p => p.ref));
  let tab = params.get("tab") || "all";
  let query = "";
  const only = params.get("activity");

  const draw = () => {
    const cats = [{ key: "all", name: "All" }, ...data.categories.filter(c => data.activities.some(a => a.category_key === c.key))];
    const list = data.activities.filter(a => (tab === "all" || a.category_key === tab) && (!only || a.slug === only) &&
      (!query || (a.title + " " + (a.summary || "")).toLowerCase().includes(query)));
    const chosenPeople = data.people.filter(p => chosen.has(p.ref));
    root().innerHTML = `<h1>Book activities</h1>
      ${data.live ? "" : notice("Staff preview — families can't book yet.", "warn")}
      ${data.signed_in ? `<p class="lead" id="whoLine">Booking for ${chosenPeople.length ? chosenPeople.map(p => `<strong>${esc(p.first_name)}</strong>${p.is_account_holder ? "" : " (" + p.age + ")"}`).join(" and ") : "nobody yet"}
          · <button class="linklike" id="changeWho">Change</button></p>
          <div id="whoBox" hidden class="pcard"><fieldset class="field"><legend>Who's coming?</legend><div class="pills">
            ${data.people.map(p => `<label class="pill-opt"><input type="checkbox" name="who" value="${esc(p.ref)}"${chosen.has(p.ref) ? " checked" : ""}> ${esc(p.first_name)}</label>`).join("")}
          </div></fieldset>${kids.length || holder ? "" : `<p><a href="/account/family/new?next=/book">Add a child</a> to book for them.</p>`}</div>`
        : `<p class="lead">Have an account? <a href="/login?next=/book">Sign in</a> to see what each child can book. New here? <a href="/register?next=/book">Create an account</a>.</p>`}
      ${data.signed_in && !data.people.length ? notice(`Add ${""}the people you're booking for first. <a href="/account/family/new?next=/book">Add a child</a>`, "warn") : ""}
      <div class="book-tools no-print"><div class="seg" role="group" aria-label="Type of activity">${cats.map(c =>
        `<button type="button" data-tab="${esc(c.key)}" aria-pressed="${c.key === tab}">${esc(c.name)}</button>`).join("")}</div>
        <label class="book-search"><span class="sr-only">Search activities</span>
          <input type="search" id="q" placeholder="Search activities" value="${esc(query)}"></label></div>
      ${only ? `<p><a href="/book">← All activities</a></p>` : ""}
      <div id="acts">${list.map(a => activityBlock(a, chosenPeople)).join("") || `<p>No activities match.</p>`}</div>
      <div class="sticky-bar" id="bar" ${basket.length ? "" : "hidden"}><div class="container sticky-bar__inner">
        <span><strong id="count">${basket.length}</strong> place${basket.length === 1 ? "" : "s"} selected</span>
        <span><button class="btn btn--ghost btn--sm" id="clear">Clear</button> <a class="btn btn--orange" href="/book/review">Continue</a></span></div></div>`;
    wire();
  };

  const activityBlock = (a, people) => {
    const tags = [a.age_text ? `Ages ${esc(a.age_text)}` : "", a.parent_must_stay ? "Parent stays" : "",
      a.haf_only ? "Free (HAF)" : "", a.requires_approval ? "Places confirmed by staff" : "", esc(a.centre)].filter(Boolean);
    return `<section class="act pcard" aria-labelledby="act-${a.id}">
      <div class="act__head"><div><h2 id="act-${a.id}">${esc(a.title)}</h2>
        ${a.summary ? `<p class="pcard__intro">${esc(a.summary)}</p>` : ""}
        <ul class="chips">${tags.map(t => `<li>${t}</li>`).join("")}</ul></div></div>
      ${a.haf_only && data.signed_in ? `<p class="hint">HAF places are for children who get benefits-related free school meals. If you're not sure, choose the day anyway — we'll check and confirm.</p>` : ""}
      <div class="act__sessions">${a.sessions.map(s => sessionRow(a, s, people)).join("")}</div></section>`;
  };

  const sessionRow = (a, s, people) => {
    const d = dayParts(s.date);
    const avail = s.state === "full" ? `<span class="avail avail--full">Full</span>`
      : s.state === "waitlist" ? `<span class="avail avail--full">Full · waiting list</span>`
      : s.state === "not_open_yet" ? `<span class="avail">Opens ${esc(new Date(s.opens_at).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" }))}</span>`
      : s.state === "few" ? `<span class="avail avail--few">Only ${s.places_left} left</span>`
      : `<span class="avail avail--ok">${s.places_left} places</span>`;
    const price = a.haf_only ? "Free" : s.price_pence ? money(s.price_pence) : "Free";
    let action;
    if (!data.signed_in) {
      action = a.level === "guest"
        ? `<a class="btn btn--sm btn--orange" href="/book/${esc(a.slug)}/guest?session=${s.id}">Book</a>`
        : `<a class="btn btn--sm btn--ghost" href="/register?for=${LEVEL_FOR[a.level]}&next=${encodeURIComponent("/book?activity=" + a.slug)}">Sign in or register to book</a>`;
    } else if (s.state === "full" || s.state === "not_open_yet") {
      action = "";
    } else if (a.level === "guest") {
      const inB = basket.find(it => it.session_id === s.id && !it.participant);
      action = `<button class="btn btn--sm ${inB ? "btn--navy" : "btn--orange"}" data-party="${s.id}" data-max="${a.max_party_size}">${inB ? "Selected ✓" : "Choose places"}</button>`;
    } else {
      const who = people.filter(p => s.eligibility && s.eligibility[p.ref]);
      const ok = who.filter(p => s.eligibility[p.ref].ok);
      const sel = ok.filter(p => basket.some(it => it.session_id === s.id && it.participant === p.ref));
      const label = s.state === "waitlist" ? "Join waiting list" : "Select";
      action = ok.length ? `<button class="btn btn--sm ${sel.length ? "btn--navy" : "btn--orange"}" data-sel="${s.id}" aria-pressed="${sel.length > 0}">${sel.length ? "Selected ✓" : label}</button>` : "";
      const notes = who.filter(p => !s.eligibility[p.ref].ok).map(p => {
        const e = s.eligibility[p.ref];
        const fix = e.code === "level" || e.code === "haf" ? ` <a href="/account/family/${esc(p.ref)}?for=${a.level}&next=${encodeURIComponent("/book?activity=" + a.slug)}">Complete details</a>` : "";
        return `<span class="elig">${e.status ? esc(p.first_name) + ": " + esc(e.status) : esc(e.message)}${fix}</span>`;
      });
      if (ok.length > 1 && s.places_left && s.places_left < ok.length && s.state !== "waitlist")
        notes.push(`<span class="elig">Only ${s.places_left} left: choose fewer children or join the waiting list.</span>`);
      if (notes.length) action += `<div class="elig-notes">${notes.join("")}</div>`;
    }
    return `<div class="session-row" data-session="${s.id}">
      <div class="session-row__date"><small>${d.wd}</small><strong>${d.day}</strong><small>${d.mon}</small></div>
      <div><h3>${esc(s.start_time)}–${esc(s.end_time)}${s.theme ? " · " + esc(s.theme) : ""}</h3><p>${price}</p></div>
      ${avail}<div class="session-row__act">${action}</div></div>`;
  };

  const updateBar = () => {
    saveBasket(basket);
    const bar = $("#bar"); bar.hidden = !basket.length;
    $("#count").textContent = basket.length;
  };

  const wire = () => {
    $$("[data-tab]").forEach(b => b.onclick = () => { tab = b.dataset.tab; draw(); });
    const q = $("#q"); q.oninput = () => { query = q.value.trim().toLowerCase(); const pos = q.selectionStart; draw(); const n = $("#q"); n.focus(); n.setSelectionRange(pos, pos); };
    const cw = $("#changeWho");
    if (cw) cw.onclick = () => { $("#whoBox").hidden = !$("#whoBox").hidden; };
    $$("input[name=who]").forEach(i => i.onchange = () => {
      chosen = new Set($$("input[name=who]:checked").map(x => x.value));
      basket = basket.filter(it => !it.participant || chosen.has(it.participant));
      saveBasket(basket); draw(); $("#whoBox").hidden = false;
    });
    $$("[data-sel]").forEach(b => b.onclick = () => {
      const sid = +b.dataset.sel;
      const a = data.activities.find(x => x.sessions.some(s => s.id === sid));
      const s = a.sessions.find(x => x.id === sid);
      const ok = data.people.filter(p => chosen.has(p.ref) && s.eligibility && s.eligibility[p.ref] && s.eligibility[p.ref].ok);
      const has = basket.some(it => it.session_id === sid);
      basket = basket.filter(it => it.session_id !== sid);
      if (!has) ok.forEach(p => basket.push({ session_id: sid, participant: p.ref }));
      draw();
      updateBar();
      const again = $(`[data-sel="${sid}"]`); if (again) again.focus();
    });
    $$("[data-party]").forEach(b => b.onclick = () => partyDialog(+b.dataset.party, +b.dataset.max));
    const clr = $("#clear"); if (clr) clr.onclick = () => { basket = []; saveBasket(basket); draw(); };
  };

  const partyDialog = (sid, max) => {
    const existing = basket.find(it => it.session_id === sid && !it.participant) || { participants: [], adults: 1 };
    const d = document.createElement("dialog"); d.className = "pcard";
    d.innerHTML = `<form method="dialog"><h2>Who's coming?</h2>
      <div class="pills">${kids.map(p => `<label class="pill-opt"><input type="checkbox" name="kid" value="${esc(p.ref)}"${existing.participants.includes(p.ref) ? " checked" : ""}> ${esc(p.first_name)}</label>`).join("")}</div>
      <div class="field"><label for="adults">Adults coming</label><select id="adults">${Array.from({ length: Math.min(max, 6) + 1 }, (_, i) => `<option${i === existing.adults ? " selected" : ""}>${i}</option>`).join("")}</select></div>
      <div class="btn-row"><button type="button" class="btn btn--ghost" value="remove">Remove</button><button class="btn btn--orange">Save</button></div></form>`;
    document.body.appendChild(d);
    $("[value=remove]", d).onclick = () => { basket = basket.filter(it => !(it.session_id === sid && !it.participant)); d.close(); d.remove(); draw(); updateBar(); };
    $("form", d).onsubmit = (e) => {
      e.preventDefault();
      const participants = $$("input[name=kid]:checked", d).map(i => i.value), adults = +$("#adults", d).value;
      basket = basket.filter(it => !(it.session_id === sid && !it.participant));
      if (participants.length || adults) basket.push({ session_id: sid, participants, adults });
      d.close(); d.remove(); draw(); updateBar();
    };
    d.showModal();
  };

  draw();
}

/* ---------------- Review & pay ---------------- */
const OUTCOME = {
  pay: ["Book & pay", "status--info"], free: ["Confirmed straight away", "status--ok"],
  approval: ["We'll check and confirm", "status--todo"], waitlist: ["Waiting list", "status--muted"],
  blocked: ["Can't book yet", "status--bad"],
};
const PAY = {
  card: ["Pay by card now", "Secure payment with Stripe. Your places are held while you pay."],
  pay_later: ["Pay later", "We'll email an invoice. Pay by card from your account, bank transfer or vouchers before the due date."],
  voucher: ["Childcare vouchers or Tax-Free Childcare", "We'll hold the places and confirm once we've set up the payment with you."],
};

export async function review() {
  await requireSignIn();
  renderNav("book");
  let basket = loadBasket();
  const idem = crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random();
  const cancelled = params.get("cancelled");
  if (!basket.length) {
    root().innerHTML = `<h1>Your booking</h1>${cancelled ? notice("Payment cancelled — nothing was booked.", "warn") : ""}<p>You haven't chosen anything yet.</p><p><a class="btn btn--orange" href="/book">Book activities</a></p>`;
    return;
  }
  let q;
  const draw = () => {
    const pays = q.pay_options;
    const lines = q.lines;
    root().innerHTML = `<h1>Your booking</h1>
      ${cancelled ? notice("Payment cancelled — nothing was charged. Your choices are below if you'd like to try again.", "warn") : ""}
      ${q.blocked ? notice("Some places can't be booked yet — see the notes and remove them or fix the details.", "err") : ""}
      <div class="pcard"><table class="table-plain"><thead><tr><th>Session</th><th>Who</th><th>What happens</th><th class="num">Price</th><th><span class="sr-only">Remove</span></th></tr></thead>
      <tbody>${lines.map((l, i) => `<tr>
        <td><strong>${esc(l.activity)}</strong><br>${esc(niceDate(l.date))}, ${esc(l.start_time)}–${esc(l.end_time)}${l.theme ? " · " + esc(l.theme) : ""}</td>
        <td>${esc(l.who)}</td>
        <td><span class="status ${OUTCOME[l.outcome][1]}">${OUTCOME[l.outcome][0]}</span>
          ${l.problems.map(p => `<br><small>${esc(p.message)}</small>`).join("")}
          ${l.fix_url ? `<br><a href="${esc(l.fix_url + (l.fix_url.includes("?") ? "&" : "?") + "next=/book/review")}">Fix this</a>` : ""}</td>
        <td class="num">${l.funding === "haf" ? "Free (HAF)" : l.price_pence ? money(l.price_pence) : "Free"}</td>
        <td><button class="linklike" data-rm="${i}">Remove</button></td></tr>`).join("")}</tbody>
      <tfoot><tr><td colspan="3">Total to pay${q.counts.waitlist || q.counts.approval ? " (waiting list and approval places are paid for once confirmed)" : ""}</td><td class="num"><strong>${money(q.total_pence)}</strong></td><td></td></tr>
      ${q.credit_pence ? `<tr><td colspan="3">Your account credit</td><td class="num">−${money(q.credit_pence)}</td><td></td></tr>
        <tr><td colspan="3">To pay now</td><td class="num"><strong>${money(q.due_now_pence)}</strong></td><td></td></tr>` : ""}</tfoot></table></div>
      <form id="payForm" class="pcard" novalidate>
        ${q.needs_payment ? `<fieldset class="field"><legend>How would you like to pay?</legend>${pays.map((p, i) =>
          `<label class="check"><input type="radio" name="pay" value="${p}"${i === 0 ? " checked" : ""}> <span><strong>${p === "card" && !q.due_now_pence ? "Use my account credit" : PAY[p][0]}</strong><br><small>${PAY[p][1]}</small></span></label>`).join("")}</fieldset>`
        : `<p>${q.counts.waitlist && !q.counts.free && !q.counts.approval ? "Nothing to pay — you'll join the waiting list." : "Nothing to pay now."}</p>`}
        <label class="check"><input type="checkbox" id="terms"> <span>I agree to the <a href="/booking-terms" target="_blank" rel="noopener">booking terms</a> (including cancellations and refunds).</span></label>
        <p class="field__error" id="payErr" hidden></p>
        <div class="btn-row"><a class="btn btn--ghost" href="/book">← Add more</a>
          <button class="btn btn--orange" type="submit"${q.blocked ? " disabled" : ""}>${q.needs_payment && pays[0] === "card" && q.due_now_pence ? "Continue to payment" : "Confirm booking"}</button></div>
      </form>`;
    $$("[data-rm]").forEach(b => b.onclick = async () => {
      const l = lines[+b.dataset.rm];
      basket = basket.filter(it => !(it.session_id === l.session_id && (it.participant || null) === (l.participant || null)));
      saveBasket(basket);
      if (!basket.length) return go("/book");
      await quote(); draw();
    });
    $("#payForm").onsubmit = async (e) => {
      e.preventDefault();
      const err = $("#payErr"); err.hidden = true;
      if (!$("#terms").checked) { err.textContent = "Please tick to accept the booking terms."; err.hidden = false; $("#terms").focus(); return; }
      const mode = ($("input[name=pay]:checked") || {}).value || null;
      try {
        const d = await busy($("button[type=submit]", e.target), () => api("/api/book/confirm",
          { items: basket, pay_mode: mode, accept_terms: true, idempotency_key: idem }));
        if (d.redirect) { location.assign(d.redirect); return; }
        saveBasket([]);
        go("/book/done?c=" + encodeURIComponent(d.checkout));
      } catch (x) {
        if (x.data && x.data.quote) { q = x.data.quote; draw(); }
        const e2 = $("#payErr"); e2.textContent = x.message; e2.hidden = false;
      }
    };
  };
  const quote = async () => { q = await api("/api/book/quote", { items: basket }); };
  try { await quote(); }
  catch (e) {
    root().innerHTML = `<h1>Your booking</h1>${notice(esc(e.message), "err")}<p><button class="btn btn--ghost" id="reset">Start again</button></p>`;
    $("#reset").onclick = () => { saveBasket([]); go("/book"); };
    return;
  }
  draw();
}

/* ---------------- after booking / paying ---------------- */
export async function done() {
  await loadMe();
  if (state.me) renderNav("bookings");
  const ref = params.get("c");
  const sid = params.get("session_id");
  let tries = 0;
  const poll = async () => {
    const d = await api(`/api/book/checkout/${encodeURIComponent(ref)}/status${sid ? "?session_id=" + encodeURIComponent(sid) : ""}`);
    if (d.status === "awaiting_payment" || d.status === "creating") {
      root().innerHTML = `<h1>Just a moment…</h1><p class="lead">We're confirming your payment with the bank.</p>`;
      if (++tries < 20) return setTimeout(() => poll().catch(showErr), 1500);
      root().innerHTML = `<h1>Payment still processing</h1>${notice("We haven't heard from the bank yet. We'll email you as soon as it's confirmed — there's no need to pay again.", "warn")}<p><a href="/account/bookings">My bookings</a></p>`;
      return;
    }
    saveBasket([]);
    if (d.status === "expired" || d.status === "failed") {
      root().innerHTML = `<h1>Payment didn't go through</h1>${notice("Nothing was charged and the places have been released.", "warn")}<p><a class="btn btn--orange" href="/book">Book activities</a></p>`;
      return;
    }
    const by = (s) => d.bookings.filter(b => b.status === s);
    root().innerHTML = `<h1>Thank you!</h1><p class="lead">We've emailed you the details.</p>
      ${groupList("Confirmed", by("confirmed"))}${groupList("Waiting for approval", by("pending_approval"), "We'll email you once we've checked these.")}
      ${groupList("On the waiting list", by("waitlisted"), "We'll email and text you if a place comes up.")}
      <p><a class="btn btn--orange" href="/account/bookings">My bookings</a> <a class="btn btn--ghost" href="/book">Book more</a></p>`;
  };
  const showErr = (e) => { root().innerHTML = notice(esc(e.message), "err"); };
  await poll().catch(showErr);
}

const groupList = (title, list, note) => list.length ? `<div class="pcard"><h2>${esc(title)}</h2>${note ? `<p class="pcard__intro">${esc(note)}</p>` : ""}
  <ul>${list.map(b => `<li>${b.who ? esc(b.who.first_name) + " — " : ""}${esc(b.activity)}, ${esc(niceDate(b.date))} ${esc(b.start_time)}–${esc(b.end_time)}</li>`).join("")}</ul></div>` : "";

/* ---------------- My bookings ---------------- */
const CHIP = { confirmed: "status--ok", pending_approval: "status--todo", pending_payment: "status--todo",
  waitlisted: "status--muted", offered: "status--info", cancelled: "status--bad" };

export async function myBookings() {
  await requireSignIn();
  renderNav("bookings");
  let d;
  const draw = async () => {
    d = await api("/api/account/bookings");
    const byDate = {};
    d.upcoming.filter(b => b.status !== "offered").forEach(b => (byDate[b.date] = byDate[b.date] || []).push(b));
    const unpaid = d.invoices.filter(i => i.balance_pence > 0);
    root().innerHTML = `<h1>My bookings</h1>
      ${d.credit_pence ? notice(`You have <strong>${money(d.credit_pence)}</strong> account credit — it's used automatically on your next booking.`, "ok") : ""}
      ${d.offers.map(b => `<div class="pcard pcard--todo"><h2>A place has come up!</h2>
        <p><strong>${esc(b.activity)}</strong>, ${esc(niceDate(b.date))} ${esc(b.start_time)}–${esc(b.end_time)}${b.who ? " for " + esc(b.who.first_name) : ""}.
        Held for you until <strong>${esc(new Date(b.offer_expires_at).toLocaleString("en-GB", { weekday: "short", hour: "numeric", minute: "2-digit" }))}</strong>.</p>
        ${b.price_pence ? `<div class="pills">${[d.card_payments ? "card" : null, "voucher"].filter(Boolean).map((p, i) =>
          `<label class="pill-opt"><input type="radio" name="pm-${esc(b.ref)}" value="${p}"${i === 0 ? " checked" : ""}> ${esc(PAY[p][0])}</label>`).join("")}</div>` : ""}
        <div class="pcard__actions"><button class="btn btn--orange" data-accept="${esc(b.ref)}">Accept${b.price_pence ? " · " + money(b.price_pence) : ""}</button>
          <button class="btn btn--ghost" data-decline="${esc(b.ref)}">No thanks</button></div></div>`).join("")}
      ${unpaid.length ? `<div class="pcard"><h2>To pay</h2><table class="table-plain"><tbody>${unpaid.map(i => `<tr>
        <td><a href="/account/invoices/${esc(i.number)}">${esc(i.number)}</a>${i.overdue ? ` <span class="status status--bad">Overdue</span>` : ""}</td>
        <td>Due ${esc(niceDate(i.due_date))}</td><td class="num"><strong>${money(i.balance_pence)}</strong></td>
        <td><a class="btn btn--sm btn--orange" href="/account/invoices/${esc(i.number)}">View & pay</a></td></tr>`).join("")}</tbody></table></div>` : ""}
      <h2>Coming up</h2>
      ${Object.keys(byDate).length ? Object.entries(byDate).map(([date, list]) => `<h3 class="date-head">${esc(new Date(date + "T12:00:00").toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" }))}</h3>
        ${list.map(b => `<div class="session-row"><div class="session-row__date"><small>${esc(b.start_time)}</small></div>
          <div><h3>${esc(b.activity)}${b.theme ? " · " + esc(b.theme) : ""}</h3>
            <p>${b.who ? esc(b.who.first_name) : b.places + " place" + (b.places === 1 ? "" : "s")} · ${esc(b.start_time)}–${esc(b.end_time)}
            ${b.price_pence ? " · " + money(b.price_pence) : b.funding === "haf" ? " · HAF" : ""}${b.invoice && b.invoice.balance_pence > 0 ? " · unpaid" : ""}
            ${b.position ? " · #" + b.position + " on the waiting list" : ""}${b.attendance === "absent_notified" ? " · we know they can't come" : ""}</p></div>
          <span class="status ${CHIP[b.status] || ""}">${esc(b.status_text)}</span>
          <div>${b.status === "pending_payment" ? "" : `<button class="btn btn--sm btn--ghost" data-cancel="${esc(b.ref)}">${b.status === "waitlisted" ? "Leave list" : "Cancel"}</button>`}</div></div>`).join("")}`).join("")
        : `<p>No upcoming bookings. <a href="/book">Book activities</a></p>`}
      ${d.invoices.length ? `<h2>Invoices</h2><table class="table-plain"><tbody>${d.invoices.map(i => `<tr><td><a href="/account/invoices/${esc(i.number)}">${esc(i.number)}</a></td>
        <td>${esc(niceDate(i.issue_date))}</td><td class="num">${money(i.total_pence)}</td><td>${i.balance_pence > 0 ? "To pay: " + money(i.balance_pence) : esc({ paid: "Paid", credited: "Credited" }[i.status] || i.status)}</td></tr>`).join("")}</tbody></table>` : ""}
      ${d.past.length ? `<details><summary>Past sessions (${d.past.length})</summary><ul>${d.past.slice().reverse().map(b => `<li>${esc(niceDate(b.date))} — ${esc(b.activity)}${b.who ? " (" + esc(b.who.first_name) + ")" : ""}</li>`).join("")}</ul></details>` : ""}`;
    wire();
  };

  const wire = () => {
    $$("[data-accept]").forEach(b => b.onclick = async () => {
      const ref = b.dataset.accept;
      const pm = ($(`input[name="pm-${CSS.escape(ref)}"]:checked`) || {}).value;
      try {
        const r = await busy(b, () => api(`/api/account/bookings/${encodeURIComponent(ref)}/accept`, { pay_mode: pm }));
        if (r.redirect) return location.assign(r.redirect);
        await draw();
      } catch (x) { alertBox(x.message); }
    });
    $$("[data-decline]").forEach(b => b.onclick = async () => {
      try { await busy(b, () => api(`/api/account/bookings/${encodeURIComponent(b.dataset.decline)}/decline-offer`, {})); await draw(); }
      catch (x) { alertBox(x.message); }
    });
    $$("[data-cancel]").forEach(b => b.onclick = () => cancelDialog(b.dataset.cancel));
  };

  const cancelDialog = async (ref) => {
    const t = await api(`/api/account/bookings/${encodeURIComponent(ref)}/cancel-terms`);
    const d2 = document.createElement("dialog"); d2.className = "pcard";
    d2.innerHTML = `<h2>${t.allowed ? "Cancel this booking?" : t.absence_only ? "Can't come?" : "Can't cancel"}</h2><p>${esc(t.message)}</p>
      <div class="btn-row"><button class="btn btn--ghost" value="close">Keep booking</button>
      ${t.allowed ? `<button class="btn btn--orange" value="cancel">Cancel booking</button>` : t.absence_only ? `<button class="btn btn--orange" value="absent">Tell us they can't come</button>` : ""}</div>`;
    document.body.appendChild(d2);
    d2.onclick = async (e) => {
      const v = e.target.closest("button") && e.target.closest("button").value;
      if (!v) return;
      if (v === "close") { d2.close(); d2.remove(); return; }
      try {
        const r = await api(`/api/account/bookings/${encodeURIComponent(ref)}/${v === "cancel" ? "cancel" : "absence"}`, {});
        d2.close(); d2.remove(); await draw();
        root().insertAdjacentHTML("afterbegin", notice(esc(r.message), "ok"));
      } catch (x) { d2.close(); d2.remove(); alertBox(x.message); }
    };
    d2.showModal();
  };

  const alertBox = (m) => root().insertAdjacentHTML("afterbegin", notice(esc(m), "err"));
  await draw();
}

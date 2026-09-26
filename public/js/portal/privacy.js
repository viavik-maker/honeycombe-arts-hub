/* Your data & communications: news preferences, a copy of your data, and
   deleting your account. Also the page one-click unsubscribe links open. */
import { $, api, busy, carerNote, esc, notice, renderNav, requireSignIn, root } from "./core.js";

export async function privacy() {
  const me = await requireSignIn();
  renderNav("details");
  if (me.carer) { root().innerHTML = `<div class="portal__narrow"><h1>Your data & messages</h1>${carerNote()}<p>To stop your carer access, ask ${esc(me.account.first_name)} or call us on 07932 772905.</p></div>`; return; }
  const p = await api("/api/account/preferences");
  root().innerHTML = `<div class="portal__narrow"><h1>Your data & messages</h1>
    <form id="prefForm" class="pcard"><h2>News from us</h2>
      <p class="pcard__intro">Emails about your bookings (confirmations, changes, invoices) always come — these settings are only about news of future events.</p>
      <label class="check"><input type="checkbox" name="email_news"${p.email_news ? " checked" : ""}> <span>Email me news about events and activities</span></label>
      ${p.has_mobile ? `<label class="check"><input type="checkbox" name="sms_news"${p.sms_news ? " checked" : ""}> <span>Text me news about events and activities</span></label>` : ""}
      <div class="pcard__actions"><button class="btn btn--orange" type="submit">Save</button><span class="pcard__saved" id="prefSaved" hidden>Saved ✓</span></div></form>
    <div class="pcard"><h2>A copy of your data</h2>
      <p>Download everything we hold about you and your family: your details, health information, bookings, invoices, consents and the messages we've sent. We'll ask for your password first.</p>
      <div class="btn-row"><button class="btn btn--orange" data-export="html">Download my data</button>
        <button class="btn btn--ghost" data-export="json">Download as JSON</button></div>
      <p class="form-note">The first is easy to read and print. JSON is for moving your data to another service. Keep the file safe: it includes health details.</p>
      <p class="form-note">Prefer us to send it, or want something explained? <button class="linklike" id="dataBtn">Ask us for a copy</button> and we'll email it within a month.</p></div>
    <div class="pcard"><h2>Delete your account</h2>
      <p>We'll cancel your upcoming bookings and delete your account and your family's details after 14 days (call us before then if you change your mind).
      We have to keep invoices for six years, and any accident records until a child turns 25.</p>
      <button class="btn btn--ghost" id="delBtn">Delete my account…</button></div>
    <p><a href="/privacy">Read our privacy notice</a></p></div>`;
  $("#prefForm").onsubmit = async (e) => {
    e.preventDefault();
    const f = e.target;
    await busy($("button[type=submit]", f), () => api("/api/account/preferences", { email_news: f.email_news.checked, sms_news: f.sms_news ? f.sms_news.checked : false }));
    $("#prefSaved").hidden = false;
  };
  document.querySelectorAll("[data-export]").forEach(b => b.onclick = async () => {
    await busy(b, () => api("/api/account/data-export/check", {}));
    location.assign("/api/account/data-export?format=" + b.dataset.export);
  });
  $("#dataBtn").onclick = async () => {
    const r = await busy($("#dataBtn"), () => api("/api/account/data-request", {}));
    $("#dataBtn").insertAdjacentHTML("afterend", notice(esc(r.message), "ok"));
    $("#dataBtn").remove();
  };
  $("#delBtn").onclick = async () => {
    const d = document.createElement("dialog"); d.className = "pcard";
    d.innerHTML = `<h2>Delete your account?</h2><p>This cancels your upcoming bookings (${esc(me.account.first_name)}, you'll get an email confirming it). You'll be signed out straight away.</p>
      <div class="btn-row"><button class="btn btn--ghost" value="no">Keep my account</button><button class="btn btn--orange" value="yes">Delete my account</button></div>`;
    document.body.appendChild(d);
    d.onclick = async (e) => {
      const v = e.target.closest("button") && e.target.closest("button").value; if (!v) return;
      d.close(); d.remove();
      if (v !== "yes") return;
      try {
        const r = await api("/api/account/delete", {});
        root().innerHTML = `<div class="portal__narrow"><h1>Your account is closing</h1>${notice(`We'll delete your details on <strong>${esc(r.erase_on)}</strong>. We've emailed you to confirm.`, "ok")}<p><a href="/">Back to the website</a></p></div>`;
        document.getElementById("portalNav").hidden = true;
      } catch (x) { root().insertAdjacentHTML("afterbegin", notice(esc(x.message), "err")); }
    };
    d.showModal();
  };
}

export async function unsubscribe() {
  root().innerHTML = `<div class="portal__narrow"><h1>Unsubscribe</h1>
    <p class="lead">Stop getting news emails from Honeycombe Arts Hub? You'll still get emails about anything you've booked.</p>
    <button class="btn btn--orange" id="unsub">Unsubscribe</button></div>`;
  $("#unsub").onclick = async () => {
    try {
      const r = await busy($("#unsub"), () => fetch(location.pathname, { method: "POST" }).then(x => x.json()));
      root().innerHTML = `<div class="portal__narrow"><h1>Done</h1>${notice(esc(r.message || r.error), r.ok ? "ok" : "err")}<p><a href="/">Back to the website</a></p></div>`;
    } catch (_) { root().innerHTML = notice("Something went wrong — please try again.", "err"); }
  };
}

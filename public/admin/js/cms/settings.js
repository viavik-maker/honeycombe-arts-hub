/* Website editing (CMS): Site settings, and the System & backups card.
   Edits change the shared copy (cms.content); nothing goes live until Save & publish (admin.js). */
import { $, $$, api, esc, post, toast } from "../ui.js";

const A = window.HAHAdmin;
const cms = A.cms;

/* ================================================================
   SETTINGS
================================================================ */
function renderSettings() {
  const s = cms.content.settings;
  const root = $("#tab-settings");
  const f = (label, key, hint, type) => `
    <div class="fgroup"><label>${label}</label>
      <input type="${type || "text"}" data-k="${key}" value="${esc(s[key])}">
      ${hint ? `<div class="fhint">${hint}</div>` : ""}</div>`;
  root.innerHTML = `
    <h1>Settings</h1>
    <p class="sub">Site-wide details. Remember to Save &amp; publish.</p>

    <div class="acard"><h2>Announcement bar</h2>
      <label class="fcheck" style="margin-bottom:1em"><input type="checkbox" data-k="announcementOn" ${s.announcementOn ? "checked" : ""}> Show the announcement bar</label>
      <div class="fgroup"><label>Announcement text</label><input type="text" data-k="announcement" value="${esc(s.announcement)}"></div>
    </div>

    <div class="acard"><h2>Contact details</h2>
      ${f("Phone", "phone")}
      ${f("General email", "emailGeneral")}
      ${f("Trustees email", "emailTrustees")}
      ${f("Student & financial support email", "emailSupport")}
      ${f("Address", "address")}
    </div>

    <div class="acard"><h2>Links</h2>
      ${f("Booking portal (CRM)", "bookingUrl", "Where “Book Now” buttons go")}
      ${f("Donations page", "donateUrl")}
      ${f("Volunteer application form", "volunteerUrl")}
      ${f("Seesaw login", "seesawUrl")}
      ${f("Facebook", "facebook")}
      ${f("Instagram", "instagram")}
      ${f("X / Twitter", "twitter")}
      ${f("YouTube", "youtube")}
    </div>

    <div class="acard"><h2>Charity details</h2>
      ${f("Registered charity number", "charityNumber")}
      ${f("Ofsted register number", "ofstedNumber")}
    </div>

    <div class="acard"><h2>Email notifications (optional)</h2>
      <p class="fhint" style="margin-bottom:1em">If your email provider gives you SMTP details, the website can forward contact-form messages straight to your email inbox. Leave blank to just use the Inbox tab here.</p>
      <div class="frow">
        <div class="fgroup"><label>SMTP host</label><input type="text" data-smtp="host" value="${esc(s.smtp.host)}"></div>
        <div class="fgroup"><label>Port</label><input type="number" data-smtp="port" value="${esc(s.smtp.port)}"></div>
      </div>
      <div class="frow">
        <div class="fgroup"><label>Username</label><input type="text" data-smtp="user" value="${esc(s.smtp.user)}"></div>
        <div class="fgroup"><label>Password</label><input type="password" data-smtp="password" value="${esc(s.smtp.password)}"></div>
      </div>
      <div class="fgroup"><label>Send notifications to</label><input type="email" data-smtp="notifyTo" value="${esc(s.smtp.notifyTo)}"></div>
    </div>

    <div class="acard"><h2>System &amp; backups</h2>
      <div id="sysBody"><p class="fhint">Checking…</p></div>
      <button class="abtn abtn--ghost abtn--sm" id="backupBtn" style="margin-top:.8em">Back up now</button>
    </div>`;

  root.addEventListener("input", (e) => {
    if (e.target.dataset.k) {
      s[e.target.dataset.k] = e.target.type === "checkbox" ? e.target.checked : e.target.value;
      cms.dirty();
    }
    if (e.target.dataset.smtp) {
      s.smtp[e.target.dataset.smtp] = e.target.type === "number" ? +e.target.value : e.target.value;
      cms.dirty();
    }
  });
  $("#backupBtn", root).addEventListener("click", async () => {
    try {
      await post("/api/admin/backup-now", {});
      toast("Backup started — it takes a minute or two");
      setTimeout(loadSystem, 4000);
    } catch (e) { toast(e.message, true); }
  });
  loadSystem();
}

/* System & backups card (Settings): read-only status from the server */
async function loadSystem() {
  const body = $("#sysBody");
  if (!body) return;
  const when = (iso) => iso ? new Date(iso).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short", timeZone: "Europe/London" }) : "never";
  try {
    const st = await api("/api/admin/system-status");
    const backupJob = st.jobs.find(j => j.name === "nightly_backup") || {};
    const warn = (text) => `<p class="fhint" style="color:var(--red);font-weight:800">⚠️ ${esc(text)}</p>`;
    body.innerHTML = [
      st.database_ok ? "" : warn("The booking database isn't answering."),
      st.backup_stale ? warn("No successful backup in the last 26 hours.") : "",
      st.offsite_backup_configured ? "" :
        warn("Off-site backup isn't set up yet — required before real family data is stored (see README → Backups)."),
      `<table class="table"><tbody>
        <tr><td>Last backup</td><td>${esc(when(backupJob.last_finished_at))} — ${esc(backupJob.last_status || "not run yet")}</td></tr>
        <tr><td>Result</td><td>${esc(backupJob.last_error || backupJob.last_detail || "—")}</td></tr>
        <tr><td>Copies on the server</td><td>${esc(st.local_backups.length)} (kept for a week)</td></tr>
        <tr><td>Database size</td><td>${esc(st.database_kb)} KB</td></tr>
        <tr><td>Background jobs</td><td>${st.worker_running ? "running" : "not running"}</td></tr>
        <tr><td>Email</td><td>${st.email.configured ? "set up" + (st.email.source === "admin settings" ? " (using the SMTP box below — please move it to the server settings)" : "") : "not set up"}</td></tr>
        <tr><td>Text messages</td><td>${st.sms.configured ? "set up (" + esc(st.sms.provider) + ")" : "not set up"}</td></tr>
        <tr><td>Waiting to send</td><td>${esc(((st.outbox.counts.email || {}).queued || 0) + ((st.outbox.counts.sms || {}).queued || 0))} · failed: ${esc(((st.outbox.counts.email || {}).failed || 0) + ((st.outbox.counts.sms || {}).failed || 0))}</td></tr>
      </tbody></table>
      ${st.outbox.recent_failures.length ? `<p class="fhint">Recent failures: ${st.outbox.recent_failures.map(f => esc(f.channel + " " + (f.template_key || "") + ": " + (f.error || ""))).join(" · ")}</p>` : ""}
      <p><button class="abtn abtn--ghost abtn--sm" id="testEmailBtn">Send me a test email</button>
         <button class="abtn abtn--ghost abtn--sm" id="testSmsBtn">Send a test text</button></p>`
    ].join("");
    $("#testEmailBtn").onclick = async () => {
      try { const d = await post("/api/admin/test-email", {}); toast("Test email queued to " + d.to); setTimeout(loadSystem, 12000); }
      catch (e) { toast(e.message, true); }
    };
    $("#testSmsBtn").onclick = async () => {
      const to = prompt("UK mobile number to text:");
      if (!to) return;
      try { await post("/api/admin/test-sms", { to }); toast("Test text queued"); setTimeout(loadSystem, 12000); }
      catch (e) { toast(e.message, true); }
    };
  } catch (e) {
    body.innerHTML = `<p class="fhint">Couldn't load system status: ${esc(e.message)}</p>`;
  }
}

cms.register("settings", renderSettings);

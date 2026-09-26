/* Import families from the MagicBooking export, and send activation emails. */
import { $, $$, api, confirmBox, esc, post, table, toast, when } from "./ui.js";

const A = window.HAHAdmin;

A.addTab({
  id: "import", label: "Import families", icon: "📥", perm: "import.run",
  state: { csv: null, filename: "", preview: null, mapping: {}, result: null },
  async render(root) {
    const st = this.state;
    const b = await api("/api/staff/import/batches");
    root.innerHTML = `<h1>Import families</h1>
      <p class="sub">Bring across families from MagicBooking's export. Only names, contact details and children's names and dates of birth
        are imported — families add health details and permissions themselves when they activate their account.</p>
      <div class="acard"><h2>1. Choose the file</h2>
        <input type="file" id="impFile" accept=".csv,text/csv"> ${st.filename ? `<span class="fhint">${esc(st.filename)}</span>` : ""}
        <p class="fhint">A CSV file (in Excel: File → Save As → CSV). One row per child; families with several children appear on several rows with the same email.</p></div>
      ${st.preview ? `<div class="acard"><h2>2. Match the columns</h2>
        <p class="fhint">${st.preview.rows} rows. We've guessed where we can — check each one.</p>
        <div class="frow">${Object.entries(st.preview.fields).map(([k, label]) => `<div class="fgroup"><label>${esc(label)}${st.preview.required.includes(k) ? " *" : ""}</label>
          <select data-map="${k}"><option value="">— not in the file —</option>${st.preview.headers.map(h => `<option${st.mapping[k] === h ? " selected" : ""}>${esc(h)}</option>`).join("")}</select></div>`).join("")}</div>
        <div class="fgroup"><label>Dates are written</label><select id="impOrder"><option value="dmy">Day/month/year (UK)</option><option value="mdy">Month/day/year (US)</option></select></div>
        <p class="fhint">First rows: ${esc(st.preview.sample.slice(0, 2).map(r => r.join(" | ")).join("  ·  "))}</p>
        <p><button class="abtn abtn--ghost" id="impDry">Check (nothing is saved)</button> <button class="abtn abtn--primary" id="impGo"${st.result && st.result.dry_run ? "" : " disabled"}>Import</button></p>
        ${st.result ? `<div class="acard"><h3>${st.result.dry_run ? "Check result" : "Imported"}</h3>
          <p>${st.result.stats.families} new families, ${st.result.stats.children} children · ${st.result.stats.existing_families} already on the new system ·
          ${st.result.stats.errors} rows with problems · ${st.result.stats.no_mobile} families without a mobile number</p>
          ${st.result.problems.length ? table([{ label: "Row", get: p => p.row }, { label: "", get: p => esc(p.result) }, { label: "Why", get: p => esc(p.message || "") }], st.result.problems.slice(0, 50)) : ""}</div>` : ""}
      </div>` : ""}
      <div class="acard"><h2>Imports</h2>${table([
        { label: "When", get: x => esc(when(x.created_at)) + (x.filename ? `<br><span class="fhint">${esc(x.filename)}</span>` : "") },
        { label: "Families", get: x => `${x.stats.families} (${x.activated} activated, ${x.waiting} waiting)` },
        { label: "Status", get: x => esc(x.status.replace("_", " ")) + (x.activation_sent ? `<br><span class="fhint">${x.activation_sent} emails sent</span>` : "") },
        { label: "", cls: "nowrap", get: x => x.status === "committed" ? `<button class="abtn abtn--primary abtn--sm" data-act="${x.id}">Send activation emails</button>
          <button class="abtn abtn--ghost abtn--sm" data-rem="${x.id}">Send reminder</button>
          <a class="abtn abtn--ghost abtn--sm" href="/api/staff/import/${x.id}/problems.csv">Problems CSV</a>
          <button class="abtn abtn--danger abtn--sm" data-rb="${x.id}">Roll back</button>` : "" }], b.batches, { empty: "Nothing imported yet." })}
        <p class="fhint">Send activation emails 3–4 weeks before bookings open. Families prove who they are with a child's date of birth, then choose a password.</p></div>`;
    const re = () => this.render(root);
    $("#impFile", root).onchange = async (e) => {
      const file = e.target.files[0]; if (!file) return;
      if (file.size > 5 * 1024 * 1024) return toast("That file is over 5 MB — split it into smaller files", true);
      st.csv = await file.text(); st.filename = file.name; st.result = null;
      try { st.preview = await post("/api/staff/import/preview", { csv: st.csv }); st.mapping = st.preview.mapping; re(); }
      catch (x) { toast(x.message, true); }
    };
    const mapping = () => { $$("[data-map]", root).forEach(s => { st.mapping[s.dataset.map] = s.value; }); return st.mapping; };
    const run = async (commit) => {
      try {
        st.result = await post("/api/staff/import/run", { csv: st.csv, filename: st.filename, mapping: mapping(), commit, date_order: $("#impOrder", root).value });
        if (commit) { toast("Imported"); st.preview = null; st.csv = null; }
        re();
      } catch (x) { toast(x.message, true); }
    };
    const dry = $("#impDry", root); if (dry) dry.onclick = () => run(false);
    const go = $("#impGo", root);
    if (go) go.onclick = async () => { if (await confirmBox(`Import ${st.result.stats.families} families now? No emails are sent yet.`, "Import")) run(true); };
    $$("[data-act]", root).forEach(x => x.onclick = async () => {
      if (!await confirmBox("Email every family in this import who hasn't had an activation email yet?", "Send")) return;
      try { const r = await post(`/api/staff/import/${x.dataset.act}/activation`, {}); toast(`${r.sent} emails queued`); re(); } catch (e) { toast(e.message, true); }
    });
    $$("[data-rem]", root).forEach(x => x.onclick = async () => {
      if (!await confirmBox("Send a reminder to every family in this import who hasn't activated yet?", "Send reminders")) return;
      try { const r = await post(`/api/staff/import/${x.dataset.rem}/activation`, { reminder: true }); toast(`${r.sent} reminders queued`); re(); } catch (e) { toast(e.message, true); }
    });
    $$("[data-rb]", root).forEach(x => x.onclick = async () => {
      if (!await confirmBox("Remove the families from this import who haven't activated or booked? Families who have are kept.", "Roll back")) return;
      try { const r = await post(`/api/staff/import/${x.dataset.rb}/rollback`, {}); toast(`${r.removed} removed, ${r.kept} kept`); re(); } catch (e) { toast(e.message, true); }
    });
  },
});

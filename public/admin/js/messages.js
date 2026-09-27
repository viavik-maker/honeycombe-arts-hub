/* Messages: email or text families (service messages or news), and the archive. */
import { $, $$, api, can, chip, confirmBox, day, esc, post, table, toast, when } from "./ui.js";

const A = window.HAHAdmin;

A.addTab({
  id: "messages2", label: "Messages", icon: "✉️", perm: "messaging.service",
  state: { view: "compose", draft: { kind: "service", channel: "email", audience: { type: "session" }, subject: "", body: "" }, q: "" },
  async render(root) {
    const st = this.state;
    if (this.options) { st.view = "compose"; Object.assign(st.draft, this.options); this.options = null; }
    const tabs = `<div class="segtabs">${[["compose", "Write a message"], ["sent", "Sent"], ["archive", "Archive"], ...(can("settings.manage") ? [["wording", "Email wording"]] : [])].map(([k, l]) =>
      `<button class="abtn abtn--sm ${k === st.view ? "abtn--honey" : "abtn--ghost"}" data-view="${k}">${l}</button>`).join("")}</div>`;
    if (st.view === "sent") {
      const d = await api("/api/staff/messages");
      root.innerHTML = `<h1>Messages</h1>${tabs}${table([
        { label: "When", get: m => esc(when(m.created_at)) + `<br><span class="fhint">${esc(m.sender || "")}</span>` },
        { label: "What", get: m => chip(m.kind === "marketing" ? "News" : "Service", m.kind === "marketing" ? "info" : "muted") + " " + esc(m.channel) + `<br><strong>${esc(m.subject || m.body.slice(0, 60))}</strong>` },
        { label: "To", get: m => esc(m.audience) + `<br><span class="fhint">${m.state === "scheduled" ? "about " + m.recipients + " people now" : m.recipients + " people"}</span>` },
        { label: "Status", get: m => m.state === "scheduled" ? chip("Scheduled", "info") + ` <span class="fhint">${esc(when(m.send_at))}</span>` +
            ` <button class="abtn abtn--ghost abtn--sm" data-cancel="${m.id}">Cancel</button>`
          : m.state === "cancelled" ? chip("Cancelled", "muted") + (m.problem ? `<br><span class="fhint">${esc(m.problem)}</span>` : "")
          : Object.entries(m.status).map(([k, v]) => `${esc(k)}: ${v}`).join(" · ") || "—" }], d.campaigns, { empty: "Nothing sent yet." })}`;
      $$("[data-cancel]", root).forEach(b => b.onclick = async () => {
        if (!await confirmBox("Cancel this scheduled message? Nobody will get it.", "Cancel message")) return;
        try { await post(`/api/staff/messages/${b.dataset.cancel}/cancel`, {}); toast("Cancelled"); this.render(root); }
        catch (x) { toast(x.message, true); }
      });
    } else if (st.view === "wording") {
      const d = await api("/api/staff/email-wording");
      root.innerHTML = `<h1>Messages</h1>${tabs}
        <p class="fhint">The emails the system sends by itself. The standard wording can't be changed (it includes things we must say), but you can add a short note that appears after the greeting: a reminder about the new start time, say. Leave it empty to remove it.</p>
        ${d.templates.map(t => `<details class="acard"><summary><strong>${esc(t.subject)}</strong> <span class="fhint">${esc(t.key)}</span>${t.intro ? " " + chip("has a note", "info") : ""}</summary>
          <div class="fgroup"><label>Your note (optional, up to ${d.max} characters)</label><textarea data-intro="${esc(t.key)}" maxlength="${d.max}">${esc(t.intro)}</textarea></div>
          <p><button class="abtn abtn--primary abtn--sm" data-save="${esc(t.key)}">Save note</button></p>
          <pre class="msgbody" data-preview="${esc(t.key)}">${esc(t.text)}</pre></details>`).join("")}`;
      $$("[data-save]", root).forEach(b => b.onclick = async () => {
        const key = b.dataset.save;
        try {
          const r = await post("/api/staff/email-wording/" + encodeURIComponent(key), { intro: $(`[data-intro="${key}"]`, root).value });
          $(`[data-preview="${key}"]`, root).textContent = r.text; toast("Saved");
        } catch (x) { toast(x.message, true); }
      });
    } else if (st.view === "archive") {
      const d = await api("/api/staff/messages/archive?q=" + encodeURIComponent(st.q));
      root.innerHTML = `<h1>Messages</h1>${tabs}
        <div class="toolbar"><input type="search" id="arQ" placeholder="Email address or mobile" value="${esc(st.q)}"></div>
        <p class="fhint">Every email and text the site has sent (one-time sign-in links are never kept).</p>
        ${table([{ label: "When", get: m => esc(when(m.created_at)) }, { label: "To", get: m => esc(m.to) },
          { label: "Message", get: m => `<details><summary>${esc(m.subject || m.template || m.channel)}</summary><pre class="msgbody">${esc(m.body)}</pre></details>` },
          { label: "Status", get: m => chip(m.status, m.status === "sent" ? "ok" : m.status === "failed" ? "bad" : "muted") +
            (m.delivery ? " " + chip(m.delivery, m.delivery === "delivered" ? "ok" : ["undelivered", "failed"].includes(m.delivery) ? "bad" : "muted") : "") + (m.error ? `<br><span class="fhint">${esc(m.error)}</span>` : "") }], d.messages)}`;
      $("#arQ", root).onkeydown = (e) => { if (e.key === "Enter") { st.q = e.target.value; this.render(root); } };
    } else {
      const [acts, sess] = await Promise.all([api("/api/staff/activities?tab=current"), api("/api/staff/sessions/upcoming")]);
      const dft = st.draft, au = dft.audience;
      root.innerHTML = `<h1>Messages</h1>${tabs}
        <form id="msgForm" class="acard">
          <div class="frow"><div class="fgroup"><label>Kind</label><select name="kind"><option value="service">About a booking (service message)</option>
            ${can("messaging.marketing") ? `<option value="marketing"${dft.kind === "marketing" ? " selected" : ""}>News (only people who opted in)</option>` : ""}</select></div>
            <div class="fgroup"><label>Send by</label><select name="channel"><option value="email">Email</option><option value="sms"${dft.channel === "sms" ? " selected" : ""}>Text message</option></select></div></div>
          <div class="fgroup" id="audBox"><label>Who to</label><select name="atype">
            <option value="session"${au.type === "session" ? " selected" : ""}>Everyone booked on a session</option>
            <option value="activity"${au.type === "activity" ? " selected" : ""}>Everyone booked on an activity (upcoming)</option>
            <option value="date"${au.type === "date" ? " selected" : ""}>Everyone booked on a day</option>
            <option value="age"${au.type === "age" ? " selected" : ""}>Everyone booked (upcoming) for a child of a certain age</option>
            <option value="accounts"${au.type === "accounts" ? " selected" : ""}>Chosen families</option></select>
            <select name="session" data-for="session" aria-label="Session">${sess.sessions.map(s => `<option value="${s.id}"${+au.session_id === s.id ? " selected" : ""}>${esc(day(s.date))} ${esc(s.start_time)} · ${esc(s.title)}</option>`).join("")}</select>
            <select name="activity" data-for="activity">${acts.activities.map(a => `<option value="${a.id}"${+au.activity_id === a.id ? " selected" : ""}>${esc(a.title)}</option>`).join("")}</select>
            <input type="date" name="date" data-for="date" value="${esc(au.date || "")}">
            <input type="text" name="refs" data-for="accounts" placeholder="Family refs, e.g. A-XXXXXXXX A-YYYYYYYY" value="${esc((au.refs || []).join(" "))}"></div>
          <div class="fgroup" id="ageBox"><label>Child's age (years)</label><span class="agepair">
            <input type="number" name="min_age" min="0" max="99" value="${esc(au.min_age ?? "")}" aria-label="Youngest age"> to
            <input type="number" name="max_age" min="0" max="99" value="${esc(au.max_age ?? "")}" aria-label="Oldest age"></span>
            <p class="fhint" id="ageHint"></p></div>
          <div class="fgroup" data-email><label>Subject</label><input type="text" name="subject" maxlength="150" value="${esc(dft.subject)}"></div>
          <div class="fgroup"><label>Message</label><textarea name="body" class="tall">${esc(dft.body)}</textarea>
            <p class="fhint">Use {{first_name}} for each person's first name. In emails: a blank line starts a new paragraph, **bold**, [link text](https://…).</p></div>
          <div class="frow"><div class="fgroup"><label>When</label><select name="when"><option value="now">Send now</option><option value="later"${dft.send_at_local ? " selected" : ""}>Schedule for later</option></select></div>
            <div class="fgroup" id="laterBox"><label>Send at (UK time)</label><input type="datetime-local" name="send_at_local" value="${esc(dft.send_at_local || "")}">
              <p class="fhint">Who gets it is worked out when it goes, so families who book before then are included.</p></div></div>
          <div id="msgPreview"></div>
          <p><button type="button" class="abtn abtn--ghost" id="prevBtn">Preview</button> <button class="abtn abtn--primary" id="sendBtn" disabled>Send</button></p></form>`;
      const f = $("#msgForm", root);
      let checked = null;
      const sync = () => {
        const nk = f.kind.value;
        $("#audBox", root).hidden = nk === "marketing";
        $$("[data-for]", f).forEach(el => el.hidden = el.dataset.for !== f.atype.value);
        $("[data-email]", f).hidden = f.channel.value !== "email";
        const ageOn = nk === "marketing" || f.atype.value === "age";
        $("#ageBox", root).hidden = !ageOn;
        $("#ageHint", root).textContent = nk === "marketing" ? "Optional: only families with a child this age (leave empty for everyone who opted in)." : "Families with an upcoming booking for a child this age.";
        $("#laterBox", root).hidden = f.when.value !== "later";
        $("#sendBtn", root).textContent = f.when.value === "later" ? "Schedule" : "Send";
        $("#sendBtn", root).disabled = true; checked = null;
      };
      const body = () => ({ kind: f.kind.value, channel: f.channel.value, subject: f.subject.value, body: f.body.value,
        send_at_local: f.when.value === "later" ? f.send_at_local.value : "",
        audience: f.kind.value === "marketing" ? { type: "newsletter", min_age: f.min_age.value, max_age: f.max_age.value }
          : { type: f.atype.value, session_id: f.session.value, activity_id: f.activity.value, date: f.date.value,
            min_age: f.min_age.value, max_age: f.max_age.value, refs: f.refs.value.split(/[\s,]+/).filter(Boolean) } });
      f.oninput = f.onchange = () => { Object.assign(st.draft, body()); sync(); };
      sync();
      $("#prevBtn", root).onclick = async () => {
        try {
          const p = await post("/api/staff/messages/preview", body());
          checked = p.count;
          const pounds = (n) => "£" + (n / 100).toFixed(2);
          const sms = p.segments ? `<p>${p.segments} text${p.segments === 1 ? "" : "s"} per person (${p.texts} in all) · about <strong>${pounds(p.cost_pence)}</strong></p>
            ${p.unicode ? `<p class="fhint">⚠ ${esc(p.odd.join(" "))} ${p.odd.length === 1 ? "isn't a standard text character" : "aren't standard text characters"}, so each text fits 70 characters instead of 160. Swap curly quotes for straight ones (' ") and remove emoji to cut the cost.</p>` : ""}` : "";
          $("#msgPreview", root).innerHTML = `<div class="acard"><p><strong>${p.count} ${p.count === 1 ? "person" : "people"}</strong> · ${esc(p.label)}${f.when.value === "later" ? " (as things stand now)" : ""}</p>${sms}
            ${p.sample.length ? `<p class="fhint">${esc(p.sample.join(", "))}${p.count > p.sample.length ? "…" : ""}</p>` : ""}
            ${p.subject ? `<p><strong>${esc(p.subject)}</strong></p>` : ""}<pre class="msgbody">${esc(p.text)}</pre></div>`;
          $("#sendBtn", root).disabled = !p.count && f.when.value !== "later";
        } catch (x) { toast(x.message, true); }
      };
      f.onsubmit = async (e) => {
        e.preventDefault();
        if (checked == null) return;
        const later = f.when.value === "later";
        if (!await confirmBox(later ? `Schedule this for ${f.send_at_local.value.replace("T", " ")}? It goes to whoever matches then (${checked} now).`
          : `Send this to ${checked} ${checked === 1 ? "person" : "people"} now?`, later ? "Schedule" : "Send")) return;
        try {
          const r = await post("/api/staff/messages/send", Object.assign(body(), { expected: checked }));
          toast(r.scheduled ? "Scheduled" : `Queued for ${r.count}`);
          st.draft = { kind: "service", channel: "email", audience: { type: "session" }, subject: "", body: "" };
          st.view = "sent"; this.render(root);
        } catch (x) { toast(x.message, true); }
      };
    }
    $$("[data-view]", root).forEach(b => b.onclick = () => { st.view = b.dataset.view; this.render(root); });
  },
});

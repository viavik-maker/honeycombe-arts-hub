/* Messages: email or text families (service messages or news), and the archive. */
import { $, $$, api, can, chip, confirmBox, day, esc, post, table, toast, when } from "./ui.js";

const A = window.HAHAdmin;

A.addTab({
  id: "messages2", label: "Messages", icon: "✉️", perm: "messaging.service",
  state: { view: "compose", draft: { kind: "service", channel: "email", audience: { type: "session" }, subject: "", body: "" }, q: "" },
  async render(root) {
    const st = this.state;
    if (this.options) { st.view = "compose"; Object.assign(st.draft, this.options); this.options = null; }
    const tabs = `<div class="segtabs">${[["compose", "Write a message"], ["sent", "Sent"], ["archive", "Archive"]].map(([k, l]) =>
      `<button class="abtn abtn--sm ${k === st.view ? "abtn--honey" : "abtn--ghost"}" data-view="${k}">${l}</button>`).join("")}</div>`;
    if (st.view === "sent") {
      const d = await api("/api/staff/messages");
      root.innerHTML = `<h1>Messages</h1>${tabs}${table([
        { label: "When", get: m => esc(when(m.created_at)) + `<br><span class="fhint">${esc(m.sender || "")}</span>` },
        { label: "What", get: m => chip(m.kind === "marketing" ? "News" : "Service", m.kind === "marketing" ? "info" : "muted") + " " + esc(m.channel) + `<br><strong>${esc(m.subject || m.body.slice(0, 60))}</strong>` },
        { label: "To", get: m => esc(m.audience) + `<br><span class="fhint">${m.recipients} people</span>` },
        { label: "Status", get: m => Object.entries(m.status).map(([k, v]) => `${esc(k)}: ${v}`).join(" · ") }], d.campaigns, { empty: "Nothing sent yet." })}`;
    } else if (st.view === "archive") {
      const d = await api("/api/staff/messages/archive?q=" + encodeURIComponent(st.q));
      root.innerHTML = `<h1>Messages</h1>${tabs}
        <div class="toolbar"><input type="search" id="arQ" placeholder="Email address or mobile" value="${esc(st.q)}"></div>
        <p class="fhint">Every email and text the site has sent (one-time sign-in links are never kept).</p>
        ${table([{ label: "When", get: m => esc(when(m.created_at)) }, { label: "To", get: m => esc(m.to) },
          { label: "Message", get: m => `<details><summary>${esc(m.subject || m.template || m.channel)}</summary><pre class="msgbody">${esc(m.body)}</pre></details>` },
          { label: "Status", get: m => chip(m.status, m.status === "sent" ? "ok" : m.status === "failed" ? "bad" : "muted") + (m.error ? `<br><span class="fhint">${esc(m.error)}</span>` : "") }], d.messages)}`;
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
            <option value="accounts"${au.type === "accounts" ? " selected" : ""}>Chosen families</option></select>
            <select name="session" data-for="session">${sess.sessions.map(s => `<option value="${s.id}"${+au.session_id === s.id ? " selected" : ""}>${esc(day(s.date))} ${esc(s.start_time)} · ${esc(s.title)}</option>`).join("")}</select>
            <select name="activity" data-for="activity">${acts.activities.map(a => `<option value="${a.id}"${+au.activity_id === a.id ? " selected" : ""}>${esc(a.title)}</option>`).join("")}</select>
            <input type="date" name="date" data-for="date" value="${esc(au.date || "")}">
            <input type="text" name="refs" data-for="accounts" placeholder="Family refs, e.g. A-XXXXXXXX A-YYYYYYYY" value="${esc((au.refs || []).join(" "))}"></div>
          <div class="fgroup" data-email><label>Subject</label><input type="text" name="subject" maxlength="150" value="${esc(dft.subject)}"></div>
          <div class="fgroup"><label>Message</label><textarea name="body" class="tall">${esc(dft.body)}</textarea>
            <p class="fhint">Use {{first_name}} for each person's first name. In emails: a blank line starts a new paragraph, **bold**, [link text](https://…).</p></div>
          <div id="msgPreview"></div>
          <p><button type="button" class="abtn abtn--ghost" id="prevBtn">Preview</button> <button class="abtn abtn--primary" id="sendBtn" disabled>Send</button></p></form>`;
      const f = $("#msgForm", root);
      let checked = null;
      const sync = () => {
        const nk = f.kind.value;
        $("#audBox", root).hidden = nk === "marketing";
        $$("[data-for]", f).forEach(el => el.hidden = el.dataset.for !== f.atype.value);
        $("[data-email]", f).hidden = f.channel.value !== "email";
        $("#sendBtn", root).disabled = true; checked = null;
      };
      const body = () => ({ kind: f.kind.value, channel: f.channel.value, subject: f.subject.value, body: f.body.value,
        audience: f.kind.value === "marketing" ? { type: "newsletter" } : { type: f.atype.value, session_id: f.session.value,
          activity_id: f.activity.value, date: f.date.value, refs: f.refs.value.split(/[\s,]+/).filter(Boolean) } });
      f.oninput = f.onchange = () => { Object.assign(st.draft, body()); sync(); };
      sync();
      $("#prevBtn", root).onclick = async () => {
        try {
          const p = await post("/api/staff/messages/preview", body());
          checked = p.count;
          $("#msgPreview", root).innerHTML = `<div class="acard"><p><strong>${p.count} ${p.count === 1 ? "person" : "people"}</strong> · ${esc(p.label)}${p.segments ? ` · ${p.segments} text(s) each` : ""}</p>
            ${p.sample.length ? `<p class="fhint">${esc(p.sample.join(", "))}${p.count > p.sample.length ? "…" : ""}</p>` : ""}
            ${p.subject ? `<p><strong>${esc(p.subject)}</strong></p>` : ""}<pre class="msgbody">${esc(p.text)}</pre></div>`;
          $("#sendBtn", root).disabled = !p.count;
        } catch (x) { toast(x.message, true); }
      };
      f.onsubmit = async (e) => {
        e.preventDefault();
        if (checked == null) return;
        if (!await confirmBox(`Send this to ${checked} ${checked === 1 ? "person" : "people"} now?`, "Send")) return;
        try {
          const r = await post("/api/staff/messages/send", Object.assign(body(), { expected: checked }));
          toast(`Queued for ${r.count}`);
          st.draft = { kind: "service", channel: "email", audience: { type: "session" }, subject: "", body: "" };
          st.view = "sent"; this.render(root);
        } catch (x) { toast(x.message, true); }
      };
    }
    $$("[data-view]", root).forEach(b => b.onclick = () => { st.view = b.dataset.view; this.render(root); });
  },
});

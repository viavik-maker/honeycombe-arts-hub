/* My account, Staff and Audit log areas. */
import { $, $$, api, can, chip, confirmBox, copyText, esc, me, modal, post, table, toast, when } from "./ui.js";

const A = window.HAHAdmin;

/* ---------------- My account ---------------- */
A.addTab({
  id: "account", label: "My account", icon: "👤",
  render(root) {
    const m = me();
    root.innerHTML = `<h1>My account</h1>
      <p class="sub">${esc(m.staff.name)} · ${esc(m.staff.email)} · ${m.staff.roles.map(esc).join(", ")}</p>
      <div class="acard"><h2>Change your password</h2>
        <div class="frow">
          <div class="fgroup"><label for="pwCur">Current password</label><input type="password" id="pwCur" autocomplete="current-password"></div>
          <div class="fgroup"><label for="pwNew">New password (10+ characters)</label><input type="password" id="pwNew" autocomplete="new-password"></div>
        </div>
        <p class="fhint">Changing it signs you out on every other device.</p>
        <button class="abtn abtn--primary" id="pwBtn">Change password</button>
      </div>
      <div class="acard"><h2>Other devices</h2>
        <p class="fhint">Left the admin signed in on a shared computer? Sign out everywhere except here.</p>
        <button class="abtn abtn--ghost" id="revokeBtn">Sign out other devices</button>
      </div>
      <div class="acard"><h2>Morning email</h2>
        <label class="fcheck"><input type="checkbox" id="digest"${m.daily_digest ? " checked" : ""}> Email me each morning with today's sessions and what's waiting in the in-tray</label>
        <p class="fhint">Numbers only: it never includes names or health details.</p>
      </div>
      <div class="acard"><h2>Two-step sign-in</h2>
        <p class="fhint">${m.totp_enabled ? "On — you sign in with a code from your authenticator app." :
          "Not set up."} Lost your phone? Ask an administrator for a reset link.</p>
      </div>`;
    $("#pwBtn", root).onclick = async () => {
      try {
        await post("/api/staff/password", { current: $("#pwCur").value, new: $("#pwNew").value });
        $("#pwCur").value = ""; $("#pwNew").value = "";
        toast("Password changed 🔒");
      } catch (e) { toast(e.message, true); }
    };
    $("#digest", root).onchange = async (e) => {
      try { await post("/api/staff/me/digest", { on: e.target.checked }); m.daily_digest = e.target.checked; toast(e.target.checked ? "You'll get the morning email" : "Morning email off"); }
      catch (x) { toast(x.message, true); }
    };
    $("#revokeBtn", root).onclick = async () => {
      try { const d = await post("/api/staff/sessions/revoke-others", {}); toast(`Signed out ${d.revoked} other session(s)`); }
      catch (e) { toast(e.message, true); }
    };
  },
});

/* ---------------- Staff ---------------- */
function linkBox(link, hours, intro, emailed) {
  return modal(emailed ? "Link emailed" : "Send this link", `<p>${esc(intro)}</p>
    ${emailed ? `<p><strong>We've emailed it to them.</strong> You can also copy it below.</p>` : ""}
    <p class="fhint">It works once, for ${hours} hours. Send it to them directly (email or message) — anyone with the link can set the password.</p>
    <input type="text" readonly value="${esc(link)}" class="linkbox">`,
    async () => { copyText(link); return true; }, "Copy link");
}

A.addTab({
  id: "staff", label: "Staff", icon: "🧑‍🤝‍🧑", perm: "staff.manage",
  async render(root) {
    const d = await api("/api/staff/users");
    const roleNames = d.roles;
    const statusChip = (u) => u.status === "active" ? chip("Active", "ok") : u.status === "invited" ? chip("Invited", "info") : chip("Disabled", "muted");
    root.innerHTML = `<h1>Staff</h1>
      <p class="sub">Everyone who can sign in to the admin, and what they can do.</p>
      <p><button class="abtn abtn--primary" id="inviteBtn">＋ Invite a staff member</button></p>
      ${table([
        { label: "Name", get: u => `<strong>${esc(u.name)}</strong><br><span class="fhint">${esc(u.email)}</span>` },
        { label: "Roles", get: u => u.roles.map(r => esc(roleNames[r] || r)).join("<br>") },
        { label: "Status", get: u => statusChip(u) + (u.totp_enabled ? " " + chip("2FA", "ok") : "") },
        { label: "Last sign-in", get: u => esc(when(u.last_login_at)) },
        { label: "", cls: "nowrap", get: u => u.id === me().staff.id ? `<span class="fhint">you</span>` : `
            <button class="abtn abtn--ghost abtn--sm" data-act="roles" data-id="${u.id}">Roles</button>
            <button class="abtn abtn--ghost abtn--sm" data-act="reset" data-id="${u.id}">${u.status === "invited" ? "New invite link" : "Reset password & 2FA"}</button>
            ${u.status === "disabled"
              ? `<button class="abtn abtn--ghost abtn--sm" data-act="enable" data-id="${u.id}">Re-enable</button>`
              : `<button class="abtn abtn--danger abtn--sm" data-act="disable" data-id="${u.id}">Disable</button>`}` },
      ], d.users)}
      ${can("audit.view") ? `<div class="acard"><h2>Two-step sign-in for everyone</h2>
        <p class="fhint">Required for all staff by default, because staff can see children's details. Only an owner can change this.</p></div>` : ""}`;

    const roleBoxes = (checked) => d.grantable.map(r => `<label class="fcheck"><input type="checkbox" name="role" value="${r}" ${checked.includes(r) ? "checked" : ""}> ${esc(roleNames[r])}</label>`).join("");
    const chosen = (f) => $$("input[name=role]:checked", f).map(i => i.value);

    $("#inviteBtn", root).onclick = async () => {
      const r = await modal("Invite a staff member", `
        <div class="fgroup"><label>Name</label><input name="name" required></div>
        <div class="fgroup"><label>Email</label><input type="email" name="email" required></div>
        <div class="fgroup"><label>Roles</label>${roleBoxes([])}</div>`,
        (f) => post("/api/staff/users/invite", { name: f.name.value, email: f.email.value, roles: chosen(f) }), "Create invite");
      if (r) { await linkBox(r.link, r.expires_hours, "Their account is ready. They'll choose a password and set up two-step sign-in.", r.emailed); this.render(root); }
    };
    root.onclick = async (e) => {
      const b = e.target.closest("[data-act]"); if (!b) return;
      const u = d.users.find(x => x.id === +b.dataset.id);
      try {
        if (b.dataset.act === "roles") {
          const r = await modal(`Roles for ${u.name}`, `<div class="fgroup">${roleBoxes(u.roles)}</div>
            ${u.roles.filter(x => !d.grantable.includes(x)).map(x => `<p class="fhint">Also: ${esc(roleNames[x])} (you can't change this one)</p>`).join("")}`,
            (f) => post(`/api/staff/users/${u.id}/update`, { roles: [...new Set([...chosen(f), ...u.roles.filter(x => !d.grantable.includes(x))])] }));
          if (r) toast("Roles updated");
        } else if (b.dataset.act === "reset") {
          if (!await confirmBox(`Create a new sign-in link for ${u.name}? Their current sign-ins end now${u.status === "active" ? " and they'll set up two-step sign-in again" : ""}.`, "Create link")) return;
          const r = await post(`/api/staff/users/${u.id}/reset`, {});
          await linkBox(r.link, r.expires_hours, `A new link for ${u.name}.`, r.emailed);
        } else if (b.dataset.act === "disable") {
          if (!await confirmBox(`Disable ${u.name}'s account? They're signed out straight away.`, "Disable")) return;
          await post(`/api/staff/users/${u.id}/status`, { status: "disabled" }); toast("Account disabled");
        } else if (b.dataset.act === "enable") {
          await post(`/api/staff/users/${u.id}/status`, { status: "active" }); toast("Account re-enabled");
        }
        this.render(root);
      } catch (x) { toast(x.message, true); }
    };
  },
});

/* ---------------- Audit log ---------------- */
A.addTab({
  id: "audit", label: "Audit log", icon: "🧾", perm: "audit.view",
  async render(root, page) {
    page = page || 1;
    const d = await api("/api/staff/audit?page=" + page);
    root.innerHTML = `<h1>Audit log</h1>
      <p class="sub">Who did what, and when. Entries can't be edited or deleted (they're kept for six years).
        It records which details changed — never the details themselves.</p>
      ${table([
        { label: "When", cls: "nowrap", get: e => esc(when(e.at)) },
        { label: "Who", get: e => esc(e.actor_name || (e.actor_type === "system" ? "System / not signed in" : e.actor_type)) },
        { label: "What", get: e => `<code>${esc(e.action)}</code>` },
        { label: "Details", get: e => e.details ? `<span class="fhint">${esc(JSON.stringify(e.details))}</span>` : "" },
        { label: "IP", get: e => `<span class="fhint">${esc(e.ip || "")}</span>` },
      ], d.entries, { empty: "Nothing logged yet." })}
      <p>${page > 1 ? `<button class="abtn abtn--ghost abtn--sm" id="prevPage">← Newer</button>` : ""}
         ${d.more ? `<button class="abtn abtn--ghost abtn--sm" id="nextPage">Older →</button>` : ""}</p>`;
    const prev = $("#prevPage", root), nxt = $("#nextPage", root);
    if (prev) prev.onclick = () => this.render(root, page - 1);
    if (nxt) nxt.onclick = () => this.render(root, page + 1);
  },
});

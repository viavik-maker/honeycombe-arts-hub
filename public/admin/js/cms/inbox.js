/* Website editing (CMS): Inbox (contact-form messages) and the newsletter list.
   Edits change the shared copy (cms.content); nothing goes live until Save & publish (admin.js). */
import { $, $$, api, esc, post, toast } from "../ui.js";

const A = window.HAHAdmin;
const cms = A.cms;

/* ================================================================
   MESSAGES
================================================================ */
function renderMessages() {
  const root = $("#tab-messages");
  const unread = cms.messages.filter(m => !m.read).length;
  $("#msgBadge").hidden = unread === 0;
  $("#msgBadge").textContent = unread;
  root.innerHTML = `
    <h1>Inbox</h1>
    <p class="sub">Messages sent from the contact form on the website.</p>
    ${cms.messages.length ? "" : '<div class="empty">No messages yet — when families use the contact form, they’ll appear here.</div>'}
    ${cms.messages.map(m => `
      <div class="msg ${m.read ? "" : "unread"}" data-id="${esc(m.id)}">
        <div class="msg__head">
          <strong>${esc(m.name)}</strong>
          <a href="mailto:${esc(m.email)}">${esc(m.email)}</a>
          ${m.phone ? `<span>${esc(m.phone)}</span>` : ""}
          <time>${esc(m.date)}</time>
        </div>
        <p>${esc(m.message)}</p>
        <div class="msg__actions">
          <a class="abtn abtn--honey abtn--sm" href="mailto:${esc(m.email)}?subject=Re:%20your%20message%20to%20Honeycombe%20Arts%20Hub">Reply by email</a>
          <button class="abtn abtn--ghost abtn--sm" data-act="toggle">${m.read ? "Mark unread" : "Mark read"}</button>
          <button class="abtn abtn--danger abtn--sm" data-act="del">Delete</button>
        </div>
      </div>`).join("")}`;
  root.onclick = async (e) => {
    const b = e.target.closest("[data-act]"); if (!b) return;
    const id = b.closest(".msg").dataset.id;
    const m = cms.messages.find(x => x.id === id);
    try {
      if (b.dataset.act === "toggle") {
        const d = await post("/api/admin/messages", { action: "read", id, read: !m.read });
        cms.messages = d.messages;
      }
      if (b.dataset.act === "del" && confirm("Delete this message?")) {
        const d = await post("/api/admin/messages", { action: "delete", id });
        cms.messages = d.messages;
      }
      renderMessages();
    } catch (err) { toast(err.message, true); }
  };
}

/* ================================================================
   SUBSCRIBERS
================================================================ */
function renderSubscribers() {
  const root = $("#tab-subscribers");
  root.innerHTML = `
    <h1>Newsletter</h1>
    <p class="sub">People who signed up through the website footer.</p>
    <div style="display:flex; gap:.6rem; margin-bottom:1.2rem">
      <a class="abtn abtn--primary" href="/api/admin/subscribers.csv">⬇ Download as CSV</a>
      <button class="abtn abtn--ghost" id="copyEmails">Copy all emails</button>
    </div>
    ${cms.subscribers.length ? `
    <table class="table">
      <tr><th>Email</th><th>Signed up</th><th></th></tr>
      ${cms.subscribers.map(s => `
        <tr><td>${esc(s.email)}</td><td>${esc(s.date)}</td>
        <td><button class="icon-btn icon-btn--danger" data-email="${esc(s.email)}" title="Remove">🗑️</button></td></tr>`).join("")}
    </table>` : '<div class="empty">No subscribers yet.</div>'}`;
  $("#copyEmails", root).addEventListener("click", () => {
    navigator.clipboard.writeText(cms.subscribers.map(s => s.email).join(", "))
      .then(() => toast("Email list copied 📋"));
  });
  root.onclick = async (e) => {
    const b = e.target.closest("[data-email]"); if (!b) return;
    if (!confirm(`Remove ${b.dataset.email} from the list?`)) return;
    try {
      const d = await post("/api/admin/subscribers", { action: "delete", email: b.dataset.email });
      cms.subscribers = d.subscribers; renderSubscribers();
    } catch (err) { toast(err.message, true); }
  };
}

cms.register("messages", renderMessages);
cms.register("subscribers", renderSubscribers);

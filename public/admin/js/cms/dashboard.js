/* Website editing (CMS): Dashboard: a welcome, website numbers and quick links.
   Edits change the shared copy (cms.content); nothing goes live until Save & publish (admin.js). */
import { $, $$, api, esc, post, toast } from "../ui.js";

const A = window.HAHAdmin;
const cms = A.cms;

/* ================================================================
   DASHBOARD
================================================================ */
function renderDashboard() {
  if (!cms.content) {
    $("#tab-dashboard").innerHTML = `<h1>Hello, ${esc(A.me().staff.name)}! 👋</h1>
      <p class="sub">Choose an area from the menu on the left.</p>`;
    return;
  }
  const unread = cms.messages.filter(m => !m.read).length;
  $("#tab-dashboard").innerHTML = `
    <h1>Hello, ${esc(A.me().staff.name)}! 👋</h1>
    <p class="sub">Here's how the website is looking today.</p>
    <div class="statgrid">
      <div class="stat"><strong>${cms.content.events.length}</strong><span>events on What's On</span></div>
      <div class="stat"><strong>${unread}</strong><span>unread message${unread === 1 ? "" : "s"}</span></div>
      <div class="stat"><strong>${cms.subscribers.length}</strong><span>newsletter subscribers</span></div>
      <div class="stat"><strong>${cms.content.gallery.length}</strong><span>photos in the gallery</span></div>
    </div>
    <div class="acard">
      <h2>Quick actions</h2>
      <div class="quicklinks">
        <button class="abtn abtn--primary" data-go="events">＋ Add an event</button>
        <button class="abtn abtn--honey" data-go="gallery">＋ Add photos</button>
        <button class="abtn abtn--ghost" data-go="messages">Read messages</button>
        <button class="abtn abtn--ghost" data-go="settings">Edit announcement bar</button>
      </div>
    </div>
    <div class="acard">
      <h2>How it works</h2>
      <p>Make your changes in any tab — nothing goes live until you press <strong>Save &amp; publish</strong> in the bar at the bottom. The live site updates instantly. If you make a mistake, press <strong>Discard</strong> to go back to the last published version.</p>
    </div>`;
  $$("#tab-dashboard [data-go]").forEach(b => b.addEventListener("click", () => {
    $(`#sideNav button[data-tab=${b.dataset.go}]`).click();
  }));
}

cms.register("dashboard", renderDashboard);

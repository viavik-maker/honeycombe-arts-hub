/* Website editing (CMS): Impact statistics and values.
   Edits change the shared copy (cms.content); nothing goes live until Save & publish (admin.js). */
import { $, $$, api, esc, post, toast } from "../ui.js";

const A = window.HAHAdmin;
const cms = A.cms;

/* ================================================================
   MISSION — impact stats & values
================================================================ */
function renderMission() {
  const root = $("#tab-mission");
  root.innerHTML = `
    <h1>Impact &amp; Values</h1>
    <p class="sub">Shown on the homepage and the Mission, Values &amp; Impact page.</p>
    <div class="acard"><h2>Impact statistics</h2><div id="impList"></div>
      <button class="abtn abtn--honey abtn--sm" id="addImp">＋ Add a statistic</button></div>
    <div class="acard"><h2>Values</h2><div id="valList"></div></div>`;

  $("#impList", root).innerHTML = cms.content.impact.map((s, i) => `
    <div data-i="${i}" style="border-bottom:1.5px dashed var(--line); padding:1em 0">
      <div class="frow">
        <div class="fgroup"><label>Number (e.g. 3,624)</label><input type="text" data-k="number" value="${esc(s.number)}"></div>
        <div class="fgroup"><label>Headline</label><input type="text" data-k="label" value="${esc(s.label)}"></div>
      </div>
      <div class="fgroup"><label>Detail</label><textarea data-k="detail">${esc(s.detail)}</textarea></div>
      <button class="abtn abtn--danger abtn--sm" data-act="del">Remove</button>
    </div>`).join("");
  $("#impList", root).addEventListener("input", (e) => {
    const c = e.target.closest("[data-i]"); if (!c || !e.target.dataset.k) return;
    cms.content.impact[+c.dataset.i][e.target.dataset.k] = e.target.value; cms.dirty();
  });
  $("#impList", root).addEventListener("click", (e) => {
    const b = e.target.closest("[data-act=del]"); if (!b) return;
    cms.content.impact.splice(+b.closest("[data-i]").dataset.i, 1); renderMission(); cms.dirty();
  });
  $("#addImp", root).addEventListener("click", () => {
    cms.content.impact.push({ number: "0", label: "", detail: "" }); renderMission(); cms.dirty();
  });

  $("#valList", root).innerHTML = cms.content.values.map((v, i) => `
    <div data-i="${i}" style="border-bottom:1.5px dashed var(--line); padding:1em 0">
      <div class="frow">
        <div class="fgroup"><label>Title</label><input type="text" data-k="title" value="${esc(v.title)}"></div>
        <div class="fgroup"><label>Colour</label>
          <select data-k="color">
            ${["orange", "honey", "sage", "navy"].map(c => `<option ${v.color === c ? "selected" : ""}>${c}</option>`).join("")}
          </select></div>
      </div>
      <div class="fgroup"><label>Text</label><textarea data-k="text">${esc(v.text)}</textarea></div>
    </div>`).join("");
  $("#valList", root).addEventListener("input", (e) => {
    const c = e.target.closest("[data-i]"); if (!c || !e.target.dataset.k) return;
    cms.content.values[+c.dataset.i][e.target.dataset.k] = e.target.value; cms.dirty();
  });
}

cms.register("mission", renderMission);

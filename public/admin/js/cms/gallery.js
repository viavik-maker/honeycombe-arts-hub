/* Website editing (CMS): Gallery photos: captions, categories, order.
   Edits change the shared copy (cms.content); nothing goes live until Save & publish (admin.js). */
import { $, $$, api, esc, post, toast } from "../ui.js";

const A = window.HAHAdmin;
const cms = A.cms;

/* ================================================================
   GALLERY
================================================================ */
function renderGallery() {
  const root = $("#tab-gallery");
  const list = cms.content.gallery;
  const cats = [...new Set(list.map(g => g.category).filter(Boolean))];
  root.innerHTML = `
    <h1>Gallery</h1>
    <p class="sub">Click a caption or category to edit it. New photos appear at the front of the gallery.</p>
    <div style="margin-bottom:1.2rem"><button class="abtn abtn--primary" id="addPhoto">＋ Upload a photo</button></div>
    <div class="ggrid" id="ggrid"></div>`;

  $("#ggrid", root).innerHTML = list.map((g, i) => `
    <div class="gcell" data-i="${i}">
      <img src="${esc(g.src)}" alt="" loading="lazy">
      <div class="gcell__body">
        <input type="text" data-k="caption" value="${esc(g.caption)}" placeholder="Caption">
        <input type="text" data-k="category" value="${esc(g.category)}" placeholder="Category" list="catList">
        <div class="gcell__row">
          <span class="fhint">#${i + 1}</span>
          <span>
            <button class="icon-btn" data-act="left" title="Move earlier">←</button>
            <button class="icon-btn" data-act="right" title="Move later">→</button>
            <button class="icon-btn icon-btn--danger" data-act="del" title="Remove">🗑️</button>
          </span>
        </div>
      </div>
    </div>`).join("") || '<div class="empty">No photos yet — upload your first!</div>';
  root.insertAdjacentHTML("beforeend",
    `<datalist id="catList">${cats.map(c => `<option value="${esc(c)}">`).join("")}</datalist>`);

  $("#ggrid", root).addEventListener("input", (e) => {
    const cell = e.target.closest(".gcell"); if (!cell) return;
    const g = list[+cell.dataset.i];
    if (e.target.dataset.k) { g[e.target.dataset.k] = e.target.value; cms.dirty(); }
  });
  $("#ggrid", root).addEventListener("click", (e) => {
    const b = e.target.closest("[data-act]"); if (!b) return;
    const i = +b.closest(".gcell").dataset.i;
    if (b.dataset.act === "left" && i > 0) { [list[i - 1], list[i]] = [list[i], list[i - 1]]; renderGallery(); }
    if (b.dataset.act === "right" && i < list.length - 1) { [list[i + 1], list[i]] = [list[i], list[i + 1]]; renderGallery(); }
    if (b.dataset.act === "del" && confirm("Remove this photo from the gallery?")) { list.splice(i, 1); renderGallery(); }
    cms.dirty();
  });
  $("#addPhoto", root).addEventListener("click", () => cms.uploadButton((url) => {
    list.unshift({ src: url, caption: "New photo", category: "Arts Club" });
    renderGallery(); cms.dirty();
  }));
}

cms.register("gallery", renderGallery);

/* Website editing (CMS): Testimonials (the homepage slider and the Testimonials page).
   Edits change the shared copy (cms.content); nothing goes live until Save & publish (admin.js). */
import { $, $$, api, esc, post, toast } from "../ui.js";

const A = window.HAHAdmin;
const cms = A.cms;

/* ================================================================
   TESTIMONIALS
================================================================ */
function renderTestimonials() {
  const root = $("#tab-testimonials");
  const list = cms.content.testimonials;
  root.innerHTML = `
    <h1>Testimonials</h1>
    <p class="sub">The first five appear in the homepage slider; all of them appear on the Testimonials page.</p>
    <div style="margin-bottom:1.2rem"><button class="abtn abtn--primary" id="addQuote">＋ Add a testimonial</button></div>
    <div class="item-list" id="qlist"></div>`;
  $("#qlist", root).innerHTML = list.map((q, i) => `
    <div class="acard" data-i="${i}" style="margin:0">
      <div class="fgroup"><label>Quote</label><textarea data-k="quote">${esc(q.quote)}</textarea></div>
      <div class="frow">
        <div class="fgroup"><label>Name</label><input type="text" data-k="author" value="${esc(q.author)}"></div>
        <div class="fgroup"><label>Role (e.g. Parent)</label><input type="text" data-k="role" value="${esc(q.role)}"></div>
      </div>
      <div style="display:flex; gap:.4rem">
        <button class="icon-btn" data-act="up" title="Move up">↑</button>
        <button class="icon-btn" data-act="down" title="Move down">↓</button>
        <button class="icon-btn icon-btn--danger" data-act="del" title="Delete">🗑️</button>
      </div>
    </div>`).join("");
  $("#qlist", root).addEventListener("input", (e) => {
    const c = e.target.closest("[data-i]"); if (!c || !e.target.dataset.k) return;
    list[+c.dataset.i][e.target.dataset.k] = e.target.value; cms.dirty();
  });
  $("#qlist", root).addEventListener("click", (e) => {
    const b = e.target.closest("[data-act]"); if (!b) return;
    const i = +b.closest("[data-i]").dataset.i;
    if (b.dataset.act === "up" && i > 0) { [list[i - 1], list[i]] = [list[i], list[i - 1]]; renderTestimonials(); }
    if (b.dataset.act === "down" && i < list.length - 1) { [list[i + 1], list[i]] = [list[i], list[i + 1]]; renderTestimonials(); }
    if (b.dataset.act === "del" && confirm("Delete this testimonial?")) { list.splice(i, 1); renderTestimonials(); }
    cms.dirty();
  });
  $("#addQuote", root).addEventListener("click", () => {
    list.unshift({ quote: "", author: "", role: "Parent" }); renderTestimonials(); cms.dirty();
  });
}

cms.register("testimonials", renderTestimonials);

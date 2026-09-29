/* Injury body map: a simple figure, front and back, with numbered marks.
   Used by Admin → Incidents (to mark where) and the family's incident notes (to show it).
   Marks are {view: "front"|"back", x, y (0–100, % of the figure), note}. */
const W = 100, H = 200;
const FIGURE = `<circle cx="50" cy="18" r="12"/><rect x="45" y="29" width="10" height="8" rx="2"/>
  <path d="M30 38 Q50 32 70 38 L68 100 Q50 106 32 100 Z"/>
  <path d="M30 40 L22 44 L11 104 L19 107 L31 62 Z"/><path d="M70 40 L78 44 L89 104 L81 107 L69 62 Z"/>
  <path d="M34 99 L49 102 L47 192 L36 192 Z"/><path d="M51 102 L66 99 L64 192 L53 192 Z"/>`;

const escText = (s) => String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function one(view, marks) {
  const dots = marks.map((m, i) => ({ m, n: i + 1 })).filter(({ m }) => m.view === view).map(({ m, n }) =>
    `<g><circle cx="${(m.x * W / 100).toFixed(1)}" cy="${(m.y * H / 100).toFixed(1)}" r="5" fill="#c62828" stroke="#fff" stroke-width="1.2"/>
      <text x="${(m.x * W / 100).toFixed(1)}" y="${(m.y * H / 100 + 2.6).toFixed(1)}" font-size="7" text-anchor="middle" fill="#fff" font-weight="700">${n}</text>
      ${m.note ? `<title>${n}. ${escText(m.note)}</title>` : ""}</g>`).join("");
  return `<figure class="bodymap__fig"><svg viewBox="0 0 ${W} ${H}" data-view="${view}" role="img" aria-label="${view === "front" ? "Front" : "Back"} of body">
    <g fill="#e8e2d6" stroke="#9e9687" stroke-width="1">${FIGURE}</g>${dots}</svg>
    <figcaption>${view === "front" ? "Front <small>(their right is on your left)</small>" : "Back"}</figcaption></figure>`;
}

/* read-only picture, e.g. for a family's incident note */
export function picture(marks) {
  if (!marks || !marks.length) return "";
  return `<div class="bodymap">${one("front", marks)}${one("back", marks)}</div>
    <ol class="bodymap__notes">${marks.map(m => `<li>${escText(m.note || (m.view === "front" ? "Front" : "Back"))}</li>`).join("")}</ol>`;
}

/* editable: click the figure to add a mark, then describe it; returns a getter for the marks */
export function editor(el, initial) {
  const marks = (initial || []).map(m => ({ ...m }));
  const draw = () => {
    el.innerHTML = `<div class="bodymap bodymap--edit">${one("front", marks)}${one("back", marks)}</div>
      <p class="fhint">Click where the injury is (up to 12 marks).</p>
      <ol class="bodymap__notes">${marks.map((m, i) => `<li><input type="text" maxlength="60" data-note="${i}" value="${escText(m.note || "")}" placeholder="e.g. graze, 2 cm" aria-label="Mark ${i + 1} description">
        <button type="button" class="abtn abtn--ghost abtn--sm" data-rm="${i}">Remove</button></li>`).join("")}</ol>`;
    el.querySelectorAll("svg[data-view]").forEach(svg => svg.addEventListener("click", (e) => {
      if (marks.length >= 12) return;
      const r = svg.getBoundingClientRect();
      marks.push({ view: svg.dataset.view, x: Math.round(1000 * (e.clientX - r.left) / r.width) / 10,
        y: Math.round(1000 * (e.clientY - r.top) / r.height) / 10, note: "" });
      draw();
      const inputs = el.querySelectorAll("[data-note]"); if (inputs.length) inputs[inputs.length - 1].focus();
    }));
    el.querySelectorAll("[data-note]").forEach(inp => inp.addEventListener("input", () => { marks[+inp.dataset.note].note = inp.value; }));
    el.querySelectorAll("[data-rm]").forEach(b => b.addEventListener("click", () => { marks.splice(+b.dataset.rm, 1); draw(); }));
  };
  draw();
  return () => marks;
}

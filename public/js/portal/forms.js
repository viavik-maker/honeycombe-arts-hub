/* Builds form sections from the shared form spec (/api/account/formspec), so
   the portal asks exactly what the server checks. Errors are shown in a
   summary at the top of the section and next to each box. */
import { $, $$, esc } from "./core.js";

const ESSENTIAL_LEVEL_WORD = { short: "baby & toddler classes", full: "holiday clubs and drop-off sessions", adult: "young adults' activities" };
export const levelWords = (l) => ESSENTIAL_LEVEL_WORD[l] || "";

function visible(f, level) { return !f.levels || f.levels.includes(level); }

export function fieldHtml(f, id, value, level, error) {
  const req = f.required.includes(level);
  const hint = f.hint ? `<span class="hint" id="${id}-hint">${esc(f.hint)}</span>` : "";
  const err = error ? `<span class="field__error" id="${id}-err">${esc(error)}</span>` : "";
  const described = [f.hint ? id + "-hint" : "", error ? id + "-err" : ""].filter(Boolean).join(" ");
  const aria = `${described ? ` aria-describedby="${described}"` : ""}${error ? ' aria-invalid="true"' : ""}${req ? " required" : ""}`;
  const label = esc(f.label);
  const v = value == null ? "" : value;
  const wide = ["textarea", "radio"].includes(f.type) ? " field--wide" : "";
  const cls = `field${wide}${error ? " field--error" : ""}`;
  const show = f.show_if ? ` data-show-if="${esc(f.show_if[0])}=${esc(f.show_if[1])}"` : "";
  switch (f.type) {
    case "textarea":
      return `<div class="${cls}"${show}><label for="${id}">${label}</label>${hint}${err}
        <textarea id="${id}" name="${f.key}" maxlength="${f.max || 1000}" placeholder="${esc(f.placeholder || "")}"${aria}>${esc(v)}</textarea></div>`;
    case "radio":
      return `<fieldset class="${cls}"${show}><legend>${label}</legend>${hint}${err}<div class="pills">${f.options.map(([ov, ol], i) =>
        `<label class="pill-opt"><input type="radio" name="${f.key}" id="${id}${i ? "-" + i : ""}" value="${esc(ov)}"${ov === v ? " checked" : ""}${i === 0 ? aria : ""}> ${esc(ol)}</label>`).join("")}</div></fieldset>`;
    case "select":
      return `<div class="${cls}"${show}><label for="${id}">${label}</label>${hint}${err}<select id="${id}" name="${f.key}"${aria}>
        ${f.options.map(([ov, ol]) => `<option value="${esc(ov)}"${ov === v ? " selected" : ""}>${esc(ol)}</option>`).join("")}</select></div>`;
    case "checkbox":
      return `<div class="${cls} field--wide"${show}><label class="check"><input type="checkbox" id="${id}" name="${f.key}"${v ? " checked" : ""}> ${label}</label>${hint}</div>`;
    default: {
      const type = { tel: "tel", phone: "tel", date: "date", secret: "password", postcode: "text" }[f.type] || "text";
      const extra = f.type === "postcode" ? ' autocapitalize="characters"' : "";
      const auto = f.autocomplete ? ` autocomplete="${esc(f.autocomplete)}"` : "";
      return `<div class="${cls}"${show}><label for="${id}">${label}</label>${hint}${err}
        <input type="${type}" id="${id}" name="${f.key}" value="${esc(type === "password" ? "" : v)}"${f.max ? ` maxlength="${f.max}"` : ""}${auto}${extra}${aria}></div>`;
    }
  }
}

/* fields of a section at a level, laid out in the two-column grid */
export function sectionFields(section, level, values, errors, prefix) {
  return `<div class="form-grid">${section.fields.filter(f => visible(f, level)).map(f =>
    fieldHtml(f, `${prefix}-${f.key}`, (values || {})[f.key], level, (errors || {})[f.key])).join("")}</div>`;
}

export function collect(form, section, level) {
  const out = {};
  for (const f of section.fields.filter(x => visible(x, level))) {
    if (f.type === "checkbox") { const el = $(`[name="${f.key}"]`, form); out[f.key] = !!(el && el.checked); continue; }
    if (f.type === "radio") { const el = $(`[name="${f.key}"]:checked`, form); out[f.key] = el ? el.value : ""; continue; }
    const el = $(`[name="${f.key}"]`, form); out[f.key] = el ? el.value : "";
  }
  return out;
}

/* show/hide fields that depend on another answer (e.g. school name) */
export function wireShowIf(form) {
  const update = () => $$("[data-show-if]", form).forEach(el => {
    const [k, v] = el.dataset.showIf.split("=");
    const cur = ($(`[name="${k}"]:checked`, form) || $(`select[name="${k}"]`, form) || {}).value;
    el.hidden = cur !== v;
  });
  form.addEventListener("change", update); update();
}

/* error summary + inline messages; focuses the summary so it's announced */
export function showErrors(box, errors, prefix, message) {
  const old = $(".error-summary", box); if (old) old.remove();
  $$(".field--error", box).forEach(el => el.classList.remove("field--error"));
  $$(".field__error", box).forEach(el => el.remove());
  const entries = Object.entries(errors || {});
  if (!entries.length && !message) return;
  const sum = document.createElement("div");
  sum.className = "error-summary"; sum.tabIndex = -1; sum.setAttribute("role", "alert");
  sum.innerHTML = `<h2>${esc(message || "There's a problem")}</h2><ul>${entries.map(([k, m]) =>
    `<li><a href="#${prefix}-${k.replace(/\./g, "-")}">${esc(m)}</a></li>`).join("")}</ul>`;
  box.prepend(sum);
  for (const [k, m] of entries) {
    const input = $(`#${prefix}-${k.replace(/\./g, "-")}`, box);
    if (!input) continue;
    const wrap = input.closest(".field, fieldset") || input.parentElement;
    wrap.classList.add("field--error");
    input.setAttribute("aria-invalid", "true");
    const span = document.createElement("span");
    span.className = "field__error"; span.textContent = m;
    (wrap.querySelector("label, legend") || wrap.firstChild).after(span);
  }
  sum.focus();
}

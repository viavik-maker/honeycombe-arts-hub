/* "Let's plan your child's support together" — the SEND support request
   (mockup 6). Files are uploaded as they're chosen and sent with the form. */
import { $, $$, api, busy, esc, notice, renderNav, requireSignIn, root, state } from "./core.js";
import { showErrors } from "./forms.js";

const kb = (n) => n > 1048576 ? (n / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(n / 1024)) + " KB";
const pills = (name, opts, type, checked) => `<div class="pills">${Object.entries(opts).map(([k, l], i) =>
  `<label class="pill-opt"><input type="${type}" name="${name}" value="${k}" id="send-${name}${i ? "-" + i : ""}"${(checked || []).includes(k) ? " checked" : ""}> ${esc(l)}</label>`).join("")}</div>`;
const area = (name, label, hint) => `<div class="field field--wide"><label for="send-${name}">${esc(label)}</label>
  <textarea id="send-${name}" name="${name}" maxlength="2000" placeholder="${esc(hint)}"></textarea></div>`;

export async function sendSupport() {
  await requireSignIn();
  renderNav("family");
  let d = await api("/api/account/send");
  const o = d.options;
  let files = d.pending_files.slice();

  const fileList = (kind) => files.filter(f => f.kind === kind).map(f => `<div class="file-row"><span>📄 ${esc(f.filename)}</span>
    <span class="hint">${kb(f.size)}</span><button type="button" class="linklike" data-rm="${esc(f.ref)}">Remove</button></div>`).join("");
  const drop = (kind, label, hint) => `<div class="dropzone" data-kind="${kind}" tabindex="0" role="button" aria-label="${esc(label)}: choose a file">
      <strong>Drag files here or <span class="linklike">choose a file</span></strong><span class="hint">${esc(hint)}</span>
      <input type="file" hidden accept=".pdf,.docx,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png" multiple></div>
    <div class="file-list" data-list="${kind}">${fileList(kind)}</div>`;

  const earlier = d.requests.length ? `<div class="pcard"><h2>Your requests</h2>${d.requests.map(r => `<p><strong>${esc(r.child.first_name)}</strong> — ${esc(r.status_text)}
      <span class="hint">(sent ${esc(new Date(r.created_at).toLocaleDateString("en-GB", { day: "numeric", month: "long" }))})</span></p>`).join("")}</div>` : "";

  root().innerHTML = `<div class="portal__narrow">
    <p><a href="/account">← My family</a></p>
    <span class="tag tag--short">SEND and additional needs</span>
    <h1>Let's plan your child's support together</h1>
    <p class="lead">Every child should feel they belong here. Tell us about your child and our SEND lead will talk with you about how we can support them before you book.</p>
    <ol class="send-steps"><li><strong>Tell us about your child</strong><span>Fill in this form and upload any plans you have.</span></li>
      <li><strong>We talk it through</strong><span>Our SEND lead gets in touch within ${d.response_days} working days.</span></li>
      <li><strong>Book with a support plan</strong><span>We agree how we'll support your child, then you book.</span></li></ol>
    ${earlier}
    <form id="sendForm" novalidate>
      <section class="pcard"><div class="pcard__head"><span class="pcard__num">1</span><h2>How we can talk</h2></div>
        <fieldset class="field"><legend>How would you like to talk?</legend>
          <div class="choice-grid choice-grid--3">${Object.entries(o.contact).map(([k, l], i) => `<label class="choice-card choice-card--sm">
            <input type="radio" name="contact_method" value="${k}" id="send-contact_method${i ? "-" + i : ""}"><h3>${esc(l)}</h3>
            <p>${esc({ phone: "Our SEND lead calls you at a time that suits you", visit: "Come in with your child to meet the team and see the space", email: "Share information in writing and we'll reply" }[k])}</p></label>`).join("")}</div></fieldset>
        <div class="field"><label for="send-best_time">Best time to contact you</label><select id="send-best_time" name="best_time">
          ${Object.entries(o.best_time).map(([k, l]) => `<option value="${k}">${esc(l)}</option>`).join("")}</select></div></section>
      <section class="pcard"><div class="pcard__head"><span class="pcard__num">2</span><h2>Your details</h2></div>
        <p>${esc(d.you.first_name)} ${esc(d.you.last_name)} · ${esc(d.you.email)} · ${esc(d.you.mobile || "no mobile number yet")}</p>
        <p class="hint">Something wrong? <a href="/account/details">Update your details</a>.</p></section>
      <section class="pcard"><div class="pcard__head"><span class="pcard__num">3</span><h2>About your child</h2></div>
        ${d.children.length ? `<fieldset class="field"><legend>Which child is this about?</legend><div class="pills">
          ${d.children.map((k, i) => `<label class="pill-opt"><input type="radio" name="child_ref" value="${esc(k.ref)}" id="send-child_ref${i ? "-" + i : ""}"${i === 0 ? " checked" : ""}> ${esc(k.first_name)} (${k.age})</label>`).join("")}
          <label class="pill-opt"><input type="radio" name="child_ref" value=""> Someone else</label></div></fieldset>` : ""}
        <div class="form-grid" id="newChild"${d.children.length ? " hidden" : ""}>
          <div class="field"><label for="send-first_name">Child's first name</label><input type="text" id="send-first_name" name="first_name" maxlength="60"></div>
          <div class="field"><label for="send-dob">Date of birth</label><input type="date" id="send-dob" name="dob"></div></div>
        <fieldset class="field"><legend>Needs (tick any that apply)</legend><span class="hint">It's fine if your child doesn't have a diagnosis.</span>
          ${pills("needs", o.needs, "checkbox")}</fieldset>
        <div class="field" id="otherNeed" hidden><label for="send-needs_other">Other needs</label><input type="text" id="send-needs_other" name="needs_other" maxlength="200"></div>
        <fieldset class="field"><legend>Which activities are you interested in?</legend>${pills("interests", o.interests, "checkbox")}</fieldset></section>
      <section class="pcard"><div class="pcard__head"><span class="pcard__num">4</span><div><h2>Needs and support</h2>
        <p class="pcard__intro">Tell us in your own words. The more we know, the better we can prepare.</p></div></div>
        <div class="form-grid">
        ${area("good_day", "What helps your child have a good day?", "e.g. a quiet space, visual timetable, time warnings before changes")}
        ${area("overwhelm", "Is there anything that can upset or overwhelm them?", "e.g. loud noises, busy rooms, certain textures")}
        ${area("communication", "How do they communicate?", "e.g. verbal, Makaton, PECS, a communication device")}
        ${area("current_support", "What support do they get at school or home?", "e.g. 1:1 support, medication, personal care")}</div>
        <fieldset class="field"><legend>Would your child need 1:1 support to take part?</legend>${pills("one_to_one", o.one_to_one, "radio")}</fieldset></section>
      <section class="pcard"><div class="pcard__head"><span class="pcard__num">5</span><div><h2>EHCP and documents</h2>
        <p class="pcard__intro">Upload anything that helps us understand your child's needs.</p></div></div>
        <fieldset class="field"><legend>Does your child have an EHCP?</legend>${pills("ehcp", o.ehcp, "radio")}</fieldset>
        <div id="ehcpFiles"><p class="upload-label">EHCP</p>${drop("ehcp", "EHCP", "PDF, Word or photo · up to 10 MB")}</div>
        <p class="upload-label">Other plans (optional)</p>${drop("other_plan", "Other plans", "e.g. care plan, health plan, behaviour support plan, risk assessment")}
        ${notice("🔒 Your documents are stored securely and only seen by our SEND lead and the staff working with your child.", "ok")}</section>
      <section class="pcard"><div class="pcard__head"><span class="pcard__num">6</span><h2>Consent</h2></div>
        <label class="check"><input type="checkbox" name="consent_share_staff" id="send-consent_share_staff"> <span>I agree to share this information with the staff team supporting my child.</span></label>
        <label class="check"><input type="checkbox" name="consent_professionals"> <span>I'm happy for you to contact my child's school or other professionals if needed (optional).</span></label></section>
      <div class="btn-row"><a class="btn btn--ghost" href="/account">Back</a><button class="btn btn--navy" type="submit">Send to our SEND lead</button></div>
    </form></div>`;

  const f = $("#sendForm");
  const sync = () => {
    const other = f.querySelector('input[name=child_ref][value=""]');
    $("#newChild").hidden = d.children.length && !(other && other.checked);
    $("#otherNeed").hidden = !f.querySelector('input[name=needs][value=other]').checked;
    const e = f.querySelector("input[name=ehcp]:checked");
    $("#ehcpFiles").hidden = !e || e.value === "no";
  };
  f.addEventListener("change", sync); sync();

  const redrawFiles = () => $$("[data-list]").forEach(el => { el.innerHTML = fileList(el.dataset.list); });
  const uploadOne = async (kind, file, zone) => {
    const fd = new FormData(); fd.append("kind", kind); fd.append("file", file);
    zone.classList.add("dropzone--busy");
    try {
      const r = await fetch("/api/account/send/files", { method: "POST", body: fd, headers: { "X-CSRF-Token": state.csrf } });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(j.error || "That file couldn't be uploaded.");
      files.push(j.file); redrawFiles();
    } catch (x) { zone.insertAdjacentHTML("afterend", notice(esc(file.name + ": " + x.message), "err")); }
    finally { zone.classList.remove("dropzone--busy"); }
  };
  $$(".dropzone").forEach(z => {
    const input = $("input[type=file]", z);
    z.onclick = () => input.click();
    z.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); } };
    input.onchange = () => { Array.from(input.files).forEach(file => uploadOne(z.dataset.kind, file, z)); input.value = ""; };
    z.ondragover = (e) => { e.preventDefault(); z.classList.add("dropzone--over"); };
    z.ondragleave = () => z.classList.remove("dropzone--over");
    z.ondrop = (e) => { e.preventDefault(); z.classList.remove("dropzone--over"); Array.from(e.dataTransfer.files).forEach(file => uploadOne(z.dataset.kind, file, z)); };
  });
  f.addEventListener("click", async (e) => {
    const b = e.target.closest("[data-rm]"); if (!b) return;
    try { await api(`/api/account/send/files/${encodeURIComponent(b.dataset.rm)}/delete`, {}); files = files.filter(x => x.ref !== b.dataset.rm); redrawFiles(); }
    catch (x) { alert(x.message); }
  });

  f.onsubmit = async (e) => {
    e.preventDefault();
    const val = (n) => (f.querySelector(`[name=${n}]:checked`) || {}).value || "";
    const childRef = val("child_ref");
    const ehcp = val("ehcp");
    const body = {
      contact_method: val("contact_method"), best_time: f.best_time.value, child_ref: childRef || null,
      child: childRef ? null : { first_name: f.first_name.value, dob: f.dob.value },
      needs: $$("input[name=needs]:checked", f).map(i => i.value), needs_other: f.needs_other.value,
      interests: $$("input[name=interests]:checked", f).map(i => i.value),
      good_day: f.good_day.value, overwhelm: f.overwhelm.value, communication: f.communication.value, current_support: f.current_support.value,
      one_to_one: val("one_to_one"), ehcp, consent_share_staff: f.consent_share_staff.checked, consent_professionals: f.consent_professionals.checked,
      files: files.filter(x => ehcp !== "no" || x.kind !== "ehcp").map(x => x.ref),
    };
    try {
      await busy($("button[type=submit]", f), () => api("/api/account/send", body));
      root().innerHTML = `<div class="portal__narrow"><h1>Thank you</h1>${notice(`We've sent this to our SEND lead, who'll be in touch within ${d.response_days} working days. We've emailed you a copy of what happens next.`, "ok")}
        <p><a class="btn btn--ghost" href="/account">Back to my family</a></p></div>`;
      window.scrollTo(0, 0);
    } catch (x) { showErrors(f, (x.data && x.data.errors) || {}, "send", x.message); }
  };
}

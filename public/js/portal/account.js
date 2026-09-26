/* Family portal pages: choose account type, register, sign in, reset,
   activate, the family dashboard and "complete your child's details". */
import { $, $$, api, busy, carerNote, esc, fail, go, loadMe, loadSpec, notice, params, renderNav, requireSignIn, root, safeNext, state } from "./core.js";
import { collect, levelWords, sectionFields, showErrors, wireShowIf } from "./forms.js";

const KINDS = {
  full: { icon: "☀️", title: "Holiday clubs & drop-off sessions", tag: ["tag--full", "Full registration · once per child"],
    text: "Parents and carers registering children under 18 for holiday clubs, Home Ed and other sessions where you drop off.",
    asks: ["Age & date of birth", "2 emergency contacts", "Doctor / GP", "School or home educated", "HAF status",
      "SEND & SEMH needs", "Allergies", "Dietary & religious requirements", "Family info for safeguarding",
      "Photography permission", "First aid, plasters, emergency treatment, going home alone", "Collection password"] },
  short: { icon: "🧸", title: "Baby & toddler classes", tag: ["tag--short", "Shorter registration"],
    text: "Classes where a parent or carer stays for the whole session.",
    asks: ["Emergency contact", "Allergies", "Photography permission"] },
  adult: { icon: "🎨", title: "Young adults (18+)", tag: ["tag--full", "Book for yourself"],
    text: "Aged 18 or over? Register and book for yourself. Under 18? A parent or carer registers you.",
    asks: ["Your details", "An emergency contact", "Photography permission", "Any access needs (optional)"] },
  guest: { icon: "🎟️", title: "One-off events", tag: ["tag--quick", "Quick booking · no registration"],
    text: "Booking a place at a single event, like a fun day or workshop.",
    asks: ["Email address", "Phone number"] },
};

const STEPS = ["Account type", "Your details", "Who's attending", "Review"];
const stepper = (current) => `<ol class="stepper">${STEPS.map((s, i) =>
  `<li data-n="${i + 1}"${i + 1 === current ? ' aria-current="step"' : ""}${i + 1 < current ? ' class="done"' : ""}>${s}</li>`).join("")}</ol>`;

/* ---------------- 1. who are you booking for? ---------------- */
export async function registerChooser() {
  await loadMe();
  if (state.me) return go("/account");
  const pre = params.get("for") || "full";
  root().innerHTML = `${stepper(1)}
    <h1>Who are you booking for?</h1>
    <p class="lead">Choose the option that fits. We'll only ask for the information we need for that kind of activity.</p>
    <form id="chooser">
      <div class="choice-grid">${Object.entries(KINDS).map(([k, v]) => `
        <label class="choice-card"><input type="radio" name="for" value="${k}"${k === pre ? " checked" : ""}>
          <span class="choice-card__icon" aria-hidden="true">${v.icon}</span>
          <h2>${esc(v.title)}</h2><p>${esc(v.text)}</p>
          <span class="tag ${v.tag[0]}">${esc(v.tag[1])}</span>
          <p class="hint" style="margin:.8em 0 .2em;font-weight:800;font-size:.78rem;letter-spacing:.05em">WE'LL ASK FOR</p>
          <ul class="chips">${v.asks.map(a => `<li>${esc(a)}</li>`).join("")}</ul>
        </label>`).join("")}</div>
      <div class="send-callout"><span aria-hidden="true">💬</span><div><strong>Does your child have SEND or additional needs?</strong>
        <p>Tell our SEND lead about them first and we'll plan their support together before you book.
        <a href="/register/details?for=full&amp;next=/send-support">Start here</a></p></div></div>
      <div class="btn-row">
        <p class="form-note">Not sure which to choose? <a href="/contact">Get in touch</a> ·
          Already registered? <a href="/login">Sign in</a> ·
          Coming from our old booking system? <a href="/activate">Activate your account</a></p>
        <button class="btn btn--orange" type="submit">Continue →</button>
      </div>
    </form>`;
  $("#chooser").onsubmit = (e) => {
    e.preventDefault();
    const k = $("input[name=for]:checked").value;
    const next = params.get("next");
    if (k === "guest") return go("/book?tab=events");
    go(`/register/details?for=${k}${next ? "&next=" + encodeURIComponent(next) : ""}`);
  };
}

/* ---------------- 2. your details, then the emailed code ---------------- */
export async function registerDetails() {
  await loadMe();
  if (state.me) return go("/account");
  const kind = ["full", "short", "adult"].includes(params.get("for")) ? params.get("for") : "full";
  const spec = await loadSpec();
  const you = spec.sections.you;
  const shortYou = { fields: you.fields.filter(f => ["first_name", "last_name", "mobile", "postcode"].includes(f.key)) };
  root().innerHTML = `<div class="portal__narrow">${stepper(2)}
    <span class="tag ${KINDS[kind].tag[0]}">${esc(KINDS[kind].title)}</span>
    <h1>${kind === "adult" ? "Register yourself" : "Create your account"}</h1>
    <p class="lead">You only need to do this once. ${kind === "adult" ? "" : "You'll add your children next."}</p>
    <form id="regForm" class="pcard" novalidate>
      <div class="pcard__head"><span class="pcard__num">1</span><div><h2>Your details</h2>
        <p class="pcard__intro">${kind === "adult" ? "You're 18 or over and booking for yourself." : "The parent or carer responsible."}</p></div></div>
      ${sectionFields(shortYou, "short", {}, {}, "reg")}
      <div class="form-grid">
        <div class="field field--wide"><label for="reg-email">Email address</label>
          <span class="hint" id="reg-email-hint">We'll send a code to check it's yours.</span>
          <input type="email" id="reg-email" name="email" autocomplete="email" required aria-describedby="reg-email-hint"></div>
        ${kind === "adult" ? `<div class="field"><label for="reg-dob">Your date of birth</label>
          <input type="date" id="reg-dob" name="dob" required></div>` : ""}
        <div class="field field--wide"><label for="reg-password">Create a password</label>
          <span class="hint" id="reg-password-hint">At least 10 characters — three random words works well.</span>
          <input type="password" id="reg-password" name="password" autocomplete="new-password" required aria-describedby="reg-password-hint"></div>
      </div>
      <input class="hp" name="website" tabindex="-1" autocomplete="off" aria-hidden="true">
      <p class="form-note">We use your details to run bookings and keep children safe — see our <a href="/privacy" target="_blank">privacy notice</a>.</p>
      <div class="btn-row"><a class="btn btn--ghost" href="/register?for=${kind}">Back</a>
        <button class="btn btn--orange" type="submit">Continue</button></div>
    </form></div>`;
  const form = $("#regForm");
  form.onsubmit = async (e) => {
    e.preventDefault();
    const body = Object.assign(collect(form, shortYou, "short"), {
      kind: kind === "adult" ? "adult" : "family", email: form.email.value, password: form.password.value,
      website: form.website.value, dob: form.dob ? form.dob.value : undefined });
    try {
      await busy($("button[type=submit]", form), () => api("/api/account/register", body));
      codeStep(body.email, kind);
    } catch (x) { showErrors(form, x.data.errors || {}, "reg", x.message); }
  };
}

function codeStep(email, kind, fromLogin) {
  root().innerHTML = `<div class="portal__narrow">${fromLogin ? "" : stepper(2)}
    <h1>Check your email</h1>
    <p class="lead">We've sent a 6-digit code to <strong>${esc(email)}</strong>. It can take a minute to arrive — check your spam folder too.</p>
    <form id="codeForm" class="pcard" novalidate>
      <div class="field"><label for="code-code">Code</label>
        <input id="code-code" name="code" class="code-input" inputmode="numeric" autocomplete="one-time-code" maxlength="7" required></div>
      <div class="btn-row"><button type="button" class="linklike" id="resend">Send a new code</button>
        <button class="btn btn--orange" type="submit">Continue</button></div>
    </form></div>`;
  const form = $("#codeForm");
  $("#code-code").focus();
  $("#resend").onclick = async () => {
    await api("/api/account/register/resend", { email });
    showErrors(form, {}, "code", "We've sent a new code — use the newest email.");
  };
  form.onsubmit = async (e) => {
    e.preventDefault();
    try {
      await busy($("button[type=submit]", form), () => api("/api/account/register/verify", { email, code: form.code.value }));
      await loadMe();
      if (fromLogin) return go(safeNext("/account"));
      if (kind === "adult") return go(`/account/family/${state.me.participants[0].ref}?setup=1`);
      if (params.get("next") === "/send-support") return go("/send-support");  // the support form adds the child
      go(`/account/family/new?level=${kind}${params.get("next") ? "&next=" + encodeURIComponent(params.get("next")) : ""}`);
    } catch (x) { showErrors(form, { code: x.message }, "code", "That didn't work"); }
  };
}

/* ---------------- sign in / forgotten password / reset / activate ---------------- */
export async function login() {
  await loadMe();
  if (state.me) return go(safeNext("/account"));
  root().innerHTML = `<div class="portal__narrow"><h1>Sign in</h1>
    <p class="lead">Sign in to book activities and manage your family's details.</p>
    <form id="loginForm" class="pcard" novalidate>
      <div class="field"><label for="login-email">Email address</label><input type="email" id="login-email" name="email" autocomplete="username" required></div>
      <div class="field"><label for="login-password">Password</label><input type="password" id="login-password" name="password" autocomplete="current-password" required></div>
      <div class="btn-row"><a href="/forgot-password">Forgotten your password?</a>
        <button class="btn btn--orange" type="submit">Sign in</button></div>
    </form>
    <p>New here? <a href="/register${params.get("next") ? "?next=" + encodeURIComponent(params.get("next")) : ""}">Create an account</a><br>
      Coming from our old booking system? <a href="/activate">Activate your account</a></p></div>`;
  const form = $("#loginForm");
  form.onsubmit = async (e) => {
    e.preventDefault();
    try {
      const d = await busy($("button[type=submit]", form), () => api("/api/account/login", { email: form.email.value, password: form.password.value }));
      if (d.next === "verify") return codeStep(form.email.value, null, true);
      go(safeNext("/account"));
    } catch (x) { showErrors(form, {}, "login", x.message); }
  };
}

function emailOnlyPage(title, lead, endpoint, button) {
  root().innerHTML = `<div class="portal__narrow"><h1>${title}</h1><p class="lead">${lead}</p>
    <form id="emailForm" class="pcard" novalidate>
      <div class="field"><label for="ef-email">Email address</label><input type="email" id="ef-email" name="email" autocomplete="email" required></div>
      <div class="btn-row"><a href="/login">Back to sign in</a><button class="btn btn--orange" type="submit">${button}</button></div>
    </form></div>`;
  const form = $("#emailForm");
  form.onsubmit = async (e) => {
    e.preventDefault();
    try {
      const d = await busy($("button[type=submit]", form), () => api(endpoint, { email: form.email.value }));
      form.outerHTML = notice(esc(d.message), "ok");
    } catch (x) { showErrors(form, {}, "ef", x.message); }
  };
}

export async function forgot() {
  emailOnlyPage("Forgotten your password?", "Enter your email and we'll send you a link to choose a new one.",
    "/api/account/password/forgot", "Send link");
}

const hashToken = () => (location.hash.match(/[#&]t=([\w-]+)/) || [])[1];

export async function reset() {
  const token = hashToken();
  if (!token) return go("/forgot-password");
  root().innerHTML = `<div class="portal__narrow"><h1>Choose a new password</h1>
    <form id="resetForm" class="pcard" novalidate>
      <div class="field"><label for="rs-password">New password</label>
        <span class="hint">At least 10 characters — three random words works well.</span>
        <input type="password" id="rs-password" name="password" autocomplete="new-password" required></div>
      <div class="btn-row"><span></span><button class="btn btn--orange" type="submit">Save and sign in</button></div>
    </form></div>`;
  const form = $("#resetForm");
  form.onsubmit = async (e) => {
    e.preventDefault();
    try {
      await busy($("button[type=submit]", form), () => api("/api/account/password/reset", { token, password: form.password.value }));
      history.replaceState(null, "", location.pathname);
      go("/account");
    } catch (x) { showErrors(form, x.data.errors ? { password: x.data.errors.password } : {}, "rs", x.message); }
  };
}

export async function activate() {
  const token = hashToken();
  if (!token) {
    return emailOnlyPage("Activate your account",
      "Booked with us through our old system? Your family's details have moved to our new booking system. Enter the email you used and we'll send you a link to set it up.",
      "/api/account/activate/resend", "Send my link");
  }
  let check;
  try { check = await api("/api/account/activate/check", { token }); }
  catch (x) { history.replaceState(null, "", location.pathname); return activate(); }
  const q = check.check === "dob"
    ? `<div class="field"><label for="ac-child_dob">The date of birth of one of your children</label>
        <span class="hint">So we know it's really you before showing your family's details.</span>
        <input type="date" id="ac-child_dob" name="child_dob" required></div>`
    : check.check === "postcode"
      ? `<div class="field"><label for="ac-postcode">Your postcode</label><input id="ac-postcode" name="postcode" autocomplete="postal-code" required></div>` : "";
  root().innerHTML = `<div class="portal__narrow"><h1>Welcome, ${esc(check.first_name)}!</h1>
    <p class="lead">Set up your account on our new booking system. There's no membership fee any more.</p>
    <form id="actForm" class="pcard" novalidate>${q}
      <div class="field"><label for="ac-password">Choose a password</label>
        <span class="hint">At least 10 characters — three random words works well.</span>
        <input type="password" id="ac-password" name="password" autocomplete="new-password" required></div>
      <div class="btn-row"><span></span><button class="btn btn--orange" type="submit">Activate my account</button></div>
    </form></div>`;
  const form = $("#actForm");
  form.onsubmit = async (e) => {
    e.preventDefault();
    try {
      await busy($("button[type=submit]", form), () => api("/api/account/activate", {
        token, password: form.password.value, child_dob: form.child_dob ? form.child_dob.value : undefined,
        postcode: form.postcode ? form.postcode.value : undefined }));
      history.replaceState(null, "", location.pathname);
      go("/account?welcome=1");
    } catch (x) { showErrors(form, x.data.errors || {}, "ac", x.message); }
  };
}

/* ---------------- My family ---------------- */
const levelStatus = (p) => {
  if (p.status !== "active") return `<span class="status status--muted">Archived</span>`;
  if (p.missing.includes("review")) return `<span class="status status--todo">Please check their details</span>`;
  if (p.level === "full") return `<span class="status status--ok">Ready for holiday clubs & drop-off sessions</span>`;
  if (p.level === "adult") return `<span class="status status--ok">Ready to book</span>`;
  if (p.level === "short" && p.target_level === "short") return `<span class="status status--ok">Ready for baby & toddler classes</span>`;
  if (p.level === "short") return `<span class="status status--todo">Ready for parent-stays classes · details needed for drop-off</span>`;
  return `<span class="status status--todo">Details needed</span>`;
};

export async function accountHome() {
  const me = await requireSignIn();
  renderNav("family");
  const welcome = params.get("welcome") ? notice("Your account is set up. Please check each person's details below before you book.", "ok") : "";
  if (me.carer) {
    root().innerHTML = `<h1>Hi ${esc(me.carer.first_name)}</h1>${carerNote()}
      <div class="people">${me.participants.filter(p => p.status === "active").map(p => `<div class="person">
        <h3>${esc(p.first_name)} ${esc(p.last_name)}</h3><p>${p.is_account_holder ? "" : "Age " + p.age}</p><p>${levelStatus(p)}</p>
        <p style="margin-top:.8em"><a class="btn btn--sm btn--ghost" href="/account/family/${esc(p.ref)}">View details</a></p></div>`).join("")}</div>
      <p><a class="btn btn--orange" href="/book">Book activities</a> <a class="btn btn--ghost" href="/account/bookings">Bookings</a></p>`;
    return;
  }
  root().innerHTML = `${welcome}<h1>Hi ${esc(me.account.first_name)}</h1>
    <p class="lead">${me.account.kind === "adult" ? "Your details and bookings." : "Your family's details. Everyone needs their details complete before you can book for them."}</p>
    ${me.acknowledged ? "" : notice(`Before your first booking, please confirm your details are correct — you'll find this at the end of each person's page.`, "warn")}
    <div class="people">${me.participants.map(p => `<div class="person">
      <h3>${esc(p.first_name)} ${esc(p.last_name)}</h3>
      <p>${p.is_account_holder ? "You" : "Age " + p.age}</p>
      <p>${levelStatus(p)}</p>
      <p style="margin-top:.8em"><a class="btn btn--sm ${p.missing.length ? "btn--orange" : "btn--ghost"}" href="/account/family/${esc(p.ref)}">${p.missing.length ? "Complete details" : "View & edit"}</a></p>
    </div>`).join("")}
    ${me.account.kind === "family" ? `<div class="person" style="display:grid;place-items:center;text-align:center">
      <p><a class="btn btn--honey" href="/account/family/new">＋ Add a child</a></p></div>` : ""}</div>
    <p><a class="btn btn--orange" href="/book">Book activities</a> <a class="btn btn--ghost" href="/account/details">Your details & emergency contacts</a>
      <a class="btn btn--ghost" href="/account/privacy">Your data & messages</a></p>
    <div class="send-callout"><span aria-hidden="true">💬</span><div><strong>Does a child need extra support (SEND)?</strong>
      <p>Tell our SEND lead and we'll plan it together. <a href="/send-support">Plan their support</a></p></div></div>`;
}

export async function childNew() {
  await requireSignIn();
  renderNav("family");
  const spec = await loadSpec();
  const level = params.get("level") === "short" ? "short" : "full";
  const childBasics = { fields: spec.sections.child.fields.filter(f => ["first_name", "last_name", "dob"].includes(f.key)) };
  root().innerHTML = `<div class="portal__narrow">${state.me.participants.length ? "" : stepper(3)}
    <h1>Who's attending?</h1>
    <p class="lead">Add a child. You can add more at the end.</p>
    <form id="childForm" class="pcard" novalidate>
      ${sectionFields(childBasics, "short", {}, {}, "nc")}
      <fieldset class="field"><legend>What will they come to?</legend>
        <span class="hint">You can change this later. We'll only ask for what's needed.</span>
        <div class="pills">
          <label class="pill-opt"><input type="radio" name="target_level" value="full"${level === "full" ? " checked" : ""}> Holiday clubs & drop-off sessions</label>
          <label class="pill-opt"><input type="radio" name="target_level" value="short"${level === "short" ? " checked" : ""}> Only classes where I stay (baby & toddler)</label>
        </div></fieldset>
      <div class="btn-row"><a class="btn btn--ghost" href="/account">Back</a><button class="btn btn--orange" type="submit">Continue</button></div>
    </form></div>`;
  const form = $("#childForm");
  form.onsubmit = async (e) => {
    e.preventDefault();
    const body = Object.assign(collect(form, childBasics, "short"), { target_level: $("input[name=target_level]:checked", form).value });
    try {
      const d = await busy($("button[type=submit]", form), () => api("/api/account/participants", body));
      const next = params.get("next");
      go(`/account/family/${d.ref}?setup=1${next ? "&next=" + encodeURIComponent(next) : ""}`);
    } catch (x) { showErrors(form, x.data.errors || {}, "nc", x.message); }
  };
}

/* ---------------- a child's (or your own) details, section by section ---------------- */
const SECTION_ORDER = {
  full: ["you", "contacts", "child", "gp", "health", "safeguarding", "consents", "collection", "confirm"],
  short: ["you", "contacts", "child", "health", "consents", "confirm"],
  adult: ["you", "contacts", "health", "consents", "confirm"],
};

export async function childPage() {
  await requireSignIn();
  renderNav("family");
  const ref = location.pathname.split("/").pop();
  const spec = await loadSpec();
  let data;
  try { data = await api("/api/account/participants/" + encodeURIComponent(ref)); }
  catch (e) { return fail(e); }
  // arriving from the Book page for something that needs the full form
  if (params.get("for") === "full" && data.summary.target_level === "short" && !data.summary.is_account_holder && !state.me.carer) {
    await api(`/api/account/participants/${encodeURIComponent(ref)}/target`, { target_level: "full" });
    data = await api("/api/account/participants/" + encodeURIComponent(ref));
  }
  const draw = () => {
    renderChild(spec, data);
    if (state.me.carer) {  // read-only for extra carers
      root().insertAdjacentHTML("afterbegin", carerNote());
      $$("input, select, textarea", root()).forEach(el => { el.disabled = true; });
      $$("button[data-save], .pcard__actions button, button[type=submit]", root()).forEach(el => { el.hidden = true; });
    }
  };
  const refresh = async () => { await loadMe(); data = await api("/api/account/participants/" + encodeURIComponent(ref)); draw(); };
  state.refreshChild = refresh;
  draw();
}

function renderChild(spec, data) {
  const s = data.summary, me = state.me;
  const level = s.is_account_holder ? "adult" : s.target_level;
  const order = SECTION_ORDER[level];
  const missing = new Set(s.missing);
  const done = (k) => !missing.has(k === "confirm" ? "confirm" : k);
  const who = s.is_account_holder ? "you" : s.first_name;
  let n = 0;
  const cards = order.map(key => {
    n += 1;
    const isDone = done(key);
    return `<section class="pcard${isDone ? "" : " pcard--todo"}" id="sec-${key}">
      <div class="pcard__head"><span class="pcard__num${isDone ? " pcard__num--done" : ""}" aria-hidden="true">${isDone ? "✓" : n}</span>
        <div><h2>${esc(sectionTitle(spec, key, s))}</h2>${sectionIntro(spec, key, s)}</div></div>
      ${sectionBody(spec, key, level, data, me)}
    </section>`;
  }).join("");
  const ready = !s.missing.length;
  root().innerHTML = `<p><a href="/account">← My family</a></p>
    <h1>${s.is_account_holder ? "Your details" : esc(s.first_name) + " " + esc(s.last_name)}</h1>
    <p class="lead">${s.is_account_holder ? "" : "Age " + s.age + " · "}${ready
      ? `All done — ${esc(who)} ${s.is_account_holder ? "are" : "is"} ready to book ${esc(levelWords(level))}.`
      : `A few sections still need filling in before ${s.is_account_holder ? "you can book" : esc(who) + " can book " + esc(levelWords(level))}. Each section saves on its own.`}</p>
    ${s.missing.includes("review") ? notice("These details came from our old booking system. Please check every section, update anything that's changed, then confirm at the bottom.", "warn") : ""}
    ${!s.is_account_holder ? `<p class="form-note">Registered for: <strong>${esc(levelWords(level))}</strong>.
      <button class="linklike" id="switchLevel">${level === "full" ? "Only coming to classes where I stay?" : "Coming to holiday clubs or drop-off sessions too?"}</button></p>` : ""}
    ${cards}
    ${s.missing.includes("review") ? `<section class="pcard pcard--todo"><h2>Everything correct?</h2>
      <p>Once you've checked each section above, confirm here.</p>
      <button class="btn btn--orange" id="reviewedBtn">Yes, ${esc(who)}'s details are correct</button></section>` : ""}
    <div class="btn-row">${ready ? `<a class="btn btn--orange" href="${esc(safeNext("/book"))}">Book activities</a>` : "<span></span>"}
      ${me.account.kind === "family" ? `<a class="btn btn--ghost" href="/account/family/new">＋ Add another child</a>` : ""}</div>`;
  wireChild(spec, level, data);
}

function sectionTitle(spec, key, s) {
  if (key === "contacts") return "Emergency contacts";
  if (key === "confirm") return "Check and confirm";
  if (key === "child") return "About " + s.first_name;
  if (key === "you") return "Your details";
  return spec.sections[key].title;
}

function sectionIntro(spec, key) {
  const intro = {
    contacts: "People we can call if we can't reach you. They're shared by all your children.",
    confirm: "",
  }[key] ?? (spec.sections[key] && spec.sections[key].intro) ?? "";
  return intro ? `<p class="pcard__intro">${esc(intro)}</p>` : "";
}

function sectionBody(spec, key, level, data, me) {
  const save = (label) => `<div class="pcard__actions"><button class="btn btn--orange btn--sm" data-save="${key}">${label || "Save"}</button>
    <span class="pcard__saved" hidden>Saved ✓</span></div>`;
  if (key === "you") {
    return `<form data-sec="you" novalidate>${sectionFields(spec.sections.you, level, me.account, {}, "you")}
      <p class="form-note">Email: <strong>${esc(me.account.email)}</strong></p>${save()}</form>`;
  }
  if (key === "contacts") {
    const need = spec.contacts.required[level];
    const rows = me.contacts.length ? me.contacts : Array.from({ length: Math.max(need, 1) }, () => ({}));
    return `<form data-sec="contacts" novalidate><p class="form-note">${need ? `We need ${need === 2 ? "two people" : "one person"}.` : "Optional when you stay for the session — but useful."}
      Tick “can collect” for anyone allowed to pick ${esc(level === "adult" ? "you" : "your children")} up.</p>
      <div id="contactRows">${rows.map((c, i) => contactRow(c, i)).join("")}</div>
      <p><button type="button" class="linklike" id="addContact">＋ Add another contact</button></p>${save("Save contacts")}</form>`;
  }
  if (key === "child") {
    return `<form data-sec="child" novalidate>${sectionFields(spec.sections.child, level, data.child, {}, "child")}
      ${data.haf_verified ? `<p class="form-note">✓ HAF place confirmed by our team.</p>` : ""}${save()}</form>`;
  }
  if (["gp", "health", "safeguarding"].includes(key)) {
    const values = key === "safeguarding" ? data.safeguarding : data[key];
    return `<form data-sec="${key}" novalidate>${sectionFields(spec.sections[key], level, values || {}, {}, key)}
      ${key === "health" && !data.health ? `<p class="form-note">Nothing to tell us? Just press Save.</p>` : ""}${save()}</form>`;
  }
  if (key === "consents") {
    return `<form data-sec="consents" novalidate>${data.consent_questions.map(q => {
      const t = spec.consent_questions[q.key];
      return `<fieldset class="field" id="consents-${q.key}"><legend>${esc(t.label)}${q.required ? "" : " (optional)"}</legend>
        ${t.help ? `<span class="hint">${esc(t.help)}</span>` : ""}
        <div class="pills">${t.options.map(([v, l], i) => `<label class="pill-opt"><input type="radio" name="${q.key}" value="${esc(v)}"${data.consents[q.key] === v ? " checked" : ""}${i === 0 ? ` id="consents-${q.key}-in"` : ""}> ${esc(l)}</label>`).join("")}</div></fieldset>`;
    }).join("")}${save()}</form>`;
  }
  if (key === "collection") {
    return `<form data-sec="collection" novalidate>
      ${data.collection.set ? `<p class="form-note">✓ A collection password is set. We never show it — if you've forgotten it, set a new one.</p>` : ""}
      <div class="form-grid">
        <div class="field"><label for="collection-password">${data.collection.set ? "New collection password" : "Collection password"}</label>
          <input type="password" id="collection-password" name="password" autocomplete="off" required></div>
        <div class="field"><label for="collection-confirm">Confirm collection password</label>
          <input type="password" id="collection-confirm" name="confirm" autocomplete="off" required></div>
      </div>${save(data.collection.set ? "Change password" : "Save")}</form>`;
  }
  if (key === "confirm") {
    return `<form data-sec="confirm" novalidate>
      <label class="check" id="confirm-info_correct"><input type="checkbox" name="info_correct"${me.acknowledged ? " checked" : ""}> I confirm the information above is correct and I'll let you know if anything changes.</label>
      <label class="check" id="confirm-privacy_ack"><input type="checkbox" name="privacy_ack"${me.acknowledged ? " checked" : ""}> I have read the <a href="/privacy" target="_blank">privacy notice</a> and <a href="/safeguarding" target="_blank">safeguarding policy</a>.</label>
      ${save("Confirm")}</form>`;
  }
  return "";
}

function contactRow(c, i) {
  return `<div class="contact-row" data-row="${i}">
    <div class="field"><label for="contacts-${i}-full_name">Full name</label><input id="contacts-${i}-full_name" name="full_name" value="${esc(c.full_name || "")}"></div>
    <div class="field"><label for="contacts-${i}-relationship">Relationship</label><input id="contacts-${i}-relationship" name="relationship" value="${esc(c.relationship || "")}" placeholder="e.g. Grandmother"></div>
    <div class="field"><label for="contacts-${i}-phone">Phone number</label><input id="contacts-${i}-phone" name="phone" type="tel" value="${esc(c.phone || "")}"></div>
    <div class="field"><label class="check"><input type="checkbox" name="can_collect"${c.can_collect ? " checked" : ""}> Can collect</label>
      <button type="button" class="linklike" data-remove="${i}">Remove</button></div>
  </div>`;
}

function wireChild(spec, level, data) {
  const sw = $("#switchLevel");
  if (sw) sw.onclick = async () => {
    await api(`/api/account/participants/${data.summary.ref}/target`, { target_level: level === "full" ? "short" : "full" });
    state.refreshChild();
  };
  const rb = $("#reviewedBtn");
  if (rb) rb.onclick = async () => { await api(`/api/account/participants/${data.summary.ref}/reviewed`, {}); state.refreshChild(); };
  const add = $("#addContact");
  if (add) add.onclick = () => {
    const rows = $("#contactRows"); const i = rows.children.length;
    if (i >= spec.contacts.max) return;
    rows.insertAdjacentHTML("beforeend", contactRow({}, i));
  };
  $$("[data-remove]").forEach(b => b.onclick = () => b.closest(".contact-row").remove());
  $$("form[data-sec]").forEach(form => {
    wireShowIf(form);
    form.onsubmit = (e) => e.preventDefault();
    const btn = $("[data-save]", form);
    btn.onclick = async (e) => {
      e.preventDefault();
      const key = form.dataset.sec;
      const ref = data.summary.ref;
      let path, body;
      if (key === "you") { path = "/api/account/details"; body = collect(form, spec.sections.you, level); }
      else if (key === "contacts") {
        path = "/api/account/contacts";
        body = { contacts: $$(".contact-row", form).map(r => ({ full_name: $("[name=full_name]", r).value,
          relationship: $("[name=relationship]", r).value, phone: $("[name=phone]", r).value,
          can_collect: $("[name=can_collect]", r).checked })).filter(c => c.full_name || c.phone || c.relationship) };
      } else if (key === "consents") {
        path = `/api/account/participants/${ref}/consents`;
        body = { answers: Object.fromEntries(data.consent_questions.map(q => [q.key, ($(`[name=${q.key}]:checked`, form) || {}).value || ""])) };
      } else if (key === "collection") {
        path = `/api/account/participants/${ref}/collection`;
        body = { password: form.password.value, confirm: form.confirm.value };
      } else if (key === "confirm") {
        path = "/api/account/acknowledge"; body = { info_correct: form.info_correct.checked, privacy_ack: form.privacy_ack.checked };
      } else {
        path = `/api/account/participants/${ref}/${key}`;
        body = collect(form, spec.sections[key], key === "safeguarding" ? "full" : level);
      }
      try {
        await busy(btn, () => api(path, body));
        const y = window.scrollY;
        await state.refreshChild();
        window.scrollTo(0, y);
        const next = $(".pcard--todo");
        const saved = $(`#sec-${key} .pcard__saved`); if (saved) saved.hidden = false;
        if (next && next.id !== "sec-" + key && params.get("setup")) next.scrollIntoView({ behavior: "smooth", block: "start" });
      } catch (x) {
        const errs = x.data.errors || {};
        const prefix = key === "consents" ? "consents" : key;
        showErrors(form, key === "consents" ? Object.fromEntries(Object.entries(errs).map(([k, v]) => [k + "-in", v])) : errs,
          prefix, x.message);
      }
    };
  });
}

/* ---------------- your details & contacts (without a child) ---------------- */
export async function details() {
  await requireSignIn();
  if (state.me.carer) { renderNav("details"); root().innerHTML = `<h1>Details</h1>${carerNote()}`; return; }
  renderNav("details");
  const spec = await loadSpec();
  const me = state.me;
  const level = me.account.kind === "adult" ? "adult" : (me.participants.some(p => p.target_level === "full") ? "full" : "short");
  root().innerHTML = `<h1>My details</h1>
    <section class="pcard" id="sec-you"><div class="pcard__head"><span class="pcard__num">1</span><div><h2>Your details</h2></div></div>
      ${sectionBody(spec, "you", level, {}, me)}</section>
    <section class="pcard" id="sec-contacts"><div class="pcard__head"><span class="pcard__num">2</span><div><h2>Emergency contacts</h2>
      <p class="pcard__intro">Shared by all your children.</p></div></div>${sectionBody(spec, "contacts", level, {}, me)}</section>`;
  state.refreshChild = async () => { await loadMe(); details(); };
  wireChild(spec, level, { summary: {}, consent_questions: [] });
  if (me.account.kind === "family") carersSection();
}

/* ---------------- extra carers (account holder only) ---------------- */
async function carersSection() {
  const d = await api("/api/account/carers");
  const box = document.createElement("section");
  box.className = "pcard"; box.id = "sec-carers";
  root().appendChild(box);
  const draw = (list) => {
    box.innerHTML = `<div class="pcard__head"><span class="pcard__num">3</span><div><h2>Other carers who can sign in</h2>
      <p class="pcard__intro">Let a partner, grandparent or other carer use this account with their own email and password (up to ${d.max}).
      They can book, pay, cancel and report absences, and see your children's details, but can't change them.
      Adding someone here doesn't let them collect a child: that's set in Emergency contacts.</p></div></div>
      ${list.length ? `<ul class="plain-list">${list.map(x => `<li><strong>${esc(x.first_name)} ${esc(x.last_name)}</strong>${x.relationship ? ` (${esc(x.relationship)})` : ""} · ${esc(x.email)}
        · ${x.status === "active" ? "can sign in" : "invitation sent"}
        ${x.status === "invited" ? `<button class="linklike" data-resend="${esc(x.ref)}">Resend</button>` : ""}
        <button class="linklike" data-remove="${esc(x.ref)}">Remove</button></li>`).join("")}</ul>` : ""}
      ${list.length < d.max ? `<form id="carerForm" novalidate><div class="field-row">
        <div class="field"><label for="cr-first_name">First name</label><input id="cr-first_name" name="first_name" required></div>
        <div class="field"><label for="cr-last_name">Last name</label><input id="cr-last_name" name="last_name" required></div></div>
        <div class="field-row"><div class="field"><label for="cr-email">Their email</label><input type="email" id="cr-email" name="email" required></div>
        <div class="field"><label for="cr-relationship">Relationship (optional)</label><input id="cr-relationship" name="relationship" placeholder="e.g. Grandparent"></div></div>
        <div class="pcard__actions"><button class="btn btn--ghost" type="submit">Send an invitation</button></div></form>` : ""}`;
    const f = $("#carerForm", box);
    if (f) f.onsubmit = async (e) => {
      e.preventDefault();
      try {
        await busy($("button[type=submit]", f), () => api("/api/account/carers", { first_name: f.first_name.value, last_name: f.last_name.value,
          email: f.email.value, relationship: f.relationship.value }));
        draw((await api("/api/account/carers")).carers);
        box.insertAdjacentHTML("beforeend", notice("Invitation sent. They'll get an email to choose a password.", "ok"));
      } catch (x) { showErrors(f, (x.data && x.data.errors) || {}, "cr", x.message); }
    };
    $$("[data-remove]", box).forEach(b => b.onclick = async () => {
      if (!confirm("Remove this carer? They'll be signed out straight away.")) return;
      await api(`/api/account/carers/${encodeURIComponent(b.dataset.remove)}/remove`, {});
      draw((await api("/api/account/carers")).carers);
    });
    $$("[data-resend]", box).forEach(b => b.onclick = async () => {
      await busy(b, () => api(`/api/account/carers/${encodeURIComponent(b.dataset.resend)}/resend`, {}));
      b.textContent = "Sent ✓"; b.disabled = true;
    });
  };
  draw(d.carers);
}

/* ---------------- a carer accepts an invitation ---------------- */
export async function carerInvite() {
  const token = hashToken();
  if (!token) return go("/login");
  let check;
  try { check = await api("/api/account/carer-invite/check", { token }); }
  catch (x) { root().innerHTML = `<div class="portal__narrow"><h1>Invitation</h1>${notice(esc(x.message), "err")}</div>`; return; }
  root().innerHTML = `<div class="portal__narrow"><h1>Welcome, ${esc(check.first_name)}</h1>
    <p class="lead">${esc(check.holder)} has invited you to help with their family's bookings at Honeycombe Arts Hub.</p>
    <form id="ciForm" class="pcard" novalidate>
      <p>You'll sign in as <strong>${esc(check.email)}</strong>.</p>
      <div class="field"><label for="ci-password">Choose a password</label>
        <span class="hint">At least 10 characters — three random words works well.</span>
        <input type="password" id="ci-password" name="password" autocomplete="new-password" required></div>
      <label class="check"><input type="checkbox" name="agree" id="ci-agree"> <span>I'll keep the family's information private and only use this account to help ${esc(check.holder)} with bookings.</span></label>
      <div class="btn-row"><span></span><button class="btn btn--orange" type="submit">Accept and sign in</button></div></form></div>`;
  const form = $("#ciForm");
  form.onsubmit = async (e) => {
    e.preventDefault();
    try {
      await busy($("button[type=submit]", form), () => api("/api/account/carer-invite/accept", { token, password: form.password.value, agree: form.agree.checked }));
      history.replaceState(null, "", location.pathname);
      go("/account");
    } catch (x) { showErrors(form, (x.data && x.data.errors) || {}, "ci", x.message); }
  };
}

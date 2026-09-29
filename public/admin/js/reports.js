/* Reports: the attendance dashboard (inline SVG chart with a table behind it). */
import { $, $$, api, can, confirmBox, esc, post, qs, table, toast, ukNowLocal, ukToday } from "./ui.js";

const A = window.HAHAdmin;
const COLOURS = ["#2e7d32", "#f57c00", "#6a1b9a", "#1565c0", "#ad1457", "#00838f", "#4e342e", "#78909c"];
const isoToday = ukToday;

function label(key, bucket) {
  const d = new Date((bucket === "month" ? key + "-01" : key) + "T12:00:00");
  return bucket === "month" ? d.toLocaleDateString("en-GB", { month: "short" }) : d.toLocaleDateString("en-GB", { day: "numeric", month: "short" });
}

function chart(d) {
  const cats = d.category_order.filter(c => d.series.some(s => s.by_category[c])).concat(
    [...new Set(d.series.flatMap(s => Object.keys(s.by_category)))].filter(c => !d.category_order.includes(c)));
  const colour = (c) => COLOURS[cats.indexOf(c) % COLOURS.length];
  const max = Math.max(1, ...d.series.map(s => s.present));
  const W = 760, H = 240, pad = 30, n = d.series.length, bw = Math.max(4, (W - pad) / n - 4);
  const bars = d.series.map((s, i) => {
    let y = H - 20;
    const x = pad + i * ((W - pad) / n);
    const parts = cats.map(c => {
      const v = s.by_category[c] || 0; if (!v) return "";
      const h = (v / max) * (H - 40); y -= h;
      return `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${bw.toFixed(1)}" height="${h.toFixed(1)}" fill="${colour(c)}"><title>${esc(c)}: ${v}</title></rect>`;
    }).join("");
    const lbl = n <= 16 || i % Math.ceil(n / 16) === 0 ? `<text x="${(x + bw / 2).toFixed(1)}" y="${H - 5}" font-size="10" text-anchor="middle" fill="#555">${esc(label(s.key, d.bucket))}</text>` : "";
    return parts + lbl;
  }).join("");
  const grid = [0, .5, 1].map(f => `<line x1="${pad}" x2="${W}" y1="${(H - 20 - f * (H - 40)).toFixed(1)}" y2="${(H - 20 - f * (H - 40)).toFixed(1)}" stroke="#e5e0d5"/>
    <text x="${pad - 4}" y="${(H - 16 - f * (H - 40)).toFixed(1)}" font-size="10" text-anchor="end" fill="#777">${Number.isInteger(f * max) ? f * max : ""}</text>`).join("");
  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Attendance ${esc(d.label)}" class="chart">${grid}${bars}</svg>
    <p class="legend">${cats.map(c => `<span><i style="background:${colour(c)}"></i>${esc(c)}</span>`).join("")}</p>`;
}

function yoyChart(years) {
  const W = 760, H = 240, pad = 34, n = 12, step = (W - pad - 10) / (n - 1);
  const vals = years.flatMap(y => y.months.filter(m => !m.future).map(m => m.total));
  const max = Math.max(1, ...vals);
  const y = (v) => (H - 22 - (v / max) * (H - 44)).toFixed(1);
  const colours = ["#b0bec5", "#f57c00", "#2e7d32", "#6a1b9a", "#1565c0", "#ad1457"];
  const lines = years.map((yr, i) => {
    const pts = yr.months.map((m, j) => m.future ? null : `${(pad + j * step).toFixed(1)},${y(m.total)}`).filter(Boolean);
    const c = colours[(colours.length - years.length + i) % colours.length];
    return `<polyline fill="none" stroke="${c}" stroke-width="${i === years.length - 1 ? 3 : 2}" points="${pts.join(" ")}"/>` +
      yr.months.map((m, j) => m.future ? "" : `<circle cx="${(pad + j * step).toFixed(1)}" cy="${y(m.total)}" r="3" fill="${c}"><title>${esc(yr.label)} ${esc(m.key)}: ${m.total}</title></circle>`).join("");
  }).join("");
  const labels = years[0].months.map((m, j) => `<text x="${(pad + j * step).toFixed(1)}" y="${H - 5}" font-size="10" text-anchor="middle" fill="#555">${new Date(m.key + "-01T12:00:00").toLocaleDateString("en-GB", { month: "short" })}</text>`).join("");
  const grid = [0, .5, 1].map(f => `<line x1="${pad}" x2="${W}" y1="${y(f * max)}" y2="${y(f * max)}" stroke="#e5e0d5"/><text x="${pad - 4}" y="${(+y(f * max) + 4).toFixed(1)}" font-size="10" text-anchor="end" fill="#777">${Math.round(f * max)}</text>`).join("");
  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Attendances per month, year on year" class="chart">${grid}${lines}${labels}</svg>
    <p class="legend">${years.map((yr, i) => `<span><i style="background:${colours[(colours.length - years.length + i) % colours.length]}"></i>${esc(yr.label)} (${yr.total})</span>`).join("")}</p>`;
}

async function historyCard(root, rerender) {
  const box = $("#histBox", root);
  const d = await api("/api/staff/reports/historic");
  const edit = can("import.run");
  box.innerHTML = `<p class="fhint">Monthly totals from MagicBooking, so the year-on-year chart reaches back before the switch-over. Categories: ${esc(d.categories.join(", "))}.</p>
    ${edit ? `<div class="fgroup"><label for="histCsv">Paste CSV: month, category, attendances, different children (optional)</label>
      <textarea id="histCsv" class="tall" placeholder="month,category,attendances,children&#10;2025-08,Holiday club,412,96&#10;2025-08,HAF,230,61"></textarea></div>
      <p><button class="abtn abtn--ghost abtn--sm" id="histPrev">Check</button> <button class="abtn abtn--primary abtn--sm" id="histSave" disabled>Save</button></p><div id="histMsg"></div>` : ""}
    <details><summary>${d.rows.length} month/category total(s) held</summary>${table([{ label: "Month", get: r => esc(r.month) }, { label: "Category", get: r => esc(r.category) },
      { label: "Attendances", get: r => r.attendances }, { label: "Children", get: r => r.children ?? "—" },
      { label: "", get: r => edit ? `<button class="abtn abtn--ghost abtn--sm" data-hdel="${r.id}">Delete</button>` : "" }], d.rows, { empty: "None yet." })}</details>`;
  if (!edit) return;
  const send = (commit) => post("/api/staff/reports/historic/import", { csv: $("#histCsv", root).value, commit });
  const show = (x) => { $("#histMsg", root).innerHTML = x.problems ? `<ul class="errlist">${x.problems.map(p => `<li>${esc(p)}</li>`).join("")}</ul>` : ""; };
  $("#histCsv", root).oninput = () => { $("#histSave", root).disabled = true; };
  $("#histPrev", root).onclick = async () => {
    try {
      const r = await send(false);
      $("#histMsg", root).innerHTML = `<p>${r.count} row(s), ${r.total} attendances${r.replaces ? ` · replaces ${r.replaces} already held` : ""}.</p>`;
      $("#histSave", root).disabled = false;
    } catch (x) { toast(x.message, true); show(x.data || {}); }
  };
  $("#histSave", root).onclick = async () => {
    try { const r = await send(true); toast(`Saved ${r.saved}`); rerender(); } catch (x) { toast(x.message, true); show(x.data || {}); }
  };
  $$("[data-hdel]", root).forEach(b => b.onclick = async () => {
    if (!await confirmBox("Delete this total?", "Delete")) return;
    await post(`/api/staff/reports/historic/${b.dataset.hdel}/delete`, {}); rerender();
  });
}

A.addTab({
  id: "reports", label: "Reports", icon: "📊", perm: "reports.view",
  state: { period: "month", date: null },
  async render(root) {
    const st = this.state;
    st.date = st.date || isoToday();
    const [d, tr, yoy] = await Promise.all([api("/api/staff/reports/attendance?" + qs({ period: st.period, date: st.date })),
      api("/api/staff/reports/trials?" + qs({ period: st.period, date: st.date })), api("/api/staff/reports/year-on-year?years=3")]);
    const tt = tr.totals;
    const t = d.totals;
    const rate = t.present + t.absent + t.absent_notified ? Math.round(100 * t.present / (t.present + t.absent + t.absent_notified)) : null;
    root.innerHTML = `<h1>Attendance</h1><p class="sub">${esc(d.label)} · from registers (only sessions that have happened count as attended)</p>
      <div class="toolbar"><div class="segtabs">${[["day", "Day"], ["week", "Week"], ["month", "Month"], ["quarter", "Quarter"], ["year", "Year"]].map(([k, l]) =>
        `<button class="abtn abtn--sm ${k === st.period ? "abtn--honey" : "abtn--ghost"}" data-p="${k}">${l}</button>`).join("")}</div>
        <input type="date" id="rDate" aria-label="Date in the period" value="${esc(st.date)}">
        <a class="abtn abtn--ghost abtn--sm" href="/api/staff/reports/attendance.csv?${qs({ period: st.period, date: st.date })}">Daily breakdown CSV</a>
        <a class="abtn abtn--ghost abtn--sm" href="/api/staff/reports/haf.csv?${qs({ from: d.first, to: d.last })}">HAF export</a></div>
      <div class="statgrid">
        <div class="stat"><strong>${t.present}</strong><span>attendances</span></div>
        <div class="stat"><strong>${t.children}</strong><span>different children (${t.new_children} new)</span></div>
        <div class="stat"><strong>${rate == null ? "—" : rate + "%"}</strong><span>turned up (${t.no_shows} no-shows)</span></div>
        <div class="stat"><strong>${t.sessions}</strong><span>sessions run</span></div>
        <div class="stat"><strong>${t.haf}</strong><span>HAF attendances</span></div>
        <div class="stat"><strong>${t.send}</strong><span>attendances by children with SEND</span></div>
        <div class="stat"><strong>${d.booked_ahead}</strong><span>places booked ahead</span></div></div>
      <div class="acard"><h2>Attendance by ${d.bucket === "month" ? "month" : "day"}</h2>${d.series.length > 1 ? chart(d) : ""}
        <details><summary>Show as a table</summary>${table([{ label: d.bucket === "month" ? "Month" : "Day", get: s => esc(label(s.key, d.bucket)) },
          { label: "Attended", get: s => s.present }], d.series)}</details></div>
      <div class="acard"><h2>By type of session</h2>${table([{ label: "Category", get: c => esc(c.category) }, { label: "Attended", get: c => c.present },
        { label: "Absent", get: c => c.absent }, { label: "Not marked yet", get: c => c.expected }], d.categories, { empty: "No sessions in this period." })}</div>
      <div class="acard"><h2>Trial sessions</h2>
        <p class="fhint">A trial “converts” when the child is booked again (not as a trial) within ${tr.convert_days} days.</p>
        <div class="statgrid">
          <div class="stat"><strong>${tt.trials}</strong><span>trials${tt.upcoming ? ` (${tt.upcoming} still to come)` : ""}</span></div>
          <div class="stat"><strong>${tt.attended}</strong><span>came (${tt.no_shows} no-shows)</span></div>
          <div class="stat"><strong>${tt.converted}</strong><span>booked again (${tt.converted_same} the same activity)</span></div>
          <div class="stat"><strong>${tt.rate == null ? "—" : tt.rate + "%"}</strong><span>conversion rate</span></div></div>
        ${table([{ label: "Activity", get: a => esc(a.title) }, { label: "Trials", get: a => a.trials }, { label: "Came", get: a => a.attended },
          { label: "Booked again", get: a => a.converted }], tr.activities, { empty: "No trial sessions in this period." })}
        ${can("bookings.view") ? `<p><a href="#" id="trialList">See trial bookings →</a></p>` : ""}</div>
      <div class="acard"><h2>Year on year</h2>
        <p class="fhint">Attendances each month of the reporting year${yoy.years.some(y => y.has_historic) ? ", including totals carried over from MagicBooking" : ""}.</p>
        ${yoyChart(yoy.years)}
        <details><summary>Show as a table</summary>${table([{ label: "Month", get: (m, i) => esc(new Date(m.key + "-01T12:00:00").toLocaleDateString("en-GB", { month: "short" })) },
          ...yoy.years.map((y, i) => ({ label: y.label, get: m => { const v = y.months[yoy.years[0].months.indexOf(m)]; return v.future ? "" : v.total + (v.historic ? "*" : ""); } }))],
          yoy.years[0].months)}<p class="fhint">* includes MagicBooking figures.</p></details></div>
      <div class="acard"><h2>MagicBooking history</h2><div id="histBox"></div></div>
      <div class="acard"><h2>Top activities</h2>${table([{ label: "Activity", get: a => esc(a.title) }, { label: "Attended", get: a => a.present },
        { label: "Absent", get: a => a.absent }], d.activities, { empty: "—" })}</div>`;
    $$("[data-p]", root).forEach(b => b.onclick = () => { st.period = b.dataset.p; this.render(root); });
    historyCard(root, () => this.render(root));
    if ($("#trialList", root)) $("#trialList", root).onclick = (e) => { e.preventDefault(); A.openTab("bookings", { quick: "trials" }); };
    $("#rDate", root).onchange = (e) => { st.date = e.target.value || isoToday(); this.render(root); };
  },
});

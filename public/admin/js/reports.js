/* Reports: the attendance dashboard (inline SVG chart with a table behind it). */
import { $, $$, api, can, esc, qs, table } from "./ui.js";

const A = window.HAHAdmin;
const COLOURS = ["#2e7d32", "#f57c00", "#6a1b9a", "#1565c0", "#ad1457", "#00838f", "#4e342e", "#78909c"];
const isoToday = () => new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 10);

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

A.addTab({
  id: "reports", label: "Reports", icon: "📊", perm: "reports.view",
  state: { period: "month", date: null },
  async render(root) {
    const st = this.state;
    st.date = st.date || isoToday();
    const [d, tr] = await Promise.all([api("/api/staff/reports/attendance?" + qs({ period: st.period, date: st.date })),
      api("/api/staff/reports/trials?" + qs({ period: st.period, date: st.date }))]);
    const tt = tr.totals;
    const t = d.totals;
    const rate = t.present + t.absent + t.absent_notified ? Math.round(100 * t.present / (t.present + t.absent + t.absent_notified)) : null;
    root.innerHTML = `<h1>Attendance</h1><p class="sub">${esc(d.label)} · from registers (only sessions that have happened count as attended)</p>
      <div class="toolbar"><div class="segtabs">${[["day", "Day"], ["week", "Week"], ["month", "Month"], ["quarter", "Quarter"], ["year", "Year"]].map(([k, l]) =>
        `<button class="abtn abtn--sm ${k === st.period ? "abtn--honey" : "abtn--ghost"}" data-p="${k}">${l}</button>`).join("")}</div>
        <input type="date" id="rDate" value="${esc(st.date)}">
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
      <div class="acard"><h2>Top activities</h2>${table([{ label: "Activity", get: a => esc(a.title) }, { label: "Attended", get: a => a.present },
        { label: "Absent", get: a => a.absent }], d.activities, { empty: "—" })}</div>`;
    $$("[data-p]", root).forEach(b => b.onclick = () => { st.period = b.dataset.p; this.render(root); });
    if ($("#trialList", root)) $("#trialList", root).onclick = (e) => { e.preventDefault(); A.openTab("bookings", { quick: "trials" }); };
    $("#rDate", root).onchange = (e) => { st.date = e.target.value || isoToday(); this.render(root); };
  },
});

/* ==================================================================
   Aeris — rendering. Plain JS, no dependencies. Loads data.live.json
   (produced by pipeline/build_data.py) and renders the selected
   Bundesland. All charts are hand-built SVG.
================================================================== */

const SVGNS = "http://www.w3.org/2000/svg";

function el(tag, attrs = {}, kids = []) {
  const node = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  for (const kid of [].concat(kids)) node.appendChild(kid);
  return node;
}
function h(tag, cls, html) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (html != null) n.innerHTML = html;
  return n;
}
function fmtDelta(sg) {
  if (sg.flat) return "stabil";
  const sign = sg.delta > 0 ? "+" : "";
  return sign + sg.delta.toLocaleString("de-DE", { maximumFractionDigits: 1 }) + (sg.unit || "");
}

let PAYLOAD = null;
let CURRENT = null;
const sel = document.getElementById("regionSelect");

/* ---------- boot ---------- */
async function boot() {
  try {
    const res = await fetch("data.live.json", { cache: "no-store" });
    PAYLOAD = await res.json();
  } catch (e) {
    document.getElementById("ctxline").innerHTML =
      `<b>Daten konnten nicht geladen werden.</b> Bitte über einen lokalen Server öffnen ` +
      `(<code>python3 -m http.server</code>) und <code>pipeline/build_data.py</code> ausführen.`;
    return;
  }

  // region picker
  PAYLOAD.states.forEach((s) => {
    const o = document.createElement("option");
    o.value = s.id; o.textContent = s.name;
    sel.appendChild(o);
  });
  // default to the highest current occupancy — the most decision-relevant
  const hottest = [...PAYLOAD.states].sort((a, b) => b.current - a.current)[0];
  sel.value = hottest.id;
  sel.addEventListener("change", render);

  // "as of" labels
  document.querySelectorAll("[data-asof]").forEach((n) => {
    n.textContent = "Stand " + PAYLOAD.asOfLabel;
  });
  // hero stats
  const nAvg = Math.round(
    PAYLOAD.states.reduce((s, x) => s + x.current, 0) / PAYLOAD.states.length);
  setText("stat-regions", PAYLOAD.states.length);
  setText("stat-national", nAvg + "%");

  render();
}

function setText(id, v) { const n = document.getElementById(id); if (n) n.textContent = v; }

/* ==================================================================
   TREND CHART
================================================================== */
function renderTrend(state) {
  const host = document.getElementById("trendChart");
  host.innerHTML = "";

  const W = 1000, H = 380;
  const m = { t: 20, r: 20, b: 40, l: 44 };
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const yMin = 45, yMax = 100;

  const obs = state.observed;
  const fc = state.horizon;
  const labels = obs.map((d) => d.label).concat(fc.map((d) => "Wo. " + d.week));
  const n = obs.length + fc.length;
  const X = (i) => m.l + (iw * i) / (n - 1);
  const Y = (v) => m.t + ih * (1 - (v - yMin) / (yMax - yMin));

  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img",
    "aria-label": "Verlauf und Prognose der Intensivbelegung" });

  const zone = (lo, hi, fill) => el("rect", {
    x: m.l, width: iw, y: Y(hi), height: Y(lo) - Y(hi), fill, opacity: 0.5 });
  svg.appendChild(zone(85, 100, "var(--critical-soft)"));
  svg.appendChild(zone(70, 85, "var(--elevated-soft)"));
  svg.appendChild(zone(yMin, 70, "var(--normal-soft)"));

  [50, 60, 70, 80, 90, 100].forEach((v) => {
    svg.appendChild(el("line", { x1: m.l, x2: m.l + iw, y1: Y(v), y2: Y(v),
      stroke: "var(--rule)", "stroke-width": 1 }));
    const t = el("text", { x: m.l - 10, y: Y(v) + 4, "text-anchor": "end",
      fill: "var(--muted)", "font-size": 12 });
    t.textContent = v;
    svg.appendChild(t);
  });
  [70, 85].forEach((v) => {
    svg.appendChild(el("line", { x1: m.l, x2: m.l + iw, y1: Y(v), y2: Y(v),
      stroke: v === 85 ? "var(--critical)" : "var(--elevated)",
      "stroke-width": 1, "stroke-dasharray": "3 5", opacity: 0.45 }));
  });

  // confidence band
  const nowI = obs.length - 1;
  const bandTop = [[X(nowI), Y(obs[nowI].pct)]];
  const bandBot = [[X(nowI), Y(obs[nowI].pct)]];
  fc.forEach((d, k) => {
    const i = obs.length + k;
    bandTop.push([X(i), Y(d.hi)]);
    bandBot.push([X(i), Y(d.lo)]);
  });
  const bandPath = "M" + bandTop.map((p) => p.join(",")).join(" L ")
    + " L " + bandBot.reverse().map((p) => p.join(",")).join(" L ") + " Z";
  svg.appendChild(el("defs", {}, [
    el("linearGradient", { id: "band", x1: 0, y1: 0, x2: 0, y2: 1 }, [
      el("stop", { offset: "0%", "stop-color": "var(--accent)", "stop-opacity": 0.20 }),
      el("stop", { offset: "100%", "stop-color": "var(--accent)", "stop-opacity": 0.06 }),
    ]),
  ]));
  svg.appendChild(el("path", { d: bandPath, fill: "url(#band)" }));

  // observed line
  svg.appendChild(el("path", { d: "M" + obs.map((d, i) => `${X(i)},${Y(d.pct)}`).join(" L "),
    fill: "none", stroke: "var(--ink)", "stroke-width": 3,
    "stroke-linecap": "round", "stroke-linejoin": "round" }));

  // forecast line (dashed), anchored at now
  const fcSeq = [obs[nowI]].concat(fc);
  const fcPath = "M" + fcSeq.map((d, k) => {
    const i = k === 0 ? nowI : obs.length + k - 1;
    return `${X(i)},${Y(d.pct)}`;
  }).join(" L ");
  svg.appendChild(el("path", { d: fcPath, fill: "none", stroke: "var(--accent)",
    "stroke-width": 3, "stroke-dasharray": "2 7",
    "stroke-linecap": "round", "stroke-linejoin": "round" }));

  // now marker
  svg.appendChild(el("line", { x1: X(nowI), x2: X(nowI), y1: m.t, y2: m.t + ih,
    stroke: "var(--rule-strong)", "stroke-width": 1, "stroke-dasharray": "2 4" }));
  const nowT = el("text", { x: X(nowI) + 6, y: m.t + 12, fill: "var(--muted)", "font-size": 11 });
  nowT.textContent = "Heute";
  svg.appendChild(nowT);

  // dots
  obs.forEach((d, i) => svg.appendChild(el("circle",
    { cx: X(i), cy: Y(d.pct), r: 4, fill: "var(--ink)" })));
  fc.forEach((d, k) => {
    const i = obs.length + k;
    const st = statusFor(d.pct);
    svg.appendChild(el("circle", { cx: X(i), cy: Y(d.pct), r: 6, fill: "#fff",
      stroke: `var(--${st})`, "stroke-width": 3 }));
    const lab = el("text", { x: X(i), y: Y(d.pct) - 14, "text-anchor": "middle",
      fill: `var(--${st})`, "font-size": 14, "font-weight": 700 });
    lab.textContent = d.pct + "%";
    svg.appendChild(lab);
  });

  labels.forEach((lb, i) => {
    const t = el("text", { x: X(i), y: H - 14, "text-anchor": "middle",
      fill: i === nowI ? "var(--ink)" : "var(--muted)", "font-size": 12,
      "font-weight": i === nowI ? 700 : 400 });
    t.textContent = lb;
    svg.appendChild(t);
  });

  host.appendChild(svg);
}

/* ==================================================================
   GAUGE
================================================================== */
function gauge(pct, status) {
  const W = 200, H = 118, cx = 100, cy = 108, r = 84;
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, class: "gauge",
    role: "img", "aria-label": `Belegung ${pct}%` });
  const ang = (v) => Math.PI * (1 - v / 100);
  const pt = (v, rad) => [cx + rad * Math.cos(ang(v)), cy - rad * Math.sin(ang(v))];
  const arc = (v0, v1, rad, color, wpx) => {
    const [x0, y0] = pt(v0, rad), [x1, y1] = pt(v1, rad);
    const large = (v1 - v0) > 50 ? 1 : 0;
    return el("path", { d: `M ${x0} ${y0} A ${rad} ${rad} 0 ${large} 1 ${x1} ${y1}`,
      fill: "none", stroke: color, "stroke-width": wpx });
  };
  svg.appendChild(arc(0, 70, r, "var(--normal-soft)", 13));
  svg.appendChild(arc(70, 85, r, "var(--elevated-soft)", 13));
  svg.appendChild(arc(85, 100, r, "var(--critical-soft)", 13));
  svg.appendChild(arc(0, Math.max(0.5, pct), r, `var(--${status})`, 13));

  const val = el("text", { x: cx, y: cy - 14, "text-anchor": "middle",
    fill: `var(--${status})`, "font-size": 40, "font-weight": 700, "letter-spacing": "-1" });
  val.textContent = pct + "%";
  svg.appendChild(val);

  const [nx, ny] = pt(pct, r - 20);
  svg.appendChild(el("line", { x1: cx, y1: cy, x2: nx, y2: ny,
    stroke: "var(--ink)", "stroke-width": 3.5, "stroke-linecap": "round" }));
  svg.appendChild(el("circle", { cx, cy, r: 6, fill: "var(--ink)" }));
  return svg;
}

/* ==================================================================
   FORECAST CARDS
================================================================== */
function renderCards(state) {
  const host = document.getElementById("forecastCards");
  host.innerHTML = "";
  const confColor = { "Hoch": "normal", "Mittel": "elevated", "Niedrig": "critical" };
  const stLabel = { normal: "Normal", elevated: "Erhöht", critical: "Kritisch" };

  state.horizon.forEach((d, k) => {
    const st = statusFor(d.pct);
    const cc = confColor[d.confidence];
    const p = Math.round(state.pCritical[k] * 100);

    const card = h("article", "fcard");
    card.appendChild(h("div", "fcard__top",
      `<span class="fcard__wk">Woche ${d.week}</span>
       <span class="fcard__range">${d.range}</span>`));

    const body = h("div", "fcard__body");
    const g = h("div", "fcard__gauge");
    g.appendChild(gauge(d.pct, st));
    g.appendChild(h("div", `fcard__status is-${st}`, stLabel[st]));
    body.appendChild(g);

    const read = h("div", "fcard__read");
    const row = h("div", "fcard__row");
    row.appendChild(h("div", "fcard__meta", `Spanne <b>${d.lo}–${d.hi}%</b>`));
    row.appendChild(h("div", `conf soft-${cc}`,
      `<i class="bg-${cc}"></i>${d.confidence}e Konfidenz`));
    read.appendChild(row);

    const pm = h("div", "pmeter");
    pm.appendChild(h("div", "pmeter__track",
      `<div class="pmeter__fill bg-${st}" style="width:${p}%"></div>`));
    pm.appendChild(h("div", "pmeter__label", `${p}% Wahrscheinlichkeit &gt; kritisch (85%)`));
    read.appendChild(pm);

    body.appendChild(read);
    card.appendChild(body);
    host.appendChild(card);
  });
}

/* ==================================================================
   SIGNAL SPARKLINES
================================================================== */
function sparkline(values, color) {
  const W = 180, H = 34, pad = 3;
  const min = Math.min(...values), max = Math.max(...values);
  const rng = max - min || 1;
  const X = (i) => pad + (W - 2 * pad) * (i / (values.length - 1 || 1));
  const Y = (v) => pad + (H - 2 * pad) * (1 - (v - min) / rng);
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, class: "signal__spark",
    preserveAspectRatio: "none" });
  const dLine = "M" + values.map((v, i) => `${X(i)},${Y(v)}`).join(" L ");
  const dArea = dLine + ` L ${X(values.length - 1)},${H} L ${X(0)},${H} Z`;
  const gid = "sg" + Math.round(Math.abs(values[0] || 0) * 1000 + values.length + rng);
  svg.appendChild(el("defs", {}, [
    el("linearGradient", { id: gid, x1: 0, y1: 0, x2: 0, y2: 1 }, [
      el("stop", { offset: "0%", "stop-color": color, "stop-opacity": 0.20 }),
      el("stop", { offset: "100%", "stop-color": color, "stop-opacity": 0 }),
    ]),
  ]));
  svg.appendChild(el("path", { d: dArea, fill: `url(#${gid})` }));
  svg.appendChild(el("path", { d: dLine, fill: "none", stroke: color,
    "stroke-width": 2, "stroke-linecap": "round", "stroke-linejoin": "round" }));
  svg.appendChild(el("circle", { cx: X(values.length - 1), cy: Y(values[values.length - 1]),
    r: 2.6, fill: color }));
  return svg;
}

function renderSignals(state) {
  const host = document.getElementById("signalGrid");
  host.innerHTML = "";
  state.signals.forEach((s) => {
    const risk = s.flat ? "flat"
      : (s.invert ? (s.delta < 0 ? "up" : (s.delta > 0 ? "down" : "flat"))
                  : (s.delta > 0 ? "up" : (s.delta < 0 ? "down" : "flat")));
    const color = risk === "up" ? "var(--critical)"
                : risk === "down" ? "var(--normal)" : "var(--muted)";

    const c = h("article", "signal");
    c.appendChild(h("div", "signal__top",
      `<div>
         <div class="signal__name">${s.name}</div>
         <div class="signal__role">${s.role}</div>
       </div>
       <span class="signal__delta delta--${risk}">${fmtDelta(s)}</span>`));
    c.appendChild(sparkline(s.spark.length ? s.spark : [0, 0], color));
    c.appendChild(h("div", "signal__detail", s.detail));

    const foot = h("div", "signal__foot");
    foot.innerHTML = `<span>Vorlauf ${s.lead}</span>
      <span class="wbar"><i style="width:${Math.round(s.weight * 100)}%"></i></span>
      <span class="signal__weight">${Math.round(s.weight * 100)}%</span>`;
    c.appendChild(foot);
    host.appendChild(c);
  });
}

/* ==================================================================
   CONTEXT LINE + HERO CARD
================================================================== */
function renderCtx(state) {
  const w2 = state.horizon[1];
  const st = statusFor(w2.pct);
  const stLabel = { normal: "normal", elevated: "erhöht", critical: "kritisch" }[st];
  document.getElementById("ctxline").innerHTML =
    `In <b>${state.name}</b> liegt die Intensivbelegung aktuell bei
     <b>${state.current}%</b> und dürfte in zwei Wochen rund <b>${w2.pct}%</b>
     erreichen — <b class="is-${st}">${stLabel}</b>, bei etwa
     <b>${state.beds.toLocaleString("de-DE")}</b> betreibbaren Intensivbetten.
     Naive Persistenz-Basislinie: <b>${state.baseline[1]}%</b>.`;
}

function renderHero(state) {
  const w2 = state.horizon[1];
  const st = statusFor(w2.pct);
  const stLabel = { normal: "Normal", elevated: "Erhöht", critical: "Kritisch" }[st];
  setText("hero-region", state.name + " · Intensivbelegung");
  setText("hero-now", state.current);
  const pill = document.getElementById("hero-pill");
  if (pill) {
    pill.textContent = `In 2 Wochen: ${w2.pct}% · ${stLabel}`;
    pill.className = "pill pill--" + st;
  }
  const pc = Math.round(state.pCritical[1] * 100);
  setText("hero-foot", `${pc}% Wahrscheinlichkeit, die kritische Kapazität zu überschreiten`);
  drawHeroSpark(state);
}

function drawHeroSpark(state) {
  const host = document.getElementById("heroSpark");
  if (!host) return;
  const obs = state.observed.map((o) => o.pct);
  const fc = state.horizon.map((hh) => hh.pct);
  const all = obs.concat(fc);
  const W = 320, H = 96, min = 45, max = 100;
  const X = (i) => (W * i) / (all.length - 1);
  const Y = (v) => H - (H - 16) * (v - min) / (max - min) - 8;
  host.innerHTML = "";
  const nowI = obs.length - 1;
  host.appendChild(el("path", {
    d: "M" + obs.map((v, i) => `${X(i)},${Y(v)}`).join(" L "),
    fill: "none", stroke: "var(--ink-2)", "stroke-width": 2.5, "stroke-linecap": "round" }));
  const fcSeq = [obs[nowI]].concat(fc);
  host.appendChild(el("path", {
    d: "M" + fcSeq.map((v, k) => `${X(nowI + k)},${Y(v)}`).join(" L "),
    fill: "none", stroke: "var(--accent)", "stroke-width": 2.5,
    "stroke-dasharray": "2 6", "stroke-linecap": "round" }));
  host.appendChild(el("circle", { cx: X(nowI), cy: Y(obs[nowI]), r: 4, fill: "var(--ink)" }));
  host.appendChild(el("circle", { cx: X(all.length - 1), cy: Y(all[all.length - 1]),
    r: 4, fill: "var(--accent)" }));
}

/* ---------- master render ---------- */
function render() {
  CURRENT = PAYLOAD.states.find((s) => s.id === sel.value) || PAYLOAD.states[0];
  renderCtx(CURRENT);
  renderTrend(CURRENT);
  renderCards(CURRENT);
  renderSignals(CURRENT);
  renderHero(CURRENT);
}

boot();

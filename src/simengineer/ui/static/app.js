/* SimEngineer — interface locale. Vanilla JS, aucune dépendance, lecture seule. */
"use strict";

const $ = (sel, el = document) => el.querySelector(sel);
let LANG = "fr";

/* ---------- utilitaires ---------- */
function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "style") el.setAttribute("style", v);
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}
const svgNS = "http://www.w3.org/2000/svg";
function s(tag, attrs = {}) {
  const el = document.createElementNS(svgNS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  return el;
}
const isNum = (v) => typeof v === "number" && Number.isFinite(v);
function num(v, d = 1) {
  if (!isNum(v)) return "—";
  const t = v.toFixed(d);
  return LANG === "fr" ? t.replace(".", ",") : t;
}
function signed(v, d = 2) {
  if (!isNum(v)) return "—";
  const r = Number(v.toFixed(d));
  return (r > 0 ? "+" : r < 0 ? "−" : "±") + num(Math.abs(r), d);
}
const LAP_FLAGS = { offtrack: "hors-piste", incident: "incident", pit: "stand", yellow: "jaune", incomplete: "incomplet" };
function lapTime(v) {
  if (!isNum(v)) return "—";
  const m = Math.floor(v / 60);
  const sec = v - m * 60;
  const t = (m ? `${m}:${sec < 10 ? "0" : ""}` : "") + sec.toFixed(3);
  return LANG === "fr" ? t.replace(".", ",") : t;
}
function setText(id, text, cls) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = text;
  if (cls !== undefined) el.className = cls;
}
async function api(path) {
  const r = await fetch(path, { cache: "no-store" });
  if (!r.ok) throw new Error(`${r.status} ${path}`);
  return r.json();
}
function dateFr(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleString(LANG === "fr" ? "fr-FR" : "en-GB", {
    day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit",
  });
}
function loading(page) {
  page.replaceChildren(h("div", { class: "waiting" }, h("div", {}, "Chargement…"), h("div", { class: "scan" })));
}
function failed(page, err) {
  page.replaceChildren(h("div", { class: "waiting" }, h("div", { class: "huge" }, "ERREUR"), h("div", {}, String(err))));
}

/* ---------- navigation ---------- */
const PAGES = ["live", "sessions", "driver", "setup", "refs"];
const state = { session: null, sessions: [], lap: null };

function route() {
  const [page, arg] = (location.hash.slice(1) || "live").split("/");
  const name = PAGES.includes(page) ? page : "live";
  for (const p of PAGES) {
    $(`#page-${p}`).classList.toggle("active", p === name);
    $(`nav.tabs a[data-page="${p}"]`).classList.toggle("active", p === name);
  }
  if (arg && /^\d+$/.test(arg)) state.session = Number(arg);
  if (name === "sessions") renderSessions();
  if (name === "driver") renderDriver();
  if (name === "setup") renderSetup();
  if (name === "refs") renderRefs();
}
window.addEventListener("hashchange", route);

/* ---------- direct ---------- */
let track = null;
let trackVersion = -1;

const PIT = {
  unknown: ["CALCUL…", "c-muted", "Il faut un tour propre pour estimer la conso."],
  no_stop: ["PAS D'ARRÊT", "c-ok", "Le carburant suffit jusqu'à l'arrivée."],
  wait: ["ATTENDRE", "c-accent", null],
  open: ["OUVERTE", "c-ok", null],
  last_chance: ["CE TOUR !", "c-bad", "Dernier tour possible pour rentrer."],
  pit_now_yellow: ["RENTRE", "c-warn", "Neutralisation : l'arrêt coûte moins cher."],
};
const FLAGS = {
  green: "Vert", yellow: "Jaune", fcy: "Neutralisation", red: "Rouge",
  blue: "Bleu", white: "Dernier tour", checkered: "Damier",
};

function setStatus(status) {
  const pill = $("#status");
  pill.className = "pill" + (status === "live" ? " live" : status === "ended" ? " ended" : "");
  setText("status-text", status === "live" ? "En direct" : status === "ended" ? "Session terminée" : status === "offline" ? "Hors ligne" : "En attente");
}

function renderLive(snap) {
  setStatus(snap.status);
  const hasData = snap.status === "live" || (snap.status === "ended" && snap.session);
  $("#live-waiting").hidden = hasData;
  $("#live-body").hidden = !hasData;
  const flag = $("#flag");
  flag.hidden = !snap.flag || snap.status !== "live";
  if (snap.flag) {
    flag.className = `pill flag ${snap.flag}`;
    flag.textContent = FLAGS[snap.flag] || snap.flag;
  }
  if (!hasData) return;

  const ses = snap.session || {};
  setText("lv-track", ses.track || "—");
  setText("lv-car", `${ses.car || ""} · ${ses.type || ""} · source ${ses.source || ""}`);
  setText("lv-ref", snap.ref_label ? `Référence : ${snap.ref_label}` : "Pas encore de référence");

  // delta
  const laps = snap.laps || {};
  const d = laps.live_delta_to_reference_s;
  setText("lv-delta", isNum(d) ? signed(d, 2) : "—", "huge " + (isNum(d) ? (d <= 0 ? "c-ok" : "c-bad") : "c-muted"));
  const bar = $("#lv-delta-bar");
  if (isNum(d)) {
    const w = Math.min(Math.abs(d), 1) * 50;
    bar.style.width = `${w}%`;
    bar.style.left = d < 0 ? `${50 - w}%` : "50%";
    bar.style.background = d <= 0 ? "var(--ok)" : "var(--bad)";
    bar.style.boxShadow = `0 0 10px ${d <= 0 ? "var(--ok)" : "var(--bad)"}`;
  } else {
    bar.style.width = "0";
  }
  $("#c-delta").className = "card s4 " + (isNum(d) ? (d <= 0 ? "ok" : "bad") : "");

  // tour
  const car = snap.car || {};
  setText("lv-lapno", car.lap ? `n° ${car.lap}` : "—");
  setText("lv-last", lapTime(laps.last_lap_s));
  setText("lv-best", lapTime(laps.best_lap_s));
  setText("lv-dbest", isNum(laps.delta_to_best_s) ? `${signed(laps.delta_to_best_s, 2)} s` : "—");
  setText("lv-speed", isNum(car.speed_kmh) ? Math.round(car.speed_kmh) : "—");
  setText("lv-gear", car.in_pits ? "STAND" : car.gear === 0 ? "N" : car.gear === -1 ? "R" : `${car.gear ?? "—"}e`);
  $("#lv-thr").style.height = `${Math.round((car.throttle || 0) * 100)}%`;
  $("#lv-brk").style.height = `${Math.round((car.brake || 0) * 100)}%`;

  // carburant
  const f = snap.fuel || {};
  setText("lv-fuel-l", isNum(f.fuel_l) ? `${num(f.fuel_l, 1)} L` : "—");
  setText("lv-fuel-laps", num(f.laps_in_tank, 1));
  const parts = [];
  if (isNum(f.avg_per_lap_l)) parts.push(`moy. ${num(f.avg_per_lap_l, 2)} L/tour`);
  if (isNum(f.race_laps_remaining)) parts.push(`${num(f.race_laps_remaining, 1)} tours à faire`);
  if (isNum(f.fuel_to_add_l) && f.fuel_to_add_l > 0) parts.push(`+${f.fuel_to_add_l} L à ajouter`);
  setText("lv-fuel-sub", parts.join(" · ") || "En attente d'un tour propre");
  const ratio = isNum(f.laps_in_tank) && isNum(f.race_laps_remaining) && f.race_laps_remaining > 0
    ? Math.min(f.laps_in_tank / f.race_laps_remaining, 1) : isNum(f.laps_in_tank) ? 1 : 0;
  const fb = $("#lv-fuel-bar");
  fb.style.width = `${ratio * 100}%`;
  const fuelCls = f.can_finish === false ? (ratio < 0.5 ? "bad" : "warn") : "";
  fb.style.background = fuelCls === "bad" ? "var(--bad)" : fuelCls === "warn" ? "var(--warn)" : "var(--accent)";
  $("#c-fuel").className = `card s3 ${fuelCls}`;
  setText("lv-fuel-say", f.say || "—");

  // stand
  const p = snap.pit || {};
  const [word, cls, fixed] = PIT[p.advice] || ["—", "c-muted", null];
  setText("lv-pit", word, `big ${cls}`);
  let sub = fixed;
  if (!sub && p.open_lap !== null && p.open_lap !== undefined) {
    sub = `Tours ${p.open_lap} → ${p.close_lap} · ${p.fuel_to_add_l} L · ${p.stops_needed} arrêt${p.stops_needed > 1 ? "s" : ""}`;
  }
  setText("lv-pit-sub", sub || "");
  $("#c-pit").className = "card s2 " + (cls === "c-bad" ? "bad" : cls === "c-warn" ? "warn" : cls === "c-ok" ? "ok" : "");

  // écarts, pneus, météo
  const g = snap.gap || {};
  setText("lv-ahead", isNum(g.ahead_s) ? `${num(g.ahead_s, 1)} s` : "—");
  setText("lv-behind", isNum(g.behind_s) ? `${num(g.behind_s, 1)} s` : "—");
  const t = snap.tyres || {};
  setText("lv-stint", isNum(t.stint_laps) ? `${t.stint_laps} tours` : "—");
  setText("lv-wear", isNum(t.wear_left_pct) ? `${Math.round(t.wear_left_pct)} %` : "—");
  const w = snap.weather || {};
  setText("lv-track-t", isNum(w.track_temp_c) ? `${Math.round(w.track_temp_c)} °C` : "—");
  setText("lv-air-t", isNum(w.air_temp_c) ? `${Math.round(w.air_temp_c)} °C` : "—");
  setText("lv-dtemp", isNum(w.track_temp_change_c) ? `${signed(w.track_temp_change_c, 0)} °C` : "—");

  // radio
  $("#lv-feed").replaceChildren(
    ...(snap.spoken || []).map((m) => h("li", { class: `p${m.p}` }, h("span", { class: `tag p${m.p}` }, `P${m.p}`), h("span", {}, m.text))),
  );
  if (!(snap.spoken || []).length) $("#lv-feed").append(h("li", {}, h("span", { class: "c-muted" }, "Silence radio.")));

  // coach
  const adv = snap.advice;
  setText("lv-adv-lap", adv ? `tour ${adv.lap} · ${signed(adv.delta_s, 2)} s vs réf.` : "");
  const items = adv ? adv.items : [];
  $("#lv-advice").replaceChildren(
    ...(items.length
      ? items.map((a) => h("li", {}, h("span", { class: "cn" }, a.corner), h("span", {}, a.text.replace(/^T\d+ : /, "")), h("span", { class: "gain" }, `−${num(a.gain_s, 2)} s`)))
      : [h("li", { class: "empty", style: "display:block;border:0;background:none" }, adv ? "Tour propre, rien à signaler." : "Les conseils arrivent après un tour complet comparé à la référence.")]),
  );

  // carte
  if (snap.track_version !== undefined && snap.track_version !== trackVersion) {
    trackVersion = snap.track_version;
    api("/api/track").then((tr) => { track = tr; drawTrack(); placeCars(snap); }).catch(() => {});
  }
  placeCars(snap);
}

function drawTrack() {
  const wrap = $("#lv-map");
  if (!track || !track.points || track.points.length < 3) return;
  const pts = track.points.map((p) => p.join(",")).join(" ");
  const svg = s("svg", { class: "track", viewBox: "-40 -40 1080 1080", preserveAspectRatio: "xMidYMid meet" });
  svg.append(s("polygon", { class: "tk-glow", points: pts }));
  svg.append(s("polygon", { class: "tk", points: pts }));
  svg.append(s("polygon", { class: "tk-line", points: pts }));
  const [x0, y0] = track.points[0];
  const [x1, y1] = track.points[1];
  const len = Math.hypot(x1 - x0, y1 - y0) || 1;
  const nx = -(y1 - y0) / len, ny = (x1 - x0) / len;
  svg.append(s("line", { class: "sf", x1: x0 - nx * 14, y1: y0 - ny * 14, x2: x0 + nx * 14, y2: y0 + ny * 14 }));
  const cx = track.points.reduce((a, p) => a + p[0], 0) / track.points.length;
  const cy = track.points.reduce((a, p) => a + p[1], 0) / track.points.length;
  for (const c of track.corners || []) {
    const [x, y] = track.points[c.i];
    const dx = x - cx, dy = y - cy, dl = Math.hypot(dx, dy) || 1;
    const label = s("text", { class: "corner", x: x + (dx / dl) * 38, y: y + (dy / dl) * 38 + 8, "text-anchor": "middle" });
    label.textContent = c.name;
    svg.append(label);
  }
  svg.append(s("g", { id: "others" }));
  svg.append(s("circle", { id: "me", class: "me", r: 13, cx: x0, cy: y0 }));
  wrap.replaceChildren(svg);
  setText("lv-map-info", `${num(track.length_m / 1000, 2)} km · ${(track.corners || []).length} virages`);
}

function posAt(pct) {
  const n = track.points.length;
  const x = ((pct % 1) + 1) % 1 * n;
  const i = Math.floor(x) % n, j = (i + 1) % n, f = x - Math.floor(x);
  const a = track.points[i], b = track.points[j];
  return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f];
}

function placeCars(snap) {
  if (!track || !track.points) return;
  const me = document.getElementById("me");
  if (!me) return;
  if (snap.car && isNum(snap.car.pct)) {
    const [x, y] = posAt(snap.car.pct);
    me.setAttribute("cx", x);
    me.setAttribute("cy", y);
  }
  const g = document.getElementById("others");
  g.replaceChildren(...(snap.others || []).map((pct) => {
    const [x, y] = posAt(pct);
    return s("circle", { class: "other", r: 8, cx: x, cy: y });
  }));
}

function connectLive() {
  if (!window.EventSource) {
    setInterval(() => api("/api/live").then(renderLive).catch(() => setStatus("offline")), 250);
    return;
  }
  const es = new EventSource("/api/live/stream");
  es.onmessage = (ev) => renderLive(JSON.parse(ev.data));
  es.onerror = () => setStatus("offline");
}

/* ---------- séances ---------- */
async function renderSessions() {
  const page = $("#page-sessions");
  loading(page);
  try {
    state.sessions = await api("/api/sessions");
  } catch (e) { failed(page, e); return; }
  if (!state.sessions.length) {
    page.replaceChildren(emptyPage("AUCUNE SÉANCE", "Roule une séance (ou lance une démo synthétique) pour la voir ici."));
    return;
  }
  if (!state.session || !state.sessions.some((x) => x.id === state.session)) state.session = state.sessions[0].id;
  const list = h("div", { class: "list" }, state.sessions.map((x) =>
    h("button", { class: x.id === state.session ? "sel" : "", onclick: () => { location.hash = `#sessions/${x.id}`; } },
      h("div", { class: "t" }, x.track),
      h("div", { class: "m" }, h("span", {}, `${x.type} · ${dateFr(x.started_at)}`), h("span", { class: "num c-accent" }, lapTime(x.best_s))),
      h("div", { class: "m" }, h("span", {}, x.car), h("span", {}, `${x.valid}/${x.laps} tours`)),
    )));
  const detail = h("div", {});
  page.replaceChildren(h("div", { class: "grid" }, h("div", { class: "s3 side" }, h("div", { class: "section-title", style: "margin-top:0" }, "Séances"), list), h("div", { class: "s9" }, detail)));
  loading(detail);
  try {
    const d = await api(`/api/sessions/${state.session}`);
    renderSessionDetail(detail, d);
  } catch (e) { failed(detail, e); }
}

function kpi(label, value, unit = "", cls = "") {
  return h("div", { class: `card ${cls}` }, h("h3", {}, label), h("div", { class: "big num" }, value, unit ? h("span", { class: "unit" }, unit) : null));
}

function renderSessionDetail(el, d) {
  const sm = d.summary;
  const byLap = Object.fromEntries(d.analyses.map((a) => [a.lap, a]));
  if (state.lap === null || !byLap[state.lap]) state.lap = d.analyses.length ? d.analyses[d.analyses.length - 1].lap : null;
  const head = h("div", { class: "page-head" },
    h("div", {}, h("h1", {}, sm.track), h("div", { class: "c-muted" }, `${sm.car} · ${sm.session_type} · ${sm.date}`)),
    h("div", { class: "c-muted" }, d.ref_label ? `Référence : ${d.ref_label}` : "Pas de référence"));
  const kpis = h("div", { class: "kpis" },
    kpi("Meilleur tour", lapTime(sm.best_lap_s), "", "ok"),
    kpi("Tour théorique", lapTime(sm.theoretical_best_s)),
    kpi("Moyenne", lapTime(sm.avg_valid_lap_s)),
    kpi("Régularité", num(sm.consistency_s, 2), "s"),
    kpi("Conso", num(sm.fuel_avg_l_per_lap, 2), "L/tour"),
    kpi("Tours valides", `${sm.laps_valid}/${sm.laps_total}`));

  const maxLoss = Math.max(0.05, ...sm.top_corners.map((c) => c.mean_loss_s));
  const corners = h("div", { class: "card warn" }, h("h3", {}, "Virages qui coûtent", h("span", { class: "r" }, sm.theme_label ? `thème : ${sm.theme_label}` : "")),
    sm.top_corners.length
      ? h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "Virage"), h("th", {}, "Défaut principal"), h("th", { class: "r" }, "Perte moy."), h("th", {}, ""))),
        h("tbody", {}, sm.top_corners.map((c) => h("tr", {},
          h("td", { class: "c-accent", style: "font-weight:700" }, c.corner),
          h("td", {}, c.issue_label),
          h("td", { class: "r" }, `${num(c.mean_loss_s, 2)} s`),
          h("td", { style: "width:30%" }, h("div", { class: "hbar" }, h("i", { style: `width:${(c.mean_loss_s / maxLoss) * 100}%` })))))))
      : h("div", { class: "empty" }, "Aucun virage nettement perdant."));

  const best = sm.best_lap_s;
  const lapRows = sm.laps.map((l) => {
    const a = byLap[l.lap];
    const top = a && a.advice.length ? a.advice[0] : null;
    return h("tr", { class: `${a ? "click" : ""} ${l.lap === state.lap ? "sel" : ""}`, onclick: a ? () => { state.lap = l.lap; renderSessionDetail(el, d); } : null },
      h("td", {}, l.lap),
      h("td", { class: l.time_s === best ? "c-ok" : "" }, lapTime(l.time_s)),
      h("td", { class: "r" }, a ? signed(a.delta_s, 2) : "—"),
      h("td", {}, l.valid ? h("span", { class: "chip ok" }, l.flags.includes("yellow") ? "valide · jaune" : "valide") : h("span", { class: "chip bad" }, l.flags.map((f) => LAP_FLAGS[f] || f).join(", ") || "invalide")),
      h("td", { class: "c-muted" }, top ? top.short : ""));
  });
  const laps = h("div", { class: "card" }, h("h3", {}, "Tours", h("span", { class: "r" }, "clique un tour pour ses conseils")),
    h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "Tour"), h("th", {}, "Temps"), h("th", { class: "r" }, "Δ réf."), h("th", {}, "État"), h("th", {}, "Conseil principal"))), h("tbody", {}, lapRows)));

  const sel = state.lap !== null ? byLap[state.lap] : null;
  const adviceCard = h("div", { class: "card ok" }, h("h3", {}, sel ? `Conseils · tour ${sel.lap}` : "Conseils", h("span", { class: "r" }, sel ? `${signed(sel.delta_s, 2)} s vs réf.` : "")),
    sel && sel.advice.length
      ? h("ul", { class: "advice" }, sel.advice.map((a) => h("li", {}, h("span", { class: "cn" }, a.corner), h("span", {}, a.text.replace(/^T\d+ : /, "")), h("span", { class: "gain" }, `−${num(a.gain_s, 2)} s`))))
      : h("div", { class: "empty" }, sel ? "Rien à signaler sur ce tour." : "Pas de tour comparable."),
    sel ? cornerDeltas(sel) : null);

  el.replaceChildren(head, kpis,
    h("div", { class: "section-title" }, "Analyse"),
    h("div", { class: "grid" }, h("div", { class: "s7" }, laps), h("div", { class: "s5", style: "display:flex;flex-direction:column;gap:14px" }, adviceCard, corners)));
}

function cornerDeltas(a) {
  const max = Math.max(0.05, ...a.corners.map((c) => Math.abs(c.loss_s)));
  return h("div", { style: "margin-top:16px" }, h("div", { class: "label" }, "Temps par virage vs référence"),
    h("table", {}, h("tbody", {}, a.corners.map((c) => h("tr", {},
      h("td", { style: "width:52px" }, c.corner),
      h("td", { style: "width:60%" }, h("div", { class: `hbar ${c.loss_s < 0 ? "gain" : ""}` }, h("i", { style: `width:${(Math.abs(c.loss_s) / max) * 100}%` }))),
      h("td", { class: `r ${c.loss_s > 0.02 ? "c-bad" : c.loss_s < -0.02 ? "c-ok" : "c-muted"}` }, `${signed(c.loss_s, 2)} s`))))));
}

function emptyPage(title, text) {
  return h("div", { class: "waiting" }, h("div", { class: "huge" }, title), h("div", {}, text));
}

/* ---------- pilote ---------- */
const STATUS = { improving: ["en progrès", "ok"], stable: ["stable", ""], regressing: ["en recul", "bad"] };

async function renderDriver() {
  const page = $("#page-driver");
  loading(page);
  let p;
  try { p = await api("/api/profile"); } catch (e) { failed(page, e); return; }
  if (!p || p.empty) {
    page.replaceChildren(emptyPage("PAS DE PROFIL", p ? `Pas encore de tours analysés pour ${p.track} / ${p.car}.` : "Aucune séance enregistrée."));
    return;
  }
  const plan = p.plan;
  const head = h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Profil pilote"), h("div", { class: "c-muted" }, `${p.track} · ${p.car}`)),
    h("div", { class: "c-muted" }, `${p.sessions} séance${p.sessions > 1 ? "s" : ""} · ${p.laps_valid} tours valides`));
  const kpis = h("div", { class: "kpis" },
    kpi("Meilleur tour", lapTime(p.best_lap_s), "", "ok"),
    kpi("Régularité", num(p.consistency_s, 2), "s"),
    kpi("Thème dominant", p.main_theme || "—", "", "warn"),
    kpi("Points faibles", p.weaknesses.join(" · ") || "—", "", "bad"));
  const planCard = h("div", { class: "card" }, h("h3", {}, "Prochaine séance", h("span", { class: "r" }, "un seul thème")),
    h("div", { class: "theme" }, plan.title),
    h("div", { class: "c-muted", style: "margin-bottom:14px" }, plan.rationale),
    plan.focus_corners.length ? h("div", { style: "margin-bottom:14px" }, h("span", { class: "label", style: "display:inline" }, "Virages ciblés  "), ...plan.focus_corners.map((c) => h("span", { class: "chip", style: "margin-right:6px;color:var(--accent)" }, c))) : null,
    h("ol", { class: "drills" }, plan.drills.map((d) => h("li", {}, d))),
    h("div", { class: "target" }, h("div", { class: "label" }, "Objectif mesurable"), plan.target));
  const max = Math.max(0.05, ...p.corners.map((c) => Math.abs(c.last_loss_s)));
  const trend = h("div", { class: "card" }, h("h3", {}, "Tendance par virage"),
    h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "Virage"), h("th", {}, "Défaut dominant"), h("th", { class: "r" }, "Dernière"), h("th", {}, ""), h("th", { class: "r" }, "Pente"), h("th", {}, "État"))),
      h("tbody", {}, p.corners.map((c) => {
        const [lbl, cls] = STATUS[c.status] || [c.status, ""];
        return h("tr", {},
          h("td", { class: "c-accent", style: "font-weight:700" }, c.corner),
          h("td", { class: c.main_issue ? "" : "c-muted" }, c.main_issue ? `${c.issue_label} (${Math.round(c.issue_share * 100)} %)` : "—"),
          h("td", { class: "r" }, `${num(c.last_loss_s, 2)} s`),
          h("td", { style: "width:22%" }, h("div", { class: `hbar ${c.last_loss_s < 0 ? "gain" : ""}` }, h("i", { style: `width:${(Math.abs(c.last_loss_s) / max) * 100}%` }))),
          h("td", { class: "r c-muted" }, `${signed(c.trend_s_per_session, 3)} s/séance`),
          h("td", {}, h("span", { class: `chip ${cls}` }, lbl)));
      }))));
  page.replaceChildren(head, kpis, h("div", { class: "section-title" }, "Programme"), h("div", { class: "grid" }, h("div", { class: "s5" }, planCard), h("div", { class: "s7" }, trend)));
}

/* ---------- setup ---------- */
const PHASES = { entry: "Entrée", mid: "Milieu", exit: "Sortie" };
const VERDICT = {
  better: ["MIEUX — garde ce réglage", "c-ok"],
  worse: ["MOINS BIEN — reviens en arrière", "c-bad"],
  inconclusive: ["NON CONCLUANT — roule plus de tours", "c-warn"],
  not_enough_laps: ["PAS ASSEZ DE TOURS PROPRES", "c-muted"],
};

async function renderSetup() {
  const page = $("#page-setup");
  loading(page);
  try {
    if (!state.sessions.length) state.sessions = await api("/api/sessions");
    if (!state.sessions.length) { page.replaceChildren(emptyPage("AUCUNE SÉANCE", "Le diagnostic de setup se fait sur les tours propres d'une séance.")); return; }
    if (!state.session) state.session = state.sessions[0].id;
    const d = await api(`/api/setup/${state.session}`);
    const ses = state.sessions.find((x) => x.id === state.session) || {};
    const picker = h("select", { onchange: (e) => { state.session = Number(e.target.value); renderSetup(); }, style: "background:var(--panel);color:var(--text);border:1px solid var(--line-2);padding:8px 10px;font:inherit" },
      state.sessions.map((x) => h("option", { value: x.id, selected: x.id === state.session }, `${x.track} · ${dateFr(x.started_at)}`)));
    const head = h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Conseiller de setup"), h("div", { class: "c-muted" }, `${ses.car || ""} · ${d.laps} tours propres analysés · tu appliques les réglages toi-même`)), picker);

    const bal = d.balance || {};
    const balance = h("div", { class: "card" }, h("h3", {}, "Équilibre par phase", h("span", { class: "r" }, "indice ≈ 1 : neutre")),
      Object.keys(bal).length
        ? h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "Phase"), h("th", { class: "r" }, "Indice sous-virage"), h("th", {}, ""), h("th", { class: "r" }, "Contre-braquages / tour"))),
          h("tbody", {}, Object.entries(bal).map(([k, b]) => {
            const idx = b.understeer_index;
            const w = isNum(idx) ? Math.min(Math.max((idx - 0.5) / 1.5, 0), 1) * 100 : 0;
            return h("tr", {}, h("td", {}, PHASES[k] || k), h("td", { class: `r ${isNum(idx) && idx > 1.6 ? "c-warn" : ""}` }, num(idx, 2)),
              h("td", { style: "width:30%" }, h("div", { class: "hbar" }, h("i", { style: `width:${w}%;background:var(--accent)` }))),
              h("td", { class: `r ${b.oversteer_events >= 1 ? "c-bad" : ""}` }, num(b.oversteer_events, 1)));
          })))
        : h("div", { class: "empty" }, "Pas de données d'accélération latérale."));

    const findings = (d.findings || []).length
      ? d.findings.map((f) => h("div", { class: "card warn" }, h("h3", {}, "Constat"), h("div", { style: "font-size:18px;font-weight:600" }, f.title), h("ul", { class: "sugg" }, f.suggestions.map((x) => h("li", {}, x)))))
      : [h("div", { class: "card ok" }, h("h3", {}, "Constat"), h("div", { style: "font-size:18px" }, "Aucun déséquilibre net détecté."))];

    let cmp = null;
    if (d.comparison) {
      const c = d.comparison;
      const [txt, cls] = VERDICT[c.verdict] || [c.verdict, ""];
      cmp = h("div", { class: "card" }, h("h3", {}, "Avant / après", h("span", { class: "r" }, c.description)),
        h("div", { class: `verdict ${cls}` }, txt),
        h("table", { style: "margin-top:12px" }, h("thead", {}, h("tr", {}, h("th", {}, ""), h("th", { class: "r" }, "Avant"), h("th", { class: "r" }, "Après"))),
          h("tbody", {},
            h("tr", {}, h("td", {}, "Tours propres"), h("td", { class: "r" }, c.before.laps), h("td", { class: "r" }, c.after.laps)),
            h("tr", {}, h("td", {}, "Meilleur"), h("td", { class: "r" }, lapTime(c.before.best_s)), h("td", { class: "r" }, lapTime(c.after.best_s))),
            h("tr", {}, h("td", {}, "Moyenne"), h("td", { class: "r" }, lapTime(c.before.mean_s)), h("td", { class: "r" }, lapTime(c.after.mean_s))))),
        isNum(c.mean_delta_s) ? h("div", { class: "sub" }, `Écart moyen : ${signed(c.mean_delta_s, 2)} s`) : null);
    } else {
      cmp = h("div", { class: "card" }, h("h3", {}, "Avant / après"), h("div", { class: "empty" }, "Aucun changement noté. Après avoir modifié un réglage : simengineer setup mark \"…\""));
    }
    const guide = h("div", { class: "card" }, h("h3", {}, "Appliquer à la main"), h("pre", { class: "guide" }, d.guide.replace(/^## .*\n\n/, "").replaceAll("`", "")));
    page.replaceChildren(head, h("div", { class: "grid" },
      h("div", { class: "s7", style: "display:flex;flex-direction:column;gap:14px" }, balance, ...findings),
      h("div", { class: "s5", style: "display:flex;flex-direction:column;gap:14px" }, cmp, guide)));
  } catch (e) { failed(page, e); }
}

/* ---------- références ---------- */
async function renderRefs() {
  const page = $("#page-refs");
  loading(page);
  let refs;
  try { refs = await api("/api/refs"); } catch (e) { failed(page, e); return; }
  const head = h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Tours de référence"), h("div", { class: "c-muted" }, "Uniquement des fichiers que tu possèdes · provenance et droits obligatoires")));
  if (!refs.length) {
    page.replaceChildren(head, h("div", { class: "card" }, h("div", { class: "empty" }, "Aucune référence. Importe un tour : simengineer ref import <fichier.ibt|.csv> --source \"…\" --rights \"…\". Sans référence, le meilleur tour de la séance sert de référence.")));
    return;
  }
  page.replaceChildren(head, h("div", { class: "card" }, h("table", {},
    h("thead", {}, h("tr", {}, h("th", {}, "Nom"), h("th", {}, "Circuit"), h("th", {}, "Voiture"), h("th", { class: "r" }, "Temps"), h("th", {}, "Provenance"), h("th", {}, "Droits"), h("th", {}, "Importé"))),
    h("tbody", {}, refs.map((r) => h("tr", {},
      h("td", { class: "c-accent", style: "font-weight:600" }, r.label), h("td", {}, r.track), h("td", {}, r.car),
      h("td", { class: "r" }, lapTime(r.lap_time)), h("td", { class: "c-muted" }, r.source), h("td", {}, h("span", { class: "chip" }, r.rights)), h("td", { class: "c-muted" }, dateFr(r.imported_at))))))));
}

/* ---------- démarrage ---------- */
api("/api/meta").then((m) => { LANG = m.language || "fr"; }).catch(() => {}).finally(() => {
  route();
  connectLive();
});

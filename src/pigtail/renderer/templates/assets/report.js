(function () {
  "use strict";
  var DATA = JSON.parse(document.getElementById("pigtail-data").textContent);
  var EVENTS = {};
  (DATA.events || []).forEach(function (e) { EVENTS[e.id] = e; });
  var CLAIMS = DATA.claims || {};
  var SVGNS = "http://www.w3.org/2000/svg";
  var KIND_LETTER = { release: "R", hn: "H", ph: "P", launch: "L", reddit: "r", press: "C", community: "U", repo: "G" };

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function safeUrl(u) { return /^https?:\/\//i.test(u || "") ? u : "#"; }
  function el(tag, attrs, parent) {
    var n = document.createElementNS(SVGNS, tag);
    for (var k in attrs) n.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(n);
    return n;
  }
  function cssVar(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
  function fmt(v) {
    if (v == null) return "—";
    var a = Math.abs(v);
    if (a >= 1e6) return (v / 1e6).toFixed(1).replace(/\.0$/, "") + "M";
    if (a >= 1e4) return Math.round(v / 1e3) + "K";
    if (a >= 1e3) return (v / 1e3).toFixed(1).replace(/\.0$/, "") + "K";
    return Math.round(v).toLocaleString();
  }
  function parseDay(s) { var p = s.split("-"); return Date.UTC(+p[0], +p[1] - 1, +p[2]); }
  function dayLabel(t) {
    return new Date(t).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric", timeZone: "UTC" });
  }

  // ---------------- theme ----------------
  var toggle = document.getElementById("themeToggle");
  try {
    var saved = localStorage.getItem("pigtail-theme");
    if (saved) document.documentElement.setAttribute("data-theme", saved);
  } catch (e) { /* storage unavailable */ }
  if (toggle) toggle.addEventListener("click", function () {
    var cur = document.documentElement.getAttribute("data-theme");
    var dark = cur ? cur === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
    var next = dark ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    try { localStorage.setItem("pigtail-theme", next); } catch (e) { /* ignore */ }
    drawAll();
  });

  // ---------------- evidence rendering ----------------
  function claimCard(cid) {
    var c = CLAIMS[cid];
    if (!c) return "";
    var h = '<div class="claim-card"><p class="stmt">' + esc(c.statement) + "</p>";
    h += '<div class="claim-meta"><span class="badge minor">' + esc(c.time) + '</span><span class="badge minor">' +
      esc(c.review_state) + "</span>" + (c.status !== "active" ? '<span class="badge material">' + esc(c.status) + "</span>" : "") + "</div>";
    (c.evidence || []).forEach(function (ev) {
      var cls = ev.evidence_class === "inferred" ? "inferred" : (ev.evidence_class.indexOf("third_party") === 0 ? "report" : "fact");
      h += '<div class="ev"><span class="badge ' + cls + '">' + esc(ev.evidence_label) + "</span> " + esc(ev.directness) +
        ", " + esc(ev.corroboration) + '<br><a href="' + esc(safeUrl(ev.url)) + '" target="_blank" rel="noopener">[' + ev.n + "] " +
        esc(ev.title) + " ↗</a>";
      if (ev.excerpt) h += "<blockquote>" + esc(ev.excerpt) + "</blockquote>";
      h += '<div class="trace">Locator: ' + esc(ev.locator) + (ev.retrieved_at ? " · retrieved " + esc(ev.retrieved_at.slice(0, 10)) : "") +
        (ev.content_hash ? " · " + esc(ev.content_hash.slice(0, 19)) + "…" : "") + "</div></div>";
    });
    return h + "</div>";
  }
  function eventDetail(e) {
    var h = '<div class="event-date">' + esc(e.when) + " · " + esc(e.kind_label) + "</div><h3>" + esc(e.title) + "</h3>";
    h += '<span class="badge ' + (e.evidence_class === "inferred" ? "inferred" : "fact") + '">' + esc(e.evidence_label) + "</span>";
    if (e.is_inferred) h += ' <span class="badge inferred">Reconstructed from claims</span>';
    h += "<p>" + esc(e.summary) + "</p>";
    if (e.metrics && e.metrics.length) h += '<p class="small"><b>' + esc(e.metrics.join(" · ")) + "</b></p>";
    h += '<small class="label">Evidence chain</small>';
    (e.claim_ids || []).forEach(function (cid) { h += claimCard(cid); });
    return h;
  }

  var drawer = document.getElementById("drawer"), scrim = document.getElementById("scrim");
  var drawerBody = document.getElementById("drawerBody");
  var lastFocus = null;
  function openDrawer(html) {
    lastFocus = document.activeElement;
    drawerBody.innerHTML = html;
    drawer.classList.add("open"); scrim.classList.add("open");
    drawer.setAttribute("aria-hidden", "false");
    document.getElementById("drawerClose").focus();
  }
  function closeDrawer() {
    drawer.classList.remove("open"); scrim.classList.remove("open");
    drawer.setAttribute("aria-hidden", "true");
    if (lastFocus) lastFocus.focus();
  }
  document.getElementById("drawerClose").addEventListener("click", closeDrawer);
  scrim.addEventListener("click", closeDrawer);
  document.addEventListener("keydown", function (ev) { if (ev.key === "Escape") closeDrawer(); });

  document.addEventListener("click", function (ev) {
    var cite = ev.target.closest(".cite");
    if (cite) {
      ev.preventDefault();
      var ids = (cite.getAttribute("data-claims") || cite.getAttribute("data-claim") || "").split(",").filter(Boolean);
      var h = "<h3>Evidence</h3><p class=\"small\">The claims below support this statement. Each links to the original source.</p>";
      ids.forEach(function (cid) { h += claimCard(cid); });
      openDrawer(h);
      return;
    }
    var link = ev.target.closest("[data-event]");
    if (link && !link.classList.contains("event")) {
      var e = EVENTS[link.getAttribute("data-event")];
      if (e) openDrawer(eventDetail(e));
    }
  });

  // ---------------- timeline ----------------
  var list = document.getElementById("timelineList");
  var detail = document.getElementById("detail");
  if (list) {
    list.addEventListener("click", function (ev) {
      var card = ev.target.closest(".event");
      if (!card) return;
      var e = EVENTS[card.getAttribute("data-event")];
      if (!e) return;
      list.querySelectorAll(".event.active").forEach(function (n) { n.classList.remove("active"); });
      card.classList.add("active");
      if (window.matchMedia("(max-width: 920px)").matches) openDrawer(eventDetail(e));
      else detail.innerHTML = eventDetail(e);
    });
    var filters = document.getElementById("filters");
    filters.addEventListener("click", function (ev) {
      var b = ev.target.closest(".filter");
      if (!b) return;
      var cat = b.getAttribute("data-cat");
      filters.querySelectorAll(".filter").forEach(function (n) { n.classList.toggle("active", n === b); });
      list.querySelectorAll(".event").forEach(function (n) {
        n.hidden = !(cat === "All" || n.getAttribute("data-cat") === cat);
      });
    });
  }

  // ---------------- star + event chart ----------------
  var chart = DATA.chart;
  var range = "all";
  function drawStarChart() {
    var svg = document.getElementById("starChart");
    if (!svg || !chart) return;
    var wrap = document.getElementById("starChartWrap");
    var tip = document.getElementById("chartTip");
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    var W = Math.max(320, wrap.clientWidth);
    var pts = chart.stars.map(function (p) { return { t: parseDay(p.t), v: p.v }; });
    if (!pts.length) return;
    var tMax = pts[pts.length - 1].t;
    var allEvents = chart.events.map(function (e) { return { e: e, t: parseDay(e.t) }; });
    var tMin = Math.min(pts[0].t, allEvents.length ? Math.min.apply(null, allEvents.map(function (x) { return x.t; })) : pts[0].t);
    var lastEventT = allEvents.length ? Math.max.apply(null, allEvents.map(function (x) { return x.t; })) : tMax;
    tMax = Math.max(tMax, lastEventT);
    if (range !== "all") tMin = Math.max(tMin, tMax - (+range) * 86400000);
    var visible = pts.filter(function (p) { return p.t >= tMin; });
    var before = pts.filter(function (p) { return p.t < tMin; });
    if (before.length) visible.unshift(before[before.length - 1]);
    var events = allEvents.filter(function (x) { return x.t >= tMin && x.t <= tMax; });

    // gains per interval
    var gains = [];
    for (var i = 1; i < visible.length; i++) gains.push({ t0: visible[i - 1].t, t: visible[i].t, v: Math.max(0, visible[i].v - visible[i - 1].v) });

    // lanes by event kind; minor kinds share one "Other events" lane
    var MAIN = ["Release", "Show HN", "Launch HN", "Hacker News", "Product Hunt", "Reddit", "Launch", "Announcement"];
    function laneOf(e) { return MAIN.indexOf(e.kind) >= 0 ? e.kind : "Other events"; }
    var kinds = [];
    events.forEach(function (x) { var l = laneOf(x.e); if (kinds.indexOf(l) < 0) kinds.push(l); });
    var order = MAIN.concat(["Other events"]);
    kinds.sort(function (a, b) { var ia = order.indexOf(a), ib = order.indexOf(b); return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib); });
    var laneH = 20, lanesTop = 6;
    var H = (W < 560 ? 280 : 340) + kinds.length * laneH;
    wrap.style.height = H + "px";
    svg.setAttribute("viewBox", "0 0 " + W + " " + H);
    var pad = { l: 96, r: 16 };
    var narrow = W < 560;
    if (narrow) pad.l = 46;
    var lanesH = kinds.length * laneH;
    var top = lanesTop + lanesH + 10;
    var gainsH = Math.round((H - top - 26) * 0.24);
    var starsH = H - top - 26 - gainsH - 12;
    var starsTop = top, gainsTop = top + starsH + 12;
    var x = function (t) { return pad.l + (W - pad.l - pad.r) * (t - tMin) / Math.max(1, tMax - tMin); };
    var vMin = visible.length ? visible[0].v : 0, vMax = Math.max.apply(null, visible.map(function (p) { return p.v; }));
    if (range === "all") vMin = 0;
    var yS = function (v) { return starsTop + starsH * (1 - (v - vMin) / Math.max(1, vMax - vMin)); };
    var gMax = Math.max(1, Math.max.apply(null, gains.map(function (g) { return g.v; }).concat([1])));
    var yG = function (v) { return gainsTop + gainsH * (1 - v / gMax); };

    var grid = cssVar("--grid"), muted = cssVar("--faint"), ink = cssVar("--ink"), assoc = cssVar("--assoc");

    // episodes (bands)
    chart.episodes.forEach(function (g) {
      var s = parseDay(g.start), e = parseDay(g.end) + 86400000;
      if (e < tMin || s > tMax) return;
      var x0 = x(Math.max(s, tMin)), x1 = x(Math.min(e, tMax));
      el("rect", { x: x0, y: starsTop, width: Math.max(2, x1 - x0), height: gainsTop + gainsH - starsTop, fill: assoc, opacity: 0.09 }, svg);
      if (!narrow) {
        var lab = el("text", { x: x0 + 3, y: starsTop + 12, "font-size": 10.5, fill: assoc, "font-weight": 700 }, svg);
        lab.textContent = g.delta;
      }
    });

    // y grid + labels (stars)
    for (var k = 0; k <= 4; k++) {
      var v = vMin + (vMax - vMin) * k / 4, yy = yS(v);
      el("line", { x1: pad.l, x2: W - pad.r, y1: yy, y2: yy, stroke: grid, "stroke-width": 1 }, svg);
      var tl = el("text", { x: pad.l - 8, y: yy + 4, "text-anchor": "end", "font-size": 11, fill: muted }, svg);
      tl.textContent = fmt(v);
    }
    var gl = el("text", { x: pad.l - 8, y: gainsTop + 10, "text-anchor": "end", "font-size": 11, fill: muted }, svg);
    gl.textContent = fmt(gMax);
    el("line", { x1: pad.l, x2: W - pad.r, y1: gainsTop + gainsH, y2: gainsTop + gainsH, stroke: grid }, svg);
    if (!narrow) {
      var sl = el("text", { x: 4, y: starsTop + starsH / 2, "font-size": 11, fill: muted }, svg); sl.textContent = "Total stars";
      var gl2 = el("text", { x: 4, y: gainsTop + gainsH / 2 + 4, "font-size": 11, fill: muted }, svg); gl2.textContent = "New stars";
    }

    // x axis ticks
    var span = tMax - tMin, years = span / (365.25 * 86400000);
    var ticks = [], d = new Date(tMin);
    if (years > 2.5) {
      for (var yv = d.getUTCFullYear() + 1; Date.UTC(yv, 0, 1) <= tMax; yv++) ticks.push({ t: Date.UTC(yv, 0, 1), l: String(yv) });
    } else {
      var step = years > 1 ? 3 : 1;
      var m = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 1));
      while (m.getTime() <= tMax) {
        if (m.getUTCMonth() % step === 0) ticks.push({ t: m.getTime(), l: m.toLocaleDateString(undefined, { month: "short", year: "2-digit", timeZone: "UTC" }) });
        m = new Date(Date.UTC(m.getUTCFullYear(), m.getUTCMonth() + 1, 1));
      }
    }
    var maxTicks = Math.floor((W - pad.l) / 60);
    var every = Math.max(1, Math.ceil(ticks.length / maxTicks));
    ticks.forEach(function (tk, idx) {
      if (idx % every) return;
      var tx = el("text", { x: x(tk.t), y: H - 6, "text-anchor": "middle", "font-size": 11, fill: muted }, svg);
      tx.textContent = tk.l;
    });

    // gain bars
    var bw = Math.max(1, (W - pad.l - pad.r) / Math.max(1, gains.length) - 1);
    gains.forEach(function (g) {
      if (g.v <= 0) return;
      var gx = x(g.t) - bw;
      el("rect", { x: gx, y: yG(g.v), width: bw, height: gainsTop + gainsH - yG(g.v), fill: muted, opacity: 0.55, rx: Math.min(2, bw / 2) }, svg);
    });

    // star line (dashed where sampled gaps are wide)
    var path = "", dashed = "";
    var sampled = chart.quality !== "exact";
    visible.forEach(function (p, idx) {
      var px = x(p.t), py = yS(p.v);
      if (idx === 0) { path += "M" + px + "," + py; return; }
      var prev = visible[idx - 1];
      var gapDays = (p.t - prev.t) / 86400000;
      if (sampled && gapDays > 45) {
        dashed += "M" + x(prev.t) + "," + yS(prev.v) + "L" + px + "," + py;
        path += "M" + px + "," + py;
      } else path += "L" + px + "," + py;
    });
    el("path", { d: path, fill: "none", stroke: ink, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }, svg);
    if (dashed) el("path", { d: dashed, fill: "none", stroke: ink, "stroke-width": 2, "stroke-dasharray": "4 4", opacity: 0.6 }, svg);

    // event lanes
    kinds.forEach(function (kd, li) {
      var ly = lanesTop + li * laneH + laneH / 2;
      el("line", { x1: pad.l, x2: W - pad.r, y1: ly, y2: ly, stroke: grid, "stroke-dasharray": "2 3" }, svg);
      var lt = el("text", { x: pad.l - 8, y: ly + 4, "text-anchor": "end", "font-size": 11, fill: muted }, svg);
      lt.textContent = narrow ? kd.slice(0, 4) : kd;
    });
    var markers = [];
    events.forEach(function (xe) {
      var li = kinds.indexOf(laneOf(xe.e)), mx = x(xe.t), my = lanesTop + li * laneH + laneH / 2;
      var color = cssVar("--k-" + xe.e.css) || cssVar("--k-other");
      el("line", { x1: mx, x2: mx, y1: my + 7, y2: gainsTop + gainsH, stroke: color, "stroke-width": 1, opacity: 0.22 }, svg);
      var g = el("g", { class: "marker", tabindex: 0, role: "button", "aria-label": xe.e.kind + ": " + xe.e.title + ", " + xe.e.when, style: "cursor:pointer" }, svg);
      el("circle", { cx: mx, cy: my, r: 7.5, fill: color, stroke: cssVar("--panel"), "stroke-width": 2 }, g);
      var tt = el("text", { x: mx, y: my + 3.5, "text-anchor": "middle", "font-size": 9, "font-weight": 800, fill: "#fff" }, g);
      tt.textContent = laneOf(xe.e) === "Other events" ? "•" : (KIND_LETTER[xe.e.css] || "•");
      el("circle", { cx: mx, cy: my, r: 12, fill: "transparent" }, g);
      markers.push({ g: g, x: mx, y: my, e: xe.e, t: xe.t });
    });

    function inEpisode(t) {
      return chart.episodes.filter(function (g) { return t >= parseDay(g.start) - 2 * 86400000 && t <= parseDay(g.end) + 86400000; })[0];
    }
    function showTip(html, px, py) {
      tip.innerHTML = html;
      tip.classList.add("show");
      var tw = tip.offsetWidth, th = tip.offsetHeight;
      var left = Math.min(W - tw - 4, Math.max(4, px + 12)), topp = Math.max(0, py - th - 10);
      tip.style.left = left + "px"; tip.style.top = topp + "px";
    }
    function hideTip() { tip.classList.remove("show"); cross.setAttribute("opacity", 0); dot.setAttribute("opacity", 0); }

    markers.forEach(function (mk) {
      function over() {
        var ep = inEpisode(mk.t);
        showTip("<b>" + esc(mk.e.title) + "</b>" + esc(mk.e.kind) + " · " + esc(mk.e.when) +
          '<span class="tip-note">' + (ep ? "Observed event near this growth period. Timing only — not proof of cause." : "Public event. Click for evidence.") + "</span>", mk.x, mk.y);
      }
      mk.g.addEventListener("mouseenter", over);
      mk.g.addEventListener("focus", over);
      mk.g.addEventListener("mouseleave", hideTip);
      mk.g.addEventListener("blur", hideTip);
      function open() { var e = EVENTS[mk.e.id]; if (e) openDrawer(eventDetail(e)); }
      mk.g.addEventListener("click", open);
      mk.g.addEventListener("keydown", function (ev) { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); open(); } });
    });

    // crosshair
    var cross = el("line", { y1: starsTop, y2: gainsTop + gainsH, stroke: ink, "stroke-width": 1, opacity: 0, "pointer-events": "none" }, svg);
    var dot = el("circle", { r: 4.5, fill: cssVar("--panel"), stroke: ink, "stroke-width": 2, opacity: 0, "pointer-events": "none" }, svg);
    var hit = el("rect", { x: pad.l, y: starsTop, width: W - pad.l - pad.r, height: gainsTop + gainsH - starsTop, fill: "transparent" }, svg);
    hit.addEventListener("mousemove", function (ev) {
      var r = svg.getBoundingClientRect();
      var px = (ev.clientX - r.left) * (W / r.width);
      var t = tMin + (px - pad.l) / (W - pad.l - pad.r) * (tMax - tMin);
      var best = visible[0];
      visible.forEach(function (p) { if (Math.abs(p.t - t) < Math.abs(best.t - t)) best = p; });
      var bx = x(best.t), by = yS(best.v);
      cross.setAttribute("x1", bx); cross.setAttribute("x2", bx); cross.setAttribute("opacity", 0.35);
      dot.setAttribute("cx", bx); dot.setAttribute("cy", by); dot.setAttribute("opacity", 1);
      var gain = gains.filter(function (g) { return g.t === best.t; })[0];
      var near = events.filter(function (xe) { return Math.abs(xe.t - best.t) <= 3 * 86400000; });
      var ep = inEpisode(best.t);
      var h = "<b>" + fmt(best.v) + " stars</b>" + dayLabel(best.t);
      if (gain) h += "<br>+" + fmt(gain.v) + " since " + dayLabel(gain.t0);
      if (ep) h += "<br>Inside growth episode (" + esc(ep.delta) + ")";
      if (near.length) h += '<span class="tip-note">Observed nearby (timing only): ' + near.slice(0, 4).map(function (n) { return esc(n.e.title); }).join("; ") + "</span>";
      showTip(h, bx, by);
    });
    hit.addEventListener("mouseleave", hideTip);
    markers.forEach(function (mk) { svg.appendChild(mk.g); });

    // legend
    var legend = document.getElementById("chartLegend");
    if (legend) {
      legend.innerHTML = kinds.map(function (kd) {
        var css = (events.filter(function (x2) { return laneOf(x2.e) === kd; })[0] || {}).e.css;
        return '<span><span class="dot k-' + esc(kd === "Other events" ? "other" : css) + '"></span>' + esc(kd) + "</span>";
      }).join("") + '<span><span class="dot" style="background:' + assoc + ';opacity:.35;border-radius:2px"></span>Growth episode</span>';
    }
  }

  var ctr = document.getElementById("chartControls");
  if (ctr) ctr.addEventListener("click", function (ev) {
    var b = ev.target.closest("[data-range]");
    if (!b) return;
    range = b.getAttribute("data-range");
    ctr.querySelectorAll("[data-range]").forEach(function (n) { n.classList.toggle("active", n === b); });
    drawStarChart();
  });

  // ---------------- metric charts ----------------
  function drawMetricCharts() {
    document.querySelectorAll("svg.metricChart").forEach(function (svg) {
      var s = (DATA.metric_series || [])[+svg.getAttribute("data-series")];
      if (!s) return;
      while (svg.firstChild) svg.removeChild(svg.firstChild);
      var wrap = svg.parentNode, tip = wrap.querySelector(".tooltip");
      var W = Math.max(300, wrap.clientWidth), H = wrap.clientHeight;
      svg.setAttribute("viewBox", "0 0 " + W + " " + H);
      var pts = s.points.map(function (p) { return { t: parseDay(p.t), v: p.v, p: p }; });
      var pad = { l: 64, r: 16, t: 12, b: 26 };
      var tMin = pts[0].t, tMax = pts[pts.length - 1].t;
      var vMax = Math.max.apply(null, pts.map(function (p) { return p.v; }));
      var x = function (t) { return pad.l + (W - pad.l - pad.r) * (t - tMin) / Math.max(1, tMax - tMin); };
      var y = function (v) { return pad.t + (H - pad.t - pad.b) * (1 - v / Math.max(1, vMax)); };
      var grid = cssVar("--grid"), muted = cssVar("--faint"), ink = cssVar("--ink");
      for (var k = 0; k <= 4; k++) {
        var v = vMax * k / 4;
        el("line", { x1: pad.l, x2: W - pad.r, y1: y(v), y2: y(v), stroke: grid }, svg);
        var tl = el("text", { x: pad.l - 8, y: y(v) + 4, "text-anchor": "end", "font-size": 11, fill: muted }, svg);
        tl.textContent = (s.currency === "USD" ? "$" : "") + fmt(v);
      }
      var d = pts.map(function (p, i) { return (i ? "L" : "M") + x(p.t) + "," + y(p.v); }).join("");
      el("path", { d: d, fill: "none", stroke: ink, "stroke-width": 2, "stroke-linejoin": "round" }, svg);
      var y0 = new Date(tMin).getUTCFullYear(), y1 = new Date(tMax).getUTCFullYear();
      for (var yr = y0 + 1; yr <= y1; yr++) {
        var tx = el("text", { x: x(Date.UTC(yr, 0, 1)), y: H - 6, "text-anchor": "middle", "font-size": 11, fill: muted }, svg);
        tx.textContent = String(yr);
      }
      pts.forEach(function (p) {
        var g = el("g", { tabindex: 0 }, svg);
        el("circle", { cx: x(p.t), cy: y(p.v), r: 4.5, fill: cssVar("--panel"), stroke: ink, "stroke-width": 2 }, g);
        el("circle", { cx: x(p.t), cy: y(p.v), r: 12, fill: "transparent" }, g);
        function over() {
          tip.innerHTML = "<b>" + esc(p.p.display) + "</b>" + esc(p.p.label) + '<span class="tip-note">As reported in the cited source; not audited.</span>';
          tip.classList.add("show");
          tip.style.left = Math.min(W - 200, x(p.t) + 10) + "px"; tip.style.top = Math.max(0, y(p.v) - 60) + "px";
        }
        g.addEventListener("mouseenter", over); g.addEventListener("focus", over);
        g.addEventListener("mouseleave", function () { tip.classList.remove("show"); });
        g.addEventListener("blur", function () { tip.classList.remove("show"); });
      });
    });
  }

  function drawAll() { drawStarChart(); drawMetricCharts(); }
  drawAll();
  var rt;
  window.addEventListener("resize", function () { clearTimeout(rt); rt = setTimeout(drawAll, 120); });
})();

/* ================================================================
   Graph2D — canvas force-directed knowledge graph for Engram.
   Standalone, no deps. Consumes the page_api graph_data payload:
     { nodes: [{id, name, type, mentions}],
       edges: [{src, dst, predicate}] }
   Public API (window.EngramGraph2D):
     init(canvas, { onNode, onBackground, isDark })
     setData(nodes, edges)
     setTheme(isDark)
     focus(nodeId)          // highlight a node + neighbors
     clearFocus()
     destroy()
   ================================================================ */
(function () {
  "use strict";

  var CFG = {
    NODE_R_MIN: 5,
    NODE_R_MAX: 16,
    FONT: 11,
    EDGE_W: 0.8,
    EDGE_W_HL: 2.0,
    EDGE_OP: 0.22,
    EDGE_OP_HL: 0.85,
    REPULSION: 2400,
    LINK_DIST: 110,
    LINK_STRENGTH: 0.03,
    GRAVITY: 0.012,
    DAMPING: 0.85,
    MAX_SPEED: 14,
    ZOOM_MIN: 0.25,
    ZOOM_MAX: 3.5,
    ZOOM_STEP: 0.0012,
    DPR_MAX: 2,
    HOVER_PAD: 6,
    COOL_FRAMES: 480
  };

  var TYPE_COLORS = {
    person: "#4ade80", place: "#7c9cff", object: "#f5c451",
    concept: "#b48cff", unknown: "#66708c"
  };
  var TYPE_LABEL = {
    person: "人物", place: "地点", object: "事物",
    concept: "概念", unknown: "其它"
  };

  function clamp(v, lo, hi) { return Math.min(hi, Math.max(lo, v)); }
  function colorOf(t) { return TYPE_COLORS[t] || TYPE_COLORS.unknown; }

  var G = {
    canvas: null, ctx: null, dpr: 1,
    width: 0, height: 0,
    nodes: [], edges: [], byId: {}, adj: {},
    view: { scale: 1, ox: 0, oy: 0 },
    raf: null, cool: 0,
    hoverId: null, focusId: null,
    drag: null, pan: null,
    dark: false,
    cb: { onNode: null, onBackground: null },
    ro: null
  };

  /* ---------- coordinate transforms ---------- */
  function worldToScreen(x, y) {
    return {
      x: x * G.view.scale + G.view.ox,
      y: y * G.view.scale + G.view.oy
    };
  }
  function screenToWorld(x, y) {
    return {
      x: (x - G.view.ox) / G.view.scale,
      y: (y - G.view.oy) / G.view.scale
    };
  }
  function nodeRadius(n) {
    var m = n.mentions || 0;
    return clamp(CFG.NODE_R_MIN + Math.sqrt(m) * 2.2,
                 CFG.NODE_R_MIN, CFG.NODE_R_MAX);
  }

  /* ---------- sizing ---------- */
  function resize() {
    if (!G.canvas) return;
    var rect = G.canvas.getBoundingClientRect();
    var w = Math.max(1, rect.width), h = Math.max(1, rect.height);
    G.dpr = Math.min(window.devicePixelRatio || 1, CFG.DPR_MAX);
    G.width = w; G.height = h;
    G.canvas.width = Math.round(w * G.dpr);
    G.canvas.height = Math.round(h * G.dpr);
    G.ctx.setTransform(G.dpr, 0, 0, G.dpr, 0, 0);
  }

  /* ---------- data ---------- */
  function setData(nodes, edges) {
    G.byId = {};
    G.nodes = (nodes || []).map(function (n) {
      var node = {
        id: n.id, name: n.name || n.id,
        type: n.type || "unknown", mentions: n.mentions || 0,
        x: (G.width || 600) / 2 + (Math.random() - 0.5) * (G.width || 600) * 0.6,
        y: (G.height || 400) / 2 + (Math.random() - 0.5) * (G.height || 400) * 0.6,
        vx: 0, vy: 0, pinned: false
      };
      G.byId[n.id] = node;
      return node;
    });
    G.adj = {};
    G.edges = (edges || []).filter(function (e) {
      return G.byId[e.src] && G.byId[e.dst];
    }).map(function (e) {
      (G.adj[e.src] = G.adj[e.src] || {})[e.dst] = true;
      (G.adj[e.dst] = G.adj[e.dst] || {})[e.src] = true;
      return { src: e.src, dst: e.dst, predicate: e.predicate || "" };
    });
    // Parallel-edge label slots. Edges between the same UNORDERED node pair
    // share a midpoint regardless of direction (A->B and B->A collide), so we
    // key by the sorted pair and assign each edge a slot index (0,1,2,...) plus
    // the pair total. At draw time we stack the labels along the edge normal so
    // multiple relations never overprint.
    function _pairKey(a, b) { return a < b ? a + "\u0000" + b : b + "\u0000" + a; }
    var pairSlots = {};
    for (var pe = 0; pe < G.edges.length; pe++) {
      var ed = G.edges[pe];
      var pk = _pairKey(ed.src, ed.dst);
      var slot = pairSlots[pk] || 0;
      ed._slot = slot;
      pairSlots[pk] = slot + 1;
    }
    for (var pe2 = 0; pe2 < G.edges.length; pe2++) {
      var ed2 = G.edges[pe2];
      ed2._slotTotal = pairSlots[_pairKey(ed2.src, ed2.dst)] || 1;
    }
    G.focusId = null; G.hoverId = null;
    G.cool = CFG.COOL_FRAMES;
    fitView();
    start();
  }

  /* ---------- physics ---------- */
  function step() {
    var nodes = G.nodes, edges = G.edges;
    var n = nodes.length;
    if (!n) return;
    var cx = G.width / 2, cy = G.height / 2;
    // repulsion (O(n^2); fine for a few hundred nodes)
    for (var i = 0; i < n; i++) {
      var a = nodes[i];
      for (var j = i + 1; j < n; j++) {
        var b = nodes[j];
        var dx = a.x - b.x, dy = a.y - b.y;
        var d2 = dx * dx + dy * dy + 0.01;
        var dist = Math.sqrt(d2);
        var f = CFG.REPULSION / d2;
        var fx = (dx / dist) * f, fy = (dy / dist) * f;
        a.vx += fx; a.vy += fy;
        b.vx -= fx; b.vy -= fy;
      }
    }
    // links (spring)
    for (var k = 0; k < edges.length; k++) {
      var e = edges[k];
      var s = G.byId[e.src], t = G.byId[e.dst];
      var ex = t.x - s.x, ey = t.y - s.y;
      var ed = Math.sqrt(ex * ex + ey * ey) + 0.01;
      var sf = (ed - CFG.LINK_DIST) * CFG.LINK_STRENGTH;
      var sfx = (ex / ed) * sf, sfy = (ey / ed) * sf;
      s.vx += sfx; s.vy += sfy;
      t.vx -= sfx; t.vy -= sfy;
    }
    // gravity + integrate
    for (var p = 0; p < n; p++) {
      var nd = nodes[p];
      if (nd.pinned) { nd.vx = 0; nd.vy = 0; continue; }
      nd.vx += (cx - nd.x) * CFG.GRAVITY;
      nd.vy += (cy - nd.y) * CFG.GRAVITY;
      nd.vx *= CFG.DAMPING; nd.vy *= CFG.DAMPING;
      var sp = Math.sqrt(nd.vx * nd.vx + nd.vy * nd.vy);
      if (sp > CFG.MAX_SPEED) {
        nd.vx = (nd.vx / sp) * CFG.MAX_SPEED;
        nd.vy = (nd.vy / sp) * CFG.MAX_SPEED;
      }
      nd.x += nd.vx; nd.y += nd.vy;
    }
  }

  /* ---------- render ---------- */
  function cssVar(name, fb) {
    try {
      var v = getComputedStyle(document.documentElement)
        .getPropertyValue(name).trim();
      return v || fb;
    } catch (e) { return fb; }
  }

  function draw() {
    var ctx = G.ctx;
    if (!ctx) return;
    ctx.clearRect(0, 0, G.width, G.height);
    var edgeBase = cssVar("--border-strong", "#dee2e6");
    var accent = cssVar("--accent", "#4c6ef5");
    var textCol = cssVar("--text", "#212529");
    var cardCol = cssVar("--bg-card", "#ffffff");

    var nb = G.focusId ? (G.adj[G.focusId] || {}) : null;

    // edges
    for (var i = 0; i < G.edges.length; i++) {
      var e = G.edges[i];
      var s = G.byId[e.src], t = G.byId[e.dst];
      var ps = worldToScreen(s.x, s.y), pt = worldToScreen(t.x, t.y);
      var hot = G.focusId && (e.src === G.focusId || e.dst === G.focusId);
      ctx.globalAlpha = hot ? CFG.EDGE_OP_HL : CFG.EDGE_OP;
      ctx.strokeStyle = hot ? accent : edgeBase;
      ctx.lineWidth = hot ? CFG.EDGE_W_HL : CFG.EDGE_W;
      ctx.beginPath();
      ctx.moveTo(ps.x, ps.y); ctx.lineTo(pt.x, pt.y);
      ctx.stroke();
      if (hot && e.predicate && G.view.scale > 0.7) {
        ctx.globalAlpha = 0.9;
        ctx.fillStyle = textCol;
        ctx.font = "9px system-ui, sans-serif";
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        // Multiple relations between the same entity pair all sit at the
        // midpoint, so stack them along the edge normal (one line each) and
        // center the stack on the midpoint. Slot order is direction-stable
        // (see _pairKey), so A->B and B->A never land on the same row.
        var total = e._slotTotal || 1, slot = e._slot || 0;
        var mx = (ps.x + pt.x) / 2, my = (ps.y + pt.y) / 2;
        if (total > 1) {
          var dx = pt.x - ps.x, dy = pt.y - ps.y;
          var len = Math.sqrt(dx * dx + dy * dy) || 1;
          // Normalize normal direction by sorted endpoints so the stack order
          // is the same regardless of which way this edge points.
          if (e.src > e.dst) { dx = -dx; dy = -dy; }
          var nx = -dy / len, ny = dx / len;   // unit normal (stable)
          var LINE = 13;                         // px per stacked label row
          var off = (slot - (total - 1) / 2) * LINE;
          mx += nx * off; my += ny * off;
        }
        ctx.fillText(e.predicate, mx, my);
        ctx.textBaseline = "alphabetic";
      }
    }
    ctx.globalAlpha = 1;

    // nodes
    for (var j = 0; j < G.nodes.length; j++) {
      var nd = G.nodes[j];
      var p = worldToScreen(nd.x, nd.y);
      var r = nodeRadius(nd) * Math.min(1.4, Math.max(0.7, G.view.scale));
      var dim = G.focusId && nd.id !== G.focusId && !(nb && nb[nd.id]);
      ctx.globalAlpha = dim ? 0.22 : 1;
      ctx.beginPath();
      ctx.arc(p.x, p.y, r, 0, Math.PI * 2);
      ctx.fillStyle = colorOf(nd.type);
      ctx.fill();
      ctx.lineWidth = (nd.id === G.hoverId || nd.id === G.focusId) ? 2.4 : 1.4;
      ctx.strokeStyle = (nd.id === G.hoverId || nd.id === G.focusId)
        ? accent : cardCol;
      ctx.stroke();
      // label when zoomed in / hovered / focused
      if (!dim && (G.view.scale > 0.75 || nd.id === G.hoverId
                   || nd.id === G.focusId)) {
        ctx.globalAlpha = dim ? 0.3 : 1;
        ctx.fillStyle = textCol;
        ctx.font = CFG.FONT + "px system-ui, sans-serif";
        ctx.textAlign = "center";
        var label = nd.name.length > 12 ? nd.name.slice(0, 12) + "…" : nd.name;
        ctx.fillText(label, p.x, p.y - r - 4);
      }
    }
    ctx.globalAlpha = 1;
  }

  function tick() {
    if (G.cool > 0) { step(); G.cool--; }
    draw();
    G.raf = requestAnimationFrame(tick);
  }
  function start() {
    if (G.raf) return;
    G.raf = requestAnimationFrame(tick);
  }

  /* ---------- view fit ---------- */
  function fitView() {
    if (!G.nodes.length || !G.width) {
      G.view = { scale: 1, ox: 0, oy: 0 };
      return;
    }
    var minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (var i = 0; i < G.nodes.length; i++) {
      var n = G.nodes[i];
      if (n.x < minX) minX = n.x; if (n.x > maxX) maxX = n.x;
      if (n.y < minY) minY = n.y; if (n.y > maxY) maxY = n.y;
    }
    var bw = Math.max(1, maxX - minX), bh = Math.max(1, maxY - minY);
    var pad = 0.82;
    var scale = clamp(Math.min(G.width * pad / bw, G.height * pad / bh),
                      CFG.ZOOM_MIN, CFG.ZOOM_MAX);
    G.view.scale = scale;
    G.view.ox = G.width / 2 - ((minX + maxX) / 2) * scale;
    G.view.oy = G.height / 2 - ((minY + maxY) / 2) * scale;
  }

  /* ---------- hit testing ---------- */
  function hitNode(sx, sy) {
    for (var i = G.nodes.length - 1; i >= 0; i--) {
      var nd = G.nodes[i];
      var p = worldToScreen(nd.x, nd.y);
      var r = nodeRadius(nd) * Math.min(1.4, Math.max(0.7, G.view.scale))
        + CFG.HOVER_PAD;
      var dx = sx - p.x, dy = sy - p.y;
      if (dx * dx + dy * dy <= r * r) return nd;
    }
    return null;
  }

  /* ---------- interaction ---------- */
  function pos(ev) {
    var rect = G.canvas.getBoundingClientRect();
    var t = ev.touches && ev.touches[0] ? ev.touches[0] : ev;
    return { x: t.clientX - rect.left, y: t.clientY - rect.top };
  }
  function onDown(ev) {
    var p = pos(ev);
    var hit = hitNode(p.x, p.y);
    if (hit) {
      G.drag = { node: hit, moved: false };
      hit.pinned = true;
      G.cool = CFG.COOL_FRAMES;
    } else {
      G.pan = { x: p.x, y: p.y, ox: G.view.ox, oy: G.view.oy, moved: false };
    }
  }
  function onMove(ev) {
    var p = pos(ev);
    if (G.drag) {
      var w = screenToWorld(p.x, p.y);
      G.drag.node.x = w.x; G.drag.node.y = w.y;
      G.drag.node.vx = 0; G.drag.node.vy = 0;
      G.drag.moved = true;
      G.cool = CFG.COOL_FRAMES;
      ev.preventDefault();
    } else if (G.pan) {
      G.view.ox = G.pan.ox + (p.x - G.pan.x);
      G.view.oy = G.pan.oy + (p.y - G.pan.y);
      G.pan.moved = true;
      ev.preventDefault();
    } else {
      var hit = hitNode(p.x, p.y);
      var id = hit ? hit.id : null;
      if (id !== G.hoverId) {
        G.hoverId = id;
        G.canvas.style.cursor = id ? "pointer" : "grab";
      }
    }
  }
  function onUp(ev) {
    if (G.drag) {
      G.drag.node.pinned = false;
      if (!G.drag.moved) selectNode(G.drag.node);
      G.drag = null;
    } else if (G.pan) {
      if (!G.pan.moved) {
        // background click clears focus
        G.focusId = null;
        if (G.cb.onBackground) G.cb.onBackground();
      }
      G.pan = null;
    }
  }
  function onWheel(ev) {
    ev.preventDefault();
    var p = pos(ev);
    var before = screenToWorld(p.x, p.y);
    var factor = Math.exp(-ev.deltaY * CFG.ZOOM_STEP);
    G.view.scale = clamp(G.view.scale * factor, CFG.ZOOM_MIN, CFG.ZOOM_MAX);
    var after = screenToWorld(p.x, p.y);
    G.view.ox += (after.x - before.x) * G.view.scale;
    G.view.oy += (after.y - before.y) * G.view.scale;
  }
  function selectNode(nd) {
    G.focusId = nd.id;
    if (G.cb.onNode) G.cb.onNode({ id: nd.id, name: nd.name, type: nd.type });
  }

  /* ---------- public API ---------- */
  function init(canvas, opts) {
    opts = opts || {};
    destroy();
    G.canvas = canvas;
    G.ctx = canvas.getContext("2d");
    G.cb.onNode = opts.onNode || null;
    G.cb.onBackground = opts.onBackground || null;
    G.dark = !!opts.isDark;
    resize();
    canvas.addEventListener("mousedown", onDown);
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    canvas.addEventListener("wheel", onWheel, { passive: false });
    canvas.addEventListener("touchstart", onDown, { passive: false });
    window.addEventListener("touchmove", onMove, { passive: false });
    window.addEventListener("touchend", onUp);
    if (window.ResizeObserver) {
      G.ro = new ResizeObserver(function () { resize(); });
      G.ro.observe(canvas);
    } else {
      window.addEventListener("resize", resize);
    }
    canvas.style.cursor = "grab";
    start();
  }
  function setTheme(isDark) { G.dark = !!isDark; }
  function focus(id) {
    if (G.byId[id]) { G.focusId = id; G.cool = Math.max(G.cool, 60); }
  }
  function clearFocus() { G.focusId = null; }
  function destroy() {
    if (G.raf) { cancelAnimationFrame(G.raf); G.raf = null; }
    if (G.canvas) {
      G.canvas.removeEventListener("mousedown", onDown);
      G.canvas.removeEventListener("wheel", onWheel);
      G.canvas.removeEventListener("touchstart", onDown);
    }
    window.removeEventListener("mousemove", onMove);
    window.removeEventListener("mouseup", onUp);
    window.removeEventListener("touchmove", onMove);
    window.removeEventListener("touchend", onUp);
    window.removeEventListener("resize", resize);
    if (G.ro) { try { G.ro.disconnect(); } catch (e) {} G.ro = null; }
    G.nodes = []; G.edges = []; G.byId = {}; G.adj = {};
    G.focusId = null; G.hoverId = null; G.drag = null; G.pan = null;
  }

  window.EngramGraph2D = {
    init: init, setData: setData, setTheme: setTheme,
    focus: focus, clearFocus: clearFocus, destroy: destroy,
    TYPE_COLORS: TYPE_COLORS, TYPE_LABEL: TYPE_LABEL
  };
})();
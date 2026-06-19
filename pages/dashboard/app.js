/* Engram Dashboard WebUI
 * Talks to the plugin backend through the AstrBot plugin-page bridge
 * (window.AstrBotPluginPage). AstrBot injects the bridge-sdk <script>
 * just before </body>, i.e. AFTER this file runs, so we never cache the
 * bridge at parse time: read it live on each use and wait for it on init.
 * Backend routes live under /astrbot_plugin_engram/page/* (page_api.py);
 * the bridge prefixes "/<plugin_name>/", so we pass "page/xxx".
 */
(function () {
  "use strict";

  function getBridge() { return window.AstrBotPluginPage || null; }

  function waitForBridge(timeoutMs) {
    return new Promise(function (resolve) {
      var b = getBridge();
      if (b) { resolve(b); return; }
      var waited = 0, step = 50;
      var timer = setInterval(function () {
        var bb = getBridge();
        if (bb || waited >= timeoutMs) { clearInterval(timer); resolve(bb || null); }
        waited += step;
      }, step);
    });
  }

  function applyTheme(isDark) {
    document.documentElement.setAttribute("data-theme", isDark ? "dark" : "light");
  }

  function toast(msg) {
    var el = document.getElementById("toast");
    el.textContent = msg;
    el.classList.add("show");
    setTimeout(function () { el.classList.remove("show"); }, 2400);
  }

  function endpoint(path) {
    var p = String(path).replace(/^\/+/, "");
    return p.indexOf("page/") === 0 ? p : "page/" + p;
  }

  async function apiGet(path, params) {
    var b = getBridge();
    if (!b) throw new Error("AstrBot 插件桥不可用，请在 AstrBot 后台打开本页面。");
    return b.apiGet(endpoint(path), params || {});
  }
  async function apiPost(path, body) {
    var b = getBridge();
    if (!b) throw new Error("AstrBot 插件桥不可用，请在 AstrBot 后台打开本页面。");
    return b.apiPost(endpoint(path), body || {});
  }

  function unwrap(resp) {
    if (resp && resp.status === "error") throw new Error(resp.message || "后端返回错误");
    if (resp && "data" in resp) return resp.data;
    return resp;
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function emptyBox(msg) { return '<div class="empty">' + escapeHtml(msg) + "</div>"; }
  function errBox(msg) { return '<div class="err-box">' + escapeHtml(msg) + "</div>"; }

  // ---------- 字段中文映射 ----------
  var FIELD_LABELS = {
    id: "记忆 ID",
    summary: "摘要",
    content: "原文内容",
    actor_id: "用户标识",
    stream: "记忆流",
    memory_type: "记忆类型",
    strength: "记忆强度",
    importance: "重要度",
    confidence: "置信度",
    created_at: "创建时间",
    updated_at: "更新时间",
    forgotten_at: "遗忘时间",
    entity_refs: "关联实体",
    cluster_id: "聚类 ID",
    valence: "情绪价",
    intensity: "情绪强度",
    score: "相关度",
    similarity: "相似度",
    returned: "本页条数",
    offset: "偏移量",
    k: "请求条数",
    mode: "模式"
  };
  function fieldLabel(k) { return FIELD_LABELS[k] || k; }

  var STREAM_LABELS = { what: "内容流（是什么）", where_when: "时空流（何时何地）", "": "未分类" };
  var MEMTYPE_LABELS = { episodic: "情景记忆", semantic: "语义记忆", prospective: "前瞻记忆" };
  function fieldValueText(k, v) {
    if (v === null || v === undefined || v === "") return "—";
    if (k === "stream") return STREAM_LABELS[v] || v;
    if (k === "memory_type") return MEMTYPE_LABELS[v] || v;
    if (k === "created_at" || k === "updated_at" || k === "forgotten_at") {
      return fmtTime(v);
    }
    if (typeof v === "number") {
      // 概率类字段保留 3 位小数，其余原样
      if (k === "strength" || k === "importance" || k === "confidence" ||
          k === "valence" || k === "intensity" || k === "score" || k === "similarity") {
        return Number(v).toFixed(3);
      }
      return String(v);
    }
    return null; // 交给上层判断对象/原样
  }
  function fmtTime(v) {
    var n = Number(v);
    if (!n) return String(v);
    // 秒级或毫秒级时间戳
    var ms = n < 1e12 ? n * 1000 : n;
    var d = new Date(ms);
    if (isNaN(d.getTime())) return String(v);
    function pad(x) { return x < 10 ? "0" + x : x; }
    return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()) +
      " " + pad(d.getHours()) + ":" + pad(d.getMinutes());
  }
  // ---------- tabs ----------
  document.querySelectorAll(".tab").forEach(function (tab) {
    tab.addEventListener("click", function () {
      document.querySelectorAll(".tab").forEach(function (t) { t.classList.remove("active"); });
      document.querySelectorAll(".panel").forEach(function (p) { p.classList.remove("active"); });
      tab.classList.add("active");
      document.querySelector('.panel[data-panel="' + tab.getAttribute("data-tab") + '"]').classList.add("active");
    });
  });

  // ---------- health ----------
  async function loadHealth() {
    var el = document.getElementById("health");
    try {
      var d = unwrap(await apiGet("page/health"));
      el.textContent = "v" + (d.version || "?") + " · " +
        (d.language === "en" ? "EN" : "中文") +
        (d.service_ready ? " · 已就绪" : " · 初始化中");
      el.className = "status ok";
    } catch (e) {
      el.textContent = "未连接";
      el.className = "status err";
      el.title = e.message;
    }
  }

  // ---------- overview ----------
  var STAT_META = {
    engrams: { label: "记忆条目", icon: "🧠" },
    fts_count: { label: "全文索引", icon: "🔎" },
    entities: { label: "语义实体", icon: "🕸" },
    atoms: { label: "记忆原子", icon: "⚛" },
    pending_triggers: { label: "待触发提醒", icon: "⏳" },
    fired_triggers: { label: "已触发提醒", icon: "✅" }
  };
  var STAT_ORDER = ["engrams", "fts_count", "entities", "atoms", "pending_triggers", "fired_triggers"];
  function fmtNum(v) { return v < 0 ? "—" : String(v); }

  async function loadStats() {
    var box = document.getElementById("stat-cards");
    box.innerHTML = emptyBox("加载中…");
    try {
      var d = unwrap(await apiGet("page/stats"));
      var present = Object.keys(d).filter(function (k) { return typeof d[k] === "number"; });
      var keys = STAT_ORDER.filter(function (k) { return present.indexOf(k) >= 0; });
      present.forEach(function (k) { if (keys.indexOf(k) < 0) keys.push(k); });
      if (!keys.length) { box.innerHTML = emptyBox("暂无数据"); return; }
      box.innerHTML = "";
      keys.forEach(function (k) {
        var meta = STAT_META[k] || { label: k, icon: "•" };
        var card = document.createElement("div");
        card.className = "card";
        card.innerHTML =
          '<div class="ico">' + meta.icon + "</div>" +
          '<div class="meta"><div class="num">' + fmtNum(d[k]) + "</div>" +
          '<div class="lbl">' + escapeHtml(meta.label) + "</div></div>";
        box.appendChild(card);
      });
    } catch (e) {
      box.innerHTML = errBox(e.message);
    }
  }

  // ---------- memories ----------
  async function loadMemories() {
    var actor = document.getElementById("mem-actor").value.trim();
    var k = document.getElementById("mem-k").value || 50;
    var wrap = document.getElementById("mem-rows");
    document.getElementById("mem-detail").innerHTML = "";
    wrap.innerHTML = emptyBox("加载中…");
    try {
      var d = unwrap(await apiGet("page/memories", { actor_id: actor, k: k, offset: 0 }));
      var items = (d && d.items) || [];
      if (!items.length) { wrap.innerHTML = emptyBox("暂无记忆"); return; }
      wrap.innerHTML = "";
      items.forEach(function (it) {
        var div = document.createElement("div");
        div.className = "mem-item";
        var head = '<div class="mem-head">' +
          '<span class="chip">#' + escapeHtml(it.id == null ? "?" : it.id) + "</span>" +
          (it.actor_id ? '<span class="chip chip-muted">用户 ' + escapeHtml(it.actor_id) + "</span>" : "") +
          (it.strength != null ? '<span class="chip chip-muted">强度 ' + Number(it.strength).toFixed(2) + "</span>" : "") +
          "</div>";
        div.innerHTML = head + '<div class="mem-summary">' +
          escapeHtml(it.summary || "（无摘要）") + "</div>";
        div.addEventListener("click", function () { showDetail(it.id); });
        wrap.appendChild(div);
      });
    } catch (e) {
      wrap.innerHTML = errBox(e.message);
    }
  }

  function kvRows(obj) {
    var html = '<div class="kv">';
    Object.keys(obj).forEach(function (k) {
      var v = obj[k];
      var isObj = v !== null && typeof v === "object";
      var text;
      if (isObj) {
        if (Array.isArray(v)) { text = v.length ? v.join("、") : "—"; isObj = v.length > 0 && typeof v[0] === "object"; if (isObj) text = JSON.stringify(v, null, 2); }
        else { text = JSON.stringify(v, null, 2); }
      } else {
        var fv = fieldValueText(k, v);
        text = (fv === null) ? String(v) : fv;
      }
      html += '<div class="kv-row"><div class="kv-k">' + escapeHtml(fieldLabel(k)) + "</div>" +
        '<div class="kv-v' + (isObj ? " mono" : "") + '">' + escapeHtml(text) + "</div></div>";
    });
    return html + "</div>";
  }

  async function showDetail(eid) {
    var el = document.getElementById("mem-detail");
    el.innerHTML = '<div class="section-title">记忆详情 #' + escapeHtml(eid) + "</div>" + emptyBox("加载详情…");
    try {
      var d = unwrap(await apiGet("page/memories/detail", { eid: eid }));
      el.innerHTML = '<div class="section-title">记忆详情 #' + escapeHtml(eid) + "</div>" +
        (d && typeof d === "object" ? kvRows(d) : '<div class="raw">' + escapeHtml(JSON.stringify(d, null, 2)) + "</div>");
    } catch (e) {
      el.innerHTML = errBox(e.message);
    }
  }

  // ---------- recall ----------
  async function runRecall() {
    var out = document.getElementById("rc-out");
    out.innerHTML = emptyBox("召回中…");
    try {
      var d = unwrap(await apiPost("page/recall/test", {
        query: document.getElementById("rc-query").value,
        mode: document.getElementById("rc-mode").value,
        k: Number(document.getElementById("rc-k").value) || 5
      }));
      var results = (d && (d.results || d.items)) || (Array.isArray(d) ? d : null);
      if (results && results.length) {
        out.innerHTML = "";
        results.forEach(function (r) {
          var score = (r.score != null) ? r.score : (r.similarity != null ? r.similarity : null);
          var text = r.summary || r.text || r.content || JSON.stringify(r);
          var div = document.createElement("div");
          div.className = "result";
          div.innerHTML =
            '<div class="result-head">' +
            (r.id != null ? '<span class="chip">#' + escapeHtml(r.id) + "</span>" : "") +
            (score != null ? '<span class="score">' + (typeof score === "number" ? score.toFixed(3) : escapeHtml(score)) + "</span>" : "") +
            "</div><div class=\"result-text\">" + escapeHtml(text) + "</div>";
          out.appendChild(div);
        });
      } else if (results) {
        out.innerHTML = emptyBox("无召回结果");
      } else {
        out.innerHTML = '<div class="raw">' + escapeHtml(JSON.stringify(d, null, 2)) + "</div>";
      }
    } catch (e) {
      out.innerHTML = errBox(e.message);
    }
  }

  // ---------- backups ----------
  async function loadBackups() {
    var tbody = document.getElementById("bk-rows");
    tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;color:var(--text-2)">加载中…</td></tr>';
    try {
      var d = unwrap(await apiGet("page/backups"));
      var items = (d && d.items) || (Array.isArray(d) ? d : []);
      if (!items.length) { tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;color:var(--text-2)">暂无备份</td></tr>'; return; }
      tbody.innerHTML = "";
      items.forEach(function (b) {
        var bid = b.id || b.backup_id || b.name || "";
        var tr = document.createElement("tr");
        tr.innerHTML =
          '<td class="mono">' + escapeHtml(String(bid)) + "</td>" +
          "<td>" + escapeHtml(String(b.created || b.time || b.mtime || "—")) + "</td>" +
          "<td>" + escapeHtml(fmtSize(b.size || b.bytes)) + "</td>" +
          '<td style="text-align:right"><button class="btn btn-danger btn-sm">恢复</button></td>';
        tr.querySelector("button").addEventListener("click", function () { restoreBackup(bid); });
        tbody.appendChild(tr);
      });
    } catch (e) {
      tbody.innerHTML = '<tr><td colspan="4">' + errBox(e.message) + "</td></tr>";
    }
  }
  function fmtSize(n) {
    n = Number(n);
    if (!n || n < 0) return "—";
    if (n < 1024) return n + " B";
    if (n < 1048576) return (n / 1024).toFixed(1) + " KB";
    return (n / 1048576).toFixed(1) + " MB";
  }

  async function restoreBackup(bid) {
    if (!confirm("确定用备份 " + bid + " 覆盖当前数据库吗？此操作不可逆。")) return;
    try {
      unwrap(await apiPost("page/backups/restore", { backup_id: bid }));
      toast("恢复请求已提交：" + bid);
    } catch (e) {
      toast("恢复失败：" + e.message);
    }
  }

  // ---------- 关系图谱（原生 SVG 力导向） ----------
  var SVGNS = "http://www.w3.org/2000/svg";
  var NODE_COLORS = {
    person: "#2f9e8b", place: "#4c6ef5", object: "#c99a16",
    concept: "#7c6fca", unknown: "#8b949e"
  };
  var NODE_TYPE_LABEL = { person: "人物", place: "地点", object: "事物", concept: "概念", unknown: "其它" };
  var graphSim = null;

  function colorOf(type) { return NODE_COLORS[type] || NODE_COLORS.unknown; }

  async function loadGraph() {
    var stage = document.getElementById("graph-stage");
    var tip = document.getElementById("graph-tip");
    var legend = document.getElementById("graph-legend");
    document.getElementById("graph-detail").innerHTML = "";
    stage.innerHTML = emptyBox("加载中…");
    try {
      var d = unwrap(await apiGet("page/graph/data", { limit: 300 }));
      var nodes = (d && d.nodes) || [];
      var edges = (d && d.edges) || [];
      if (!nodes.length) { stage.innerHTML = emptyBox("暂无实体，先让 Bot 多聊一些再回来看"); legend.innerHTML = ""; tip.textContent = ""; return; }
      // legend
      var types = {};
      nodes.forEach(function (n) { types[n.type || "unknown"] = true; });
      legend.innerHTML = Object.keys(types).map(function (ty) {
        return '<span class="lg"><span class="dot" style="background:' + colorOf(ty) + '"></span>' +
          escapeHtml(NODE_TYPE_LABEL[ty] || ty) + "</span>";
      }).join("");
      tip.textContent = "实体 " + nodes.length + " · 关系 " + edges.length +
        (d.truncated ? "（已截断）" : "") + " · 拖动节点 / 点击查看关系";
      renderGraph(stage, nodes, edges);
    } catch (e) {
      stage.innerHTML = errBox(e.message);
    }
  }

  function renderGraph(stage, nodes, edges) {
    if (graphSim) { clearInterval(graphSim); graphSim = null; }
    stage.innerHTML = "";
    var W = stage.clientWidth || 900, H = stage.clientHeight || 460;
    var svg = document.createElementNS(SVGNS, "svg");
    svg.setAttribute("viewBox", "0 0 " + W + " " + H);
    stage.appendChild(svg);

    var byId = {};
    nodes.forEach(function (n, idx) {
      n.x = W / 2 + (Math.random() - 0.5) * W * 0.6;
      n.y = H / 2 + (Math.random() - 0.5) * H * 0.6;
      n.vx = 0; n.vy = 0;
      n.r = Math.max(6, Math.min(16, 6 + Math.sqrt(n.mentions || 0) * 2));
      byId[n.id] = n;
    });
    edges = edges.filter(function (e) { return byId[e.src] && byId[e.dst]; });

    // adjacency for highlight
    var adj = {};
    edges.forEach(function (e) {
      (adj[e.src] = adj[e.src] || {})[e.dst] = true;
      (adj[e.dst] = adj[e.dst] || {})[e.src] = true;
    });

    var edgeEls = edges.map(function (e) {
      var ln = document.createElementNS(SVGNS, "line");
      ln.setAttribute("class", "g-edge");
      svg.appendChild(ln);
      var lbl = null;
      if (e.predicate) {
        lbl = document.createElementNS(SVGNS, "text");
        lbl.setAttribute("class", "g-edgelabel");
        lbl.setAttribute("text-anchor", "middle");
        lbl.textContent = e.predicate;
        svg.appendChild(lbl);
      }
      return { e: e, ln: ln, lbl: lbl };
    });

    var nodeEls = nodes.map(function (n) {
      var g = document.createElementNS(SVGNS, "g");
      g.setAttribute("class", "g-node");
      var c = document.createElementNS(SVGNS, "circle");
      c.setAttribute("r", n.r);
      c.setAttribute("fill", colorOf(n.type));
      var tx = document.createElementNS(SVGNS, "text");
      tx.setAttribute("text-anchor", "middle");
      tx.setAttribute("dy", -(n.r + 4));
      tx.textContent = (n.name || n.id).slice(0, 12);
      g.appendChild(c); g.appendChild(tx);
      svg.appendChild(g);
      enableDrag(g, n, svg, W, H);
      c.addEventListener("click", function (ev) {
        ev.stopPropagation();
        highlight(n.id, byId, adj, nodeEls, edgeEls);
        showEntityDetail(n.name || n.id);
      });
      return { n: n, g: g };
    });

    svg.addEventListener("click", function () { clearHighlight(nodeEls, edgeEls); });

    // simple force simulation
    var iter = 0;
    graphSim = setInterval(function () {
      step(nodes, edges, byId, W, H);
      edgeEls.forEach(function (o) {
        var a = byId[o.e.src], b = byId[o.e.dst];
        o.ln.setAttribute("x1", a.x); o.ln.setAttribute("y1", a.y);
        o.ln.setAttribute("x2", b.x); o.ln.setAttribute("y2", b.y);
        if (o.lbl) { o.lbl.setAttribute("x", (a.x + b.x) / 2); o.lbl.setAttribute("y", (a.y + b.y) / 2); }
      });
      nodeEls.forEach(function (o) { o.g.setAttribute("transform", "translate(" + o.n.x + "," + o.n.y + ")"); });
      iter++;
      if (iter > 320) { clearInterval(graphSim); graphSim = null; }
    }, 16);
  }

  function step(nodes, edges, byId, W, H) {
    var k = 0.9, rep = 1600, spring = 0.02, len = 70;
    for (var i = 0; i < nodes.length; i++) {
      var a = nodes[i];
      for (var j = i + 1; j < nodes.length; j++) {
        var b = nodes[j];
        var dx = a.x - b.x, dy = a.y - b.y;
        var dist2 = dx * dx + dy * dy + 0.01;
        var f = rep / dist2;
        var dist = Math.sqrt(dist2);
        var fx = (dx / dist) * f, fy = (dy / dist) * f;
        a.vx += fx; a.vy += fy; b.vx -= fx; b.vy -= fy;
      }
    }
    edges.forEach(function (e) {
      var a = byId[e.src], b = byId[e.dst];
      var dx = b.x - a.x, dy = b.y - a.y;
      var dist = Math.sqrt(dx * dx + dy * dy) + 0.01;
      var f = (dist - len) * spring;
      var fx = (dx / dist) * f, fy = (dy / dist) * f;
      a.vx += fx; a.vy += fy; b.vx -= fx; b.vy -= fy;
    });
    var cx = W / 2, cy = H / 2;
    nodes.forEach(function (n) {
      if (n.fixed) return;
      n.vx += (cx - n.x) * 0.002; n.vy += (cy - n.y) * 0.002;
      n.vx *= k; n.vy *= k;
      n.x += n.vx; n.y += n.vy;
      n.x = Math.max(n.r + 2, Math.min(W - n.r - 2, n.x));
      n.y = Math.max(n.r + 14, Math.min(H - n.r - 2, n.y));
    });
  }

  function enableDrag(g, n, svg, W, H) {
    var dragging = false;
    g.addEventListener("mousedown", function (ev) {
      dragging = true; n.fixed = true; ev.preventDefault();
    });
    window.addEventListener("mousemove", function (ev) {
      if (!dragging) return;
      var pt = svgPoint(svg, ev.clientX, ev.clientY, W, H);
      n.x = pt.x; n.y = pt.y; n.vx = 0; n.vy = 0;
    });
    window.addEventListener("mouseup", function () {
      if (dragging) { dragging = false; n.fixed = false; }
    });
  }

  function svgPoint(svg, clientX, clientY, W, H) {
    var rect = svg.getBoundingClientRect();
    return { x: (clientX - rect.left) / rect.width * W, y: (clientY - rect.top) / rect.height * H };
  }

  function highlight(id, byId, adj, nodeEls, edgeEls) {
    var nb = adj[id] || {};
    nodeEls.forEach(function (o) {
      var on = (o.n.id === id) || nb[o.n.id];
      o.g.classList.toggle("dim", !on);
    });
    edgeEls.forEach(function (o) {
      var on = (o.e.src === id || o.e.dst === id);
      o.ln.classList.toggle("hl", on);
    });
  }
  function clearHighlight(nodeEls, edgeEls) {
    nodeEls.forEach(function (o) { o.g.classList.remove("dim"); });
    edgeEls.forEach(function (o) { o.ln.classList.remove("hl"); });
  }

  async function showEntityDetail(name) {
    var el = document.getElementById("graph-detail");
    el.innerHTML = '<div class="section-title">实体关系 · ' + escapeHtml(name) + "</div>" + emptyBox("加载中…");
    try {
      var d = unwrap(await apiPost("page/graph/query", { name: name }));
      var ent = (d && d.entity) || {};
      var rels = (d && d.relations) || [];
      var refs = (d && d.engram_refs) || [];
      var html = '<div class="section-title">实体关系 · ' + escapeHtml(ent.name || name) + "</div>";
      html += kvRows({ name: ent.name, type: ent.type });
      if (rels.length) {
        html += '<div class="section-title">关系（' + rels.length + "）</div>";
        rels.forEach(function (r) {
          html += '<div class="result"><div class="result-text">' +
            escapeHtml(r.src) + ' <span class="chip">' + escapeHtml(r.predicate || "关联") + "</span> " +
            escapeHtml(r.dst) + "</div></div>";
        });
      } else {
        html += emptyBox("该实体暂无关系记录");
      }
      if (refs.length) {
        html += '<div class="section-title">关联记忆（' + refs.length + "）</div>";
        refs.forEach(function (m) {
          html += '<div class="mem-item"><div class="mem-head"><span class="chip">#' +
            escapeHtml(m.id) + '</span></div><div class="mem-summary">' +
            escapeHtml(m.summary || "（无摘要）") + "</div></div>";
        });
      }
      el.innerHTML = html;
    } catch (e) {
      el.innerHTML = errBox(e.message);
    }
  }
  // ---------- wire ----------
  document.getElementById("btn-refresh-stats").addEventListener("click", loadStats);
  document.getElementById("btn-load-mem").addEventListener("click", loadMemories);
  document.getElementById("btn-recall").addEventListener("click", runRecall);
  document.getElementById("btn-load-backups").addEventListener("click", loadBackups);
  document.getElementById("btn-load-graph").addEventListener("click", loadGraph);
  document.getElementById("rc-query").addEventListener("keydown", function (e) {
    if (e.key === "Enter") runRecall();
  });

  async function init() {
    var b = await waitForBridge(8000);
    if (b && b.ready) {
      try {
        var ctx = await b.ready();
        if (ctx && typeof ctx.isDark === "boolean") applyTheme(ctx.isDark);
      } catch (e) { /* non-fatal */ }
    }
    await loadHealth();
    await loadStats();
  }
  init();
})();
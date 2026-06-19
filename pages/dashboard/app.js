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
    fts: { label: "全文索引", icon: "🔎" },
    entities: { label: "语义实体", icon: "🕸" },
    atoms: { label: "记忆原子", icon: "⚛" },
    prospective_pending: { label: "待触发", icon: "⏳" },
    prospective_fired: { label: "已触发", icon: "✅" },
    valence: { label: "情感记忆", icon: "💗" },
    clusters: { label: "聚类摘要", icon: "📚" }
  };
  function fmtNum(v) { return v < 0 ? "—" : String(v); }

  async function loadStats() {
    var box = document.getElementById("stat-cards");
    box.innerHTML = emptyBox("加载中…");
    try {
      var d = unwrap(await apiGet("page/stats"));
      var keys = Object.keys(d).filter(function (k) { return typeof d[k] === "number"; });
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
          (it.actor_id ? '<span class="chip chip-muted">' + escapeHtml(it.actor_id) + "</span>" : "") +
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
      var text = isObj ? JSON.stringify(v, null, 2) : String(v);
      html += '<div class="kv-row"><div class="kv-k">' + escapeHtml(k) + "</div>" +
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

  // ---------- wire ----------
  document.getElementById("btn-refresh-stats").addEventListener("click", loadStats);
  document.getElementById("btn-load-mem").addEventListener("click", loadMemories);
  document.getElementById("btn-recall").addEventListener("click", runRecall);
  document.getElementById("btn-load-backups").addEventListener("click", loadBackups);
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
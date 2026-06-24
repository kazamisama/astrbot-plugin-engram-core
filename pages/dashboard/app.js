/* Engram Dashboard WebUI
 * Talks to the plugin backend through the AstrBot plugin-page bridge
 * (window.AstrBotPluginPage). AstrBot injects the bridge-sdk <script>
 * just before </body>, i.e. AFTER this file runs, so we never cache the
 * bridge at parse time: read it live on each use and wait for it on init.
 * Backend routes live under /astrbot_plugin_engram_core/page/* (page_api.py);
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
    mode: "模式",
    topics: "话题",
    tags: "标签",
    tier: "分层",
    persona_id: "关联人格"
  };
  function fieldLabel(k) { return FIELD_LABELS[k] || k; }

  var STREAM_LABELS = { what: "内容流（是什么）", where_when: "时空流（何时何地）", "": "未分类" };
  var MEMTYPE_LABELS = { episodic: "情景记忆", semantic: "语义记忆", prospective: "前瞻记忆", diary: "日记" };
  var TIER_LABELS = { "": "未分类", hot: "热 (hot)", warm: "温 (warm)", cold: "冷 (cold)" };
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
      if (tab.getAttribute("data-tab") === "diary" && !_diaryState.optionsLoaded) {
        loadDiaryOptions();
      }
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
  // v1.50: inline fold for memory list (mirrors the diary pattern).
  // Each row holds its own collapsible body; clicking the head toggles
  // the fold; the action buttons (展开 / 软删除) live on the right and
  // stop propagation so they don't also trigger the fold. Editing still
  // works - inside the fold body we render kvRows by default with a
  // 查看 / 编辑 toggle, and "编辑" swaps in the same editForm() used
  // before (with sliders, save, etc.).
  var _memDetailCache = {};

  async function loadMemories() {
    var q = document.getElementById("mem-search").value.trim();
    var k = document.getElementById("mem-k").value || 50;
    var wrap = document.getElementById("mem-rows");
    // v1.50: detail panel below the list is no longer used (folded
    // inline). Empty it out, keep the DOM element for layout stability.
    var legacy = document.getElementById("mem-detail");
    if (legacy) legacy.innerHTML = "";
    _memDetailCache = {};
    wrap.innerHTML = emptyBox("加载中…");
    try {
      var d = unwrap(await apiGet("page/memories", { q: q, k: k, offset: 0 }));
      var items = (d && d.items) || [];
      if (!items.length) { wrap.innerHTML = emptyBox("暂无记忆"); return; }
      wrap.innerHTML = "";
      items.forEach(function (it) {
        wrap.appendChild(_buildMemoryRow(it));
      });
    } catch (e) {
      wrap.innerHTML = errBox(e.message);
    }
  }

  function _buildMemoryRow(it) {
    var div = document.createElement("div");
    div.className = "mem-item memory-item";
    div.setAttribute("data-eid", it.id || "");
    var groupTxt = "";
    if (it.group_name && it.group_id) groupTxt = it.group_name + " (" + it.group_id + ")";
    else if (it.group_name) groupTxt = String(it.group_name);
    else if (it.group_id) groupTxt = String(it.group_id);
    else if (it.channel_id) groupTxt = String(it.channel_id);
    div.innerHTML =
      '<div class="mem-head memory-head">' +
        '<span class="chip">#' + escapeHtml(it.id == null ? "?" : it.id) + "</span>" +
        (it.actor_id ? '<span class="chip chip-muted">用户 ' + escapeHtml(it.actor_id) + "</span>" : "") +
        (it.strength != null ? '<span class="chip chip-muted">强度 ' + Number(it.strength).toFixed(2) + "</span>" : "") +
        (groupTxt ? '<span class="chip chip-muted">群 ' + escapeHtml(groupTxt) + "</span>" : "") +
        (it.persona_id ? '<span class="chip chip-muted">人格 ' + escapeHtml(it.persona_id) + "</span>" : "") +
        '<div class="row-actions">' +
          '<button type="button" class="btn btn-ghost btn-sm mem-toggle">展开 ▾</button>' +
          '<button type="button" class="btn btn-danger btn-sm mem-del">软删除</button>' +
        "</div>" +
      "</div>" +
      '<div class="mem-summary">' +
        escapeHtml(it.summary || "（无摘要）") +
      "</div>" +
      '<div class="mem-detail-body" style="display:none;"></div>';

    var head = div.querySelector(".mem-head");
    head.addEventListener("click", function (ev) {
      if (ev.target.closest(".row-actions")) return;
      _toggleMemoryRow(div, it.id);
    });
    div.querySelector(".mem-toggle").addEventListener("click", function (ev) {
      ev.stopPropagation();
      _toggleMemoryRow(div, it.id);
    });
    div.querySelector(".mem-del").addEventListener("click", function (ev) {
      ev.stopPropagation();
      _deleteMemoryRow(div, it.id, false);
    });
    return div;
  }

  function _toggleMemoryRow(rowDiv, eid) {
    var body = rowDiv.querySelector(".mem-detail-body");
    var btn = rowDiv.querySelector(".mem-toggle");
    if (!body || !btn) return;
    if (body.style.display !== "none") {
      body.style.display = "none";
      btn.textContent = "展开 ▾";
      return;
    }
    body.style.display = "block";
    btn.textContent = "收起 ▴";
    if (_memDetailCache[eid]) {
      body.innerHTML = _memDetailCache[eid];
      _wireMemoryFoldButtons(rowDiv, eid);
      return;
    }
    body.innerHTML = '<div class="section-title">记忆详情 #' + escapeHtml(eid) + "</div>" + emptyBox("加载详情…");
    _renderMemoryDetailInto(eid, body, rowDiv, "view");
  }

  async function _renderMemoryDetailInto(eid, body, rowDiv, mode) {
    try {
      var d = unwrap(await apiGet("page/memories/detail", { eid: eid }));
      var modeBody = (mode === "edit")
        ? editForm(d)
        : "";
      var html = '<div class="section-title">记忆详情 #' + escapeHtml(eid) + "</div>" +
        '<div class="mem-mode-actions">' +
          '<button type="button" class="btn btn-ghost btn-sm mem-mode-view"' +
            (mode === "view" ? ' style="display:none;"' : "") + '>查看</button>' +
          '<button type="button" class="btn btn-ghost btn-sm mem-mode-edit"' +
            (mode === "edit" ? ' style="display:none;"' : "") + '>编辑</button>' +
        "</div>" +
        '<div class="mem-mode-body">' + modeBody + "</div>";
      _memDetailCache[eid] = html;
      body.innerHTML = html;
      _wireMemoryFoldButtons(rowDiv, eid);
    } catch (e) {
      body.innerHTML = errBox(e.message);
    }
  }

  function _wireMemoryFoldButtons(rowDiv, eid) {
    var body = rowDiv.querySelector(".mem-detail-body");
    var viewBtn = body.querySelector(".mem-mode-view");
    var editBtn = body.querySelector(".mem-mode-edit");
    if (viewBtn) viewBtn.addEventListener("click", function () {
      delete _memDetailCache[eid];
      _renderMemoryDetailInto(eid, body, rowDiv, "view");
    });
    if (editBtn) editBtn.addEventListener("click", function () {
      delete _memDetailCache[eid];
      _renderMemoryDetailInto(eid, body, rowDiv, "edit");
    });
    var saveBtn = body.querySelector("#ed-save");
    if (saveBtn) saveBtn.addEventListener("click", function () {
      _saveMemoryEdit(eid, body, rowDiv);
    });
    var delBtn = body.querySelector("#ed-del");
    if (delBtn) delBtn.addEventListener("click", function () {
      _deleteMemoryRow(rowDiv, eid, false);
    });
    var delHardBtn = body.querySelector("#ed-del-hard");
    if (delHardBtn) delHardBtn.addEventListener("click", function () {
      _deleteMemoryRow(rowDiv, eid, true);
    });
    ["ed-importance", "ed-strength"].forEach(function (sid) {
      var sl = body.querySelector("#" + sid);
      var lab = body.querySelector("#" + sid + "-val");
      if (sl && lab) {
        sl.addEventListener("input", function () {
          lab.textContent = Number(sl.value).toFixed(2);
        });
      }
    });
  }

  async function _saveMemoryEdit(eid, body, rowDiv) {
    function _val(id) {
      var el = body.querySelector("#" + id);
      return el ? el.value : undefined;
    }
    var fields = {};
    var summary = _val("ed-summary"); if (summary != null) fields.summary = summary;
    var content = _val("ed-content"); if (content != null) fields.content = content;
    var memtype = _val("ed-memtype"); if (memtype != null) fields.memory_type = memtype;
    var importance = _val("ed-importance"); if (importance != null) fields.importance = importance;
    var strength = _val("ed-strength"); if (strength != null) fields.strength = strength;
    var topics = _val("ed-topics"); if (topics != null) fields.topics = topics;
    var tags = _val("ed-tags"); if (tags != null) fields.tags = tags;
    var persona = _val("ed-persona"); if (persona != null) fields.persona_id = persona;
    var tier = _val("ed-tier"); if (tier != null) fields.tier = tier;
    var msg = body.querySelector("#ed-msg");
    try {
      var r = unwrap(await apiPost("page/memories/update",
                                  { eid: eid, fields: fields }));
      var ch = (r && r.changed) || [];
      var re = r && r.reembedded;
      if (msg) {
        msg.textContent = ch.length
          ? ("已保存：" + ch.join("、") + (re ? "（已重算向量）" : ""))
          : "无变更";
        msg.className = "edit-msg ok";
      }
      delete _memDetailCache[eid];
      await loadMemories();
    } catch (e) {
      if (msg) { msg.textContent = "保存失败：" + e.message; msg.className = "edit-msg err"; }
    }
  }

  async function _deleteMemoryRow(rowDiv, eid, hard) {
    var label = hard ? "永久删除" : "软删除";
    if (!confirm(label + "记忆 #" + eid + "？"
                + (hard ? "此操作不可恢复。" : "软删除可被遗忘机制清理。")) return;
    try {
      unwrap(await apiPost("page/memories/delete", { eid: eid, hard: !!hard }));
      delete _memDetailCache[eid];
      if (rowDiv && rowDiv.parentNode) rowDiv.parentNode.removeChild(rowDiv);
      var wrap = document.getElementById("mem-rows");
      if (wrap && !wrap.children.length) wrap.innerHTML = emptyBox("暂无记忆");
    } catch (e) {
      alert(label + "失败：" + e.message);
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

  function editForm(d) {
    function field(id, label, value, type) {
      var v = value == null ? "" : String(value);
      var input = (type === "textarea")
        ? '<textarea id="' + id + '" class="edit-input" rows="4">' + escapeHtml(v) + "</textarea>"
        : '<input id="' + id + '" class="edit-input" value="' + escapeHtml(v) + '" />';
      return '<div class="edit-row"><label class="edit-k">' + escapeHtml(label) + "</label>" + input + "</div>";
    }
    function selectField(id, label, value, opts, labels) {
      var v = value == null ? "" : String(value);
      var html = opts.map(function (o) {
        return '<option value="' + o + '"' + (o === v ? " selected" : "") + ">" +
          escapeHtml((labels && labels[o]) || o || "未分类") + "</option>";
      }).join("");
      return '<div class="edit-row"><label class="edit-k">' + escapeHtml(label) + "</label>" +
        '<select id="' + id + '" class="edit-input">' + html + "</select></div>";
    }
    function sliderField(id, label, value) {
      var num = Number(value);
      if (isNaN(num)) num = 0;
      num = Math.max(0, Math.min(1, num));
      var shown = num.toFixed(2);
      return '<div class="edit-row"><label class="edit-k">' + escapeHtml(label) +
        ' <span id="' + id + '-val" class="slider-val">' + shown + "</span></label>" +
        '<input id="' + id + '" class="edit-slider" type="range" min="0" max="1" step="0.01" value="' + num + '" />' +
        "</div>";
    }
    var memType = d.memory_type == null ? "" : String(d.memory_type);
    return '<div class="edit-box">' +
      '<div class="section-title">编辑记忆</div>' +
      field("ed-summary", "摘要", d.summary, "textarea") +
      field("ed-content", "原文内容", d.content, "textarea") +
      selectField("ed-memtype", "记忆类型", memType,
        ["episodic", "semantic", "prospective", "diary"], MEMTYPE_LABELS) +
      sliderField("ed-importance", "重要度", d.importance) +
      sliderField("ed-strength", "记忆强度", d.strength) +
      field("ed-topics", "话题 (逗号分隔)", (d.topics || []).join("、"), "text") +
      field("ed-tags", "标签 (逗号分隔)", (d.tags || []).join("、"), "text") +
      field("ed-persona", "关联人格", d.persona_id, "text") +
      selectField("ed-tier", "记忆分层", d.tier,
        ["", "hot", "warm", "cold"], TIER_LABELS) +
      '<div class="edit-actions">' +
        '<button id="ed-save" class="btn btn-sm">保存修改</button>' +
        '<button id="ed-del" class="btn btn-sm btn-danger">软删除</button>' +
        '<button id="ed-del-hard" class="btn btn-sm btn-danger">永久删除</button>' +
        '<span id="ed-msg" class="edit-msg"></span></div>' +
      '<div class="edit-hint">修改“原文内容”会重新计算向量。软删除可被遗忘机制清理，永久删除不可恢复。</div>' +
      "</div>";
  }

  async function saveEdit(eid) {
    // v1.50: legacy saveEdit - delegate to the inline-row path when
    // the row exists in the new list, otherwise fall back to the
    // legacy bottom-panel selectors.
    var row = document.querySelector(
      '.memory-item[data-eid="' + cssEscape(eid) + '"]');
    if (row) {
      var body = row.querySelector(".mem-detail-body");
      if (body) return _saveMemoryEdit(eid, body, row);
    }
    var msg = document.getElementById("ed-msg");
    if (msg) { msg.textContent = "保存中…"; msg.className = "edit-msg"; }
    var fields = {
      summary: document.getElementById("ed-summary").value,
      content: document.getElementById("ed-content").value,
      memory_type: document.getElementById("ed-memtype").value,
      importance: document.getElementById("ed-importance").value,
      strength: document.getElementById("ed-strength").value,
      topics: document.getElementById("ed-topics").value,
      tags: document.getElementById("ed-tags").value,
      tier: document.getElementById("ed-tier").value,
      persona_id: document.getElementById("ed-persona").value
    };
    try {
      var r = unwrap(await apiPost("page/memories/update", { eid: eid, fields: fields }));
      var ch = (r && r.changed) || [];
      var re = r && r.reembedded;
      if (msg) {
        msg.textContent = ch.length
          ? ("已保存：" + ch.join("、") + (re ? "（已重算向量）" : ""))
          : "无变更";
        msg.className = "edit-msg ok";
      }
      await loadMemories();
      await showDetail(eid);
    } catch (e) {
      if (msg) { msg.textContent = "保存失败：" + e.message; msg.className = "edit-msg err"; }
    }
  }

  // v1.50: legacy entry point kept for back-compat with any caller
  // that still calls showDetail(eid). Routes to the new inline fold
  // when a row is found; otherwise falls back to the (now empty)
  // bottom panel.
  async function showDetail(eid) {
    var row = document.querySelector(
      '.memory-item[data-eid="' + cssEscape(eid) + '"]');
    if (row) { _toggleMemoryRow(row, eid); return; }
    var box = document.getElementById("mem-detail");
    if (!box) return;
    box.innerHTML = '<div class="section-title">记忆详情 #' + escapeHtml(eid) + "</div>" + emptyBox("加载详情…");
    try {
      var d = unwrap(await apiGet("page/memories/detail", { eid: eid }));
      box.innerHTML = '<div class="section-title">记忆详情 #' + escapeHtml(eid) + "</div>" +
        '<div class="mem-mode-body">' + kvRows(d) + "</div>";
    } catch (e) {
      box.innerHTML = errBox(e.message);
    }
  }

  var _delArm = { eid: null, hard: null, timer: null };
  function _disarmDelete() {
    if (_delArm.timer) { clearTimeout(_delArm.timer); _delArm.timer = null; }
    _delArm.eid = null; _delArm.hard = null;
    var b1 = document.getElementById("ed-del");
    var b2 = document.getElementById("ed-del-hard");
    if (b1) { b1.textContent = "软删除"; b1.classList.remove("btn-armed"); }
    if (b2) { b2.textContent = "永久删除"; b2.classList.remove("btn-armed"); }
  }

  async function deleteMem(eid, hard) {
    // v1.50: legacy deleteMem - delegate to the per-row path when the
    // row exists in the new list, otherwise fall back to the legacy
    // two-step arm/disarm flow on the bottom panel.
    var row = document.querySelector(
      '.memory-item[data-eid="' + cssEscape(eid) + '"]');
    if (row) return _deleteMemoryRow(row, eid, hard);
    var msg = document.getElementById("ed-msg");
    var label = hard ? "永久删除" : "软删除";
    var btn = document.getElementById(hard ? "ed-del-hard" : "ed-del");
    if (!(_delArm.eid === eid && _delArm.hard === hard)) {
      _disarmDelete();
      _delArm.eid = eid; _delArm.hard = hard;
      if (btn) { btn.textContent = "再次点击确认" + label; btn.classList.add("btn-armed"); }
      if (msg) {
        msg.textContent = hard ? "永久删除不可恢复，确认请再点一次。" : "软删除可被遗忘机制清理，确认请再点一次。";
        msg.className = "edit-msg";
      }
      _delArm.timer = setTimeout(_disarmDelete, 4000);
      return;
    }
    _disarmDelete();
    if (msg) { msg.textContent = label + "中…"; msg.className = "edit-msg"; }
    try {
      unwrap(await apiPost("page/memories/delete", { eid: eid, hard: !!hard }));
      var el = document.getElementById("mem-detail");
      if (el) { el.innerHTML = emptyBox("已" + label + "记忆 #" + eid); }
      await loadMemories();
    } catch (e) {
      if (msg) { msg.textContent = label + "失败：" + e.message; msg.className = "edit-msg err"; }
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

  // ---------- 关系图谱（canvas 力导向粒子网，EngramGraph2D） ----------
  var GRAPH_TYPES = (window.EngramGraph2D && window.EngramGraph2D.TYPE_LABEL) ||
    { person: "人物", place: "地点", object: "事物", concept: "概念", unknown: "其它" };
  var GRAPH_COLORS = (window.EngramGraph2D && window.EngramGraph2D.TYPE_COLORS) ||
    { person: "#2f9e8b", place: "#4c6ef5", object: "#c99a16", concept: "#7c6fca", unknown: "#8b949e" };
  var graphReady = false;

  function colorOf(type) { return GRAPH_COLORS[type] || GRAPH_COLORS.unknown; }

  function ensureGraphCanvas(stage) {
    var canvas = document.getElementById("graph-canvas");
    if (!canvas) {
      stage.innerHTML = "";
      canvas = document.createElement("canvas");
      canvas.id = "graph-canvas";
      canvas.className = "graph-canvas";
      stage.appendChild(canvas);
      graphReady = false;
    }
    return canvas;
  }

  async function loadGraph() {
    var stage = document.getElementById("graph-stage");
    var tip = document.getElementById("graph-tip");
    var legend = document.getElementById("graph-legend");
    document.getElementById("graph-detail").innerHTML = "";
    if (!window.EngramGraph2D) {
      stage.innerHTML = errBox("图谱渲染模块未加载");
      return;
    }
    stage.innerHTML = emptyBox("加载中…");
    try {
      var d = unwrap(await apiGet("page/graph/data", { limit: 300 }));
      var nodes = (d && d.nodes) || [];
      var edges = (d && d.edges) || [];
      if (!nodes.length) {
        stage.innerHTML = emptyBox("暂无实体，先让 Bot 多聊一些再回来看");
        legend.innerHTML = ""; tip.textContent = "";
        graphReady = false;
        return;
      }
      // legend
      var types = {};
      nodes.forEach(function (n) { types[n.type || "unknown"] = true; });
      legend.innerHTML = Object.keys(types).map(function (ty) {
        return '<span class="lg"><span class="dot" style="background:' + colorOf(ty) + '"></span>' +
          escapeHtml(GRAPH_TYPES[ty] || ty) + "</span>";
      }).join("");
      tip.textContent = "实体 " + nodes.length + " · 关系 " + edges.length +
        (d.truncated ? "（已截断）" : "") + " · 拖动节点 / 滚轮缩放 / 点击查看关系";

      var canvas = ensureGraphCanvas(stage);
      if (!graphReady) {
        window.EngramGraph2D.init(canvas, {
          isDark: document.documentElement.getAttribute("data-theme") === "dark",
          onNode: function (node) { showEntityDetail(node.name || node.id); },
          onBackground: function () { document.getElementById("graph-detail").innerHTML = ""; }
        });
        graphReady = true;
      }
      window.EngramGraph2D.setData(nodes, edges);
    } catch (e) {
      stage.innerHTML = errBox(e.message);
      graphReady = false;
    }
  }

  function _entId(name) { return "ent_" + String(name).replace(/[^a-zA-Z0-9_]/g, "_"); }

  async function showEntityDetail(name) {
    var el = document.getElementById("graph-detail");
    el.innerHTML = '<div class="section-title">\u5b9e\u4f53\u5173\u7cfb \u00b7 ' + escapeHtml(name) + "</div>" + emptyBox("\u52a0\u8f7d\u4e2d\u2026");
    try {
      var d = unwrap(await apiPost("page/graph/query", { name: name }));
      var ent = (d && d.entity) || {};
      var rels = (d && d.relations) || [];
      var refs = (d && d.engram_refs) || [];
      var eid = ent.id || "";
      var html = '<div class="section-title">\u5b9e\u4f53\u5173\u7cfb \u00b7 ' + escapeHtml(ent.name || name) + "</div>";
      html += kvRows({ name: ent.name, type: ent.type });
      html += '<div class="edit-actions">' +
        '<button id="ent-del-hard" class="btn btn-sm btn-danger" data-eid="' + escapeHtml(eid) + '">\u6c38\u4e45\u5220\u9664\u5b9e\u4f53</button>' +
        '<span id="ent-msg" class="edit-msg"></span></div>';
      if (rels.length) {
        html += '<div class="section-title">\u5173\u7cfb\uff08' + rels.length + "\uff09</div>";
        rels.forEach(function (r) {
          var rid = r.id || "";
          var cv = Number(r.confidence);
          if (isNaN(cv)) cv = 0; cv = Math.max(0, Math.min(1, cv));
          var sid = "rel-conf-" + rid;
          html += '<div class="result" data-rid="' + escapeHtml(rid) + '">' +
            '<div class="result-text">' +
            escapeHtml(r.src) + ' <span class="chip">' + escapeHtml(r.predicate || "\u5173\u8054") + "</span> " +
            escapeHtml(r.dst) + "</div>" +
            '<div class="edit-row"><label class="edit-k">\u7f6e\u4fe1\u5ea6 ' +
            '<span id="' + sid + '-val" class="slider-val">' + cv.toFixed(2) + "</span></label>" +
            '<input id="' + sid + '" class="edit-slider rel-slider" type="range" min="0" max="1" step="0.01" value="' + cv + '" data-rid="' + escapeHtml(rid) + '" /></div>' +
            '<div class="edit-actions">' +
            '<button class="btn btn-sm rel-save" data-rid="' + escapeHtml(rid) + '">\u4fdd\u5b58\u7f6e\u4fe1\u5ea6</button>' +
            '<button class="btn btn-sm btn-danger rel-del" data-rid="' + escapeHtml(rid) + '">\u5220\u9664\u5173\u7cfb</button></div>' +
            "</div>";
        });
      } else {
        html += emptyBox("\u8be5\u5b9e\u4f53\u6682\u65e0\u5173\u7cfb\u8bb0\u5f55");
      }
      if (refs.length) {
        html += '<div class="section-title">\u5173\u8054\u8bb0\u5fc6\uff08' + refs.length + "\uff09</div>";
        refs.forEach(function (m) {
          html += '<div class="mem-item"><div class="mem-head"><span class="chip">#' +
            escapeHtml(m.id) + '</span></div><div class="mem-summary">' +
            escapeHtml(m.summary || "\uff08\u65e0\u6458\u8981\uff09") + "</div></div>";
        });
      }
      el.innerHTML = html;
      var entDel = document.getElementById("ent-del-hard");
      if (entDel) { entDel.addEventListener("click", function () { deleteEntity(eid, ent.name || name); }); }
      Array.prototype.forEach.call(el.querySelectorAll(".rel-slider"), function (sl) {
        var lab = document.getElementById(sl.id + "-val");
        if (lab) { sl.addEventListener("input", function () { lab.textContent = Number(sl.value).toFixed(2); }); }
      });
      Array.prototype.forEach.call(el.querySelectorAll(".rel-save"), function (b) {
        b.addEventListener("click", function () {
          var rid = b.getAttribute("data-rid");
          var sl = document.getElementById("rel-conf-" + rid);
          saveRelationConfidence(rid, sl ? sl.value : 0, ent.name || name);
        });
      });
      Array.prototype.forEach.call(el.querySelectorAll(".rel-del"), function (b) {
        b.addEventListener("click", function () { deleteRelation(b, b.getAttribute("data-rid"), ent.name || name); });
      });
    } catch (e) {
      el.innerHTML = errBox(e.message);
    }
  }

  var _entDelArm = { eid: null, timer: null };
  function _disarmEntDelete() {
    if (_entDelArm.timer) { clearTimeout(_entDelArm.timer); _entDelArm.timer = null; }
    _entDelArm.eid = null;
    var b = document.getElementById("ent-del-hard");
    if (b) { b.textContent = "\u6c38\u4e45\u5220\u9664\u5b9e\u4f53"; b.classList.remove("btn-armed"); }
  }

  async function deleteEntity(eid, name) {
    var msg = document.getElementById("ent-msg");
    var btn = document.getElementById("ent-del-hard");
    if (!eid) { if (msg) { msg.textContent = "\u7f3a\u5c11\u5b9e\u4f53 ID"; msg.className = "edit-msg err"; } return; }
    if (_entDelArm.eid !== eid) {
      _disarmEntDelete();
      _entDelArm.eid = eid;
      if (btn) { btn.textContent = "\u518d\u6b21\u70b9\u51fb\u786e\u8ba4\u6c38\u4e45\u5220\u9664"; btn.classList.add("btn-armed"); }
      if (msg) { msg.textContent = "\u6c38\u4e45\u5220\u9664\u5b9e\u4f53\u53ca\u5176\u5168\u90e8\u5173\u7cfb\uff0c\u4e0d\u53ef\u6062\u590d\uff0c\u786e\u8ba4\u8bf7\u518d\u70b9\u4e00\u6b21\u3002"; msg.className = "edit-msg"; }
      _entDelArm.timer = setTimeout(_disarmEntDelete, 4000);
      return;
    }
    _disarmEntDelete();
    if (msg) { msg.textContent = "\u5220\u9664\u4e2d\u2026"; msg.className = "edit-msg"; }
    try {
      var r = unwrap(await apiPost("page/graph/entity/delete", { eid: eid }));
      document.getElementById("graph-detail").innerHTML = emptyBox("\u5df2\u6c38\u4e45\u5220\u9664\u5b9e\u4f53 " + (name || eid) + "\uff08\u540c\u65f6\u79fb\u9664 " + ((r && r.relations_removed) || 0) + " \u6761\u5173\u7cfb\uff09");
      await loadGraph();
    } catch (e) {
      if (msg) { msg.textContent = "\u5220\u9664\u5931\u8d25\uff1a" + e.message; msg.className = "edit-msg err"; }
    }
  }

  async function saveRelationConfidence(rid, value, entName) {
    var msg = document.getElementById("ent-msg");
    if (!rid) return;
    try {
      var r = unwrap(await apiPost("page/graph/relation/update", { rid: rid, confidence: Number(value) }));
      if (msg) { msg.textContent = "\u5df2\u4fdd\u5b58\u7f6e\u4fe1\u5ea6 " + Number((r && r.confidence) || value).toFixed(2); msg.className = "edit-msg ok"; }
    } catch (e) {
      if (msg) { msg.textContent = "\u4fdd\u5b58\u5931\u8d25\uff1a" + e.message; msg.className = "edit-msg err"; }
    }
  }

  var _relDelArm = { rid: null, timer: null };
  async function deleteRelation(btn, rid, entName) {
    var msg = document.getElementById("ent-msg");
    if (!rid) return;
    if (_relDelArm.rid !== rid) {
      if (_relDelArm.timer) clearTimeout(_relDelArm.timer);
      _relDelArm.rid = rid;
      if (btn) { btn.textContent = "\u518d\u6b21\u786e\u8ba4"; btn.classList.add("btn-armed"); }
      if (msg) { msg.textContent = "\u5220\u9664\u5173\u7cfb\u4e0d\u53ef\u6062\u590d\uff0c\u786e\u8ba4\u8bf7\u518d\u70b9\u4e00\u6b21\u3002"; msg.className = "edit-msg"; }
      _relDelArm.timer = setTimeout(function () {
        _relDelArm.rid = null;
        if (btn) { btn.textContent = "\u5220\u9664\u5173\u7cfb"; btn.classList.remove("btn-armed"); }
      }, 4000);
      return;
    }
    if (_relDelArm.timer) { clearTimeout(_relDelArm.timer); _relDelArm.timer = null; }
    _relDelArm.rid = null;
    try {
      unwrap(await apiPost("page/graph/relation/delete", { rid: rid }));
      if (entName) { await showEntityDetail(entName); }
      if (msg) { msg.textContent = "\u5df2\u5220\u9664\u5173\u7cfb"; msg.className = "edit-msg ok"; }
    } catch (e) {
      if (msg) { msg.textContent = "\u5220\u9664\u5931\u8d25\uff1a" + e.message; msg.className = "edit-msg err"; }
    }
  }

  // ---------- wire ----------
  // ---------- diary ----------
  var _diaryState = { offset: 0, k: 50, total: 0, optionsLoaded: false };

  function _diaryFilters() {
    return {
      channel_id: document.getElementById("diary-channel").value || "",
      persona_id: document.getElementById("diary-persona").value || "",
      day: document.getElementById("diary-day").value || "",
      q: document.getElementById("diary-search").value.trim()
    };
  }

  async function loadDiaryOptions() {
    try {
      var d = unwrap(await apiGet("page/diaries/options", {}));
      var chSel = document.getElementById("diary-channel");
      var pSel = document.getElementById("diary-persona");
      var daySel = document.getElementById("diary-day");
      var chVal = chSel.value, pVal = pSel.value, dayVal = daySel.value;
      chSel.innerHTML = '<option value="">\u5168\u90e8\u4f1a\u8bdd</option>';
      ((d && d.channels) || []).forEach(function (c) {
        var o = document.createElement("option");
        o.value = c.channel_id;
        o.textContent = c.label || c.channel_id;
        chSel.appendChild(o);
      });
      pSel.innerHTML = '<option value="">\u5168\u90e8\u4eba\u683c</option>';
      ((d && d.personas) || []).forEach(function (p) {
        var o = document.createElement("option");
        if (p === "") { o.value = "__none__"; o.textContent = "\uff08\u65e0\u4eba\u683c\uff09"; }
        else { o.value = p; o.textContent = p; }
        pSel.appendChild(o);
      });
      daySel.innerHTML = '<option value="">\u5168\u90e8\u65e5\u671f</option>';
      ((d && d.days) || []).forEach(function (dy) {
        var o = document.createElement("option");
        o.value = dy; o.textContent = dy;
        daySel.appendChild(o);
      });
      chSel.value = chVal; pSel.value = pVal; daySel.value = dayVal;
      _diaryState.optionsLoaded = true;
    } catch (e) { /* options are best-effort */ }
  }

  function renderDiaryPager() {
    var pager = document.getElementById("diary-pager");
    if (!pager) return;
    var st = _diaryState;
    var start = st.total ? st.offset + 1 : 0;
    var end = Math.min(st.offset + st.k, st.total);
    var hasPrev = st.offset > 0;
    var hasNext = st.offset + st.k < st.total;
    pager.innerHTML =
      '<button class="btn btn-ghost btn-sm" id="diary-prev"' + (hasPrev ? "" : " disabled") + ">\u4e0a\u4e00\u9875</button>" +
      '<span class="pager-info">' + start + "\u2013" + end + " / \u5171 " + st.total + " \u7bc7</span>" +
      '<button class="btn btn-ghost btn-sm" id="diary-next"' + (hasNext ? "" : " disabled") + ">\u4e0b\u4e00\u9875</button>";
    var prev = document.getElementById("diary-prev");
    var next = document.getElementById("diary-next");
    if (prev && hasPrev) prev.addEventListener("click", function () {
      _diaryState.offset = Math.max(0, _diaryState.offset - _diaryState.k); loadDiaries(true);
    });
    if (next && hasNext) next.addEventListener("click", function () {
      _diaryState.offset = _diaryState.offset + _diaryState.k; loadDiaries(true);
    });
  }

  // Per-row cache of the rendered detail HTML so re-toggling is instant
  // and we don't re-hit /diaries/detail every time.
  var _diaryDetailCache = {};

  async function loadDiaries(keepOffset) {
    if (!_diaryState.optionsLoaded) { await loadDiaryOptions(); }
    if (!keepOffset) _diaryState.offset = 0;
    _diaryDetailCache = {};  // page changed, drop the cache
    var f = _diaryFilters();
    var wrap = document.getElementById("diary-rows");
    // v1.46: detail is now inline-folded under each row, so the legacy
    // bottom panel stays empty (kept in the DOM for layout stability).
    var legacy = document.getElementById("diary-detail");
    if (legacy) legacy.innerHTML = "";
    wrap.innerHTML = emptyBox("\u52a0\u8f7d\u4e2d\u2026");
    try {
      var d = unwrap(await apiGet("page/diaries", {
        channel_id: f.channel_id, persona_id: f.persona_id, day: f.day,
        q: f.q, k: _diaryState.k, offset: _diaryState.offset
      }));
      _diaryState.total = (d && d.total) || 0;
      var items = (d && d.items) || [];
      if (!items.length) { wrap.innerHTML = emptyBox("\u6682\u65e0\u65e5\u8bb0"); renderDiaryPager(); return; }
      wrap.innerHTML = "";
      items.forEach(function (it) {
        wrap.appendChild(_buildDiaryRow(it));
      });
      renderDiaryPager();
    } catch (e) {
      wrap.innerHTML = errBox(e.message);
    }
  }

  function _buildDiaryRow(it) {
    var div = document.createElement("div");
    div.className = "mem-item diary-item";
    div.setAttribute("data-eid", it.id || "");
    var ctype = it.chat_type === "group" ? "\u7fa4\u804a" : (it.chat_type === "private" ? "\u79c1\u804a" : "");
    div.innerHTML =
      '<div class="mem-head diary-head">' +
        (it.day ? '<span class="chip">' + escapeHtml(it.day) + "</span>" : "") +
        (ctype ? '<span class="chip chip-muted">' + ctype + "</span>" : "") +
        (it.channel_label ? '<span class="chip chip-muted">' + escapeHtml(it.channel_label) + "</span>" : "") +
        (it.persona_id ? '<span class="chip chip-muted">\u4eba\u683c ' + escapeHtml(it.persona_id) + "</span>" : "") +
        '<div class="diary-actions">' +
          '<button type="button" class="btn btn-ghost btn-sm diary-toggle">' +
            "\u5c55\u5f00 \u25be" +
          "</button>" +
          '<button type="button" class="btn btn-danger btn-sm diary-delete" title="\u8f6f\u5220\u9664\uff08\u9ed8\u8ba4\uff09">' +
            "\u5220\u9664" +
          "</button>" +
        "</div>" +
      "</div>" +
      '<div class="mem-summary">' +
        escapeHtml(it.summary || "\uff08\u65e0\u6458\u8981\uff09") +
      "</div>" +
      '<div class="diary-detail-body" style="display:none;"></div>';
    // Click anywhere on the head (but not on the action buttons)
    // toggles the inline fold.
    var head = div.querySelector(".mem-head");
    head.addEventListener("click", function (ev) {
      if (ev.target.closest(".diary-actions")) return;  // ignore btn clicks
      _toggleDiaryRow(div, it.id);
    });
    // Delete button: confirm + soft delete by default (no accidental loss).
    var delBtn = div.querySelector(".diary-delete");
    delBtn.addEventListener("click", function (ev) {
      ev.stopPropagation();
      _deleteDiaryRow(div, it.id, false);
    });
    return div;
  }

  function _toggleDiaryRow(rowDiv, eid) {
    var body = rowDiv.querySelector(".diary-detail-body");
    var btn = rowDiv.querySelector(".diary-toggle");
    if (!body || !btn) return;
    if (body.style.display !== "none") {
      // fold
      body.style.display = "none";
      btn.textContent = "\u5c55\u5f00 \u25be";
      return;
    }
    // expand
    body.style.display = "block";
    btn.textContent = "\u6536\u8d77 \u25b4";
    if (_diaryDetailCache[eid]) {
      body.innerHTML = _diaryDetailCache[eid];
      return;
    }
    body.innerHTML = emptyBox("\u52a0\u8f7d\u4e2d\u2026");
    _renderDiaryDetailInto(eid, body);
  }

  async function _renderDiaryDetailInto(eid, body) {
    try {
      var d = unwrap(await apiGet("page/diaries/detail", { eid: eid }));
      var meta = {};
      meta["\u65e5\u671f"] = d.day || "\u2014";
      meta["\u7c7b\u578b"] = d.chat_type === "group" ? "\u7fa4\u804a" : (d.chat_type === "private" ? "\u79c1\u804a" : "\u2014");
      meta["\u4f1a\u8bdd"] = d.channel_label || d.channel_id || "\u2014";
      meta["\u4eba\u683c"] = d.persona_id || "\uff08\u65e0\uff09";
      if (d.participants && d.participants.length) meta["\u53c2\u4e0e\u8005"] = d.participants.join("\u3001");
      if (d.topics && d.topics.length) meta["\u4e3b\u9898"] = d.topics.join("\u3001");
      if (d.created_at != null) meta["\u5199\u5165\u65f6\u95f4"] = fmtTime(d.created_at);
      var html = '<div class="detail">' +
        '<div class="section-title">\u65e5\u8bb0\u8be6\u60c5 #' + escapeHtml(d.id) + "</div>" +
        kvRows(meta) +
        '<div class="raw">' + escapeHtml(d.content || d.summary_full || "") + "</div>" +
        "</div>";
      _diaryDetailCache[eid] = html;
      body.innerHTML = html;
    } catch (e) {
      body.innerHTML = errBox(e.message);
    }
  }

  async function _deleteDiaryRow(rowDiv, eid, hard) {
    if (hard) {
      if (!confirm("\u786c\u5220\u9664\u65e5\u8bb0 #" + eid + "\uff1f\u6b64\u64cd\u4f5c\u4e0d\u53ef\u9006\u3002")) return;
    } else {
      if (!confirm("\u8f6f\u5220\u9664\u65e5\u8bb0 #" + eid + "\uff1f\u4f1a\u9690\u85cf\u4f46\u4fdd\u7559\u5728\u5e93\u91cc\uff0c\u53ef\u4ee5 /mem forget \u6062\u590d\u3002")) return;
    }
    try {
      unwrap(await apiPost("page/diaries/delete", { eid: eid, hard: !!hard }));
      delete _diaryDetailCache[eid];
      rowDiv.parentNode.removeChild(rowDiv);
      _diaryState.total = Math.max(0, _diaryState.total - 1);
      // If the page is now empty, show the empty-state again.
      var wrap = document.getElementById("diary-rows");
      if (wrap && !wrap.children.length) {
        wrap.innerHTML = emptyBox("\u6682\u65e0\u65e5\u8bb0");
      }
      renderDiaryPager();
    } catch (e) {
      alert("\u5220\u9664\u5931\u8d25\uff1a" + e.message);
    }
  }

  // Legacy export kept so any old callers don't 404; the new
  // path uses inline folds via _toggleDiaryRow above.
  async function showDiaryDetail(eid) {
    var body = document.querySelector(
      '.diary-item[data-eid="' + cssEscape(eid) + '"] .diary-detail-body');
    if (body) { _toggleDiaryRow(body.parentNode, eid); return; }
    var box = document.getElementById("diary-detail");
    if (box) await _renderDiaryDetailInto(eid, box);
  }

  function cssEscape(s) {
    if (window.CSS && CSS.escape) return CSS.escape(s);
    return String(s).replace(/[^a-zA-Z0-9_-]/g, function (c) {
      return "\\" + c.charCodeAt(0).toString(16) + " ";
    });
  }

  document.getElementById("btn-refresh-stats").addEventListener("click", loadStats);
  document.getElementById("btn-load-mem").addEventListener("click", loadMemories);
  document.getElementById("btn-recall").addEventListener("click", runRecall);
  document.getElementById("btn-load-backups").addEventListener("click", loadBackups);
  document.getElementById("btn-load-graph").addEventListener("click", loadGraph);
  document.getElementById("btn-load-diary").addEventListener("click", function () { loadDiaries(false); });
  ["diary-channel", "diary-persona", "diary-day"].forEach(function (id) {
    document.getElementById(id).addEventListener("change", function () { loadDiaries(false); });
  });
  document.getElementById("diary-search").addEventListener("keydown", function (e) {
    if (e.key === "Enter") loadDiaries(false);
  });
  document.getElementById("rc-query").addEventListener("keydown", function (e) {
    if (e.key === "Enter") runRecall();
  });

  // v1.51: race b.ready() against a 3s timeout so a stuck bridge
  // can't block loadHealth/loadStats. Surface the ready-result
  // (including timeout / error) inline so the operator can see
  // exactly where init is hanging without DevTools.
  function _withTimeout(promise, ms, label) {
    var t = new Promise(function (resolve) { setTimeout(function () {
      resolve({__timeout__: true, label: label});
    }, ms); });
    return Promise.race([promise, t]);
  }
  async function init() {
    var el = document.getElementById("health");
    var b = await waitForBridge(8000);
    if (!b) {
      if (el) { el.textContent = "未连接 (桥未注入)"; el.className = "status err"; }
      return;
    }
    if (b.ready) {
      var r = await _withTimeout(Promise.resolve().then(function () {
        return b.ready();
      }), 3000, "bridge.ready");
      if (r && r.__timeout__) {
        // bridge.ready hung - still try to load health/stat.
        if (el) el.textContent = "桥 ready 超时…"; el.className = "status";
        console.warn("[engram] bridge.ready() did not resolve within 3s, continuing without theme");
      } else if (r && typeof r.isDark === "boolean") {
        applyTheme(r.isDark);
      }
    }
    try {
      await loadHealth();
      await loadStats();
    } catch (e) {
      console.error("[engram] init post-bridge failure:", e);
    }
  }
  init();
})();
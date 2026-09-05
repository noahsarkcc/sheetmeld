const API = "";

const state = {
  config: null,
  mode: "local",       // "local" | "revision" | "browse" | "overview" | "merge"
  files: [],
  modifiedFiles: [],
  conflictedFiles: [],               // 来自 /api/svn/conflicted 的相对路径
  selectedFile: null,
  filterText: "",
  showOnlyModified: false,
  diff: null,
  activeSheet: null,
  loading: false,
  diffView: (typeof localStorage !== "undefined" && localStorage.getItem("smartdiff_diff_view")) || "inline",  // "inline" | "split"
  // revision mode
  revLog: [],
  revOld: null,
  revNew: null,
  // overview mode
  overviewLog: [],
  overviewFiles: null,
  overviewFilter: "all",  // "all" | "data-only"
  overviewExpanded: {},
  modifiedClassify: {},
  // merge mode
  mergeData: null,
  mergeTheirsRev: "HEAD",
  mergeFromSvnConflict: false,
  mergeOnlyConflicts: true,          // 工具栏过滤：只看待解决（默认勾选）
  mergeExpandMode: "smart",          // "smart"（按需）| "all" | "none"
  mergeRowExpanded: {},              // 单行手动展开覆盖：{ "sheet:rowKey": true|false }
  // 更新流程上下文（语义合并队列）
  updateContext: null,               // { skip, theirs, mine, semanticQueue:[], semanticDone:[], totalSemantic }
  svnUpdateInFlight: false,           // Directory/single-file SVN update is running
  // in-app update
  updateInfo: null,                  // /api/update/check 结果
  updateBusy: false,                 // 下载/重启流程进行中
};

let _lastMtime = 0;
let _scrollHintUpdaters = [];
let _scrollHintElements = [];
function _refreshScrollHints() { _scrollHintUpdaters.forEach(fn => fn()); }
function _cleanupScrollHints() {
  _scrollHintElements.forEach(el => el.remove());
  _scrollHintElements = [];
  _scrollHintUpdaters = [];
}

function _fixScrollableHeight() {
  const content = document.getElementById("content");
  if (!content) return;
  const targets = content.querySelectorAll(".diff-container, .overview-files");
  targets.forEach(el => {
    if (el.closest(".ov-file-detail")) return;
    el.style.maxHeight = "";
    const contentRect = content.getBoundingClientRect();
    const elRect = el.getBoundingClientRect();
    const avail = contentRect.bottom - elRect.top;
    if (avail > 50) el.style.maxHeight = avail + "px";
  });
  content.querySelectorAll(".ov-file-detail .diff-container").forEach(dc => {
    dc.style.maxHeight = "";
    const top = dc.getBoundingClientRect().top;
    const avail = window.innerHeight - top - 20;
    const h = Math.max(200, Math.min(avail, 600));
    dc.style.maxHeight = h + "px";
  });
  requestAnimationFrame(_refreshScrollHints);
}
window.addEventListener("resize", () => { _fixScrollableHeight(); });

// ── API helpers ──

async function api(url, opts) {
  const res = await fetch(API + url, opts);
  if (!res.ok) {
    const body = await res.json().catch(() => ({ error: res.statusText }));
    const err = new Error(body.error || res.statusText);
    // Expose HTTP status + parsed body so callers can distinguish e.g. 409 stale
    // from a generic 500. Existing `catch(e) { alert(e.message) }` callers are
    // unaffected because the message remains the primary readable field.
    err.status = res.status;
    err.body = body;
    throw err;
  }
  return res.json();
}

function formatSize(bytes) {
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
  return (bytes / 1024 / 1024).toFixed(1) + " MB";
}

// ── Init ──

async function init() {
  try {
    state.config = await api("/api/config");
    renderHeader();
    await loadFiles();
    if (state.config.svn_available) {
      loadModifiedFiles();
      loadModifiedClassify();
      loadConflictedFiles();
      startRemoteVersionCheck();
    }
    setTimeout(checkUpdateSilent, 5000);
  } catch (e) {
    document.querySelector(".main").innerHTML =
      `<div class="placeholder"><div class="icon">!</div><div class="text">${t('error.connection', escHtml(e.message))}</div></div>`;
  }
}

async function loadFiles() {
  try {
    const data = await api("/api/files");
    state.files = data.files || [];
    renderFileList();
  } catch (e) {
    state.files = [];
    renderFileList();
  }
}

async function loadModifiedFiles() {
  const data = await api("/api/svn/modified");
  state.modifiedFiles = data.files;
  renderFileList();
}

async function loadModifiedClassify() {
  try {
    const data = await api("/api/svn/modified-classify");
    state.modifiedClassify = data.classify || {};
    renderFileList();
  } catch (_) { /* non-critical */ }
}

async function loadConflictedFiles() {
  try {
    const data = await api("/api/svn/conflicted");
    state.conflictedFiles = data.files || [];
    renderFileList();
  } catch (_) { state.conflictedFiles = []; }
}

function isConflicted(name) {
  return state.conflictedFiles && state.conflictedFiles.indexOf(name) >= 0;
}

// ── Render: Header ──

function renderHeader() {
  const cfg = state.config;
  const badge = document.getElementById("svnBadge");
  if (cfg.svn_available) {
    badge.textContent = `SVN ${cfg.svn_version}`;
    badge.className = "svn-badge";
  } else {
    badge.textContent = t('svn.disconnected');
    badge.className = "svn-badge offline";
  }
  renderWorkspaceSelect();
}

function renderWorkspaceSelect() {
  const sel = document.getElementById("workspaceSelect");
  const cfg = state.config;
  if (!cfg.workspaces) return;
  sel.innerHTML = cfg.workspaces.map((ws, i) =>
    `<option value="${i}" ${i === cfg.active_workspace ? "selected" : ""}>${escHtml(ws.name)} — ${escHtml(ws.path)}</option>`
  ).join("");
}

function showWsSwitchOverlay(title) {
  const el = document.getElementById("wsSwitchOverlay");
  if (!el) return;
  const titleEl = document.getElementById("wsSwitchTitle");
  const stepEl = document.getElementById("wsSwitchStep");
  if (titleEl) titleEl.textContent = title || "";
  if (stepEl) stepEl.textContent = "";
  el.style.display = "flex";
}
function setWsSwitchStep(step) {
  const stepEl = document.getElementById("wsSwitchStep");
  if (stepEl) stepEl.textContent = step || "";
}
function hideWsSwitchOverlay() {
  const el = document.getElementById("wsSwitchOverlay");
  if (el) el.style.display = "none";
}

async function switchWorkspace(idx) {
  const cfg = state.config || {};
  const wsName = (cfg.workspaces && cfg.workspaces[idx] && cfg.workspaces[idx].name) || "";
  showWsSwitchOverlay(t('workspace.switching', wsName));
  try {
    setWsSwitchStep(t('workspace.stepRequest'));
    await api("/api/workspaces/switch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ index: idx }),
    });
    state.selectedFile = null;
    state.diff = null;
    state.activeSheet = null;
    state.overviewFiles = null;
    state.overviewLog = [];
    state.revLog = [];
    state.modifiedClassify = {};
    document.getElementById("updateBanner").style.display = "none";
    _bannerDismissed = false;

    setWsSwitchStep(t('workspace.stepLoadFiles'));
    state.config = await api("/api/config");
    renderHeader();
    await loadFiles();

    if (state.config.svn_available) {
      setWsSwitchStep(t('workspace.stepLoadSvn'));
      // allSettled so one slow / failing SVN call doesn't block the overlay
      await Promise.allSettled([
        loadModifiedFiles(),
        loadModifiedClassify(),
        loadConflictedFiles(),
      ]);
      // remote version check is a background poll; don't await
      startRemoteVersionCheck();
    } else {
      state.conflictedFiles = [];
    }
    if (state.mode === "overview") {
      loadOverviewLog();
    }
    renderToolbar();
    renderContent();
  } catch (e) {
    alert(t('workspace.switchFailed', e.message));
  } finally {
    hideWsSwitchOverlay();
  }
}

async function addWorkspace() {
  try {
    const resp = await fetch("/api/pick-dir", { method: "POST" });
    if (resp.ok) {
      const data = await resp.json();
      if (data.path) await _doAddWorkspace(data.path);
      return;
    }
    if (resp.status === 501) { showDirBrowser(); return; }
  } catch (_) { /* network error */ }
  showDirBrowser();
}

async function _doAddWorkspace(path) {
  try {
    await api("/api/workspaces/add", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    });
    state.selectedFile = null;
    state.diff = null;
    state.config = await api("/api/config");
    renderHeader();
    renderToolbar();
    renderContent();
    await loadFiles();
    if (state.config.svn_available) {
      loadModifiedFiles();
      loadConflictedFiles();
      startRemoteVersionCheck();
    }
  } catch (e) {
    alert(t('workspace.addFailed', e.message));
  }
}

async function showDirBrowser() {
  const overlay = document.createElement("div");
  overlay.className = "update-modal-overlay";
  overlay.id = "dirBrowserOverlay";

  overlay.innerHTML = `<div class="update-modal dir-browser-modal">
    <h3>${t('dirBrowser.title')}</h3>
    <div class="dir-path-bar">
      <button class="dir-up-btn" onclick="dirBrowserUp()" title="${t('dirBrowser.upTitle')}">↑</button>
      <input type="text" id="dirPathInput" placeholder="${t('dirBrowser.pathPlaceholder')}" onkeydown="if(event.key==='Enter')dirBrowserGo()" />
      <button onclick="dirBrowserGo()">${t('dirBrowser.go')}</button>
    </div>
    <div class="dir-breadcrumb" id="dirBreadcrumb"></div>
    <div class="dir-list" id="dirList"><div class="loading">${t('diff.loading')}</div></div>
    <div class="modal-footer">
      <button class="primary" onclick="dirBrowserSelect()">${t('dirBrowser.select')}</button>
      <button onclick="closeDirBrowser()">${t('dirBrowser.cancel')}</button>
    </div>
  </div>`;

  document.body.appendChild(overlay);
  await dirBrowserNavigate("");
}

let _dirBrowserPath = "";

function _dirNorm(p) { return p.replace(/\\/g, "/"); }

function _dirParent(p) {
  const norm = _dirNorm(p).replace(/\/+$/, "");
  if (/^[A-Za-z]:$/.test(norm)) return "";
  const idx = norm.lastIndexOf("/");
  if (idx <= 0) return norm.match(/^[A-Za-z]:/) ? norm.slice(0, 2) : "";
  return norm.slice(0, idx);
}

async function dirBrowserNavigate(path) {
  _dirBrowserPath = path;
  const listEl = document.getElementById("dirList");
  const inputEl = document.getElementById("dirPathInput");
  const breadEl = document.getElementById("dirBreadcrumb");
  if (!listEl) return;

  listEl.innerHTML = `<div style="padding:10px;color:var(--text-dim)">${t('diff.loading')}</div>`;
  try {
    const data = await api(`/api/browse-dir?path=${encodeURIComponent(path)}`);
    _dirBrowserPath = data.path || "";
    if (inputEl) inputEl.value = _dirBrowserPath;

    if (breadEl) {
      if (data.is_root) {
        breadEl.innerHTML = `<span class="crumb active">${t('dirBrowser.myComputer')}</span>`;
      } else {
        const norm = _dirNorm(_dirBrowserPath);
        const parts = norm.split("/").filter(Boolean);
        let crumbs = `<span class="crumb" onclick="dirBrowserNavigate('')">${t('dirBrowser.myComputer')}</span>`;
        for (let i = 0; i < parts.length; i++) {
          const seg = parts.slice(0, i + 1).join("/");
          const isLast = i === parts.length - 1;
          crumbs += ` <span class="sep">\u203a</span> <span class="crumb${isLast ? " active" : ""}" onclick="dirBrowserNavigate(${jsArg(seg)})">${escHtml(parts[i])}</span>`;
        }
        breadEl.innerHTML = crumbs;
      }
    }

    let html = "";
    if (!data.is_root && _dirBrowserPath) {
      const parent = _dirParent(_dirBrowserPath);
      html += `<div class="dir-item dir-item-up" onclick="dirBrowserNavigate(${jsArg(_dirNorm(parent))})">\ud83d\udcc1 ..</div>`;
    }
    if (data.dirs.length === 0 && !html) {
      listEl.innerHTML = `<div style="padding:10px;color:var(--text-dim)">${t('dirBrowser.noSubdirs')}</div>`;
    } else {
      for (const d of data.dirs) {
        const name = _dirNorm(d).split("/").filter(Boolean).pop() || d;
        const dSafe = jsArg(_dirNorm(d));
        html += `<div class="dir-item" onclick="dirBrowserHighlight(this)" ondblclick="dirBrowserNavigate(${dSafe})">\ud83d\udcc2 ${escHtml(name)}</div>`;
      }
      listEl.innerHTML = html;
    }
  } catch (e) {
    listEl.innerHTML = `<div style="padding:10px;color:var(--red)">${t('dirBrowser.loadFailed', escHtml(e.message))}</div>`;
  }
}

function dirBrowserHighlight(el) {
  document.querySelectorAll(".dir-item.selected").forEach(e => e.classList.remove("selected"));
  el.classList.add("selected");
}

function dirBrowserUp() {
  if (!_dirBrowserPath) return;
  dirBrowserNavigate(_dirParent(_dirBrowserPath));
}

function dirBrowserGo() {
  const input = document.getElementById("dirPathInput");
  if (input && input.value.trim()) {
    dirBrowserNavigate(input.value.trim());
  }
}

async function dirBrowserSelect() {
  const selected = document.querySelector(".dir-item.selected");
  let path = _dirBrowserPath;
  if (selected) {
    const text = selected.textContent.replace(/^\ud83d\udcc2\s*/, "").trim();
    if (text && text !== "..") {
      const dirs = await api(`/api/browse-dir?path=${encodeURIComponent(_dirBrowserPath)}`).catch(() => null);
      if (dirs) {
        const match = dirs.dirs.find(d => _dirNorm(d).split("/").filter(Boolean).pop() === text);
        if (match) path = match;
      }
    }
  }
  if (!path) {
    alert(t('dirBrowser.selectFirst'));
    return;
  }
  closeDirBrowser();
  await _doAddWorkspace(path);
}

function closeDirBrowser() {
  const overlay = document.getElementById("dirBrowserOverlay");
  if (overlay) overlay.remove();
}

async function openWorkspaceDir() {
  try {
    await api("/api/open-dir", { method: "POST" });
  } catch (e) {
    alert(t('error.openDir', e.message));
  }
}

async function removeWorkspace() {
  const cfg = state.config;
  if (!cfg.workspaces || cfg.workspaces.length <= 1) {
    alert(t('workspace.keepOne'));
    return;
  }
  const idx = cfg.active_workspace;
  const ws = cfg.workspaces[idx];
  if (!confirm(t('workspace.confirmDelete', ws.name, ws.path))) return;
  try {
    const res = await api("/api/workspaces/remove", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ index: idx }),
    });
    state.selectedFile = null;
    state.diff = null;
    state.config = await api("/api/config");
    renderHeader();
    await loadFiles();
    if (state.config.svn_available) loadModifiedFiles();
    renderToolbar();
    renderContent();
  } catch (e) {
    alert(t('workspace.deleteFailed', e.message));
  }
}

// ── SVN Remote Version Check & Update ──

let _remoteCheckTimer = null;
let _bannerDismissed = false;

function startRemoteVersionCheck() {
  if (_remoteCheckTimer) clearInterval(_remoteCheckTimer);
  _remoteCheckTimer = setInterval(checkRemoteVersion, 30000);
  checkRemoteVersion();
}

async function checkRemoteVersion() {
  if (!state.config || !state.config.svn_available) return;
  // 流程进行中（语义合并队列 / 正在更新）不要覆盖 banner
  if (state.updateContext || state.svnUpdateInFlight || state.mergeApplyInFlight) return;
  if (_bannerDismissed) return;
  try {
    const data = await api("/api/svn/remote-revision");
    const banner = document.getElementById("updateBanner");
    if (data.has_update) {
      const diff = data.remote_revision - data.local_revision;
      banner.innerHTML = `${t('update.available', diff, data.local_revision, data.remote_revision)} ` +
        `<button class="btn-update" onclick="doSvnUpdate()">${t('update.btn')}</button>` +
        `<button class="btn-dismiss" onclick="dismissBanner()">${t('update.dismiss')}</button>`;
      banner.style.display = "flex";
    } else {
      banner.innerHTML = "";
      banner.style.display = "none";
    }
  } catch (_) {}
}

function dismissBanner() {
  document.getElementById("updateBanner").style.display = "none";
  _bannerDismissed = true;
  setTimeout(() => { _bannerDismissed = false; }, 300000);
}

async function doSvnUpdate() {
  if (state.svnUpdateInFlight) return;
  const banner = document.getElementById("updateBanner");
  banner.innerHTML = `${t('update.checking')}`;

  try {
    const checkData = await api("/api/svn/update", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ check_only: true }),
    });

    if (checkData.conflicts && checkData.conflicts.length > 0) {
      showUpdateConflictModal(checkData);
    } else {
      await _runUpdateAndReport({});
    }
  } catch (e) {
    banner.innerHTML = `${t('update.failed', escHtml(e.message))} <button class="btn-dismiss" onclick="dismissBanner()">${t('update.close')}</button>`;
  }
}

function showUpdateConflictModal(checkData) {
  const overlay = document.createElement("div");
  overlay.className = "update-modal-overlay";
  overlay.id = "updateModalOverlay";

  const conflicts = checkData.conflicts;
  const safeCount = checkData.safe_updates;

  let conflictHtml = "";
  for (const fname of conflicts) {
    const isXml = fname.toLowerCase().endsWith(".xml");
    const semBtn = isXml
      ? `<button onclick="setConflictChoice(this,'semantic')" title="${t('conflict.mergeTitle')}" class="btn-merge">${t('conflict.mergeBtn')}</button>`
      : "";
    conflictHtml += `<div class="conflict-item" data-file="${escHtml(fname)}">
      <span class="fname">${escHtml(fname)}</span>
      <div class="actions">
        <button onclick="setConflictChoice(this,'mine')" title="${t('conflict.keepMineTitle')}">${t('conflict.keepMine')}</button>
        <button onclick="setConflictChoice(this,'theirs')" title="${t('conflict.useTheirsTitle')}">${t('conflict.useTheirs')}</button>
        <button onclick="setConflictChoice(this,'skip')" title="${t('conflict.skipTitle')}">${t('conflict.skip')}</button>
        ${semBtn}
      </div>
    </div>`;
  }

  overlay.innerHTML = `<div class="update-modal">
    <h3>${t('conflict.title')}</h3>
    <p class="safe-info">${t('conflict.safeInfo', safeCount)}</p>
    <div class="conflict-list">${conflictHtml}</div>
    <div class="modal-footer">
      <button onclick="skipAllConflicts()">${t('conflict.skipAll')}</button>
      <button class="primary" onclick="executeUpdate()">${t('conflict.confirmUpdate')}</button>
      <button onclick="closeUpdateModal()">${t('conflict.cancel')}</button>
    </div>
  </div>`;

  document.body.appendChild(overlay);
}

function setConflictChoice(btn, choice) {
  const item = btn.closest(".conflict-item");
  item.querySelectorAll("button").forEach(b => {
    b.className = b.classList.contains("btn-merge") ? "btn-merge" : "";
  });
  if (choice === "theirs") btn.classList.add("selected-theirs");
  else if (choice === "mine") btn.classList.add("selected-mine");
  else if (choice === "semantic") btn.classList.add("selected-semantic");
  else btn.classList.add("selected-skip");
  item.dataset.choice = choice;
}

function skipAllConflicts() {
  document.querySelectorAll(".conflict-item").forEach(item => {
    item.dataset.choice = "skip";
    item.querySelectorAll("button").forEach(b => {
      b.className = b.classList.contains("btn-merge") ? "btn-merge" : "";
    });
    const buttons = item.querySelectorAll(".actions > button");
    // skip 是固定第 3 个按钮（mine / theirs / skip [/ semantic]）
    const skipBtn = buttons[2];
    if (skipBtn) skipBtn.classList.add("selected-skip");
  });
  executeUpdate();
}

async function executeUpdate() {
  const items = document.querySelectorAll(".conflict-item");
  const skip_files = [];
  const theirs_files = [];
  const mine_files = [];
  const semantic_files = [];

  items.forEach(item => {
    const fname = item.dataset.file;
    const choice = item.dataset.choice || "skip";
    if (choice === "skip") skip_files.push(fname);
    else if (choice === "theirs") theirs_files.push(fname);
    else if (choice === "mine") mine_files.push(fname);
    else if (choice === "semantic") semantic_files.push(fname);
  });

  closeUpdateModal();
  const banner = document.getElementById("updateBanner");
  banner.innerHTML = `${t('update.inProgress')}`;
  banner.style.display = "flex";

  if (semantic_files.length > 0) {
    // 进入语义合并队列：每个文件让用户在 merge 模式手动决议；
    // 队列完成后再统一调一次 /api/svn/update，把已合并文件作为 semantic_files 传入。
    state.updateContext = {
      skip_files, theirs_files, mine_files,
      semanticQueue: semantic_files.slice(),
      semanticDone: [],
      totalSemantic: semantic_files.length,
    };
    _processNextSemantic();
    return;
  }

  await _runUpdateAndReport({ skip_files, theirs_files, mine_files, semantic_files: [] });
}

async function _runUpdateAndReport(payload) {
  if (state.svnUpdateInFlight) return;
  state.svnUpdateInFlight = true;
  const banner = document.getElementById("updateBanner");
  try {
    const result = await api("/api/svn/update", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const parts = [];
    if (result.updated) parts.push(t('update.doneUpdated', result.updated));
    if (result.skipped && result.skipped.length) parts.push(t('update.doneSkipped', result.skipped.length));
    if (result.theirs && result.theirs.length) parts.push(t('update.doneTheirs', result.theirs.length));
    if (result.mine && result.mine.length) parts.push(t('update.doneMine', result.mine.length));
    if (result.semantic && result.semantic.length) parts.push(t('update.doneSemantic', result.semantic.length));
    if (result.errors && result.errors.length) {
      _bannerDismissed = true; // Keep the error/recovery path visible until dismissed.
      const details = result.errors.concat(result.backups || []).join("\n");
      banner.innerHTML = `${t('update.failed', escHtml(details))} <button class="btn-dismiss" onclick="dismissBanner()">${t('update.close')}</button>`;
      banner.style.display = "flex";
    } else {
      banner.innerHTML = `${t('update.doneDetail', parts.join(", "))}`;
      setTimeout(() => { banner.style.display = "none"; checkRemoteVersion(); }, 5000);
    }
    await reloadAfterUpdate({ skipRemoteCheck: true });
  } catch (e) {
    banner.innerHTML = `${t('update.failed', escHtml(e.message))} <button class="btn-dismiss" onclick="dismissBanner()">${t('update.close')}</button>`;
    banner.style.display = "flex";
  } finally {
    state.svnUpdateInFlight = false;
    renderToolbar();
  }
}

async function _processNextSemantic() {
  const ctx = state.updateContext;
  if (!ctx || ctx.semanticQueue.length === 0) {
    await _finishSemanticQueue();
    return;
  }
  const fname = ctx.semanticQueue[0];
  const idx = ctx.semanticDone.length + 1;
  const total = ctx.totalSemantic;
  const banner = document.getElementById("updateBanner");
  banner.innerHTML = t('update.semanticStep', escHtml(fname), idx, total);
  banner.style.display = "flex";

  state.mergeFromSvnConflict = true;
  // 必须先 await setMode 完成 loadConflictedFiles，再 selectFile，避免 setMode 的非冲突清空在
  // selectFile 之后触发把 selectedFile 清成 null 的竞态。
  await setMode("merge");
  selectFile(fname);
}

async function _finishSemanticQueue() {
  const ctx = state.updateContext;
  if (!ctx) return;
  const banner = document.getElementById("updateBanner");
  banner.innerHTML = t('update.semanticQueueDone');
  banner.style.display = "flex";

  const payload = {
    skip_files: ctx.skip_files,
    theirs_files: ctx.theirs_files,
    mine_files: ctx.mine_files,
    semantic_files: ctx.semanticDone,
  };
  state.updateContext = null;
  state.mergeFromSvnConflict = false;
  await _runUpdateAndReport(payload);
}

function cancelSemanticQueue() {
  if (!state.updateContext) return;
  if (state.updateContext.semanticDone.length > 0) {
    alert(t('merge.queueCancelLocked'));
    return;
  }
  if (!confirm(t('merge.confirmCancelQueue'))) return;
  state.updateContext = null;
  state.mergeFromSvnConflict = false;
  const banner = document.getElementById("updateBanner");
  if (banner) banner.style.display = "none";
  checkRemoteVersion();
}

function closeUpdateModal() {
  const overlay = document.getElementById("updateModalOverlay");
  if (overlay) overlay.remove();
}

async function reloadAfterUpdate(options = {}) {
  state.config = await api("/api/config");
  renderHeader();
  await loadFiles();
  if (state.config.svn_available) {
    loadModifiedFiles();
    loadModifiedClassify();
    loadConflictedFiles();
  }
  if (state.selectedFile && state.mode === "local") {
    doDiffLocal();
  }
  if (!options.skipRemoteCheck) checkRemoteVersion();
}

// ── Render: File list ──

function renderFileList() {
  const list = document.getElementById("fileList");
  const modMap = {};
  state.modifiedFiles.forEach(f => { modMap[f.name] = f.status; });
  const conflictSet = new Set(state.conflictedFiles || []);

  let files = state.files;
  const isMergeMode = state.mode === "merge";
  if (isMergeMode) {
    files = files
      .filter(f => f.name.toLowerCase().endsWith(".xml"))
      .filter(f => conflictSet.has(f.name));
  }
  if (state.filterText) {
    const ft = state.filterText.toLowerCase();
    files = files.filter(f => f.name.toLowerCase().includes(ft));
  }
  if (state.showOnlyModified) {
    files = files.filter(f => modMap[f.name] || conflictSet.has(f.name));
  }

  let body;
  if (files.length === 0 && isMergeMode) {
    body = `<div class="empty-hint">${t('mergeList.noConflicts')}</div>`;
  } else {
    body = files.map(f => {
      const status = modMap[f.name] || "";
      const active = state.selectedFile === f.name ? " active" : "";
      let dotClass = "";
      let dotTitle = "";
      if (conflictSet.has(f.name)) {
        dotClass = " conflicted";
        dotTitle = t('dot.conflicted');
      } else if (status === "added") { dotClass = " added"; dotTitle = t('dot.added'); }
      else if (status === "deleted") { dotClass = " deleted"; dotTitle = t('dot.deleted'); }
      else if (status === "conflicted") { dotClass = " conflicted"; dotTitle = t('dot.conflicted'); }
      else if (status) {
        const cls = state.modifiedClassify[f.name];
        dotClass = cls === "meta" ? " meta-change" : " data-change";
        dotTitle = cls === "meta" ? t('dot.metaChange') : t('dot.dataChange');
      }
      const lowName = f.name.toLowerCase();
      const typeBadge = lowName.endsWith(".xlsx") ? '<span class="type-badge xlsx">XLSX</span>'
                      : lowName.endsWith(".xls") ? '<span class="type-badge xls">XLS</span>' : '';
      const slashIdx = f.name.lastIndexOf("/");
      const displayName = slashIdx >= 0 ? f.name.substring(slashIdx + 1) : f.name;
      const dirBadge = slashIdx >= 0 ? `<span class="type-badge dir">${escHtml(f.name.substring(0, slashIdx))}</span>` : '';
      return `<div class="file-item${active}" onclick="selectFile(${jsArg(f.name)})">
        <span class="status-dot${dotClass}"${dotTitle ? ` title="${dotTitle}"` : ""}></span>
        <span class="name" title="${escHtml(f.name)}">${escHtml(displayName)}</span>
        ${typeBadge}${dirBadge}
        <span class="size">${formatSize(f.size)}</span>
      </div>`;
    }).join("");
  }

  list.innerHTML = body;
}

// ── Mode switching ──

async function setMode(mode) {
  state.mode = mode;
  document.querySelectorAll(".mode-tab").forEach(t => {
    t.classList.toggle("active", t.dataset.mode === mode);
  });
  state.diff = null;
  state.activeSheet = null;
  state.mergeData = null;

  const sidebar = document.querySelector(".sidebar");
  const app = document.querySelector(".app");
  if (mode === "overview") {
    sidebar.style.display = "none";
    app.style.gridTemplateColumns = "1fr";
    state.overviewFiles = null;
    state.overviewExpanded = {};
    loadOverviewLog();
  } else {
    sidebar.style.display = "";
    app.style.gridTemplateColumns = "320px 1fr";
  }

  if (mode === "merge") {
    if (state.config && state.config.svn_available) {
      await loadConflictedFiles();
    }
    // 防死循环：进 merge 模式时清掉非冲突 selectedFile，避免对它跑 doMergePreview() 后弹假"无差异"卡
    const list = state.conflictedFiles || [];
    if (state.selectedFile && list.indexOf(state.selectedFile) < 0) {
      state.selectedFile = null;
    }
  }

  renderFileList();
  renderToolbar();
  renderContent();
  if (state.selectedFile) {
    if (mode === "local") doDiffLocal();
    else if (mode === "revision") loadRevLog();
    else if (mode === "browse") doBrowse();
    else if (mode === "merge") doMergePreview();
  }
}

// ── File selection ──

async function selectFile(name) {
  _lastMtime = 0;
  state.selectedFile = name;
  state.diff = null;
  state.activeSheet = null;
  state.mergeData = null;
  renderFileList();
  renderToolbar();

  if (state.mode === "local") {
    await doDiffLocal();
  } else if (state.mode === "revision") {
    await loadRevLog();
  } else if (state.mode === "browse") {
    await doBrowse();
  } else if (state.mode === "merge") {
    await doMergePreview();
  }
}

// ── Toolbar ──

function _viewToggleHtml() {
  const isSplit = state.diffView === "split";
  const label = isSplit ? t('toolbar.viewSplit') : t('toolbar.viewInline');
  return `<button class="btn view-toggle" onclick="toggleDiffView()" title="${t('toolbar.viewToggleTitle')}">&#8646; ${label}</button>`;
}

function toggleDiffView() {
  state.diffView = state.diffView === "split" ? "inline" : "split";
  try { localStorage.setItem("smartdiff_diff_view", state.diffView); } catch (e) {}
  renderToolbar();
  renderContent();
}

function renderToolbar() {
  const tb = document.getElementById("toolbar");

  if (state.mode === "overview") {
    if (state.overviewLog.length > 0) renderOverviewToolbar();
    else tb.innerHTML = `<span style="color:var(--text-dim)">${t('toolbar.loadingRevisions')}</span>`;
    return;
  }

  if (!state.selectedFile) {
    tb.innerHTML = "";
    return;
  }

  if (state.mode === "local") {
    tb.innerHTML = `
      <span style="font-size:13px;color:var(--text-bright)">${escHtml(state.selectedFile)}</span>
      <div class="spacer" style="flex:1"></div>
      ${_viewToggleHtml()}
      <button class="btn" onclick="doDiffLocal()" title="${t('toolbar.refreshTitle')}">&#8635; ${t('toolbar.refresh')}</button>`;
  } else if (state.mode === "revision") {
    if (state.revLog.length === 0) {
      tb.innerHTML = `
        <span style="font-size:13px;color:var(--text-bright)">${escHtml(state.selectedFile)}</span>
        <span style="margin-left:12px;color:var(--text-dim)">${t('toolbar.noRevisions')}</span>`;
    } else if (state.revLog.length === 1) {
      const e = state.revLog[0];
      tb.innerHTML = `
        <span style="font-size:13px;color:var(--text-bright)">${escHtml(state.selectedFile)}</span>
        <span style="margin-left:12px;font-size:12px;color:var(--text-dim);padding:4px 8px;border:1px solid var(--border);border-radius:var(--radius);background:var(--bg)">r${e.revision} - ${escHtml(e.author)}</span>
        <span style="font-size:11px;color:var(--text-dim);margin-left:8px">${t('toolbar.singleRevision')}</span>`;
    } else {
      const opts = state.revLog.map(e =>
        `<option value="${e.revision}">r${e.revision} - ${escHtml(e.author)} - ${escHtml(e.message.substring(0, 30))}</option>`
      ).join("");
      tb.innerHTML = `
        <span style="font-size:13px;color:var(--text-bright)">${escHtml(state.selectedFile)}</span>
        <label>${t('toolbar.oldRev')}</label>
        <select id="revOld">${opts}</select>
        <label>${t('toolbar.newRev')}</label>
        <select id="revNew">${opts}</select>
        <button class="btn primary" onclick="doDiffRevision()">${t('toolbar.compare')}</button>
        <div class="spacer" style="flex:1"></div>
        ${_viewToggleHtml()}`;
      document.getElementById("revOld").value = state.revLog[1].revision;
      document.getElementById("revNew").value = state.revLog[0].revision;
    }
  } else if (state.mode === "browse") {
    tb.innerHTML = `
      <span style="font-size:13px;color:var(--text-bright)">${escHtml(state.selectedFile)}</span>
      <div style="flex:1"></div>
      <span style="font-size:12px;color:var(--text-dim)">${t('toolbar.browseHint')}</span>`;
  } else if (state.mode === "merge") {
    renderMergeToolbar();
  }
}

function renderMergeToolbar() {
  const tb = document.getElementById("toolbar");
  const md = state.mergeData;

  let stats = "";
  let progress = "";
  let applyBtnDisabled = (!md || !md.sheets || md.error || state.mergeApplyInFlight || state.svnUpdateInFlight) ? "disabled" : "";
  let extras = "";
  if (md && md.summary) {
    const s = md.summary;
    const remaining = countRemainingConflicts();
    const total = (s.conflicts || 0);
    const resolved = total - remaining;
    const pct = total > 0 ? Math.round((resolved / total) * 100) : 100;
    const cls = remaining > 0 ? "merge-stat-pending" : "merge-stat-ok";
    stats = `<span class="merge-stats ${cls}">${t('merge.statsAuto', s.auto_resolved)} · ${t('merge.statsConflict', total)}${remaining > 0 ? ` · ${t('merge.statsUnresolved', remaining)}` : t('merge.statsAllResolved')}</span>`;
    progress = total > 0
      ? `<div class="merge-progress" title="${resolved}/${total} ${t('merge.resolved')}">
           <div class="merge-progress-bar ${remaining === 0 ? "done" : ""}" style="width:${pct}%"></div>
           <span class="merge-progress-text">${pct}%</span>
         </div>`
      : "";
    if (remaining > 0) applyBtnDisabled = "disabled";

    const filterActive = state.mergeOnlyConflicts ? " active" : "";
    const expandLabel = state.mergeExpandMode === "all" ? t('merge.collapseAll')
      : state.mergeExpandMode === "none" ? t('merge.smartExpand')
      : t('merge.expandAll');
    extras = `
      <button class="btn merge-toolbar-toggle${filterActive}" onclick="toggleMergeFilter()" title="${t('merge.filterTitle')}">
        ${state.mergeOnlyConflicts ? "✓ " : ""}${t('merge.filterLabel')}${remaining > 0 ? ` (${remaining})` : ""}
      </button>
      <button class="btn merge-toolbar-toggle" onclick="cycleMergeExpandMode()" title="${t('merge.toggleExpandTitle')}">
        ${expandLabel}
      </button>`;
  }

  const fname = state.selectedFile
    ? `<span style="font-size:13px;color:var(--text-bright)">${escHtml(state.selectedFile)}</span>`
    : `<span style="font-size:13px;color:var(--text-dim)">${t('merge.noFile')}</span>`;

  let queueHint = "";
  if (state.updateContext) {
    const ctx = state.updateContext;
    const idx = ctx.semanticDone.length + (ctx.semanticQueue.length > 0 ? 1 : 0);
    queueHint = `<span class="merge-queue-hint" title="${t('merge.queueHint', idx, ctx.totalSemantic)}">
      ${t('merge.queueHint', idx, ctx.totalSemantic)}
      <button class="btn" onclick="cancelSemanticQueue()">${t('merge.queueCancel')}</button>
    </span>`;
  }

  tb.innerHTML = `
    ${fname}
    <label>${t('merge.compareVersion')}</label>
    <input type="text" id="mergeTheirsRev" value="${escHtml(state.mergeTheirsRev)}" style="width:80px" title="${t('merge.revInputTitle')}" />
    <button class="btn" onclick="doMergePreview()">${t('merge.refresh')}</button>
    ${stats}
    ${progress}
    ${extras}
    ${queueHint}
    <div style="flex:1"></div>
    <button class="btn primary" id="applyMergeBtn" onclick="applyMerge()" ${applyBtnDisabled}>${t('merge.applyAndSave')}</button>`;

  const revInput = document.getElementById("mergeTheirsRev");
  if (revInput) {
    revInput.addEventListener("change", () => {
      state.mergeTheirsRev = revInput.value.trim() || "HEAD";
    });
  }
}

function toggleMergeFilter() {
  state.mergeOnlyConflicts = !state.mergeOnlyConflicts;
  renderMergeToolbar();
  renderMergeView(document.getElementById("content"));
}

function cycleMergeExpandMode() {
  const order = { smart: "all", all: "none", none: "smart" };
  state.mergeExpandMode = order[state.mergeExpandMode] || "smart";
  state.mergeRowExpanded = {};
  renderMergeToolbar();
  renderMergeView(document.getElementById("content"));
}

// "Pure local noise" rows: the remote (THEIRS) brought no change at all and
// the merge result is identical to the working copy. Showing them in the
// semantic-merge view is purely visual noise (e.g. hundreds of locally-added
// rows in an SVN-conflicted file the user just hasn't committed yet) so they
// are filtered out unconditionally, even when "Unresolved only" is off.
function isLocalNoiseRow(row) {
  switch (row && row.status) {
    case "added_mine":        // BASE 无 + MINE 加 + THEIRS 无
    case "added_both_same":   // 双方都加同样的，结果一致
    case "removed_mine":      // BASE 有 + MINE 删 + THEIRS 未动
    case "removed_both":      // 双方都删，结果一致
      return true;
    default:
      return false;
  }
}

function rowNeedsAttention(row) {
  if (row.is_row_conflict && row.row_decision === null) return true;
  if (rowKeepsCells(row)) {
    for (const col in row.cells) {
      const c = row.cells[col];
      if (c.status === "conflict" && c.resolved === null) return true;
    }
  }
  return false;
}

function isRowExpanded(sheetName, row) {
  const key = `${sheetName}:${row.row_key}`;
  if (state.mergeRowExpanded[key] !== undefined) return state.mergeRowExpanded[key];
  if (state.mergeExpandMode === "all") return true;
  if (state.mergeExpandMode === "none") return false;
  return rowNeedsAttention(row);
}

function toggleRowExpanded(sheetName, rowKey) {
  const md = state.mergeData;
  if (!md) return;
  const sheet = md.sheets[sheetName];
  if (!sheet) return;
  const row = sheet.rows.find(r => r.row_key === rowKey);
  if (!row) return;
  const key = `${sheetName}:${rowKey}`;
  const current = isRowExpanded(sheetName, row);
  state.mergeRowExpanded[key] = !current;
  renderMergeView(document.getElementById("content"));
}

function countRemainingConflicts() {
  const md = state.mergeData;
  if (!md || !md.sheets) return 0;
  let n = 0;
  for (const sheetName in md.sheets) {
    const sheet = md.sheets[sheetName];
    for (const row of sheet.rows) {
      if (row.is_row_conflict && row.row_decision === null) n++;
      else if (rowKeepsCells(row)) {
        for (const col in row.cells) {
          const c = row.cells[col];
          if (c.status === "conflict" && c.resolved === null) n++;
        }
      }
    }
  }
  return n;
}

function rowKeepsCells(row) {
  const s = row.status;
  if (s === "modified") return true;
  if (s === "added_both_diff") return row.row_decision === "merge";
  return false;
}

function renderOverviewToolbar() {
  const tb = document.getElementById("toolbar");
  const opts = state.overviewLog.map(e =>
    `<option value="${e.revision}">r${e.revision} - ${escHtml(e.author)} - ${escHtml(e.message.substring(0, 40))}</option>`
  ).join("");
  tb.innerHTML = `
    <label>${t('toolbar.oldRev')}</label>
    <select id="ovRevOld">${opts}</select>
    <label>${t('toolbar.newRev')}</label>
    <select id="ovRevNew">${opts}</select>
    <button class="btn primary" onclick="doOverview()">${t('toolbar.compare')}</button>
    <div style="flex:1"></div>
    <button class="btn ${state.overviewFilter === 'all' ? 'primary' : ''}" data-filter-key="all" onclick="setOverviewFilter('all')">${t('overview.allFiles')}</button>
    <button class="btn ${state.overviewFilter === 'data-only' ? 'primary' : ''}" data-filter-key="data-only" onclick="setOverviewFilter('data-only')">${t('overview.dataOnly')}</button>
    ${_viewToggleHtml()}`;
  if (state.overviewLog.length >= 2) {
    document.getElementById("ovRevOld").value = state.overviewLog[1].revision;
    document.getElementById("ovRevNew").value = state.overviewLog[0].revision;
  }
}

// ── Actions ──

async function doDiffLocal() {
  if (!state.selectedFile) return;
  state.loading = true;
  renderContent();
  try {
    state.diff = await api("/api/diff/local", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ file: state.selectedFile }),
    });
    if (state.diff.sheets) {
      const names = Object.keys(state.diff.sheets);
      state.activeSheet = names[0] || null;
    }
  } catch (e) {
    state.diff = { error: e.message };
  }
  state.loading = false;
  renderContent();
}

async function loadRevLog() {
  if (!state.selectedFile) return;
  try {
    const data = await api(`/api/svn/log?file=${encodeURIComponent(state.selectedFile)}&limit=30`);
    state.revLog = data.entries;
    renderToolbar();
  } catch (e) {
    state.revLog = [];
    renderToolbar();
  }
}

async function doDiffRevision() {
  if (!state.selectedFile) return;
  const revOldEl = document.getElementById("revOld");
  const revNewEl = document.getElementById("revNew");
  const revOld = revOldEl ? revOldEl.value : (state.revLog.length >= 2 ? String(state.revLog[state.revLog.length - 1].revision) : "");
  const revNew = revNewEl ? revNewEl.value : (state.revLog.length ? String(state.revLog[0].revision) : "");
  state.loading = true;
  renderContent();
  try {
    state.diff = await api("/api/diff/revisions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        file: state.selectedFile,
        rev_old: revOld,
        rev_new: revNew,
      }),
    });
    if (state.diff.sheets) {
      const names = Object.keys(state.diff.sheets);
      state.activeSheet = names[0] || null;
    }
  } catch (e) {
    state.diff = { error: e.message };
  }
  state.loading = false;
  renderContent();
}

async function doBrowse() {
  if (!state.selectedFile) return;
  state.loading = true;
  renderContent();
  try {
    const data = await api(`/api/parse?file=${encodeURIComponent(state.selectedFile)}`);
    state.diff = { browse: true, parsed: data };
    if (data.sheets) {
      const names = Object.keys(data.sheets);
      state.activeSheet = names[0] || null;
    }
  } catch (e) {
    state.diff = { error: e.message };
  }
  state.loading = false;
  renderContent();
}

// ── Render: Main content ──

function renderContent() {
  _cleanupScrollHints();
  const main = document.getElementById("content");

  if (state.loading) {
    main.innerHTML = `<div class="loading"><img class="icon-img pixel-bounce" src="/static/img/miku.svg" alt="" />${t('diff.loading')}</div>`;
    return;
  }

  if (state.mode === "overview") {
    if (!state.overviewFiles) {
      main.innerHTML = `<div class="placeholder">
        <img class="icon-img" src="/static/img/miku.svg" alt="" />
        <div class="text">${t('placeholder.overview')}</div>
        <div class="hint">${t('placeholder.overviewHint')}</div>
      </div>`;
      return;
    }
    renderOverviewView(main);
    return;
  }

  if (state.mode === "merge") {
    if (!state.selectedFile) {
      main.innerHTML = `<div class="placeholder">
        <img class="icon-img" src="/static/img/miku.svg" alt="" />
        <div class="text">${t('placeholder.merge')}</div>
        <div class="hint">${t('placeholder.mergeHint')}</div>
      </div>`;
      return;
    }
    if (!state.mergeData) {
      main.innerHTML = `<div class="placeholder">
        <img class="icon-img" src="/static/img/miku.svg" alt="" />
        <div class="text">${t('placeholder.mergeLoading')}</div>
      </div>`;
      return;
    }
    if (state.mergeData.error) {
      main.innerHTML = `<div class="placeholder"><div class="icon">&#9888;</div><div class="text">${escHtml(state.mergeData.error)}</div></div>`;
      return;
    }
    renderMergeView(main);
    return;
  }

  if (!state.selectedFile) {
    main.innerHTML = `<div class="placeholder">
      <img class="icon-img" src="/static/img/miku.svg" alt="" />
      <div class="text">${t('placeholder.text')}</div>
      <div class="hint">${t('placeholder.hint')}</div>
    </div>`;
    return;
  }

  if (!state.diff) {
    main.innerHTML = `<div class="placeholder">
      <img class="icon-img" src="/static/img/miku.svg" alt="" />
      <div class="text">${t('placeholder.clickToolbar')}</div>
    </div>`;
    return;
  }

  if (state.diff.error) {
    main.innerHTML = `<div class="placeholder">
      <div class="icon">&#9888;</div>
      <div class="text">${escHtml(state.diff.error)}</div>
    </div>`;
    return;
  }

  if (state.diff.browse) {
    renderBrowseView(main);
    return;
  }

  renderDiffView(main);
}

function getDiffBlocksHtml(adds, dels, mods) {
  const total = adds + dels + mods;
  if (total === 0) return `<span class="diff-blocks"><span class="block empty"></span><span class="block empty"></span><span class="block empty"></span><span class="block empty"></span><span class="block empty"></span></span>`;
  let addB = Math.round((adds / total) * 5);
  let delB = Math.round((dels / total) * 5);
  let modB = Math.round((mods / total) * 5);
  
  while (addB + delB + modB > 5) {
    if (addB >= delB && addB >= modB) addB--;
    else if (delB >= addB && delB >= modB) delB--;
    else modB--;
  }
  
  let emptyB = 5 - (addB + delB + modB);
  let html = `<span class="diff-blocks" title="${t('diff.blockTitle', adds, dels, mods)}">`;
  for(let i=0; i<addB; i++) html += `<span class="block add"></span>`;
  for(let i=0; i<delB; i++) html += `<span class="block del"></span>`;
  for(let i=0; i<modB; i++) html += `<span class="block mod"></span>`;
  for(let i=0; i<emptyB; i++) html += `<span class="block empty"></span>`;
  html += `</span>`;
  return html;
}

function renderDiffView(container) {
  const diff = state.diff;
  const summary = diff.summary;

  let html = "";

  // Sheet tabs
  const sheetNames = Object.keys(diff.sheets);
  html += `<div class="sheet-tabs">`;
  for (const name of sheetNames) {
    const sd = diff.sheets[name];
    const active = state.activeSheet === name ? " active" : "";
    let badge = "";
    if (sd.status === "added") badge = `<span class="badge added">${t('diff.added')}</span>`;
    else if (sd.status === "removed") badge = `<span class="badge removed">${t('diff.removed')}</span>`;
    else if (sd.status === "modified") {
      const parts = [];
      if (sd.modified_cells.length > 0) parts.push(`<span class="badge changed">~${sd.modified_cells.length}</span>`);
      if (sd.added_rows.length > 0) parts.push(`<span class="badge added">+${sd.added_rows.length}</span>`);
      if (sd.removed_rows.length > 0) parts.push(`<span class="badge removed">-${sd.removed_rows.length}</span>`);
      badge = parts.join("");
    }
    html += `<button class="sheet-tab${active}" onclick="setActiveSheet(${jsArg(name)})">${escHtml(name)}${badge}</button>`;
  }
  html += `</div>`;

  // Stats panel
  html += `<div class="diff-stats">`;
  html += `<span class="diff-stats-label">${escHtml(diff.old_label || t('diff.old'))} → ${escHtml(diff.new_label || t('diff.new'))}</span>`;
  if (summary.has_changes) {
    html += `<span class="diff-stats-counts">`;
    if (summary.total_added_rows > 0)
      html += `<span class="stat-added">+${summary.total_added_rows} ${t('diff.unitRows')}</span>`;
    if (summary.total_removed_rows > 0)
      html += `<span class="stat-removed">-${summary.total_removed_rows} ${t('diff.unitRows')}</span>`;
    if (summary.total_modified_cells > 0)
      html += `<span class="stat-modified">~${summary.total_modified_cells} ${t('diff.unitCells')}</span>`;
    
    html += getDiffBlocksHtml(summary.total_added_rows, summary.total_removed_rows, summary.total_modified_cells);
    const total = summary.total_added_rows + summary.total_removed_rows + summary.total_modified_cells;
    html += `<span class="stat-total">${t('diff.totalChanges', total)}</span>`;
    html += `</span>`;
  } else {
    html += `<span class="no-changes">${t('diff.metaOnly')}</span>`;
  }
  html += `</div>`;

  // Diff table
  if (state.activeSheet && diff.sheets[state.activeSheet]) {
    html += renderDiffTable(diff.sheets[state.activeSheet]);
  }

  container.innerHTML = html;
  requestAnimationFrame(() => { attachScrollHints(container, true); _fixScrollableHeight(); });
}

function attachScrollHints(root, clearAll) {
  if (clearAll) _cleanupScrollHints();
  const containers = root.querySelectorAll(".diff-container");
  containers.forEach(container => {
    const modCells = container.querySelectorAll("td.cell-modified");
    if (modCells.length === 0) return;

    const leftHint = document.createElement("div");
    leftHint.className = "scroll-hint scroll-hint-left";
    leftHint.style.display = "none";
    document.body.appendChild(leftHint);
    _scrollHintElements.push(leftHint);

    const rightHint = document.createElement("div");
    rightHint.className = "scroll-hint scroll-hint-right";
    rightHint.style.display = "none";
    document.body.appendChild(rightHint);
    _scrollHintElements.push(rightHint);

    function updateHints() {
      if (!container.isConnected) {
        leftHint.style.display = "none";
        rightHint.style.display = "none";
        return;
      }
      const cr = container.getBoundingClientRect();
      if (cr.width < 50 || cr.height < 30 || cr.bottom < 0 || cr.top > window.innerHeight) {
        leftHint.style.display = "none";
        rightHint.style.display = "none";
        return;
      }

      let leftCount = 0, rightCount = 0;
      let nearestLeftOffset = 0, nearestRightOffset = Infinity;
      modCells.forEach(cell => {
        const r = cell.getBoundingClientRect();
        if (r.right < cr.left + 2) {
          leftCount++;
          const off = cell.offsetLeft;
          if (off > nearestLeftOffset) nearestLeftOffset = off;
        } else if (r.left > cr.right - 2) {
          rightCount++;
          const off = cell.offsetLeft;
          if (off < nearestRightOffset) nearestRightOffset = off;
        }
      });

      const centerY = cr.top + cr.height / 2;
      if (leftCount > 0) {
        leftHint.textContent = t('diff.scrollLeft', leftCount);
        leftHint.style.display = "block";
        leftHint.style.top = centerY + "px";
        leftHint.style.left = (cr.left + 8) + "px";
        leftHint.style.right = "";
        leftHint.onclick = () => {
          container.scrollTo({ left: Math.max(0, nearestLeftOffset - 40), behavior: "smooth" });
        };
      } else {
        leftHint.style.display = "none";
      }
      if (rightCount > 0) {
        rightHint.textContent = t('diff.scrollRight', rightCount);
        rightHint.style.display = "block";
        rightHint.style.top = centerY + "px";
        rightHint.style.left = "";
        rightHint.style.right = (window.innerWidth - cr.right + 8) + "px";
        rightHint.onclick = () => {
          container.scrollTo({ left: Math.max(0, nearestRightOffset - 40), behavior: "smooth" });
        };
      } else {
        rightHint.style.display = "none";
      }
    }

    container.addEventListener("scroll", updateHints);
    _scrollHintUpdaters.push(updateHints);
  });
  requestAnimationFrame(_refreshScrollHints);
}


const BATCH_SIZE = 150;
let _tableIdCounter = 0;

function _colLetters(count) {
  const letters = [];
  for (let i = 0; i < count; i++) {
    let n = i + 1, s = "";
    while (n > 0) { n--; s = String.fromCharCode(65 + n % 26) + s; n = Math.floor(n / 26); }
    letters.push(s);
  }
  return letters;
}

/**
 * Smart tokenizer for cell diffs.
 * Groups consecutive digits and ASCII letters into single tokens so a value
 * change like 738 -> 7074 becomes one whole-token change instead of scattered
 * character fragments. Every other character (delimiters, CJK, symbols) is its
 * own token, acting as an alignment anchor while keeping CJK at char-level.
 */
function _tokenizeCell(s) {
  const toks = [];
  let i = 0;
  const n = s.length;
  while (i < n) {
    const ch = s[i];
    if (ch >= "0" && ch <= "9") {
      let j = i + 1;
      while (j < n && ((s[j] >= "0" && s[j] <= "9") || s[j] === ".")) j++;
      toks.push(s.slice(i, j));
      i = j;
    } else if ((ch >= "a" && ch <= "z") || (ch >= "A" && ch <= "Z")) {
      let j = i + 1;
      while (j < n && ((s[j] >= "a" && s[j] <= "z") || (s[j] >= "A" && s[j] <= "Z"))) j++;
      toks.push(s.slice(i, j));
      i = j;
    } else {
      toks.push(ch);
      i++;
    }
  }
  return toks;
}

/**
 * Token-level diff using LCS (Longest Common Subsequence).
 * Returns an array of ops: [{type:"eq"|"del"|"ins", val}].
 */
function _diffOps(oldStr, newStr) {
  const oldToks = _tokenizeCell(oldStr);
  const newToks = _tokenizeCell(newStr);

  const m = oldToks.length, n = newToks.length;
  const dp = [];
  for (let i = 0; i <= m; i++) {
    dp[i] = new Uint16Array(n + 1);
  }
  for (let i = 1; i <= m; i++) {
    for (let j = 1; j <= n; j++) {
      if (oldToks[i - 1] === newToks[j - 1]) {
        dp[i][j] = dp[i - 1][j - 1] + 1;
      } else {
        dp[i][j] = dp[i][j - 1] > dp[i - 1][j] ? dp[i][j - 1] : dp[i - 1][j];
      }
    }
  }

  const ops = [];
  let i = m, j = n;
  while (i > 0 || j > 0) {
    if (i > 0 && j > 0 && oldToks[i - 1] === newToks[j - 1]) {
      ops.push({ type: "eq", val: oldToks[i - 1] });
      i--; j--;
    } else if (j > 0 && (i === 0 || dp[i][j - 1] >= dp[i - 1][j])) {
      ops.push({ type: "ins", val: newToks[j - 1] });
      j--;
    } else {
      ops.push({ type: "del", val: oldToks[i - 1] });
      i--;
    }
  }
  ops.reverse();
  return ops;
}

/**
 * Inline merged diff: <del> for removed, <ins> for added, plain for equal.
 */
function inlineDiff(oldStr, newStr) {
  if (oldStr === newStr) return escHtml(newStr);
  if (!oldStr) return `<ins>${escHtml(newStr)}</ins>`;
  if (!newStr) return `<del>${escHtml(oldStr)}</del>`;

  const ops = _diffOps(oldStr, newStr);
  let html = "";
  let delBuf = "", insBuf = "";
  const flush = () => {
    if (delBuf) { html += `<del>${escHtml(delBuf)}</del>`; delBuf = ""; }
    if (insBuf) { html += `<ins>${escHtml(insBuf)}</ins>`; insBuf = ""; }
  };
  for (const op of ops) {
    if (op.type === "eq") {
      flush();
      html += escHtml(op.val);
    } else if (op.type === "del") {
      if (insBuf) flush();
      delBuf += op.val;
    } else {
      insBuf += op.val;
    }
  }
  flush();
  return html;
}

/**
 * Split diff: returns {oldHtml, newHtml}. Old row shows equal + removed
 * (changed parts highlighted red); new row shows equal + added (green).
 * Each row is the complete value so old -> new is read at a glance.
 */
function inlineDiffSplit(oldStr, newStr) {
  const ops = _diffOps(oldStr, newStr);
  let oldHtml = "", newHtml = "";
  let delBuf = "", insBuf = "";
  const flushDel = () => { if (delBuf) { oldHtml += `<del>${escHtml(delBuf)}</del>`; delBuf = ""; } };
  const flushIns = () => { if (insBuf) { newHtml += `<ins>${escHtml(insBuf)}</ins>`; insBuf = ""; } };
  for (const op of ops) {
    if (op.type === "eq") {
      flushDel(); flushIns();
      oldHtml += escHtml(op.val);
      newHtml += escHtml(op.val);
    } else if (op.type === "del") {
      delBuf += op.val;
    } else {
      insBuf += op.val;
    }
  }
  flushDel(); flushIns();
  return { oldHtml, newHtml };
}

function _rowHtml(row, colLetters, headers) {
  const st = row._status;
  let h;
  if (st === "added") h = `<tr class=" row-added">`;
  else if (st === "removed") h = `<tr class=" row-removed">`;
  else h = `<tr>`;
  h += `<td class="row-num">${row._row}</td>`;

  if (st === "modified" && row.changes) {
    const changes = row.changes;
    for (let i = 0; i < colLetters.length; i++) {
      const col = colLetters[i];
      const ch = changes[col];
      if (ch) {
        if (state.diffView === "split") {
          const { oldHtml, newHtml } = inlineDiffSplit(ch.old, ch.new);
          h += `<td class="cell-modified split" title="${col}${row._row}"><div class="split-wrap"><div class="cell-old">${oldHtml}</div><div class="cell-new">${newHtml}</div></div></td>`;
        } else {
          const diffHtml = inlineDiff(ch.old, ch.new);
          h += `<td class="cell-modified" title="${col}${row._row}"><div class="inline-diff">${diffHtml}</div></td>`;
        }
      } else {
        h += `<td>${escHtml(row.cells[col] || "")}</td>`;
      }
    }
  } else {
    const cells = row.cells;
    for (let i = 0; i < colLetters.length; i++) {
      h += `<td>${escHtml(cells[colLetters[i]] || "")}</td>`;
    }
  }
  h += `</tr>`;
  return h;
}

function _appendRowsBatch(tbodyId, rows, colLetters, headers, start) {
  const tbody = document.getElementById(tbodyId);
  if (!tbody) return;
  const end = Math.min(start + BATCH_SIZE, rows.length);
  let html = "";
  for (let i = start; i < end; i++) html += _rowHtml(rows[i], colLetters, headers);
  tbody.insertAdjacentHTML("beforeend", html);
  if (end < rows.length) {
    requestAnimationFrame(() => _appendRowsBatch(tbodyId, rows, colLetters, headers, end));
  }
}

function renderDiffTable(sheetDiff) {
  const oldHeaders = sheetDiff.old_headers || [];
  const newHeaders = sheetDiff.new_headers || [];
  const headers = Array.from({ length: Math.max(oldHeaders.length, newHeaders.length) },
    (_, i) => newHeaders[i] || oldHeaders[i] || "");
  if (headers.length === 0) return `<div class="placeholder"><div class="text">${t('diff.sheetNoData')}</div></div>`;

  const allRows = [];
  // Old and new physical row numbers are different coordinate systems.
  (sheetDiff.removed_rows || []).forEach(r => { allRows.push({ ...r, _status: "removed" }); });
  (sheetDiff.added_rows || []).forEach(r => { allRows.push({ ...r, _status: "added" }); });
  (sheetDiff.modified_rows || []).forEach(mr => {
    allRows.push({ _row: mr._row, cells: mr.cells, old_cells: mr.old_cells, changes: mr.changes, _status: "modified" });
  });
  allRows.sort((a, b) => a._row - b._row);

  if (allRows.length === 0) {
    return `<div class="summary-bar"><span class="no-changes">${t('diff.sheetNoChanges')}</span></div>`;
  }

  const tid = `dtb_${++_tableIdCounter}`;
  const colLetters = _colLetters(headers.length);
  let headHtml = `<div class="diff-container"><table class="diff-table"><thead><tr><th class="row-num">#</th>`;
  for (let i = 0; i < headers.length; i++) headHtml += `<th title="${colLetters[i]}">${escHtml(headers[i] || colLetters[i])}</th>`;
  headHtml += `</tr></thead><tbody id="${tid}">`;

  const firstBatch = allRows.slice(0, BATCH_SIZE);
  let bodyHtml = "";
  for (const row of firstBatch) bodyHtml += _rowHtml(row, colLetters, headers);
  headHtml += bodyHtml + `</tbody></table></div>`;

  if (allRows.length > BATCH_SIZE) {
    requestAnimationFrame(() => _appendRowsBatch(tid, allRows, colLetters, headers, BATCH_SIZE));
  }
  return headHtml;
}

function renderBrowseView(container) {
  const parsed = state.diff.parsed;
  const sheetNames = Object.keys(parsed.sheets);

  let html = `<div class="sheet-tabs">`;
  for (const name of sheetNames) {
    const sd = parsed.sheets[name];
    const active = state.activeSheet === name ? " active" : "";
    html += `<button class="sheet-tab${active}" onclick="setActiveSheet(${jsArg(name)})">${escHtml(name)} <span class="badge" style="border:1px solid var(--border);color:var(--text-dim)">${sd.row_count}</span></button>`;
  }
  html += `</div>`;

  html += `<div class="summary-bar"><span>${t('diff.browseTime', parsed._parse_ms)}</span></div>`;

  if (state.activeSheet && parsed.sheets[state.activeSheet]) {
    const sheet = parsed.sheets[state.activeSheet];
    const headers = sheet.headers;
    const colLetters = _colLetters(headers.length);
    const dataRows = sheet.rows.slice(1);

    const browseTid = `btb_${++_tableIdCounter}`;
    html += `<div class="diff-container"><table class="diff-table"><thead><tr>`;
    html += `<th class="row-num">#</th>`;
    for (let i = 0; i < headers.length; i++) html += `<th title="${colLetters[i]}">${escHtml(headers[i] || colLetters[i])}</th>`;
    html += `</tr></thead><tbody id="${browseTid}">`;

    const firstBatch = dataRows.slice(0, BATCH_SIZE);
    for (const row of firstBatch) {
      html += `<tr><td class="row-num">${row._row}</td>`;
      for (const col of colLetters) html += `<td>${escHtml(row.cells[col] || "")}</td>`;
      html += `</tr>`;
    }
    html += `</tbody></table></div>`;

    if (dataRows.length > BATCH_SIZE) {
      requestAnimationFrame(() => _appendBrowseRowsBatch(browseTid, dataRows, colLetters, BATCH_SIZE));
    }
  }

  container.innerHTML = html;
  requestAnimationFrame(_fixScrollableHeight);
}

function _appendBrowseRowsBatch(tbodyId, rows, colLetters, start) {
  const tbody = document.getElementById(tbodyId);
  if (!tbody) return;
  const end = Math.min(start + BATCH_SIZE, rows.length);
  let html = "";
  for (let i = start; i < end; i++) {
    const row = rows[i];
    html += `<tr><td class="row-num">${row._row}</td>`;
    for (const col of colLetters) html += `<td>${escHtml(row.cells[col] || "")}</td>`;
    html += `</tr>`;
  }
  tbody.insertAdjacentHTML("beforeend", html);
  if (end < rows.length) {
    requestAnimationFrame(() => _appendBrowseRowsBatch(tbodyId, rows, colLetters, end));
  }
}

function setActiveSheet(name) {
  state.activeSheet = name;
  renderContent();
}

// ── Overview mode ──

async function loadOverviewLog() {
  try {
    const data = await api("/api/svn/dir-log?limit=50");
    state.overviewLog = data.entries;
    renderOverviewToolbar();
  } catch (e) {
    state.overviewLog = [];
  }
}

function setOverviewFilter(filter) {
  state.overviewFilter = filter;
  renderContent();
  document.querySelectorAll("#toolbar .btn[data-filter-key]").forEach(b => {
    b.classList.toggle("primary", b.dataset.filterKey === filter);
  });
}

function toggleOverviewFile(fname) {
  state.overviewExpanded[fname] = !state.overviewExpanded[fname];
  const card = Array.from(document.querySelectorAll(".ov-file-card")).find(el => el.dataset.file === fname);
  if (!card) { renderContent(); return; }

  const expanded = state.overviewExpanded[fname];
  card.classList.toggle("expanded", expanded);

  const arrow = card.querySelector(".ov-expand");
  if (arrow) arrow.textContent = expanded ? "\u25BC" : "\u25B6";

  const existingDetail = card.querySelector(".ov-file-detail");
  if (expanded) {
    if (!existingDetail) {
      const f = (state.overviewFiles.files || []).find(x => x.file === fname);
      if (f && f.diff) {
        const detailHtml = renderOverviewFileDetail(f);
        card.insertAdjacentHTML("beforeend", detailHtml);
        const dc = card.querySelector(".diff-container");
        if (dc) {
          dc.offsetHeight;
          const top = dc.getBoundingClientRect().top;
          const avail = window.innerHeight - top - 20;
          dc.style.maxHeight = Math.max(200, Math.min(avail, 600)) + "px";
        }
        requestAnimationFrame(() => attachScrollHints(card));
      }
    }
  } else {
    if (existingDetail) existingDetail.remove();
  }
}

function setOverviewSheet(fname, sheetName) {
  const key = `_sheet_${fname}`;
  state.overviewExpanded[key] = sheetName;
  const card = Array.from(document.querySelectorAll(".ov-file-card")).find(el => el.dataset.file === fname);
  if (!card) { renderContent(); return; }

  const existingDetail = card.querySelector(".ov-file-detail");
  if (existingDetail) existingDetail.remove();

  const f = (state.overviewFiles.files || []).find(x => x.file === fname);
  if (f && f.diff) {
    const detailHtml = renderOverviewFileDetail(f);
    card.insertAdjacentHTML("beforeend", detailHtml);
    const dc = card.querySelector(".diff-container");
    if (dc) {
      dc.offsetHeight;
      const top = dc.getBoundingClientRect().top;
      const avail = window.innerHeight - top - 20;
      dc.style.maxHeight = Math.max(200, Math.min(avail, 600)) + "px";
    }
    requestAnimationFrame(() => attachScrollHints(card));
  }
}

async function doOverview() {
  const revOld = document.getElementById("ovRevOld").value;
  const revNew = document.getElementById("ovRevNew").value;
  state.loading = true;
  state.overviewExpanded = {};
  renderContent();
  try {
    state.overviewFiles = await api("/api/diff/overview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rev_old: revOld, rev_new: revNew }),
    });
  } catch (e) {
    state.overviewFiles = { error: e.message };
  }
  state.loading = false;
  renderContent();
}

function renderOverviewView(container) {
  const ov = state.overviewFiles;
  if (ov.error) {
    container.innerHTML = `<div class="placeholder"><div class="icon">&#9888;</div><div class="text">${escHtml(ov.error)}</div></div>`;
    return;
  }

  let files = ov.files || [];
  if (state.overviewFilter === "data-only") {
    files = files.filter(f => {
      const s = f.summary || {};
      return (s.total_modified_cells || 0) + (s.total_added_rows || 0) + (s.total_removed_rows || 0) > 0;
    });
  }

  let html = `<div class="overview-summary">`;
  html += `<span>r${ov.rev_old} \u2192 r${ov.rev_new}</span>`;
  html += `<span class="stat">${t('overview.totalFiles', ov.total_files)}</span>`;
  html += `<span class="stat">${t('overview.dataChanged', ov.data_changed_files)}</span>`;
  if (state.overviewFilter === "data-only") {
    html += `<span class="stat">${t('overview.showing', files.length)}</span>`;
  }
  html += `</div>`;

  if (files.length === 0) {
    html += `<div class="placeholder"><div class="text">${t('overview.noMatch')}</div></div>`;
    container.innerHTML = html;
    return;
  }

  html += `<div class="overview-files">`;
  for (const f of files) {
    const expanded = state.overviewExpanded[f.file] || false;
    const statusClass = f.status === "added" ? "file-added" : f.status === "deleted" ? "file-deleted" : "";
    const statusIcon = f.status === "added" ? "+" : f.status === "deleted" ? "\u2212" : "\u2022";
    const statusColor = f.status === "added" ? "var(--green)" : f.status === "deleted" ? "var(--red)" : "var(--yellow)";
    const s = f.summary || {};

    let changeSummary = "";
    if (f.status === "deleted") {
      changeSummary = `<span class="ov-badge del">${t('overview.deleted')}</span>`;
    } else if (f.status === "added") {
      changeSummary = `<span class="ov-badge add">${t('overview.added')}</span>`;
    } else if (f.status === "error") {
      changeSummary = `<span class="ov-badge err">${t('overview.error', escHtml(f.error || ""))}</span>`;
    } else if (!s.has_changes) {
      changeSummary = `<span class="ov-badge meta">${t('overview.metaOnly')}</span>`;
    } else {
      const parts = [];
      if (s.total_added_rows > 0) parts.push(`<span class="ov-badge add">+${s.total_added_rows}</span>`);
      if (s.total_removed_rows > 0) parts.push(`<span class="ov-badge del">-${s.total_removed_rows}</span>`);
      if (s.total_modified_cells > 0) parts.push(`<span class="ov-badge mod">~${s.total_modified_cells}</span>`);
      changeSummary = parts.join(" ");
    }

    html += `<div class="ov-file-card ${statusClass}${expanded ? " expanded" : ""}" data-file="${escHtml(f.file)}">`;
    html += `<div class="ov-file-header" onclick="toggleOverviewFile(${jsArg(f.file)})">`;
    html += `<span class="ov-expand">${expanded ? "\u25BC" : "\u25B6"}</span>`;
    html += `<span class="ov-status" style="color:${statusColor}">${statusIcon}</span>`;
    html += `<span class="ov-filename">${escHtml(f.file)}</span>`;
    html += `<div class="ov-badges">${changeSummary}</div>`;
    html += `</div>`;

    if (expanded && f.diff) {
      html += renderOverviewFileDetail(f);
    }
    html += `</div>`;
  }
  html += `</div>`;

  container.innerHTML = html;
  requestAnimationFrame(_fixScrollableHeight);
}

function renderOverviewFileDetail(f) {
  const diff = f.diff;
  if (!diff || !diff.sheets) return "";

  const sheetNames = Object.keys(diff.sheets);
  const activeSheetKey = `_sheet_${f.file}`;
  let activeSheet = state.overviewExpanded[activeSheetKey] || sheetNames[0];
  if (!diff.sheets[activeSheet]) activeSheet = sheetNames[0];

  let html = `<div class="ov-file-detail">`;

  if (sheetNames.length > 1) {
    html += `<div class="sheet-tabs">`;
    for (const name of sheetNames) {
      const sd = diff.sheets[name];
      const active = activeSheet === name ? " active" : "";
      let badge = "";
      if (sd.status === "added") badge = `<span class="badge added">${t('diff.added')}</span>`;
      else if (sd.status === "removed") badge = `<span class="badge removed">${t('diff.removed')}</span>`;
      else if (sd.status === "modified") {
        const parts = [];
        if (sd.modified_cells.length > 0) parts.push(`<span class="badge changed">~${sd.modified_cells.length}</span>`);
        if (sd.added_rows.length > 0) parts.push(`<span class="badge added">+${sd.added_rows.length}</span>`);
        if (sd.removed_rows.length > 0) parts.push(`<span class="badge removed">-${sd.removed_rows.length}</span>`);
        badge = parts.join("");
      }
      html += `<button class="sheet-tab${active}" onclick="event.stopPropagation();setOverviewSheet(${jsArg(f.file)},${jsArg(name)})">${escHtml(name)}${badge}</button>`;
    }
    html += `</div>`;
  }

  if (activeSheet && diff.sheets[activeSheet]) {
    html += renderDiffTable(diff.sheets[activeSheet]);
  }

  html += `</div>`;
  return html;
}

function escHtml(s) {
  if (s === null || s === undefined) return "";
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

// HTML attributes are decoded before an inline handler is compiled. Escape
// both contexts: a JSON string literal first, then the enclosing HTML.
function jsArg(value) {
  return escHtml(JSON.stringify(String(value)));
}

// ── Merge mode ──

async function doMergePreview() {
  if (!state.selectedFile) return;
  if (!state.selectedFile.toLowerCase().endsWith(".xml")) {
    state.mergeData = { error: t('merge.xmlOnly') };
    renderToolbar();
    renderContent();
    return;
  }
  state.loading = true;
  renderContent();
  try {
    state.mergeData = await api("/api/merge/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        file: state.selectedFile,
        theirs_rev: state.mergeTheirsRev || "HEAD",
      }),
    });
    if (state.mergeData.sheets) {
      const names = Object.keys(state.mergeData.sheets);
      state.activeSheet = names[0] || null;
    }
  } catch (e) {
    state.mergeData = { error: e.message };
  }
  state.loading = false;
  renderToolbar();
  renderContent();
}

async function applyMerge() {
  if (!state.mergeData || !state.selectedFile) return;
  // Reentry guard: once apply is in flight, ignore further clicks (the noop
  // banner button and the toolbar button can both fire applyMerge() while
  // the previous request, svn update, or queue advance is still running -
  // hitting it twice on the same file makes the second call read a
  // poisoned working copy after svn update has injected conflict markers).
  if (state.mergeApplyInFlight || state.svnUpdateInFlight) return;
  if (countRemainingConflicts() > 0) {
    alert(t('merge.unresolvedAlert'));
    return;
  }

  const resolutions = collectResolutions();
  const fromSvn = !!state.mergeFromSvnConflict;
  const currentFile = state.selectedFile;
  const mergeTargetRevision = state.mergeData.theirs_revision || state.mergeTheirsRev || "HEAD";
  const inQueue = !!(state.updateContext && state.updateContext.semanticQueue.length > 0
                     && state.updateContext.semanticQueue[0] === currentFile);

  state.mergeApplyInFlight = true;
  const applyBtn = document.getElementById("applyMergeBtn");
  if (applyBtn) applyBtn.disabled = true;

  try {
    const result = await api("/api/merge/apply", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        file: currentFile,
        theirs_rev: mergeTargetRevision,
        resolutions: resolutions,
        mark_resolved: fromSvn,
        // Echo back the fingerprint preview computed; backend rejects with 409
        // if SVN state drifted in the meantime (external svn resolve / update /
        // editor save).
        merge_signature: state.mergeData.merge_signature || null,
      }),
    });

    // Clear stale merge data immediately so an accidental re-click while the
    // queue advances / svn update runs cannot re-fire apply with the wrong
    // sources (the file is no longer in SVN conflict state).
    state.mergeData = null;
    state.activeSheet = null;

    if (inQueue) {
      state.updateContext.semanticQueue.shift();
      state.updateContext.semanticDone.push({
        file: currentFile,
        theirs_revision: result.theirs_revision || mergeTargetRevision,
      });
      loadConflictedFiles();
      if (state.updateContext.semanticQueue.length > 0) {
        await _processNextSemantic();
      } else {
        await _finishSemanticQueue();
      }
      return;
    }

    const changeCount = (typeof result.total_changes === 'number') ? result.total_changes : result.applied;
    const msg = t('merge.applySuccess', changeCount) + (result.svn_resolved ? t('merge.svnResolved') : "");
    state.mergeFromSvnConflict = false;
    alert(msg);
    setMode("local");
    loadFiles();
    loadModifiedFiles();
    loadModifiedClassify();
    loadConflictedFiles();
    checkRemoteVersion();
  } catch (e) {
    // 409 stale signature: SVN state drifted externally between preview and
    // apply. Alert the user, silently re-run preview so they see the fresh
    // state before retrying. The re-preview will overwrite state.mergeData
    // (which we already nulled above on the success path; on this path the
    // try block threw before nulling, so mergeData still holds the stale copy
    // — doMergePreview will replace it).
    if (e && e.status === 409 && e.body && e.body.stale) {
      alert(t('merge.staleSignature'));
      try {
        await doMergePreview();
      } catch (_) { /* preview errors will surface their own alerts */ }
      return;
    }
    alert(t('merge.applyFailed', e.message));
  } finally {
    state.mergeApplyInFlight = false;
    renderToolbar();
  }
}

// Returns true when the user has actively chosen a value different from the
// auto-resolution default for an auto_* cell, or any explicit choice for a
// conflict cell. Used to decide which cells to send to /api/merge/apply.
function isCellOverridden(cell) {
  if (cell.resolved === null || cell.resolved === undefined) return false;
  if (cell.status === "conflict") return true;
  // For auto_* cells, the default resolved value is set by three_way_diff
  // at preview time. If the user picks a different button, cell.resolved
  // diverges from that default and we must forward the choice to the backend.
  let def;
  if (cell.status === "auto_mine") def = cell.mine;
  else if (cell.status === "auto_theirs") def = cell.theirs;
  else if (cell.status === "auto_both") def = cell.mine; // mine === theirs in auto_both
  else return false;
  return cell.resolved !== def;
}

function collectResolutions() {
  const md = state.mergeData;
  const out = [];
  if (!md || !md.sheets) return out;
  for (const sheetName in md.sheets) {
    const sheet = md.sheets[sheetName];
    for (const row of sheet.rows) {
      if (row.is_row_conflict) {
        out.push({
          sheet: sheetName,
          row_key: row.row_key,
          choice: row.row_decision,
        });
      } else if (row.row_decision && row.row_decision !== "merge" && row.row_decision !== "keep") {
        out.push({
          sheet: sheetName,
          row_key: row.row_key,
          choice: row.row_decision,
        });
      }
      if (rowKeepsCells(row)) {
        for (const col in row.cells) {
          const c = row.cells[col];
          if (!isCellOverridden(c)) continue;
          let choice = "custom";
          let value = c.resolved;
          if (c.resolved === c.mine) choice = "mine";
          else if (c.resolved === c.theirs) choice = "theirs";
          else if (c.resolved === c.base) choice = "base";
          out.push({
            sheet: sheetName, row_key: row.row_key, col: col,
            choice: choice, value: value,
          });
        }
      }
    }
  }
  return out;
}

function setCellChoice(sheetName, rowKey, col, choice, customValue) {
  const md = state.mergeData;
  if (!md) return;
  const sheet = md.sheets[sheetName];
  if (!sheet) return;
  const row = sheet.rows.find(r => r.row_key === rowKey);
  if (!row) return;
  const cell = row.cells[col];
  if (!cell) return;

  if (choice === "mine") cell.resolved = cell.mine;
  else if (choice === "theirs") cell.resolved = cell.theirs;
  else if (choice === "base") cell.resolved = cell.base;
  else if (choice === "custom") {
    const v = customValue !== undefined ? customValue
      : prompt(t('merge.customPrompt', col, row.row_num_mine || row.row_num_theirs || ""), cell.resolved || cell.mine || cell.theirs);
    if (v === null) return;
    cell.resolved = v;
  }
  renderToolbar();
  renderMergeView(document.getElementById("content"));
}

function setRowChoice(sheetName, rowKey, choice) {
  const md = state.mergeData;
  if (!md) return;
  const sheet = md.sheets[sheetName];
  if (!sheet) return;
  const row = sheet.rows.find(r => r.row_key === rowKey);
  if (!row) return;
  row.row_decision = choice;
  renderToolbar();
  renderMergeView(document.getElementById("content"));
}

function getRowStatusLabel(status) {
  const map = {
    modified: t('merge.row.modified'),
    added_mine: t('merge.row.addedMine'),
    added_theirs: t('merge.row.addedTheirs'),
    added_both_same: t('merge.row.addedBothSame'),
    added_both_diff: t('merge.row.addedBothDiff'),
    removed_mine: t('merge.row.removedMine'),
    removed_theirs: t('merge.row.removedTheirs'),
    removed_both: t('merge.row.removedBoth'),
    mine_del_theirs_mod: t('merge.row.mineDelTheirsMod'),
    mine_mod_theirs_del: t('merge.row.mineModTheirsDel'),
  };
  return map[status] || status;
}

function getCellStatusLabel(status) {
  const map = {
    unchanged: t('merge.cell.unchanged'),
    auto_mine: t('merge.cell.autoMine'),
    auto_theirs: t('merge.cell.autoTheirs'),
    auto_both: t('merge.cell.autoBoth'),
    conflict: t('merge.cell.conflict'),
  };
  return map[status] || status;
}

function renderMergeView(container) {
  const md = state.mergeData;
  if (!md.sheets) {
    container.innerHTML = `<div class="placeholder"><div class="text">${t('merge.noContent')}</div></div>`;
    return;
  }
  const sheetNames = Object.keys(md.sheets);
  if (sheetNames.length === 0) {
    container.innerHTML = `<div class="placeholder"><div class="text">${t('merge.noSheet')}</div></div>`;
    return;
  }
  if (!state.activeSheet || !md.sheets[state.activeSheet]) {
    state.activeSheet = sheetNames[0];
  }

  let html = `<div class="merge-sticky-top">`;
  html += `<div class="sheet-tabs">`;
  for (const name of sheetNames) {
    const sd = md.sheets[name];
    const active = state.activeSheet === name ? " active" : "";
    let badge = "";
    if (sd.conflict_count > 0) badge = `<span class="badge changed">${t('merge.conflictBadge', sd.conflict_count)}</span>`;
    else if (sd.auto_resolved_count > 0) badge = `<span class="badge added">${t('merge.autoBadge', sd.auto_resolved_count)}</span>`;
    html += `<button class="sheet-tab${active}" onclick="setActiveSheet(${jsArg(name)})">${escHtml(name)}${badge}</button>`;
  }
  html += `</div>`;

  html += `<div class="merge-versions-bar">
    <span class="ver base">${escHtml(md.base_label || "BASE")}</span>
    <span class="ver-sep">←</span>
    <span class="ver mine">${escHtml(md.mine_label || t('merge.labelMine'))}</span>
    <span class="ver-sep">·</span>
    <span class="ver theirs">${escHtml(md.theirs_label || t('merge.labelTheirs'))}</span>
  </div>`;
  html += `</div>`;

  // Format-only / semantically-identical files: three-way diff yields zero
  // conflicts and zero auto-decided cells, every sheet shows "no changes",
  // and there is no obvious next step. Surface a big "confirm & finish" CTA
  // so the user (especially mid-queue) does not feel stuck.
  const s = md.summary || {};
  // 仅在"该文件是 SVN 冲突态、但语义合并发现无任何冲突/自动合并"时显示确认完成横幅。
  // 非冲突文件没有这个横幅意义（后端不会 svn resolve），避免假"无差异"卡 + 死循环。
  if ((s.conflicts || 0) === 0 && (s.auto_resolved || 0) === 0 && md.from_svn_conflict) {
    html += `<div class="merge-noop-banner">
      <div class="title">${t('merge.noopTitle')}</div>
      <div class="hint">${t('merge.noopHint')}</div>
      <button class="btn primary lg" onclick="applyMerge()">${t('merge.noopConfirm')}</button>
    </div>`;
  }

  const sheet = md.sheets[state.activeSheet];
  html += renderMergeSheet(state.activeSheet, sheet);

  container.innerHTML = html;
  requestAnimationFrame(_fixScrollableHeight);
}

function renderMergeSheet(sheetName, sheet) {
  if (sheet.sheet_status === "added_theirs") {
    return `<div class="placeholder"><div class="text">${t('merge.sheetOnlyTheirs')}</div></div>`;
  }
  if (sheet.sheet_status === "mine_only") {
    return `<div class="placeholder"><div class="text">${t('merge.sheetOnlyMine')}</div></div>`;
  }
  if (!sheet.rows || sheet.rows.length === 0) {
    return `<div class="merge-empty"><div class="text">${t('merge.sheetNoChanges')}</div></div>`;
  }

  let html = `<div class="merge-container">`;
  if (sheet.id_column) {
    html += `<div class="merge-id-hint">${t('merge.idInfo', sheet.id_column, sheet.rows.length)}</div>`;
  } else {
    html += `<div class="merge-id-hint warn">${t('merge.noIdColumn')}</div>`;
  }

  let visible = 0;
  let html_rows = "";
  for (const row of sheet.rows) {
    if (isLocalNoiseRow(row)) continue;                                  // 永久屏蔽纯本地噪音
    if (state.mergeOnlyConflicts && !rowNeedsAttention(row)) continue;   // 勾选时再筛掉自动合并项
    html_rows += renderMergeRow(sheetName, sheet, row);
    visible++;
  }

  if (visible === 0 && state.mergeOnlyConflicts) {
    html += `<div class="merge-empty"><div class="text">${t('merge.nothingToResolve')}</div><div class="text" style="font-size:11px;color:var(--text-dim);margin-top:6px">${t('merge.cancelFilterHint')}</div></div>`;
  } else {
    html += html_rows;
  }
  html += `</div>`;
  return html;
}

function renderMergeRow(sheetName, sheet, row) {
  const status = row.status;
  const label = getRowStatusLabel(status);
  const isConflict = row.is_row_conflict;
  const needsAttn = rowNeedsAttention(row);
  const expanded = isRowExpanded(sheetName, row);
  const rowNum = row.row_num_mine || row.row_num_theirs || row.row_num_base || "?";
  const sk = jsArg(sheetName);
  const rk = jsArg(row.row_key);

  const cls = ["merge-row"];
  if (isConflict) cls.push("row-conflict");
  if (needsAttn) cls.push("needs-attention");
  if (!expanded) cls.push("collapsed");

  let html = `<div class="${cls.join(" ")}" data-status="${status}">`;

  // Header
  html += `<div class="merge-row-header" onclick="toggleRowExpanded(${sk},${rk})">
    <span class="row-toggle">${expanded ? "▾" : "▸"}</span>
    <span class="row-key">${escHtml(String(row.row_key))}</span>
    <span class="row-num">#${rowNum}</span>
    <span class="row-status-badge ${isConflict ? "danger" : (needsAttn ? "warn" : "")}">${label}</span>`;

  if (isConflict || ["added_theirs", "removed_theirs", "removed_mine", "removed_both", "added_both_diff", "added_mine"].includes(status)) {
    html += `<div class="row-decision-actions" onclick="event.stopPropagation()">${renderRowDecisionButtonsInner(sheetName, row)}</div>`;
  }
  html += `</div>`;

  // Body: 始终显示完整行表（带表头），无论展开/折叠
  html += renderRowFullTable(sheetName, sheet, row);

  // Body: 展开时显示变更详情
  if (expanded) {
    if (status === "modified" || (status === "added_both_diff" && row.row_decision === "merge")) {
      const changedCells = [];
      for (const col in row.cells) {
        const cell = row.cells[col];
        if (cell.status === "unchanged") continue;
        changedCells.push(renderMergeCell(sheetName, row, col, cell));
      }
      if (changedCells.length > 0) {
        html += `<div class="merge-row-detail">
          <div class="merge-detail-label">${t('merge.changeDetails', changedCells.length)}</div>
          <div class="merge-cells">${changedCells.join("")}</div>
        </div>`;
      }
    } else if (["added_both_diff"].includes(status)) {
      html += `<div class="merge-row-detail">
        <div class="merge-detail-label">${t('merge.candidateCompare')}</div>
        <div class="row-side-by-side">
          ${renderRowPreviewHorizontal(sheet, row, "mine", t('merge.mineVersion'))}
          ${renderRowPreviewHorizontal(sheet, row, "theirs", t('merge.theirsVersion'))}
        </div>
      </div>`;
    }
  }

  html += `</div>`;
  return html;
}

function _resolvedRowSource(row) {
  // 根据当前 row_decision + status 决定行的"有效值来源"
  const status = row.status;
  const dec = row.row_decision;

  // 删除类决定 → 行不会出现在结果中；显示删除前的值 + strike-through
  if (dec === "keep_mine_delete" || dec === "accept_theirs_delete" || dec === "delete") {
    let side = "mine";
    if (status === "removed_mine" || status === "removed_both") side = "base";
    else if (status === "mine_del_theirs_mod") side = "theirs";
    return { side, deleted: true };
  }
  // 接受远程：用 theirs 值
  if (dec === "accept_theirs") return { side: "theirs", deleted: false };
  // 保留本地（已存在）/ keep_mine 新增 → mine
  if (dec === "keep_mine") {
    // 对于 added_theirs + keep_mine = "忽略远程新增"，根本没行；置为虚化
    if (status === "added_theirs") return { side: "theirs", ignored: true };
    return { side: "mine", deleted: false };
  }
  // 行级冲突 + 尚未决议：选择最能说明"当前情况"的一侧
  if (dec === null) {
    if (status === "mine_del_theirs_mod") return { side: "theirs", deleted: false }; // 本地已删，但显示远程修改后的内容供判断
    if (status === "mine_mod_theirs_del") return { side: "mine", deleted: false };   // 本地修改的版本（远程已删除）
    if (status === "added_both_diff") return { side: "mine", deleted: false };       // 默认显示本地版本，下方有 side-by-side 详情
  }
  // merge / null / keep（modified / added_both_same）→ 用 resolved（cell-level）
  return { side: "resolved", deleted: false };
}

function renderRowFullTable(sheetName, sheet, row) {
  const cols = Object.keys(row.cells).sort((a, b) => {
    const f = (s) => s.split("").reduce((acc, ch) => acc * 26 + (ch.charCodeAt(0) - 64), 0);
    return f(a) - f(b);
  });
  if (cols.length === 0) return "";

  const eff = _resolvedRowSource(row);
  const trCls = ["rt-row"];
  if (eff.deleted) trCls.push("deleted");
  if (eff.ignored) trCls.push("ignored");

  const headerCells = cols.map(col => {
    const cell = row.cells[col];
    return `<th class="rt-th"><span class="rt-h">${escHtml(cell.header || "")}</span><span class="rt-l">${col}</span></th>`;
  }).join("");

  const dataCells = cols.map(col => {
    const cell = row.cells[col];
    let v;
    let cellCls = "rt-td";
    let badge = "";
    let titleHint = `${t('merge.cellTooltipMine', cell.mine || t('merge.emptyValue'))}\n${t('merge.cellTooltipTheirs', cell.theirs || t('merge.emptyValue'))}\n${t('merge.cellTooltipBase', cell.base || t('merge.emptyValue'))}`;

    if (eff.side === "resolved") {
      // 行决议交给单元格层：根据每个 cell 的状态着色 + 加 badge
      v = cell.resolved !== null && cell.resolved !== undefined ? cell.resolved : cell.mine;
      if (cell.status === "unchanged") {
        // 不加 badge
      } else if (cell.status === "auto_mine") {
        cellCls += " val-mine"; badge = `<span class="rt-tag tag-mine" title="${t('merge.autoMineTip')}">${t('merge.badgeMine')}</span>`;
      } else if (cell.status === "auto_theirs") {
        cellCls += " val-theirs"; badge = `<span class="rt-tag tag-theirs" title="${t('merge.autoTheirsTip')}">${t('merge.badgeTheirs')}</span>`;
      } else if (cell.status === "auto_both") {
        cellCls += " val-both"; badge = `<span class="rt-tag tag-both" title="${t('merge.autoBothTip')}">=</span>`;
      } else if (cell.status === "conflict") {
        if (cell.resolved !== null && cell.resolved !== undefined) {
          let src = "?", sCls = "tag-mine";
          if (cell.resolved === cell.mine) { cellCls += " val-mine"; src = t('merge.badgeMine'); sCls = "tag-mine"; }
          else if (cell.resolved === cell.theirs) { cellCls += " val-theirs"; src = t('merge.badgeTheirs'); sCls = "tag-theirs"; }
          else { cellCls += " val-custom"; src = "✎"; sCls = "tag-custom"; }
          badge = `<span class="rt-tag ${sCls}" title="${t('merge.resolvedTip')}">${src}</span>`;
        } else {
          cellCls += " val-conflict";
          badge = `<span class="rt-tag tag-warn" title="${t('merge.unresolvedTip')}">!</span>`;
        }
      }
    } else {
      // 行级决议直接选择了一侧（mine / theirs / base）：整行统一着色，
      // 仅对"该单元格在两侧实际不同"的列加细微高亮。
      v = cell[eff.side];
      const sideCls = eff.side === "mine" ? "val-mine" : (eff.side === "theirs" ? "val-theirs" : "");
      if (sideCls) cellCls += " " + sideCls;
      // 与 base 对比标识
      if (eff.side === "mine" && cell.mine !== cell.base) {
        badge = `<span class="rt-tag tag-mine" title="${t('merge.localModified')}">${t('merge.badgeModified')}</span>`;
      } else if (eff.side === "theirs" && cell.theirs !== cell.base) {
        badge = `<span class="rt-tag tag-theirs" title="${t('merge.remoteModified')}">${t('merge.badgeModified')}</span>`;
      }
    }

    return `<td class="${cellCls}" title="${escHtml(titleHint)}">${badge}<span class="rt-v">${escHtml(v || "—")}</span></td>`;
  }).join("");

  let footnote = "";
  if (eff.deleted) footnote = `<div class="rt-footnote">${t('merge.rowWillDelete')}</div>`;
  else if (eff.ignored) footnote = `<div class="rt-footnote">${t('merge.rowWillIgnore')}</div>`;

  return `<div class="merge-row-table">
    <table class="rt"><thead><tr>${headerCells}</tr></thead><tbody><tr class="${trCls.join(" ")}">${dataCells}</tr></tbody></table>
    ${footnote}
  </div>`;
}

function renderRowDecisionButtonsInner(sheetName, row) {
  const status = row.status;
  const dec = row.row_decision;
  const sk = jsArg(sheetName);
  const rk = jsArg(row.row_key);

  const btn = (choice, label, title, kind) => {
    const active = dec === choice ? " selected" : "";
    const k = kind ? ` btn-${kind}` : "";
    return `<button class="row-decision-btn${active}${k}" title="${title || ""}" onclick="setRowChoice(${sk},${rk},'${choice}')">${label}</button>`;
  };

  if (status === "added_theirs") return btn("accept_theirs", t('merge.btn.acceptTheirs'), t('merge.btn.acceptTheirsTitle'), "theirs") + btn("keep_mine", t('merge.btn.ignore'), t('merge.btn.ignoreTitle'), "mine");
  if (status === "added_mine") return btn("keep_mine", t('merge.btn.keepAdded'), "", "mine");
  if (status === "removed_mine") return btn("keep_mine_delete", t('merge.btn.keepDelete'), "", "delete") + btn("accept_theirs", t('merge.btn.restore'), t('merge.btn.restoreTitle'), "theirs");
  if (status === "removed_theirs") return btn("accept_theirs_delete", t('merge.btn.acceptDelete'), "", "delete") + btn("keep_mine", t('merge.btn.keep'), t('merge.btn.keepMineTitle'), "mine");
  if (status === "removed_both") return btn("delete", t('merge.btn.confirmDelete'), "", "delete");
  if (status === "added_both_diff") return btn("keep_mine", t('merge.btn.keepMine'), "", "mine") + btn("accept_theirs", t('merge.btn.useTheirs'), "", "theirs") + btn("merge", t('merge.btn.perCell'), t('merge.btn.perCellTitle'), "merge");
  if (status === "mine_del_theirs_mod") return btn("keep_mine_delete", t('merge.btn.keepDelete'), "", "delete") + btn("accept_theirs", t('merge.btn.restoreAcceptTheirs'), "", "theirs");
  if (status === "mine_mod_theirs_del") return btn("keep_mine", t('merge.btn.keepModified'), "", "mine") + btn("accept_theirs_delete", t('merge.btn.acceptDelete'), "", "delete");
  return "";
}

function renderRowPreviewHorizontal(sheet, row, side, label) {
  const cols = Object.keys(row.cells).sort((a, b) => {
    const f = (s) => s.split("").reduce((acc, ch) => acc * 26 + (ch.charCodeAt(0) - 64), 0);
    return f(a) - f(b);
  });
  const headers = cols.map(col => {
    const cell = row.cells[col];
    return `<th class="rt-th"><span class="rt-h">${escHtml(cell.header || "")}</span><span class="rt-l">${col}</span></th>`;
  }).join("");
  const data = cols.map(col => {
    const cell = row.cells[col];
    const v = cell[side];
    const cls = side === "mine" ? "val-mine" : (side === "theirs" ? "val-theirs" : "");
    return `<td class="rt-td ${cls}" title="${escHtml(v || "")}"><span class="rt-v">${escHtml(v || "—")}</span></td>`;
  }).join("");
  return `<div class="merge-row-preview-inline">
    <div class="preview-label">${label}</div>
    <div class="merge-row-table">
      <table class="rt"><thead><tr>${headers}</tr></thead><tbody><tr>${data}</tr></tbody></table>
    </div>
  </div>`;
}

function renderMergeCell(sheetName, row, col, cell) {
  const sk = jsArg(sheetName);
  const rk = jsArg(row.row_key);
  const isConflict = cell.status === "conflict";
  const isResolved = cell.resolved !== null && cell.resolved !== undefined;
  const statusLabel = getCellStatusLabel(cell.status);
  const header = cell.header || col;

  let resolvedDisplay = "";
  if (isResolved && (isConflict || cell.status === "auto_mine" || cell.status === "auto_theirs" || cell.status === "auto_both")) {
    let src = "";
    let srcCls = "";
    if (cell.resolved === cell.mine) { src = t('merge.src.mine'); srcCls = "src-mine"; }
    else if (cell.resolved === cell.theirs) { src = t('merge.src.theirs'); srcCls = "src-theirs"; }
    else if (cell.resolved === cell.base) { src = t('merge.src.base'); srcCls = "src-base"; }
    else { src = t('merge.src.custom'); srcCls = "src-custom"; }
    resolvedDisplay = `<span class="cell-arrow">→</span><span class="cell-resolved ${srcCls}"><strong>${escHtml(cell.resolved || t('merge.emptyValue'))}</strong><span class="src">${src}</span></span>`;
  }

  let btns = "";
  const canOverride = ["conflict", "auto_mine", "auto_theirs", "auto_both"].includes(cell.status);
  if (canOverride) {
    const sel = (choice) => {
      let v = "";
      if (choice === "mine") v = cell.mine;
      else if (choice === "theirs") v = cell.theirs;
      else if (choice === "base") v = cell.base;
      return cell.resolved === v ? " selected" : "";
    };
    const customSel = (cell.resolved !== null && cell.resolved !== cell.mine && cell.resolved !== cell.theirs && cell.resolved !== cell.base) ? " selected" : "";
    btns = `<div class="merge-resolve-btns">
      <button class="btn-mine${sel("mine")}" onclick="setCellChoice(${sk},${rk},'${col}','mine')" title="${t('merge.cellBtn.keepMineTitle')}">${t('merge.cellBtn.keepMine')}</button>
      <button class="btn-theirs${sel("theirs")}" onclick="setCellChoice(${sk},${rk},'${col}','theirs')" title="${t('merge.cellBtn.useTheirsTitle')}">${t('merge.cellBtn.useTheirs')}</button>
      <button class="btn-custom${customSel}" onclick="setCellChoice(${sk},${rk},'${col}','custom')" title="${t('merge.cellBtn.customTitle')}">${t('merge.cellBtn.custom')}</button>
    </div>`;
  }

  const cls = ["merge-cell"];
  if (cell.status === "conflict") cls.push("conflict");
  if (cell.status === "auto_mine") cls.push("auto-mine");
  if (cell.status === "auto_theirs") cls.push("auto-theirs");
  if (cell.status === "auto_both") cls.push("auto-both");
  if (isResolved && cell.status === "conflict") cls.push("resolved");

  // 紧凑布局：标题行 + 三方对比内联 + 决议/按钮
  return `<div class="${cls.join(" ")}">
    <div class="merge-cell-header">
      <span class="col-name">${escHtml(header)}</span>
      <span class="col-letter">${col}</span>
      <span class="cell-status">${statusLabel}</span>
      ${resolvedDisplay}
    </div>
    <div class="merge-cell-sides">
      <div class="side base"><span class="side-label">BASE</span><span class="side-value">${escHtml(cell.base || "—")}</span></div>
      <div class="side mine ${cell.mine !== cell.base ? "changed" : ""}"><span class="side-label">${t('merge.side.mine')}</span><span class="side-value">${escHtml(cell.mine || "—")}</span></div>
      <div class="side theirs ${cell.theirs !== cell.base ? "changed" : ""}"><span class="side-label">${t('merge.side.theirs')}</span><span class="side-value">${escHtml(cell.theirs || "—")}</span></div>
    </div>
    ${btns}
  </div>`;
}

async function openMergeFromConflict(fname) {
  // 兼容旧入口（如果还有从外部调用），直接打开 merge 视图
  closeUpdateModal();
  state.mergeFromSvnConflict = true;
  await setMode("merge");
  selectFile(fname);
}

// ── i18n: full UI re-render ──

// ── Settings modal ──

function openSettings() {
  const hr = (state.config && state.config.header_row) || 1;
  const ver = (state.config && state.config.version) || "?";
  const overlay = document.createElement("div");
  overlay.className = "update-modal-overlay";
  overlay.id = "settingsOverlay";
  overlay.innerHTML = `<div class="update-modal settings-modal">
    <h3>${t('settings.title')}</h3>
    <div class="settings-form-group">
      <label>${t('settings.headerRow')}</label>
      <input type="number" id="settingsHeaderRow" min="1" value="${hr}">
      <div class="hint">${t('settings.headerRowHint')}</div>
    </div>
    <div class="settings-form-group update-section">
      <label>${t('update.section')}</label>
      <div class="update-row">
        <span class="update-current">${t('update.current')}: v${escHtml(ver)}</span>
        <button class="update-check-btn" id="updateCheckBtn" onclick="checkUpdateManual()">${t('update.checkBtn')}</button>
      </div>
      <div id="updateStatus"></div>
    </div>
    <div class="modal-footer">
      <button onclick="closeSettings()">${t('settings.cancel')}</button>
      <button class="primary" onclick="saveSettings()">${t('settings.save')}</button>
    </div>
  </div>`;
  document.body.appendChild(overlay);
  document.getElementById("settingsHeaderRow").focus();
  renderUpdateStatus();
}

function closeSettings() {
  const overlay = document.getElementById("settingsOverlay");
  if (overlay) overlay.remove();
}

async function saveSettings() {
  const input = document.getElementById("settingsHeaderRow");
  const val = parseInt(input.value, 10);
  if (isNaN(val) || val < 1) {
    alert(t('settings.invalidInput'));
    return;
  }
  try {
    await api("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ header_row: val }),
    });
    state.config.header_row = val;
    closeSettings();

    if (state.mode === "local" && state.diff && !state.diff.error) {
      doDiffLocal();
    } else if (state.mode === "revision" && state.diff && !state.diff.error) {
      doDiffRevision();
    } else if (state.mode === "browse" && state.diff && state.diff.browse) {
      doBrowse();
    } else if (state.mode === "overview" && state.overviewFiles) {
      doOverview();
    } else if (state.mode === "merge" && state.mergeData) {
      doMergePreview();
    }
  } catch (e) {
    alert(t('error.connection', e.message));
  }
}

// ── In-app update ──

function _setUpdateDot(visible) {
  const dot = document.getElementById("updateDot");
  if (!dot) return;
  dot.hidden = !visible;
  const btn = document.getElementById("settingsBtn");
  if (btn) btn.title = visible ? t('update.badgeTitle') : t('settings.title');
}

async function checkUpdateSilent() {
  try {
    const info = await api("/api/update/check");
    state.updateInfo = info;
    _setUpdateDot(!!info.has_update);
  } catch (_) { /* silent: never disturb the user */ }
}

async function checkUpdateManual() {
  const btn = document.getElementById("updateCheckBtn");
  if (btn) { btn.disabled = true; btn.textContent = t('update.checking'); }
  try {
    state.updateInfo = await api("/api/update/check?force=1");
    _setUpdateDot(!!state.updateInfo.has_update);
    renderUpdateStatus();
  } catch (e) {
    const box = document.getElementById("updateStatus");
    if (box) box.innerHTML = `<div class="update-msg error">${escHtml(t('update.checkFailed', e.message))}</div>`;
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = t('update.checkBtn'); }
  }
}

function renderUpdateStatus() {
  const box = document.getElementById("updateStatus");
  if (!box) return;
  const info = state.updateInfo;
  if (!info) { box.innerHTML = ""; return; }
  if (!info.has_update) {
    box.innerHTML = `<div class="update-msg ok">${escHtml(t('update.upToDate'))} (v${escHtml(info.latest || info.current)})</div>`;
    return;
  }
  const links = `<a href="${escHtml(info.html_url)}" target="_blank" rel="noopener">${t('update.openRelease')}</a>
    <a href="${escHtml(info.proxy_page_url || info.html_url)}" target="_blank" rel="noopener">(${t('update.proxyLink')})</a>`;
  let action;
  if (!info.is_frozen) {
    action = `<div class="update-msg">${escHtml(t('update.sourceMode'))}</div><div class="update-links">${links}</div>`;
  } else if (!info.asset_url) {
    action = `<div class="update-msg">${escHtml(t('update.noAsset'))}</div><div class="update-links">${links}</div>`;
  } else {
    action = `<button class="update-now-btn" id="updateNowBtn" onclick="startUpdateDownload()">${t('update.updateNow')}</button>
      <div class="update-links">${links}</div>`;
  }
  const notes = (info.notes || "").trim();
  box.innerHTML = `<div class="update-msg new">${escHtml(t('update.newVersion', info.latest))}</div>
    ${notes ? `<div class="update-notes">${escHtml(notes)}</div>` : ""}
    ${action}
    <div id="updateProgress"></div>`;
}

async function startUpdateDownload() {
  if (state.updateBusy) return;
  state.updateBusy = true;
  const btn = document.getElementById("updateNowBtn");
  if (btn) btn.disabled = true;
  try {
    await api("/api/update/download", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ asset_url: state.updateInfo.asset_url }),
    });
    _pollUpdateProgress();
  } catch (e) {
    state.updateBusy = false;
    if (btn) btn.disabled = false;
    _renderUpdateProgress(`<div class="update-msg error">${escHtml(t('update.downloadFailed', e.message))}</div>`);
  }
}

function _renderUpdateProgress(html) {
  const el = document.getElementById("updateProgress");
  if (el) el.innerHTML = html;
}

function _pollUpdateProgress() {
  const timer = setInterval(async () => {
    let p;
    try {
      p = await api("/api/update/progress");
    } catch (_) { return; }
    if (p.status === "downloading") {
      const pct = p.percent || 0;
      _renderUpdateProgress(`<div class="update-progress-bar"><div class="fill" style="width:${pct}%"></div></div>
        <div class="update-msg">${escHtml(t('update.downloading', pct))}${p.total ? ` (${formatSize(p.downloaded)} / ${formatSize(p.total)})` : ""}</div>`);
    } else if (p.status === "ready") {
      clearInterval(timer);
      _renderUpdateProgress(`<div class="update-progress-bar"><div class="fill" style="width:100%"></div></div>
        <div class="update-msg">${escHtml(t('update.restarting'))}</div>`);
      _applyUpdateAndRestart();
    } else if (p.status === "error") {
      clearInterval(timer);
      state.updateBusy = false;
      const btn = document.getElementById("updateNowBtn");
      if (btn) btn.disabled = false;
      _renderUpdateProgress(`<div class="update-msg error">${escHtml(t('update.downloadFailed', p.error || "?"))}</div>`);
    }
  }, 500);
}

async function _applyUpdateAndRestart() {
  const oldVersion = (state.config && state.config.version) || "";
  try {
    await api("/api/update/apply", { method: "POST" });
  } catch (e) {
    state.updateBusy = false;
    _renderUpdateProgress(`<div class="update-msg error">${escHtml(t('update.applyFailed', e.message))}</div>`);
    return;
  }
  // Server exits, the helper script swaps the exe and relaunches it.
  // Poll /api/config until the new version answers, then reload the page.
  const started = Date.now();
  const timer = setInterval(async () => {
    if (Date.now() - started > 120000) {
      clearInterval(timer);
      state.updateBusy = false;
      _renderUpdateProgress(`<div class="update-msg error">${escHtml(t('update.applyFailed', "timeout"))}</div>`);
      return;
    }
    try {
      const cfg = await fetch(`${API}/api/config`).then(r => r.json());
      if (cfg.version && cfg.version !== oldVersion) {
        clearInterval(timer);
        location.reload();
      }
    } catch (_) { /* server restarting, keep polling */ }
  }, 1000);
}

function reRenderAll() {
  I18N.applyDOMTexts();
  renderHeader();
  renderFileList();
  renderToolbar();
  renderContent();
  const toggle = document.getElementById("langToggle");
  if (toggle) toggle.textContent = I18N.current === 'zh' ? 'EN' : '中';
}

// ── Event bindings ──

document.addEventListener("DOMContentLoaded", () => {
  I18N.init();
  const langBtn = document.getElementById("langToggle");
  langBtn.textContent = I18N.current === 'zh' ? 'EN' : '中';
  langBtn.addEventListener("click", () => {
    I18N.setLocale(I18N.current === 'zh' ? 'en' : 'zh');
  });
  I18N.applyDOMTexts();

  document.querySelectorAll(".mode-tab").forEach(tab => {
    tab.addEventListener("click", () => setMode(tab.dataset.mode));
  });

  document.getElementById("searchInput").addEventListener("input", (e) => {
    state.filterText = e.target.value;
    renderFileList();
  });

  document.getElementById("filterModified").addEventListener("click", (e) => {
    state.showOnlyModified = !state.showOnlyModified;
    e.target.classList.toggle("active", state.showOnlyModified);
    renderFileList();
  });

  document.getElementById("workspaceSelect").addEventListener("change", (e) => {
    switchWorkspace(parseInt(e.target.value));
  });

  document.getElementById("wsAddBtn").addEventListener("click", addWorkspace);
  document.getElementById("wsRemoveBtn").addEventListener("click", removeWorkspace);
  document.getElementById("wsOpenBtn").addEventListener("click", openWorkspaceDir);
  document.getElementById("settingsBtn").addEventListener("click", openSettings);

  init();

  let _refreshCycle = 0;
  setInterval(async () => {
    _refreshCycle++;
    if (_refreshCycle % 4 === 0 && state.config && state.config.svn_available && !state.loading) {
      loadModifiedFiles();
    }
    if (_refreshCycle % 10 === 0 && state.config && state.config.svn_available && !state.loading) {
      loadModifiedClassify();
      // 30 秒在 merge 模式也刷一次 SVN 冲突列表，捕获用户在命令行 svn resolve
      // 之后的状态变化（否则列表会停在 setMode 时拉的快照上）。
      if (state.mode === "merge") loadConflictedFiles();
    }
    if (!state.selectedFile || state.loading) return;
    if (state.mode !== "local" && state.mode !== "browse") return;
    try {
      const data = await fetch(`${API}/api/file-mtime?file=${encodeURIComponent(state.selectedFile)}`).then(r => r.json());
      const mtime = data.mtime || 0;
      if (_lastMtime && mtime && mtime !== _lastMtime) {
        if (state.mode === "local") doDiffLocal();
        else if (state.mode === "browse") doBrowse();
        loadFiles();
        loadModifiedFiles();
      }
      _lastMtime = mtime;
    } catch (_) {}
  }, 3000);
});

/* ═══════════════════════════════════════════════════════════════
   Subtitle Translator — 前端逻辑（v3 重构）
   保留原单文件版全部业务行为；所有 API 路径与数据流不变。
   ═══════════════════════════════════════════════════════════════ */
const API = '/api';
const $ = id => document.getElementById(id);
let curPath = '/', selVideo = null, selTrack = null, detectedAnime = null, timer = null, curSort = 'name';

// ═══ 文件浏览 ═══
function sortBrowse(s) {
  curSort = s;
  document.querySelectorAll('.sort-btn').forEach(b => b.classList.toggle('active', b.dataset.s === s));
  browse(curPath, true);
}

async function browse(p, keep) {
  if (!keep) curPath = p;
  $('path-inp').value = '';
  try {
    const r = await fetch(`${API}/browse?path=${encodeURIComponent(p)}&sort=${curSort}`);
    const d = await r.json();
    if (d.error) { toast(d.error, 'err'); return; }
    let h = `<div class="f-item" onclick="browse('${escArg(up(p))}')"><div class="icon">..</div><div class="name">..</div></div>`;
    for (const e of d.entries) {
      let ic = e.is_video ? '🎬' : (e.is_srt ? '📝' : (e.is_dir ? '📁' : '📄'));
      let sel = e.path === selVideo ? ' sel' : '';
      let tag = e.is_video ? '<span class="tag">视频</span>' : (e.is_srt ? '<span class="tag green">字幕</span>' : '');
      let time = e.mtime_str ? `<span class="meta">${e.mtime_str}</span>` : '';
      h += `<div class="f-item${sel}" onclick="${e.is_dir ? `browse('${escArg(e.path)}')` : e.is_srt ? `openSrtFile('${escArg(e.path)}')` : `selectVideo('${escArg(e.path)}')`}"><div class="icon">${ic}</div><div class="name">${esc(e.name)}</div>${tag}${time}</div>`;
    }
    $('file-list').innerHTML = h;
  } catch (e) { toast('加载失败', 'err'); console.error(e); }
}
function up(p) { let a = p.split('/').filter(Boolean); a.pop(); return '/' + a.join('/'); }

function loadPath() {
  let p = $('path-inp').value.trim();
  if (!p) return;
  if (p.match(/\.(mkv|mp4|avi|mov|ts)$/i) || !p.startsWith('/')) { selVideo = p; loadTracks(p); }
  else browse(p);
}

async function openSrtFile(p) {
  switchTab('samples');
  $('panel-samples').innerHTML = emptyState('加载中...', '');
  try {
    let r = await fetch(`${API}/subtitle_read?path=${encodeURIComponent(p)}`);
    let d = await r.json();
    if (!d.entries) { toast('No entries', 'err'); return; }
    let nm = p.split('/').pop();
    window._outPath = p;
    let h = `<div class="panel-toolbar"><span class="hint">${esc(nm)} · ${d.total}条</span><div class="ops">
      <button class="btn primary sm" onclick="saveEdits('${escArg(p)}')">保存</button>
      <button class="btn sm" onclick="downloadBilingual('${escArg(p)}')">双语字幕</button>
      <a class="btn sm" href="${API}/download?path=${encodeURIComponent(p)}" download>下载</a>
      <button class="btn sm" onclick="showSearchReplace()" id="btn-search">搜索替换</button></div></div>`;
    h += `<div class="samples-grid">`;
    window._origSamples = d.entries.map((e, i) => ({ index: i, en: "", zh: e.text }));
    d.entries.forEach((e, i) => {
      h += `<div class="sample-card" style="padding:10px 14px"><div class="idx">${i}</div><div class="zh-cell"><input class="edit-input" data-idx="${i}" value="${esc(e.text)}"></div></div>`;
    });
    h += `</div>`;
    $('panel-samples').innerHTML = h;
  } catch (e) { toast(e.message, 'err'); }
}

async function selectVideo(p) {
  selVideo = p;
  $('path-inp').value = p;
  await browse(curPath, true);
  let base = p.replace(/\.[^.]+$/, '');
  let chiPath = base + '.chi.srt';
  try {
    let r = await fetch(`${API}/subtitle_read?path=${encodeURIComponent(chiPath)}`);
    if (r.ok) {
      let d = await r.json();
      if (d.entries && d.entries.length > 0) {
        try { let tr = await fetch(API + '/tracks?path=' + encodeURIComponent(p)); let td = await tr.json(); detectedAnime = td.anime_name || detectedAnime; } catch (e) {}
        openHistoryEditor(chiPath, detectedAnime || '');
        return;
      }
    }
  } catch (e) {}
  loadTracks(p);
}

// ═══ 字幕轨 ═══
async function loadTracks(p) {
  selVideo = p; selTrack = null;
  window._currentGuide = ''; window._guideShown = false;
  $('panel-guide').innerHTML = `<div class="empty"><div class="icon-box">…</div><h3>翻译指南</h3><p>选择视频后可手动生成指南</p><button id="btn-gen-guide" class="btn" style="margin-top:16px;border-color:var(--accent);color:var(--accent)" onclick="generateGuideManual()">生成指南</button></div>`;
  try {
    let r = await fetch(`${API}/tracks?path=${encodeURIComponent(p)}`);
    if (!r.ok) {
      let e = await r.json().catch(() => ({}));
      $('panel-tracks').innerHTML = emptyState('错误', esc(e.detail || `HTTP ${r.status}`), '❌');
      if (r.status === 400) openSettings();
      return;
    }
    let d = await r.json();
    detectedAnime = d.anime_name;
    $('main-title').textContent = d.filename;
    $('main-sub').textContent = detectedAnime ? `自动识别: ${detectedAnime}` : '未识别到番剧名';
    $('btn-start').disabled = false;
    if (detectedAnime) loadCachedGuideOnly(detectedAnime);
    if (!d.tracks.length) { $('panel-tracks').innerHTML = emptyState('无字幕轨', '未找到任何字幕轨', '⚠️'); return; }
    let eng = d.tracks.filter(t => ['eng', 'en', 'en-US', 'en-GB'].includes(t.language));
    let h = '<div class="track-list">';
    for (let t of d.tracks) {
      let isE = eng.includes(t);
      h += `<div class="track-card${isE && !selTrack ? ' sel' : ''}" onclick="pickTrack(${t.index})"><span class="lang">${t.language}</span><span class="title">${esc(t.title || '(无标题)')}${isE ? ' ⭐推荐' : ''}</span><span class="idx">#${t.index}</span></div>`;
    }
    h += '</div>';
    $('panel-tracks').innerHTML = h;
    if (eng.length) selTrack = eng[0].index; else if (d.tracks.length) selTrack = d.tracks[0].index;
    switchTab('tracks');
  } catch (e) { $('panel-tracks').innerHTML = emptyState('错误', esc(e.message), '❌'); }
}
function pickTrack(i) {
  selTrack = i;
  document.querySelectorAll('.track-card').forEach(c => { c.classList.toggle('sel', c.querySelector('.idx')?.textContent.includes('#' + i)); });
}

// ═══ 翻译 ═══
async function startTranslate() {
  if (window._currentTaskId) { stopTranslation(); return; }
  if (!selVideo || selTrack == null) { toast('请先选择视频和字幕轨', 'err'); return; }
  window._guideShown = false;
  $('panel-tracks').innerHTML = emptyState('开始翻译...', '', '…');
  $('panel-samples').innerHTML = emptyState('等待翻译...', '', '…');
  switchTab('tracks');
  $('status-dot').className = 'status-dot active';
  $('btn-start').disabled = false;
  setStartBtnStop();
  window._translating = true;
  window._currentTaskId = null;
  try {
    let r = await fetch(`${API}/translate`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path: selVideo, track_index: selTrack, anime_name: detectedAnime }) });
    if (!r.ok) { let e = await r.json(); toast(e.detail || '失败', 'err'); resetBtn(); return; }
    let d = await r.json();
    window._currentTaskId = d.task_id;
    poll(d.task_id);
  } catch (e) { toast('失败: ' + e.message, 'err'); resetBtn(); }
}

function setStartBtnStop() {
  const b = $('btn-start');
  b.textContent = '■ 停止';
  b.classList.remove('primary');
  b.classList.add('danger');
}
function resetBtn() {
  const b = $('btn-start');
  b.disabled = false;
  b.classList.remove('danger');
  b.classList.add('primary');
  b.textContent = '翻译';
  window._translating = false;
  window._currentTaskId = null;
  timer = null;
}

function poll(tid) {
  if (timer) clearInterval(timer);
  timer = setInterval(async () => {
    try {
      let r = await fetch(`${API}/task/${tid}`);
      let t = await r.json();
      var pct = t.total ? Math.round(t.progress / t.total * 100) : 0;
      var combined = '<div class="prog-wrap"><div class="prog-head"><span class="label">' + esc(t.message) + '</span><span class="pct">' + pct + '%</span></div><div class="prog-bar"><div class="prog-fill" style="width:' + pct + '%"></div></div></div>';
      if (t.logs && t.logs.length) {
        var lh = '';
        t.logs.forEach(function (l) { var c = ''; if (l.indexOf('[error]') >= 0) c = ' err'; else if (l.indexOf('Complete') >= 0 || l.indexOf('Saved') >= 0 || l.indexOf('done') >= 0 || l.indexOf('Cached') >= 0) c = ' ok'; lh += '<div class="log-entry' + c + '">' + esc(l) + '</div>'; });
        combined += '<div class="log-box" style="max-height:300px;margin-top:12px">' + lh + '</div>';
      }
      var _lb = document.querySelector('#panel-tracks .log-box');
      var _pos = _lb ? _lb.scrollTop : 0;
      var _atBottom = _lb && (_lb.scrollHeight - _lb.scrollTop - _lb.clientHeight < 50);
      $('panel-tracks').innerHTML = combined;
      var _nb = document.querySelector('#panel-tracks .log-box');
      if (_nb) { _nb.scrollTop = _atBottom ? _nb.scrollHeight : _pos; }
      if (t.state === 'translating' || t.state === 'analyzing' || t.state === 'extracting') { setLogBadge(true); }
      if (t.guide) {
        $('panel-guide').innerHTML = `<div class="guide-box">${esc(t.guide).replace(/(\d+)\./g, '<b>$1.</b>').replace(/\n/g, '<br>')}</div>`;
        if (!window._guideShown) { window._guideShown = true; switchTab('guide'); }
      }
      if (t.samples?.length) {
        window._origSamples = JSON.parse(JSON.stringify(t.samples));
        let outPath = t.output || '';
        let h = `<div class="panel-toolbar">
          <span class="hint">共 ${t.samples.length} 条字幕</span>
          <div class="ops">
            <button class="btn primary sm" onclick="saveEdits('${escArg(outPath)}')">保存修改</button>
            <button class="btn sm" onclick="refineGuide()">微调指南</button>
            <a class="btn sm" href="${API}/download?path=${encodeURIComponent(outPath)}" download>下载 .srt</a>
          </div>
        </div>`;
        h += `<div class="samples-grid">`;
        t.samples.forEach((s, i) => {
          h += `<div class="sample-card">
            <div class="idx">${i}</div>
            <div class="en">${esc(s.en || '')}</div>
            <div class="zh-cell"><input type="text" class="edit-input" data-idx="${i}" value="${esc(s.zh || '')}"></div>
          </div>`;
        });
        h += `</div>`;
        $('panel-samples').innerHTML = h;
        window._outPath = outPath;
      }
      if (t.state === 'done') { clearInterval(timer); resetBtn(); setLogBadge(false); switchTab('samples'); $('status-dot').className = 'status-dot'; loadHistory(); }
      if (t.state === 'cancelled') {
        clearInterval(timer); resetBtn(); setLogBadge(false); $('status-dot').className = 'status-dot'; toast('翻译已中断', 'err');
        $('panel-tracks').innerHTML = emptyState('翻译已中断', '用户手动停止了翻译', '■');
      }
      if (t.state === 'error') { clearInterval(timer); resetBtn(); setLogBadge(false); $('status-dot').className = 'status-dot'; toast(t.message, 'err'); }
    } catch (e) {}
  }, 1000);
}

// ═══ Tabs ═══
function switchTab(n) {
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.querySelector(`[data-tab="${n}"]`)?.classList.add('active');
  $('panel-' + n)?.classList.add('active');
  if (n === 'history') loadHistory();
  if (n === 'watch') loadWatch();
}

// ═══ 配置开关 ═══
async function toggleContext() {
  let v = $('ctx-switch').checked;
  try {
    await fetch(`${API}/config`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ use_context: v }) });
    toast(v ? '上下文模式已开启' : '上下文模式已关闭', 'ok');
  } catch (e) {}
}

// ═══ 设置 ═══
async function testWebhook() {
  let url = $('cfg-webhook').value.trim();
  let type = $('cfg-webhook-type').value;
  if (!url) { toast('请先填入 Webhook URL', 'err'); return; }
  let btn = event.target; btn.disabled = true; btn.textContent = '发送中...';
  try {
    let r = await fetch(API + '/webhook_test', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ url: url, type: type }) });
    let d = await r.json();
    if (d.ok) toast('测试消息已发送，请检查群聊', 'ok');
    else toast('发送失败: ' + (d.error || '未知错误'), 'err');
  } catch (e) { toast('网络错误: ' + e.message, 'err'); }
  finally { btn.disabled = false; btn.textContent = '测试通知'; }
}

async function openSettings() {
  $('settings-modal').classList.add('show');
  try {
    let r = await fetch(`${API}/config`);
    let c = await r.json();
    $('cfg-key').value = c.api_key || '';
    $('cfg-url').value = c.base_url || '';
    $('cfg-model').value = c.model || '';
    $('cfg-cmodel').value = c.context_model || '';
    $('cfg-batch').value = c.batch_size || 12;
    $('ctx-switch').checked = c.use_context || false;
    $('cfg-webhook').value = c.webhook_url || '';
    $('cfg-webhook-type').value = c.webhook_type || 'wecom';
  } catch (e) {}
}
function closeSettings() { $('settings-modal').classList.remove('show'); }
async function saveSettings() {
  let b = {
    api_key: $('cfg-key').value, base_url: $('cfg-url').value, model: $('cfg-model').value,
    context_model: $('cfg-cmodel').value, batch_size: parseInt($('cfg-batch').value),
    use_context: $('ctx-switch').checked, webhook_url: $('cfg-webhook').value, webhook_type: $('cfg-webhook-type').value
  };
  try {
    let r = await fetch(`${API}/config`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(b) });
    let d = await r.json();
    $('settings-msg').textContent = d.ok ? '已保存' : '失败';
    if (d.ok) setTimeout(closeSettings, 600);
  } catch (e) { $('settings-msg').textContent = '保存失败'; }
}

// ═══ 批量 ═══
function openBatch() { $('batch-modal').classList.add('show'); $('batch-path').value = selVideo ? selVideo.replace(/[^/]+$/, '') : curPath; }
function closeBatch() { $('batch-modal').classList.remove('show'); }
function batchSelectAll() { document.querySelectorAll('.batch-cb').forEach(function (c) { c.checked = true; }); }
function batchDeselectAll() { document.querySelectorAll('.batch-cb').forEach(function (c) { c.checked = false; }); }
function batchToggle() { document.querySelectorAll('.batch-cb').forEach(function (c) { c.checked = !c.checked; }); }

async function scanBatch() {
  let p = $('batch-path').value.trim();
  if (!p) return;
  $('batch-list').innerHTML = '扫描中...';
  $('batch-go').disabled = true;
  try {
    let r = await fetch(`${API}/scan_folder?path=${encodeURIComponent(p)}`);
    let d = await r.json();
    var un = d.videos.filter(function (v) { return !v.translated; }).length;
    var h = '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px"><span style="font-size:12px;color:var(--text-3)">共 ' + d.total + ' 个，' + un + ' 个待翻译</span><div style="display:flex;gap:4px"><button class="btn sm" onclick="batchSelectAll()">全选</button><button class="btn sm" onclick="batchDeselectAll()">取消</button><button class="btn sm" onclick="batchToggle()">反选</button></div></div>';
    d.videos.forEach(function (v) { h += '<div class="batch-row"><input type="checkbox" class="batch-cb" data-path="' + esc(v.path) + '" ' + (v.translated ? '' : 'checked') + '><span class="name">' + esc(v.name) + '</span><span class="state ' + (v.translated ? 'done' : 'wait') + '">' + (v.translated ? '已翻译' : '待翻译') + '</span></div>'; });
    $('batch-list').innerHTML = h;
    $('batch-go').disabled = !un;
    $('batch-msg').textContent = '';
    var firstVid = d.videos[0];
    if (firstVid) {
      detectedAnime = firstVid.anime || '';
      var parts = firstVid.path.split('/');
      for (var pi = 0; pi < parts.length - 1; pi++) {
        if (/^Season|^S\d+/i.test(parts[pi])) { detectedAnime = parts[pi - 1]; break; }
      }
      if (detectedAnime) $('main-sub').textContent = '批量翻译 ' + un + ' 个 · ' + detectedAnime;
    }
    var firstUntrans = d.videos.find(function (v) { return !v.translated; });
    if (firstUntrans) {
      fetch(API + '/tracks?path=' + encodeURIComponent(firstUntrans.path)).then(function (tr) { return tr.json(); }).then(function (td) {
        var eng = td.tracks.filter(function (t) { return ['eng', 'en', 'en-US', 'en-GB'].indexOf(t.language) >= 0; });
        var sel = (eng.length ? eng[0] : td.tracks[0]) || {};
        window._batchTrackIdx = sel.index || 0;
        var trackInfo = '已选: <b>' + (sel.language || '').toUpperCase() + '</b> 轨 #' + sel.index;
        document.getElementById('batch-track-selector').innerHTML = trackInfo + ' <input type="number" id="batch-track-input" value="' + sel.index + '" min="0" max="20" style="width:50px;padding:3px 6px;border:1px solid var(--border);border-radius:4px;font-size:11px;background:var(--surface-2);color:var(--text)"> <span style="font-size:10px;color:var(--text-3)">可手动修改</span>';
      }).catch(function (e) { document.getElementById('batch-track-selector').innerHTML = '获取失败'; });
    }
  } catch (e) { $('batch-list').innerHTML = '扫描失败'; }
}

async function startBatch() {
  let p = $('batch-path').value.trim();
  if (!p) return;
  var cbs = document.querySelectorAll('.batch-cb:checked');
  if (!cbs.length) { toast('请至少选择一个视频', 'err'); return; }
  var paths = [];
  cbs.forEach(function (cb) { paths.push(cb.dataset.path); });
  $('batch-go').disabled = true;
  $('batch-go').textContent = '提交中...';
  window._batchPaths = paths;
  var trackInput = document.getElementById('batch-track-input');
  var trackIdx = trackInput ? parseInt(trackInput.value) : (window._batchTrackIdx || 0);
  try {
    let r = await fetch(`${API}/translate_batch`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path: p, videos: paths, track_index: trackIdx }) });
    let d = await r.json();
    $('batch-msg').textContent = d.message;
    toast(d.message, 'ok');
    closeBatch();
    $('panel-tracks').innerHTML = '<div class="prog-wrap"><div class="prog-head"><span class="label">批量任务已启动...</span><span class="pct">0%</span></div><div class="prog-bar"><div class="prog-fill" style="width:0%"></div></div></div>';
    switchTab('tracks');
    $('status-dot').className = 'status-dot active';
    pollBatchTasks(d.tasks || []);
  } catch (e) { $('batch-go').disabled = false; $('batch-go').textContent = '开始批量翻译'; }
}

// ═══ 批量轮询 ═══
async function pollBatchTasks(tids) {
  if (!tids || !tids.length) { setLogBadge(false); return; }
  window._batchTids = tids;
  var pollId = setInterval(async function () {
    window._batchPollId = pollId;
    try {
      var done = 0, err = 0, total = tids.length, rows = '', logs = [], progP = 0, progT = 0;
      function progPush(p, t) { progP += p; progT += t; }
      for (var i = 0; i < tids.length; i++) {
        try {
          var tr = await fetch(API + '/task/' + tids[i]);
          if (!tr.ok) continue;
          var task = await tr.json();
          if (task.state === 'done') done++;
          if (task.state === 'error') err++;
          var p = task.progress || 0; var t = task.total || 0;
          if (p > 0 && t > 0) progPush(p, t);
          var name = (task.video || '').split('/').pop() || 'task' + i;
          var icon = task.state === 'done' ? '✓' : task.state === 'error' ? '✗' : task.state === 'translating' ? '►' : task.state === 'cancelled' ? '■' : '?';
          var color = task.state === 'done' ? 'color:var(--green)' : task.state === 'error' ? 'color:var(--red)' : task.state === 'cancelled' ? 'color:var(--yellow)' : 'color:var(--text-2)';
          rows += '<div style="font-size:11px;padding:4px 0;' + color + '"><span style="margin-right:8px">' + icon + '</span><span' + (task.output ? ' onclick="openHistoryEditor(\'' + escArg(task.output) + '\',\'' + escArg(name) + '\')" style="cursor:pointer;text-decoration:underline"' : '') + '>' + esc(name) + '</span></div>';
          if (task.logs) { for (var li = 0; li < task.logs.length; li++) { logs.push(task.logs[li]); } }
          if ((!task.logs || !task.logs.length) && task.message && task.message !== 'Queued...') { logs.push('[msg] ' + task.message); }
        } catch (e) {}
      }
      var allDone = (done + err >= total);
      var pct = (progT > 0 ? Math.round(progP / progT * 100) : (total > 0 ? Math.round((done + err) / total * 100) : 0));
      $('panel-tracks').innerHTML = '<div class="prog-wrap"><div class="prog-head"><span class="label">批量翻译 ' + (done + err) + '/' + total + ' (' + done + '完成 ' + err + '失败)' + (allDone ? '' : '<button class="btn sm" style="margin-left:8px;border-color:var(--red);color:var(--red)" onclick="stopBatchTasks()">停止</button>') + '</span><span class="pct">' + pct + '%</span></div><div class="prog-bar"><div class="prog-fill" style="width:' + pct + '%"></div></div></div>' + rows;
      var logHtml = '<div class="log-box" style="max-height:300px;margin-top:12px">';
      if (logs.length == 0) logHtml += '<div class="log-entry">任务进行中...</div>';
      for (var li = 0; li < logs.length && li < 200; li++) {
        var l = logs[li];
        var cls = '';
        if (l.indexOf('[error]') >= 0) cls = ' err';
        else if (l.indexOf('Complete') >= 0 || l.indexOf('Saved') >= 0 || l.indexOf('done') >= 0) cls = ' ok';
        logHtml += '<div class="log-entry' + cls + '">' + esc(l) + '</div>';
      }
      logHtml += '</div>';
      var existingLog = document.querySelector('#panel-tracks .log-box');
      if (existingLog) existingLog.outerHTML = logHtml;
      else document.getElementById('panel-tracks').insertAdjacentHTML('beforeend', logHtml);
      if (detectedAnime && !document.getElementById('guide-content')) { loadExistingGuide(detectedAnime); }
      if (allDone) {
        clearInterval(pollId);
        setLogBadge(false);
        $('status-dot').className = 'status-dot';
        var samplesHtml = '';
        if (rows) samplesHtml = '<div style="margin-bottom:12px"><span class="hint" style="font-size:13px;color:var(--text-2)">已完成 ' + done + '/' + total + '</span></div>' + rows;
        $('panel-samples').innerHTML = samplesHtml;
        toast('批量完成: ' + done + '/' + total, 'ok');
        loadHistory();
      }
    } catch (e) {}
  }, 3000);
}

async function stopBatchTasks() {
  var tids = window._batchTids || [];
  if (!tids.length) { toast('没有可取消的任务', 'err'); return; }
  var count = 0;
  for (var i = 0; i < tids.length; i++) {
    try { var r = await fetch(API + '/task/' + tids[i] + '/cancel', { method: 'POST' }); if (r.ok) count++; } catch (e) {}
  }
  toast('已请求取消 ' + count + ' 个任务', 'ok');
  clearInterval(window._batchPollId || 0);
  setLogBadge(false);
  $('status-dot').className = 'status-dot';
}

// ═══ 订阅 ═══
async function loadWatch() {
  try {
    let r = await fetch(`${API}/watch`);
    let d = await r.json();
    let fs = d.folders || {};
    if (!Object.keys(fs).length) {
      $('panel-watch').innerHTML = `<div class="empty"><div class="icon-box">🔔</div><h3>订阅文件夹</h3><p>自动翻译新集数<br><small>进入文件夹后点击下方按钮添加</small></p><button class="btn primary" style="margin-top:16px" onclick="addWatch()">添加订阅</button></div>`;
      return;
    }
    let h = '<div style="margin-bottom:12px"><button class="btn primary sm" onclick="addWatch()">添加订阅</button></div>';
    for (let [p, i] of Object.entries(fs)) {
      h += `<div class="watch-item"><span class="path">${esc(p)}</span><span class="status">${i.enabled ? '启用中' : '已暂停'}</span><button class="btn sm" onclick="toggleWatch('${escArg(p)}')">${i.enabled ? '暂停' : '启用'}</button><button class="btn sm" style="color:var(--red);border-color:var(--red-bg)" onclick="removeWatch('${escArg(p)}')">删除</button></div>`;
    }
    $('panel-watch').innerHTML = h;
    var watchTasks = [];
    try {
      var atr = await fetch(API + '/tasks_active');
      var atd = await atr.json();
      watchTasks = (atd.tasks || []).filter(function (t) { return t.source === 'watch'; });
    } catch (e) {}
    if (watchTasks.length) {
      var wtHtml = '<div style="margin-top:16px"><div style="font-size:12px;font-weight:600;color:var(--accent);margin-bottom:8px">▶ 订阅任务运行中</div>';
      watchTasks.forEach(function (t) {
        var pct = t.total > 0 ? Math.round(t.progress / t.total * 100) : 0;
        wtHtml += '<div class="watch-item" onclick="trackTaskProgress(\'' + escArg(t.task_id) + '\')" style="cursor:pointer"><span class="path">' + esc((t.video || '').split('/').pop()) + '</span><span class="status" style="color:var(--accent)">' + (t.state === 'pending' ? '排队' : pct + '%') + '</span></div>';
      });
      wtHtml += '</div>';
      $('panel-watch').insertAdjacentHTML('beforeend', wtHtml);
    }
  } catch (e) { $('panel-watch').innerHTML = emptyState('错误', '加载失败', '❌'); }
}

// ═══ 订阅任务进度 ═══
var _watchProgressTimer = null;
function trackTaskProgress(taskId) {
  if (!taskId) return;
  if (_watchProgressTimer) { clearInterval(_watchProgressTimer); _watchProgressTimer = null; }
  switchTab('tracks');
  $('panel-tracks').innerHTML = emptyState('加载中...', '', '…');
  _watchProgressTimer = setInterval(async function () {
    try {
      var r = await fetch(API + '/task/' + taskId);
      if (!r.ok) {
        clearInterval(_watchProgressTimer); _watchProgressTimer = null;
        $('panel-tracks').innerHTML = emptyState('任务已结束', '', '❌');
        return;
      }
      var t = await r.json();
      var pct = t.total > 0 ? Math.round(t.progress / t.total * 100) : 0;
      $('panel-tracks').innerHTML = '<div class="prog-wrap"><div class="prog-head"><span class="label">' + (t.message || '') + '</span><span class="pct">' + pct + '%</span></div><div class="prog-bar"><div class="prog-fill" style="width:' + pct + '%"></div></div></div>';
      if (t.logs && t.logs.length) {
        var lh = '<div class="log-box" style="max-height:300px;margin-top:12px">';
        (t.logs.slice(-100)).forEach(function (l) {
          var cls = l.indexOf('[error]') >= 0 ? ' err' : (l.indexOf('Complete') >= 0 || l.indexOf('Saved') >= 0 || l.indexOf('done') >= 0) ? ' ok' : '';
          lh += '<div class="log-entry' + cls + '">' + esc(l) + '</div>';
        });
        lh += '</div>';
        var existingLog = document.querySelector('#panel-tracks .log-box');
        if (existingLog) existingLog.outerHTML = lh;
        else document.getElementById('panel-tracks').insertAdjacentHTML('beforeend', lh);
      }
      if (t.state === 'done' || t.state === 'error' || t.state === 'cancelled') {
        clearInterval(_watchProgressTimer); _watchProgressTimer = null;
        if (t.state === 'done') toast('已完成', 'ok');
      }
    } catch (e) {}
  }, 2000);
}
async function addWatch() {
  let p = curPath;
  if (p === '/') { toast('请先进入具体文件夹', 'err'); return; }
  try { await fetch(`${API}/watch/add?path=${encodeURIComponent(p)}`, { method: 'POST' }); toast('已订阅: ' + p, 'ok'); loadWatch(); } catch (e) {}
}
async function removeWatch(p) { await fetch(`${API}/watch/remove?path=${encodeURIComponent(p)}`, { method: 'POST' }); loadWatch(); }
async function toggleWatch(p) { await fetch(`${API}/watch/toggle?path=${encodeURIComponent(p)}`, { method: 'POST' }); loadWatch(); }

// ═══ 历史 ═══
async function loadHistory() {
  try {
    let r = await fetch(`${API}/history`);
    let d = await r.json();
    if (!d.history.length) { $('panel-history').innerHTML = emptyState('暂无记录', '翻译完成后会自动记录', '📋'); return; }
    let h = `<div class="panel-toolbar"><span class="hint">${d.history.length} 条记录</span><div class="ops"><button class="btn sm" style="color:var(--red);border-color:var(--red-bg)" onclick="clearHistory()">清空全部</button></div></div>`;
    d.history.forEach((e, i) => {
      let canOpen = e.state === 'done' && e.output;
      let click = canOpen ? ` onclick="openHistoryEditor('${escArg(e.output)}','${escArg(e.anime || e.video || '')}')"` : '';
      h += `<div class="hist-item"${click}${canOpen ? ' style="cursor:pointer"' : ''}><div style="min-width:0"><div class="vname">${esc(e.video || '')}</div><div class="meta">${e.time || ''} · ${e.anime || ''} · ${e.total || 0}条 · ${e.source || ''}</div></div><div style="display:flex;align-items:center;gap:8px;flex-shrink:0">${canOpen ? '<span style="font-size:10px;color:var(--accent)">点击编辑</span>' : ''}<span class="state ${e.state === 'done' ? 'done' : 'err'}">${e.state === 'done' ? '完成' : '失败'}</span><button class="icon-btn" onclick="event.stopPropagation();deleteHistory(${i})" title="删除此记录">🗑</button></div></div>`;
    });
    $('panel-history').innerHTML = h;
  } catch (e) { $('panel-history').innerHTML = emptyState('错误', '加载失败', '❌'); }
}

async function deleteHistory(idx) {
  try {
    let r = await fetch(API + '/history/delete', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ index: idx }) });
    let d = await r.json();
    if (d.ok) { toast('已删除', 'ok'); loadHistory(); } else { toast('删除失败', 'err'); }
  } catch (e) { toast('删除失败', 'err'); }
}
async function clearHistory() {
  if (!confirm('确认清空全部翻译历史？此操作不可恢复。')) return;
  try {
    let r = await fetch(API + '/history/clear', { method: 'POST' });
    let d = await r.json();
    if (d.ok) { toast('已清空全部记录', 'ok'); loadHistory(); }
  } catch (e) { toast('清空失败', 'err'); }
}

// ═══ 翻译控制 ═══
async function stopTranslation() {
  let tid = window._currentTaskId;
  if (!tid) { return; }
  try { await fetch(`${API}/task/${tid}/cancel`, { method: 'POST' }); toast('已请求中断', 'ok'); } catch (e) {}
  if (timer) { clearInterval(timer); timer = null; }
  resetBtn();
  $('status-dot').className = 'status-dot';
  setLogBadge(false);
}

// ═══ 双语 + 搜索替换 + 统计 ═══
async function downloadBilingual(path) {
  try {
    var r = await fetch(API + '/bilingual_srt?path=' + encodeURIComponent(path));
    if (!r.ok) { toast('双语字幕生成失败', 'err'); return; }
    var text = await r.text();
    var blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = path.split('/').pop().replace('.chi.srt', '.bilingual.srt');
    a.click();
    toast('双语字幕已下载', 'ok');
  } catch (e) { toast('失败: ' + e.message, 'err'); }
}

function showSearchReplace() {
  var h = '<div style="margin-bottom:12px;display:flex;gap:8px;align-items:center"><input type="text" id="sr-search" placeholder="搜索..." class="edit-input" style="flex:1"><input type="text" id="sr-replace" placeholder="替换为..." class="edit-input" style="flex:1"><button class="btn primary sm" onclick="doSearchReplace()">替换</button></div>';
  $('panel-samples').insertAdjacentHTML('afterbegin', h);
  document.getElementById('sr-search').focus();
}

async function doSearchReplace() {
  var search = document.getElementById('sr-search').value;
  var replace = document.getElementById('sr-replace').value;
  if (!search) { toast('请输入搜索内容', 'err'); return; }
  try {
    var r = await fetch(API + '/search_replace', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ srt_path: window._outPath, search: search, replace: replace }) });
    var d = await r.json();
    if (d.ok) {
      toast('已替换 ' + d.count + ' 处', 'ok');
      openHistoryEditor(window._outPath, detectedAnime || '');
    }
  } catch (e) { toast('替换失败: ' + e.message, 'err'); }
}

async function loadStats() {
  try {
    var r = await fetch(API + '/stats');
    var d = await r.json();
    var h = '<div style="padding:4px 0"><h3 style="margin-bottom:16px">翻译统计</h3>';
    h += '<div class="stat-grid">';
    h += '<div class="stat-card"><div class="num">' + d.total_tasks + '</div><div class="lbl">翻译任务</div></div>';
    h += '<div class="stat-card"><div class="num">' + d.total_lines + '</div><div class="lbl">翻译行数</div></div>';
    h += '<div class="stat-card"><div class="num">' + d.guide_cache_count + '</div><div class="lbl">缓存指南</div></div>';
    h += '<div class="stat-card"><div class="num">' + d.guide_cache_size + '</div><div class="lbl">指南字符</div></div>';
    h += '</div>';
    h += '<h4 style="margin-bottom:8px">番剧翻译次数</h4>';
    for (var k in d.animes) { h += '<div style="padding:6px 0;font-size:12px;color:var(--text-2);border-bottom:1px solid var(--border-soft)">' + esc(k) + ' <span style="color:var(--text-4)">×' + d.animes[k] + '</span></div>'; }
    h += '</div>';
    $('panel-samples').innerHTML = h;
  } catch (e) { toast('统计加载失败', 'err'); }
}

// ═══ 工具 ═══
function toast(m, c) {
  let e = document.createElement('div');
  e.className = `toast ${c || ''}`;
  e.textContent = m;
  $('toast-wrap').appendChild(e);
  setTimeout(() => e.remove(), 3000);
}
function esc(s) { return (s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;'); }
function escArg(s) { return esc(s).replace(/&#39;/g, "\\'"); }
function setLogBadge(show) { var b = document.getElementById('log-badge'); if (b) b.style.display = show ? 'block' : 'none'; }
function emptyState(title, sub, icon) {
  return `<div class="empty"><div class="icon-box">${icon || '…'}</div><h3>${title}</h3>${sub ? `<p>${sub}</p>` : ''}</div>`;
}

// ═══ 编辑器 ═══
async function editGuide() {
  let guideText = window._currentGuide || '';
  if (!guideText) { toast('没有指南内容', 'err'); return; }
  let gc = document.getElementById('guide-content');
  let isEditing = gc && gc.tagName === 'TEXTAREA';
  if (isEditing) {
    let newGuide = gc.value.trim();
    if (!newGuide) return;
    window._currentGuide = newGuide;
    try {
      await fetch(API + '/guide_refine', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ anime_name: detectedAnime || '', corrections: [], current_guide: newGuide }) });
    } catch (e) {}
    gc.outerHTML = `<div class="guide-box" id="guide-content">${newGuide.replace(/(\d+)\./g, '<b>$1.</b>').replace(/\n/g, '<br>')}</div>`;
    let btns = document.querySelectorAll('#panel-guide button');
    btns.forEach(b => { if (b.textContent.includes('保存')) b.textContent = '手动编辑'; });
    toast('指南已保存', 'ok');
  } else {
    let text = guideText;
    if (gc) gc.outerHTML = `<textarea id="guide-content" style="width:100%;min-height:300px;padding:16px;background:var(--surface-2);border:1px solid var(--accent);border-radius:var(--radius);font-family:var(--font);font-size:13px;color:var(--text);line-height:1.8;resize:vertical;outline:none">${esc(text)}</textarea>`;
    let btns = document.querySelectorAll('#panel-guide button');
    btns.forEach(b => { if (b.textContent.includes('手动编辑')) b.textContent = '保存指南'; });
    toast('编辑模式 - 修改后点保存', 'ok');
  }
}

async function openHistoryEditor(path, name) {
  if (!path) { toast('无字幕文件路径', 'err'); return; }
  switchTab('samples');
  $('panel-samples').innerHTML = emptyState('加载字幕...', '', '…');
  try {
    var enEntries = [];
    var engPath = path;
    if (engPath.endsWith('.chi.srt')) { engPath = engPath.slice(0, -8) + '.eng.srt'; }
    else if (engPath.endsWith('.srt') && !engPath.endsWith('.eng.srt')) { engPath = engPath.slice(0, -4) + '.eng.srt'; }
    try {
      var enResp = await fetch(API + '/subtitle_read?path=' + encodeURIComponent(engPath));
      if (enResp.ok) { var enData = await enResp.json(); enEntries = enData.entries || []; }
    } catch (e) {}
    let r = await fetch(`${API}/subtitle_read?path=${encodeURIComponent(path)}`);
    let d = await r.json();
    if (!d.entries) { toast('读取失败', 'err'); return; }
    detectedAnime = name || detectedAnime;
    window._outPath = path;
    let h = `<div class="panel-toolbar">
      <span class="hint">${esc(name)} · ${d.total}条字幕</span>
      <div class="ops">
        <button class="btn primary sm" onclick="saveEdits('${escArg(path)}')">保存修改</button>
        <button class="btn sm" onclick="refineGuide()" id="btn-refine">微调指南</button>
      </div>
    </div>`;
    h += `<div class="samples-grid">`;
    window._origSamples = d.entries.map(function (e, i) {
      var en = enEntries[i] ? enEntries[i].text : e.text;
      return { index: i, en: en, zh: e.text };
    });
    d.entries.forEach(function (e, i) {
      var en = (enEntries[i] ? esc(enEntries[i].text) : '(原英文未找到)');
      h += `<div class="sample-card">
        <div class="en">${en}</div>
        <div class="zh-cell"><input type="text" class="edit-input" data-idx="${i}" value="${esc(e.text)}"></div>
      </div>`;
    });
    h += `</div>`;
    $('panel-samples').innerHTML = h;
  } catch (e) { toast('加载失败: ' + e.message, 'err'); }
}

async function refineGuide() {
  if (!detectedAnime) { toast('未识别番剧名', 'err'); return; }
  let inputs = document.querySelectorAll('.edit-input');
  let corrections = [];
  let origSamples = window._origSamples || [];
  inputs.forEach(inp => {
    let idx = parseInt(inp.dataset.idx);
    let en = origSamples[idx]?.en || '';
    let oldZh = origSamples[idx]?.zh || '';
    let newZh = inp.value;
    if (newZh !== oldZh && en && newZh) { corrections.push({ en: en, old_zh: oldZh, new_zh: newZh }); }
  });
  if (!corrections.length) { toast('没有发现修改过的字幕', 'err'); return; }
  let refineBtn = document.getElementById('btn-refine');
  if (refineBtn) { refineBtn.disabled = true; refineBtn.textContent = '微调中...'; refineBtn.style.opacity = '0.5'; }
  $('panel-guide').innerHTML = `<div class="empty" style="padding:40px"><div class="icon-box" style="border-color:var(--accent)">…</div><h3>正在微调翻译指南...</h3><p>AI 分析了 ${corrections.length} 处修正，正在生成改进指南</p></div>`;
  switchTab('guide');
  try {
    let r = await fetch(API + '/guide_refine', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ anime_name: detectedAnime, corrections: corrections, current_guide: window._currentGuide || '' }) });
    let d = await r.json();
    if (d.ok) {
      window._currentGuide = d.guide;
      $('panel-guide').innerHTML = `<div class="panel-toolbar"><span class="hint">翻译指南（已微调 ${d.chars}字）</span><div class="ops"><button class="btn sm" onclick="editGuide()">手动编辑</button></div></div><div class="guide-box" id="guide-content">${d.guide.replace(/(\d+)\./g, '<b>$1.</b>').replace(/\n/g, '<br>')}</div>`;
      toast('指南已微调 (' + d.chars + '字)', 'ok');
    } else { toast('微调失败', 'err'); $('panel-guide').innerHTML = emptyState('微调失败', '', '❌'); }
  } catch (e) { toast('微调失败: ' + e.message, 'err'); $('panel-guide').innerHTML = emptyState('微调失败', esc(e.message), '❌'); }
  finally { if (refineBtn) { refineBtn.disabled = false; refineBtn.textContent = '微调指南'; refineBtn.style.opacity = '1'; } }
}

window._origSamples = [];
async function saveEdits(p) {
  p = p || window._outPath || '';
  if (!p) { toast('没有字幕文件路径', 'err'); return; }
  let inputs = document.querySelectorAll('.edit-input');
  let entries = [];
  inputs.forEach(inp => { entries.push({ index: parseInt(inp.dataset.idx), text: inp.value }); });
  if (!entries.length) { toast('没有可保存的内容', 'err'); return; }
  try {
    let r = await fetch(API + '/subtitle_save', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path: p, entries: entries }) });
    let d = await r.json();
    if (d.ok) { toast('已保存 ' + d.saved + ' 条修改', 'ok'); } else { toast('保存失败', 'err'); }
  } catch (e) { toast('保存失败: ' + e.message, 'err'); }
}

// ═══ 指南 ═══
function showGuidePanel(guide, label) {
  window._currentGuide = guide || '';
  $('panel-guide').innerHTML = `<div class="panel-toolbar"><span class="hint">翻译指南 · ${label}</span><div class="ops"><button class="btn sm" onclick="editGuide()">手动编辑</button></div></div><div class="guide-box" id="guide-content">${guide.replace(/(\d+)\./g, '<b>$1.</b>').replace(/\n/g, '<br>')}</div><div id="regen-btn-container" style="margin-top:16px"><button class="btn" style="border-color:var(--accent);color:var(--accent)" onclick="generateGuideManual()">重新生成指南</button></div>`;
}

async function loadCachedGuideOnly(animeName) {
  if (!animeName) return;
  try {
    var r = await fetch(API + '/guide_cache_get?anime=' + encodeURIComponent(animeName));
    var d = await r.json();
    if (d.ok && d.guide) {
      showGuidePanel(d.guide, '已缓存');
      var gbtn = document.getElementById('btn-gen-guide');
      if (gbtn) { gbtn.style.display = 'inline-block'; gbtn.textContent = '重新生成'; }
    } else {
      var gbtn = document.getElementById('btn-gen-guide');
      if (gbtn) { gbtn.style.display = 'inline-block'; gbtn.textContent = '生成指南'; }
      var msg = document.getElementById('guide-empty-msg');
      if (msg) msg.textContent = '检测到番剧: ' + animeName;
    }
    loadCachedGuideList();
  } catch (e) {}
}

async function loadExistingGuide(animeName) {
  window._guideLoading = false;
  if (!animeName) return;
  try {
    let r = await fetch(API + '/generate_guide', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path: selVideo || curPath, anime_name: animeName }) });
    let d = await r.json();
    if (d.ok && d.cached) {
      showGuidePanel(d.guide, '已缓存');
      let gbtn = document.getElementById('btn-gen-guide');
      if (gbtn) { gbtn.style.display = 'inline-block'; gbtn.textContent = '重新生成'; }
    } else {
      let gbtn = document.getElementById('btn-gen-guide');
      if (gbtn) { gbtn.style.display = 'inline-block'; gbtn.textContent = '生成指南'; }
      $('guide-empty-msg').textContent = '检测到番剧: ' + animeName;
    }
    loadCachedGuideList();
  } catch (e) {}
}

async function loadCachedGuideList() {
  if (detectedAnime && !document.getElementById('guide-content') && !window._guideLoading) {
    window._guideLoading = true;
    loadCachedGuideOnly(detectedAnime);
  }
  showRegenerateButton();
}
function showRegenerateButton() {
  var existing = document.getElementById('regen-btn-container');
  if (existing) existing.remove();
  var gc = document.getElementById('guide-content');
  if (!gc) return;
  var btn = document.createElement('div');
  btn.id = 'regen-btn-container';
  btn.style.marginTop = '16px';
  btn.innerHTML = '<button class="btn" style="border-color:var(--accent);color:var(--accent)" onclick="generateGuideManual()">重新生成指南</button>';
  gc.parentNode.insertBefore(btn, gc.nextSibling);
}

async function generateGuideManual() {
  if (!selVideo) { toast('请先选择视频', 'err'); return; }
  toast('正在重新生成指南（分析中，约需10-30秒）...');
  let gbtn = document.getElementById('btn-gen-guide');
  if (gbtn) { gbtn.disabled = true; gbtn.textContent = '生成中...'; gbtn.style.opacity = '0.5'; }
  try {
    let r = await fetch(API + '/generate_guide', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path: selVideo, anime_name: detectedAnime, force: true }) });
    let d = await r.json();
    if (d.ok) {
      showGuidePanel(d.guide, d.cached ? '缓存' : '新生成');
      switchTab('guide');
      toast(d.cached ? '已加载缓存指南' : '指南已生成', 'ok');
    } else { toast('生成失败', 'err'); }
  } catch (e) { toast('生成失败: ' + e.message, 'err'); }
  finally { if (gbtn) { gbtn.disabled = false; gbtn.textContent = '重新生成'; gbtn.style.opacity = '1'; } }
}

// ═══ 滚动 ═══
function scrollToTop() {
  let panel = document.querySelector('.panel.active');
  if (panel) panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
  document.querySelector('.content').scrollTop = 0;
}
document.addEventListener('DOMContentLoaded', function () {
  let contentEl = document.querySelector('.content');
  if (contentEl) contentEl.addEventListener('scroll', function () {
    let btn = document.getElementById('btn-top');
    if (btn) btn.style.display = contentEl.scrollTop > 300 ? 'flex' : 'none';
  });
});

// ═══ 主题 ═══
function setTheme(dark) {
  if (dark) { document.body.classList.remove('light'); localStorage.setItem('theme', 'dark'); }
  else { document.body.classList.add('light'); localStorage.setItem('theme', 'light'); }
  updateThemeIcons(dark);
}
function toggleTheme() {
  let isDark = !document.body.classList.contains('light');
  setTheme(!isDark);
}
function updateThemeIcons(dark) {
  let sun = document.getElementById('theme-icon-sun');
  let moon = document.getElementById('theme-icon-moon');
  if (sun) sun.style.display = dark ? 'none' : 'inline';
  if (moon) moon.style.display = dark ? 'inline' : 'none';
}

// ═══ 初始化 ═══
fetch(API + '/config').then(function (r) { return r.json(); }).then(function (c) {
  var ctxToggle = document.getElementById('ctx-switch');
  if (ctxToggle) ctxToggle.checked = c.use_context || false;
}).catch(function () {});

browse('/').then(function () {
  fetch(API + '/tasks_active').then(function (r) { return r.json(); }).then(function (d) {
    var tasks = d.tasks || [];
    if (tasks.length) {
      var t = tasks[0];
      window._currentTaskId = t.task_id;
      window._translating = true;
      $('status-dot').className = 'status-dot active';
      $('btn-start').disabled = false;
      setStartBtnStop();
      $('panel-tracks').innerHTML = '<div class="prog-wrap"><div class="prog-head"><span class="label">检测到正在运行的任务...</span><span class="pct">?</span></div><div class="prog-bar"><div class="prog-fill" style="width:5%"></div></div></div>';
      poll(t.task_id);
    }
  }).catch(function () {});
}).catch(function () {});

let savedTheme = localStorage.getItem('theme') || 'dark';
setTheme(savedTheme === 'light' ? false : true);
$('settings-modal').addEventListener('click', function (e) { if (e.target === this) closeSettings(); });
$('batch-modal').addEventListener('click', function (e) { if (e.target === this) closeBatch(); });

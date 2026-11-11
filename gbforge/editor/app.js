/* GBForge エディタ (依存ライブラリなし) */
'use strict';

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const h = (tag, attrs = {}, ...kids) => {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
    else if (k === 'class') e.className = v;
    else if (v === true) e.setAttribute(k, '');
    else if (v !== false && v != null) e.setAttribute(k, v);
  }
  for (const c of kids.flat()) if (c != null) e.append(c instanceof Node ? c : document.createTextNode(c));
  return e;
};

const GB = [[224, 248, 208], [136, 192, 112], [52, 104, 86], [8, 24, 32]];
const S = {
  proj: null, tabs: [], active: null,
  scene: null, sceneName: null, sceneDirty: false, sel: -1,
  tool: 'select', images: {}, texts: {}, textsDirty: false, textSel: null,
  script: null, scriptDirty: false, font: {}, asset: null,
};

// ---------------------------------------------------------------- API
async function api(method, url, body, raw) {
  const opt = { method, headers: {} };
  if (raw) { opt.body = raw; opt.headers['Content-Type'] = 'application/octet-stream'; }
  else if (body !== undefined) { opt.body = JSON.stringify(body); opt.headers['Content-Type'] = 'application/json'; }
  const r = await fetch(url, opt);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}

function log(msg, cls) {
  const el = $('#log');
  for (const line of String(msg).split('\n')) {
    let c = cls;
    if (!c) c = /エラー|error|失敗/i.test(line) ? 'e' : /警告|warning/i.test(line) ? 'w' : /成功|完了|OK/.test(line) ? 'o' : line.startsWith('$') ? 'c' : '';
    el.append(h('div', { class: c }, line));
  }
  el.scrollTop = el.scrollHeight;
}
const fail = (e) => log('エラー: ' + (e.message || e), 'e');

// ---------------------------------------------------------------- GB 色変換
function shadeOf(r, g, b) {
  const y = 0.299 * r + 0.587 * g + 0.114 * b;
  return y >= 192 ? 0 : y >= 128 ? 1 : y >= 64 ? 2 : 3;
}
function loadImage(url) {
  if (S.images[url]) return S.images[url];
  S.images[url] = new Promise((res, rej) => {
    const im = new Image();
    im.onload = () => res(im); im.onerror = rej;
    im.src = url + '?t=' + Date.now();
  });
  return S.images[url];
}
/* PNG をゲームボーイ表示に変換したキャンバスを返す (kind: bg / sprite) */
async function gbCanvas(url, kind, bgp = 0xE4) {
  const key = url + '|' + kind + '|' + bgp;
  if (S.images[key]) return S.images[key];
  S.images[key] = (async () => {
    const im = await loadImage(url);
    const c = h('canvas', { width: im.width, height: im.height });
    const x = c.getContext('2d');
    x.drawImage(im, 0, 0);
    const d = x.getImageData(0, 0, im.width, im.height);
    for (let i = 0; i < d.data.length; i += 4) {
      let s = shadeOf(d.data[i], d.data[i + 1], d.data[i + 2]);
      if (kind === 'sprite') {
        if (d.data[i + 3] < 128) { d.data[i + 3] = 0; continue; }
        s = [0, 2, 2, 3][s];
      } else {
        s = (bgp >> (s * 2)) & 3;
      }
      [d.data[i], d.data[i + 1], d.data[i + 2]] = GB[s];
      d.data[i + 3] = 255;
    }
    x.putImageData(d, 0, 0);
    return c;
  })();
  return S.images[key];
}
function clearImageCache() { S.images = {}; }

// ---------------------------------------------------------------- プロジェクト
async function loadProject() {
  S.proj = await api('GET', '/api/project');
  $('#projName').textContent = S.proj.settings.name + ' — ' + S.proj.root;
  $('#version').textContent = 'v' + S.proj.version;
  document.title = S.proj.settings.name + ' - GBForge';
  renderTree();
}

function treeSection(title, items, onAdd, addLabel = '＋') {
  const sec = h('div', { class: 'tree-sec' },
    h('div', { class: 'tree-head' }, title, onAdd ? h('button', { onclick: onAdd, title: '追加' }, addLabel) : null));
  items.forEach((it) => sec.append(it));
  return sec;
}

function renderTree() {
  const p = S.proj, t = $('#projectTree');
  t.innerHTML = '';
  const isSel = (kind, name) => S.active && S.active.kind === kind && S.active.name === name;
  if (p.type === 'c') {
    const tgt = { dmg: '初代', both: '初代+カラー', cgb: 'カラー専用' }[p.target] || p.target;
    t.append(treeSection(`📄 ソース (C) — ${tgt}`, p.sources.map((f) =>
      h('div', { class: 'tree-item' + (isSel('file', f) ? ' sel' : ''), onclick: () => openTab('file', f), ondblclick: () => openInIde(f), title: 'ダブルクリックで IDE で開く' },
        f.startsWith('lib/') ? h('span', { class: 'muted' }, f) : f)), newSourceFile));
    if (window.EXT) EXT.tree(t, isSel);
    if (p.res.length || !p.adopted) t.append(treeSection('🖼 リソース (res)', p.res.map((f) =>
      h('div', { class: 'tree-item' + (isSel('res', f) ? ' sel' : ''), onclick: () => openTab('res', f) },
        h('img', { class: 'thumb', src: '/asset/' + f.replace(/^res\/([a-z]+)\//, 'res_$1/') }), f.replace(/^res\//, ''))),
      () => upload(confirm('スプライトとして追加しますか？ (キャンセル = 背景マップ)') ? 'res_sprite' : 'res_background'), '⤒'));
    t.append(treeSection('⚙ 設定', [h('div', { class: 'tree-item' + (isSel('settings', 'settings') ? ' sel' : ''), onclick: () => openTab('settings', 'settings') }, 'プロジェクト設定')]));
    return;
  }
  t.append(treeSection('🎬 シーン', p.scenes.map((n) =>
    h('div', { class: 'tree-item' + (isSel('scene', n) ? ' sel' : ''), onclick: () => openTab('scene', n), title: n },
      n, n === (p.settings.start_scene || p.scenes[0]) ? h('span', { class: 'badge' }, '開始') : null)), newScene));
  t.append(treeSection('🧍 スプライト', p.sprites.map((s) =>
    h('div', { class: 'tree-item' + (s.error ? ' err' : '') + (isSel('sprite', s.name) ? ' sel' : ''), onclick: () => openTab('sprite', s.name), title: s.error || '' },
      h('img', { class: 'thumb', src: `/asset/sprites/${s.name}.png` }), s.name, h('span', { class: 'badge' }, s.frames + 'f'))),
    () => upload('sprite'), '⤒'));
  t.append(treeSection('🖼 背景', p.backgrounds.map((b) =>
    h('div', { class: 'tree-item' + (isSel('background', b.file) ? ' sel' : ''), onclick: () => openTab('background', b.file) },
      b.file, h('span', { class: 'badge' }, `${b.width}×${b.height}`))), () => upload('background'), '⤒'));
  t.append(treeSection('📜 スクリプト (C)', p.scripts.map((n) =>
    h('div', { class: 'tree-item' + (isSel('script', n) ? ' sel' : ''), onclick: () => openTab('script', n) }, n + '.c')), newScript));
  t.append(treeSection('💬 テキスト', [h('div', { class: 'tree-item' + (isSel('texts', 'texts') ? ' sel' : ''), onclick: () => openTab('texts', 'texts') },
    `テキスト一覧 (${p.texts.length})`)]));
  t.append(treeSection('⚙ 設定', [h('div', { class: 'tree-item' + (isSel('settings', 'settings') ? ' sel' : ''), onclick: () => openTab('settings', 'settings') }, 'プロジェクト設定')]));
}

async function newScene() {
  const name = prompt('新しいシーンの名前 (半角英数字)', 'scene' + (S.proj.scenes.length + 1));
  if (!name) return;
  try {
    await api('POST', '/api/scene/' + encodeURIComponent(name), {});
    await loadProject(); openTab('scene', name);
  } catch (e) { fail(e); }
}
async function newSourceFile() {
  const name = prompt('新しいファイル (src/ からの相対パス。.c か .h)', 'src/game.c');
  if (!name) return;
  try {
    await api('POST', '/api/file?path=' + encodeURIComponent(name));
    await loadProject(); openTab('file', name);
  } catch (e) { fail(e); }
}
async function newScript() {
  const name = prompt('新しいスクリプトの名前 (例: Coin)', 'NewScript');
  if (!name) return;
  try {
    await api('POST', '/api/script/' + encodeURIComponent(name));
    await loadProject(); openTab('script', name);
  } catch (e) { fail(e); }
}
function upload(kind) {
  const fi = $('#fileInput');
  fi.value = '';
  fi.onchange = async () => {
    const f = fi.files[0];
    if (!f) return;
    let name = f.name.replace(/\.png$/i, '').replace(/[^A-Za-z0-9_]/g, '_');
    if (!/^[A-Za-z_]/.test(name)) name = 'img_' + name;
    name = prompt('アセット名 (半角英数字)', name);
    if (!name) return;
    try {
      const r = await api('POST', `/api/upload?kind=${kind}&name=${encodeURIComponent(name)}.png`, undefined, await f.arrayBuffer());
      clearImageCache();
      log(`${name}.png を追加しました`, 'o');
      if (r.warning) log('警告: ' + r.warning, 'w');
      await loadProject();
      if (S.active && S.active.kind === 'scene') drawScene();
    } catch (e) { fail(e); }
  };
  fi.click();
}

// ---------------------------------------------------------------- タブ
const VIEW_OF = { scene: 'sceneView', script: 'scriptView', file: 'scriptView', res: 'assetView', texts: 'textView', sprite: 'assetView', background: 'assetView', settings: 'settingsView' };

async function openTab(kind, name) {
  if (!(await leaveCurrent(kind, name))) return;
  let tab = S.tabs.find((t) => t.kind === kind && t.name === name);
  if (!tab) { tab = { kind, name }; S.tabs.push(tab); }
  S.active = tab;
  renderTabs();
  $$('.view').forEach((v) => v.classList.remove('active'));
  $('#' + (VIEW_OF[kind] || (window.EXT && EXT.views[kind]))).classList.add('active');
  try {
    if (window.EXT && EXT.open[kind]) await EXT.open[kind](name);
    else if (kind === 'scene') await openScene(name);
    else if (kind === 'script') await openScript(name);
    else if (kind === 'file') await openFile(name);
    else if (kind === 'texts') await openTexts();
    else if (kind === 'settings') openSettings();
    else await openAsset(kind, name);
  } catch (e) { fail(e); }
  renderTree();
  renderInspector();
}

/* 未保存の変更を保存してから切り替える */
async function leaveCurrent(kind, name) {
  if (!S.active || (S.active.kind === kind && S.active.name === name)) return true;
  await saveAll(true);
  return true;
}

function renderTabs() {
  const el = $('#tabs');
  el.innerHTML = '';
  const icon = { stage: '🗺', carray: '🧩', convert: '🧰', versions: '🕘', image: '🖼', file: '📄', res: '🖼', scene: '🎬', script: '📜', texts: '💬', sprite: '🧍', background: '🖼', settings: '⚙' };
  for (const t of S.tabs) {
    el.append(h('div', { class: 'tab' + (t === S.active ? ' active' : ''), onclick: () => openTab(t.kind, t.name) },
      `${icon[t.kind]} ${t.kind === 'convert' ? '変換ツール' : t.kind === 'versions' ? 'バージョン' : t.kind === 'carray' ? t.name.split('#').pop() : t.kind === 'file' || t.kind === 'res' || t.kind === 'image' ? t.name.split('/').pop() : t.kind === 'script' ? t.name + '.c' : t.kind === 'texts' ? 'テキスト' : t.kind === 'settings' ? '設定' : t.name}`,
      h('span', { class: 'x', onclick: (e) => { e.stopPropagation(); closeTab(t); } }, '×')));
  }
}
async function closeTab(t) {
  await saveAll(true);
  S.tabs = S.tabs.filter((x) => x !== t);
  if (S.active === t) {
    S.active = null;
    const n = S.tabs[S.tabs.length - 1];
    if (n) return openTab(n.kind, n.name);
    $$('.view').forEach((v) => v.classList.remove('active'));
    S.scene = null; S.sceneName = null;
    renderHierarchy(); renderInspector();
  }
  renderTabs(); renderTree();
}

function markSaved() {
  const dirty = S.sceneDirty || S.scriptDirty || S.textsDirty || S.stageDirty;
  $('#saveState').textContent = dirty ? '● 未保存' : '保存済み';
}

async function saveAll(quiet) {
  try {
    if (window.EXT) await EXT.save(quiet);
    if (S.sceneDirty && S.scene) {
      await api('PUT', '/api/scene/' + encodeURIComponent(S.sceneName), S.scene);
      S.sceneDirty = false;
      if (!quiet) log(`シーン ${S.sceneName} を保存しました`, 'o');
    }
    if (S.scriptDirty && S.filePath) {
      await api('PUT', '/api/file?path=' + encodeURIComponent(S.filePath), { code: $('#scriptCode').value });
      S.scriptDirty = false;
      if (!quiet) log(`${S.filePath} を保存しました`, 'o');
    } else if (S.scriptDirty && S.script) {
      await api('PUT', '/api/script/' + encodeURIComponent(S.script), { code: $('#scriptCode').value });
      S.scriptDirty = false;
      if (!quiet) log(`${S.script}.c を保存しました`, 'o');
    }
    if (S.textsDirty) {
      await api('PUT', '/api/texts', S.texts);
      S.textsDirty = false;
      if (!quiet) log('テキストを保存しました', 'o');
      await loadProject();
    }
  } catch (e) { fail(e); }
  markSaved();
}

// ---------------------------------------------------------------- シーンビュー
async function openScene(name) {
  S.scene = await api('GET', '/api/scene/' + encodeURIComponent(name));
  S.sceneName = name; S.sel = -1; S.sceneDirty = false;
  $('#sceneEmpty').classList.add('hidden');
  normalizeCollision();
  renderHierarchy();
  await drawScene();
  markSaved();
}

function sceneSize() {
  const bs = S.scene && S.scene._bg_size;
  return bs ? { w: bs.width >> 3, h: bs.height >> 3 } : { w: 20, h: 18 };
}
function normalizeCollision() {
  const { w, h: hh } = sceneSize();
  const c = S.scene.collision || [];
  const rows = [];
  for (let y = 0; y < hh; y++) rows.push(((c[y] || '') + '0'.repeat(w)).slice(0, w).replace(/[^01]/g, '0'));
  S.scene.collision = rows;
}
function dirtyScene() { S.sceneDirty = true; markSaved(); }

function spriteInfo(name) { return S.proj.sprites.find((s) => s.name === name); }
function frameFor(a) {
  const si = spriteInfo(a.sprite);
  if (!si) return 0;
  const d = Math.max(0, S.proj.directions.indexOf(a.dir));
  if (si.frames === 4) return d;
  if (si.frames === 8) return d * 2;
  return 0;
}

async function drawScene() {
  const sc = S.scene;
  if (!sc) return;
  const z = +$('#optZoom').value;
  const cv = $('#sceneCanvas');
  const { w, h: hh } = sceneSize();
  cv.width = w * 8 * z; cv.height = hh * 8 * z;
  const x = cv.getContext('2d');
  x.imageSmoothingEnabled = false;
  x.fillStyle = '#000'; x.fillRect(0, 0, cv.width, cv.height);
  const bgp = parseInt(sc.palette || 'E4', 16) || 0xE4;
  if (sc.background) {
    try { x.drawImage(await gbCanvas(`/asset/backgrounds/${sc.background}`, 'bg', bgp), 0, 0, w * 8 * z, hh * 8 * z); } catch (e) { /* 画像なし */ }
  }
  if ($('#optColl').checked) {
    x.fillStyle = 'rgba(248,81,73,0.38)';
    sc.collision.forEach((row, ty) => [...row].forEach((c, tx) => { if (c === '1') x.fillRect(tx * 8 * z, ty * 8 * z, 8 * z, 8 * z); }));
  }
  if ($('#optGrid').checked) {
    x.lineWidth = 1;
    for (let gx = 0; gx <= w; gx++) { x.strokeStyle = gx % 2 ? 'rgba(0,0,0,.12)' : 'rgba(0,0,0,.28)'; x.beginPath(); x.moveTo(gx * 8 * z + .5, 0); x.lineTo(gx * 8 * z + .5, cv.height); x.stroke(); }
    for (let gy = 0; gy <= hh; gy++) { x.strokeStyle = gy % 2 ? 'rgba(0,0,0,.12)' : 'rgba(0,0,0,.28)'; x.beginPath(); x.moveTo(0, gy * 8 * z + .5); x.lineTo(cv.width, gy * 8 * z + .5); x.stroke(); }
  }
  for (let i = 0; i < sc.actors.length; i++) {
    const a = sc.actors[i];
    const px = a.x * z, py = a.y * z;
    if (a.sprite && spriteInfo(a.sprite)) {
      try {
        const img = await gbCanvas(`/asset/sprites/${a.sprite}.png`, 'sprite');
        x.globalAlpha = a.hidden ? 0.4 : 1;
        x.drawImage(img, frameFor(a) * 16, 0, 16, 16, px, py, 16 * z, 16 * z);
        x.globalAlpha = 1;
      } catch (e) { /* */ }
    } else {
      x.fillStyle = a.behaviour === 'warp' ? 'rgba(76,139,245,.35)' : 'rgba(255,255,255,.2)';
      x.fillRect(px, py, 16 * z, 16 * z);
      x.setLineDash([4, 3]); x.strokeStyle = a.behaviour === 'warp' ? '#4c8bf5' : '#fff';
      x.strokeRect(px + .5, py + .5, 16 * z - 1, 16 * z - 1); x.setLineDash([]);
      x.fillStyle = '#fff'; x.font = `${4 * z}px sans-serif`;
      x.fillText(a.behaviour === 'warp' ? '⇥' + (a.warp?.scene || '') : a.name, px + 2, py + 6 * z);
    }
    if (i === S.sel) {
      x.lineWidth = 2; x.strokeStyle = '#ffd34d';
      x.strokeRect(px - 1, py - 1, 16 * z + 2, 16 * z + 2);
      x.lineWidth = 1;
    }
  }
  const player = sc.actors.find((a) => a.behaviour === 'player');
  if ($('#optCam').checked && player) {
    let cx = Math.min(Math.max(player.x + 8 - 80, 0), w * 8 - 160);
    let cy = Math.min(Math.max(player.y + 8 - 72, 0), hh * 8 - 144);
    x.setLineDash([6, 4]); x.strokeStyle = '#3fb950'; x.lineWidth = 2;
    x.strokeRect(cx * z, cy * z, 160 * z, 144 * z);
    x.setLineDash([]); x.lineWidth = 1;
    x.fillStyle = '#3fb950'; x.font = '12px sans-serif'; x.fillText('カメラ (開始時)', cx * z + 4, cy * z + 14);
  }
}

function canvasPos(ev) {
  const cv = $('#sceneCanvas'), r = cv.getBoundingClientRect(), z = +$('#optZoom').value;
  return { x: (ev.clientX - r.left) / z, y: (ev.clientY - r.top) / z };
}
function actorAt(p) {
  const as = S.scene.actors;
  for (let i = as.length - 1; i >= 0; i--) {
    const a = as[i];
    if (p.x >= a.x && p.x < a.x + 16 && p.y >= a.y && p.y < a.y + 16) return i;
  }
  return -1;
}
function setCollision(tx, ty, v) {
  const rows = S.scene.collision;
  if (ty < 0 || ty >= rows.length || tx < 0 || tx >= rows[0].length) return false;
  const r = rows[ty];
  if (r[tx] === v) return false;
  rows[ty] = r.slice(0, tx) + v + r.slice(tx + 1);
  return true;
}

let drag = null;
function setupSceneEvents() {
  const cv = $('#sceneCanvas');
  cv.addEventListener('contextmenu', (e) => e.preventDefault());
  cv.addEventListener('mousedown', (e) => {
    if (!S.scene) return;
    const p = canvasPos(e);
    if (S.tool === 'select') {
      const i = actorAt(p);
      S.sel = i;
      if (i >= 0) drag = { i, dx: p.x - S.scene.actors[i].x, dy: p.y - S.scene.actors[i].y, moved: false };
      renderHierarchy(); renderInspector(); drawScene();
    } else if (S.tool === 'collision') {
      const v = (e.button === 2 || e.shiftKey) ? '0' : '1';
      drag = { paint: v };
      if (setCollision(Math.floor(p.x / 8), Math.floor(p.y / 8), v)) { dirtyScene(); drawScene(); }
    } else if (S.tool === 'actor') {
      if (S.scene.actors.length >= 10) { log('アクターは 1 シーン 10 体までです', 'w'); return; }
      const snap = $('#optSnap').checked;
      const ax = snap ? Math.floor((p.x - 8) / 8) * 8 : Math.round(p.x - 8);
      const ay = snap ? Math.floor((p.y - 8) / 8) * 8 : Math.round(p.y - 8);
      S.scene.actors.push({ name: 'Actor' + (S.scene.actors.length + 1), sprite: S.proj.sprites[0]?.name || null, x: Math.max(0, ax), y: Math.max(0, ay),
        dir: 'down', behaviour: 'talk', script: null, text: null, speed: 1, solid: true, hidden: false, warp: { scene: '', x: 0, y: 0 } });
      S.sel = S.scene.actors.length - 1;
      setTool('select');
      dirtyScene(); renderHierarchy(); renderInspector(); drawScene();
    }
  });
  window.addEventListener('mousemove', (e) => {
    if (!S.scene || !$('#sceneView').classList.contains('active')) return;
    const p = canvasPos(e);
    const { w, h: hh } = sceneSize();
    if (p.x >= 0 && p.y >= 0 && p.x < w * 8 && p.y < hh * 8)
      $('#cursorInfo').textContent = `x ${Math.floor(p.x)}, y ${Math.floor(p.y)}  (タイル ${Math.floor(p.x / 8)}, ${Math.floor(p.y / 8)})`;
    if (!drag) return;
    if (drag.paint) {
      if (setCollision(Math.floor(p.x / 8), Math.floor(p.y / 8), drag.paint)) { dirtyScene(); drawScene(); }
    } else {
      const a = S.scene.actors[drag.i];
      let nx = p.x - drag.dx, ny = p.y - drag.dy;
      if ($('#optSnap').checked) { nx = Math.round(nx / 8) * 8; ny = Math.round(ny / 8) * 8; }
      nx = Math.max(0, Math.min(w * 8 - 16, Math.round(nx)));
      ny = Math.max(0, Math.min(hh * 8 - 16, Math.round(ny)));
      if (nx !== a.x || ny !== a.y) { a.x = nx; a.y = ny; drag.moved = true; dirtyScene(); drawScene(); renderInspector(); }
    }
  });
  window.addEventListener('mouseup', () => { drag = null; });
  $$('#tools button').forEach((b) => b.addEventListener('click', () => setTool(b.dataset.tool)));
  ['optGrid', 'optColl', 'optCam', 'optZoom'].forEach((id) => $('#' + id).addEventListener('change', drawScene));
}
function setTool(t) {
  S.tool = t;
  $$('#tools button').forEach((b) => b.classList.toggle('active', b.dataset.tool === t));
}

function renderHierarchy() {
  const el = $('#hierarchy');
  el.innerHTML = '';
  $('#hierScene').textContent = S.sceneName || '';
  if (!S.scene) return;
  el.append(h('div', { class: 'tree-item' + (S.sel === -1 ? ' sel' : ''), onclick: () => { S.sel = -1; renderHierarchy(); renderInspector(); drawScene(); } }, '🎬 ' + S.sceneName + ' (シーン)'));
  S.scene.actors.forEach((a, i) => {
    const icon = { player: '🕹', talk: '💬', warp: '⇥', wander: '🐾', none: '◻' }[a.behaviour] || '◻';
    el.append(h('div', { class: 'tree-item' + (S.sel === i ? ' sel' : ''), onclick: () => { S.sel = i; renderHierarchy(); renderInspector(); drawScene(); } },
      icon + ' ' + a.name, h('span', { class: 'badge' }, a.script ? a.script + '.c' : '')));
  });
}

// ---------------------------------------------------------------- インスペクター
function prop(label, input, hint) {
  return h('div', {}, h('div', { class: 'prop' }, h('span', {}, label), input), hint ? h('div', { class: 'hint' }, hint) : null);
}
function select(options, value, onchange, allowNone) {
  const s = h('select', { onchange: (e) => onchange(e.target.value || null) });
  if (allowNone) s.append(h('option', { value: '' }, allowNone));
  for (const o of options) {
    const [v, label] = Array.isArray(o) ? o : [o, o];
    s.append(h('option', { value: v, selected: v === value }, label));
  }
  return s;
}
function num(value, onchange, min, max) {
  return h('input', { type: 'number', value, min, max, onchange: (e) => onchange(parseInt(e.target.value, 10) || 0) });
}

const BHV_LABEL = { none: 'なし (スクリプトのみ)', player: 'プレイヤー操作', talk: '話しかける (テキスト)', warp: 'ワープ (シーン移動)', wander: 'うろうろ歩く' };
const DIR_LABEL = { down: '下', up: '上', left: '左', right: '右' };

async function renderInspector() {
  const el = $('#inspector');
  el.innerHTML = '';
  if (!S.active) { el.append(h('div', { class: 'hint' }, '項目を選択してください')); return; }
  if (window.EXT && EXT.inspect[S.active.kind]) { EXT.inspect[S.active.kind](el); return; }
  if (S.active.kind === 'file') {
    el.append(h('div', { class: 'comp' }, h('div', { class: 'comp-title' }, '📄 ' + S.active.name),
      h('button', { onclick: () => openInIde(S.active.name) }, '</> Visual Studio / VS Code で開く'),
      h('div', { class: 'hint' }, 'IDE 用の設定 (.sln / .vscode) は GBDK と gbjp のインクルードパス込みで自動生成されます。IDE からのビルド (Ctrl+Shift+B / Ctrl+B) や実行 (F5) も GBForge を呼び出します。')));
    el.append(h('div', { class: 'comp' }, h('div', { class: 'comp-title' }, '日本語 (gbjp)'),
      h('pre', { class: 'hint mono' }, 'jp_init(128, 128);\njp_print(x, y, "日本語");\njp_print_box(x, y, w, h, "長文…");\njp_print_int(x, y, n);\njp_set_palette(p);  // GBC\njp_set_layer(JP_WIN);\njp_set_speed(2);'),
      h('div', { class: 'hint' }, 'ソースに書いた文字だけがフォントとして ROM に入ります (設定で全文字モードも可)。')));
    return;
  }
  if (S.active.kind !== 'scene' || !S.scene) {
    el.append(h('div', { class: 'hint' }, S.active.kind === 'script'
      ? 'スクリプトはアクターまたはシーンのインスペクターで割り当てます。関数 名前_start / 名前_update / 名前_interact を定義すると自動で呼ばれます。'
      : S.active.kind === 'texts' ? 'テキスト ID はアクターの「話しかける」やスクリプトの TEXT_ID 定数で使います。'
        : ''));
    return;
  }
  const sc = S.scene;
  const upd = (fn) => (v) => { fn(v); dirtyScene(); drawScene(); renderHierarchy(); };
  if (S.sel < 0) {
    el.append(h('div', { class: 'comp' }, h('div', { class: 'comp-title' }, '🎬 シーン: ' + S.sceneName),
      prop('背景', select(S.proj.backgrounds.map((b) => b.file), sc.background, async (v) => {
        sc.background = v; dirtyScene(); await saveAll(true); await openScene(S.sceneName); renderInspector();
      }, '(未設定)'), '160×144〜256×256px の PNG。ユニークな 8×8 タイルは 128 個まで'),
      prop('スクリプト', select(S.proj.scripts, sc.script, upd((v) => { sc.script = v; }), '(なし)'), 'シーン用スクリプトでは self は NULL'),
      prop('BG パレット', h('input', { value: sc.palette || 'E4', class: 'mono', maxlength: 2, onchange: (e) => { sc.palette = e.target.value.toUpperCase(); dirtyScene(); drawScene(); } }), 'BGP レジスタ値 (16進)。E4 が標準'),
      h('div', { class: 'hint' }, `サイズ ${sceneSize().w}×${sceneSize().h} タイル / アクター ${sc.actors.length}/10`),
      h('div', {}, h('button', { onclick: () => { if (S.sceneName === S.proj.settings.start_scene) return; S.proj.settings.start_scene = S.sceneName; api('PUT', '/api/project', { start_scene: S.sceneName }).then(loadProject); } }, '開始シーンにする'), ' ',
        h('button', { class: 'danger', onclick: deleteScene }, 'シーンを削除'))));
    return;
  }
  const a = sc.actors[S.sel];
  if (!a) return;
  const preview = h('canvas', { width: 48, height: 48 });
  if (a.sprite && spriteInfo(a.sprite)) {
    gbCanvas(`/asset/sprites/${a.sprite}.png`, 'sprite').then((img) => {
      const x = preview.getContext('2d'); x.imageSmoothingEnabled = false;
      x.drawImage(img, frameFor(a) * 16, 0, 16, 16, 0, 0, 48, 48);
    }).catch(() => {});
  }
  el.append(h('div', { class: 'insp-head' }, preview,
    h('input', { value: a.name, onchange: (e) => { a.name = e.target.value.replace(/[^A-Za-z0-9_]/g, '_') || 'Actor'; dirtyScene(); renderHierarchy(); renderInspector(); } })));
  el.append(h('div', { class: 'comp' }, h('div', { class: 'comp-title' }, 'Transform'),
    prop('X (px)', num(a.x, upd((v) => { a.x = v; }), 0, 240)),
    prop('Y (px)', num(a.y, upd((v) => { a.y = v; }), 0, 240)),
    prop('向き', select(S.proj.directions.map((d) => [d, DIR_LABEL[d]]), a.dir, upd((v) => { a.dir = v; })))));
  el.append(h('div', { class: 'comp' }, h('div', { class: 'comp-title' }, 'Sprite Renderer'),
    prop('スプライト', select(S.proj.sprites.map((s) => s.name), a.sprite, upd((v) => { a.sprite = v; renderInspector(); }), '(非表示)'),
      '16×16。4フレーム=下上左右、8フレーム=各向き2コマ歩行'),
    prop('非表示', h('input', { type: 'checkbox', checked: a.hidden, onchange: (e) => upd((v) => { a.hidden = v; })(e.target.checked) })),
    prop('当たり判定', h('input', { type: 'checkbox', checked: a.solid, onchange: (e) => upd((v) => { a.solid = v; })(e.target.checked) }), 'ON だとプレイヤーが通り抜けられない')));
  const bh = h('div', { class: 'comp' }, h('div', { class: 'comp-title' }, 'ビヘイビア'),
    prop('種類', select(S.proj.behaviours.map((b) => [b, BHV_LABEL[b]]), a.behaviour, upd((v) => { a.behaviour = v; renderInspector(); }))));
  if (a.behaviour === 'player' || a.behaviour === 'wander')
    bh.append(prop('速度', num(a.speed, upd((v) => { a.speed = Math.max(1, Math.min(4, v)); }), 1, 4), 'px / フレーム'));
  if (a.behaviour === 'talk' || a.behaviour === 'wander' || a.behaviour === 'none') {
    bh.append(prop('テキスト', select(S.proj.texts, a.text, upd((v) => { a.text = v; renderInspector(); }), '(なし)'),
      'A ボタンで話しかけると表示'));
    if (a.text) {
      const pv = h('div');
      bh.append(pv);
      if (!S.textsLoaded) { S.texts = await api('GET', '/api/texts'); S.textsLoaded = true; }
      const t = S.texts[a.text];
      if (t) renderTextPreview(pv, t.text, t.choice, 1);
    }
  }
  if (a.behaviour === 'warp') {
    a.warp = a.warp || { scene: '', x: 0, y: 0 };
    bh.append(prop('行き先シーン', select(S.proj.scenes, a.warp.scene, upd((v) => { a.warp.scene = v; }), '(選択)')),
      prop('出現 X (タイル)', num(a.warp.x, upd((v) => { a.warp.x = v; }), 0, 31)),
      prop('出現 Y (タイル)', num(a.warp.y, upd((v) => { a.warp.y = v; }), 0, 31)),
      h('div', { class: 'hint' }, 'プレイヤーが触れると行き先シーンのタイル座標 (X, Y) に移動します'));
  }
  el.append(bh);
  el.append(h('div', { class: 'comp' }, h('div', { class: 'comp-title' }, 'スクリプト'),
    prop('C スクリプト', select(S.proj.scripts, a.script, upd((v) => { a.script = v; }), '(なし)')),
    a.script ? h('button', { onclick: () => openTab('script', a.script) }, `${a.script}.c を開く`) : null,
    h('div', { class: 'hint' }, `スクリプトからは ACTOR_${(S.sceneName || '').toUpperCase()}_${a.name.toUpperCase()} で参照できます`)));
  el.append(h('div', {},
    h('button', { onclick: () => { const c = JSON.parse(JSON.stringify(a)); c.name += '_copy'; c.x = Math.min(c.x + 16, 240); sc.actors.push(c); S.sel = sc.actors.length - 1; dirtyScene(); renderHierarchy(); renderInspector(); drawScene(); } }, '複製'), ' ',
    h('button', { class: 'danger', onclick: () => { sc.actors.splice(S.sel, 1); S.sel = -1; dirtyScene(); renderHierarchy(); renderInspector(); drawScene(); } }, '削除')));
}

async function deleteScene() {
  if (!confirm(`シーン ${S.sceneName} を削除しますか？`)) return;
  try {
    await api('DELETE', '/api/scene/' + encodeURIComponent(S.sceneName));
    S.sceneDirty = false;
    const t = S.active; await loadProject(); closeTab(t);
  } catch (e) { fail(e); }
}

// ---------------------------------------------------------------- スクリプト
async function openFile(path) {
  const r = await api('GET', '/api/file?path=' + encodeURIComponent(path));
  S.filePath = path; S.script = null; S.scriptDirty = false;
  $('#scriptName').textContent = path;
  $('#scriptHint').classList.add('hidden');
  $('#scriptCode').value = r.code;
  $('#apiRef').textContent = '';
  markSaved();
}
/* 開いているファイルを Visual Studio / VS Code で開く */
async function openInIde(file) {
  await saveAll(true);
  try {
    const r = await api('POST', '/api/ide/open', { file: file || null });
    log(`${r.ide} で開きました (GBDK と gbjp のインクルード設定済み)`, 'o');
  } catch (e) { fail(e); }
}
async function openScript(name) {
  const r = await api('GET', '/api/script/' + encodeURIComponent(name));
  S.script = name; S.filePath = null; S.scriptDirty = false;
  $('#scriptName').textContent = name + '.c';
  $('#scriptHint').classList.remove('hidden');
  $('#scriptCode').value = r.code;
  markSaved();
}
function setupScriptEditor() {
  const ta = $('#scriptCode');
  ta.addEventListener('input', () => { S.scriptDirty = true; markSaved(); });
  ta.addEventListener('keydown', (e) => {
    if (e.key === 'Tab') {
      e.preventDefault();
      const s = ta.selectionStart;
      ta.setRangeText('    ', s, ta.selectionEnd, 'end');
      S.scriptDirty = true; markSaved();
    }
  });
  $('#btnScriptSave').onclick = () => saveAll();
  $('#btnScriptIde').onclick = () => openInIde(S.filePath || (S.script ? `scripts/${S.script}.c` : null));
  $('#btnApiRef').onclick = async () => {
    const pre = $('#apiRef');
    if (!pre.textContent) pre.textContent = (await api('GET', '/api/api_reference')).header;
    pre.classList.toggle('hidden');
  };
}

// ---------------------------------------------------------------- テキスト
const BORDER = [[0, 0, 0, 0x1F, 0x10, 0x10, 0x10, 0x10], [0, 0, 0, 0xFF, 0, 0, 0, 0], [0, 0, 0, 0xF8, 8, 8, 8, 8], [0x10, 0x10, 0x10, 0x10, 0x10, 0x10, 0x10, 0x10],
  [8, 8, 8, 8, 8, 8, 8, 8], [0x10, 0x10, 0x10, 0x10, 0x1F, 0, 0, 0], [0, 0, 0, 0, 0xFF, 0, 0, 0], [8, 8, 8, 8, 0xF8, 0, 0, 0]];

async function ensureGlyphs(chars) {
  const need = [...new Set(chars)].filter((c) => !(c in S.font));
  if (!need.length) return;
  const r = await api('GET', '/api/font?chars=' + encodeURIComponent(need.join('')));
  for (const c of need) S.font[c] = r[c] ? r[c].match(/../g).map((x) => parseInt(x, 16)) : null;
}
function drawTile(x, ox, oy, rows, z) {
  x.fillStyle = `rgb(${GB[3]})`;
  rows.forEach((b, y) => { for (let i = 0; i < 8; i++) if (b & (0x80 >> i)) x.fillRect((ox + i) * z, (oy + y) * z, z, z); });
}
/* GB のメッセージウィンドウと同じ見た目でプレビュー */
async function renderTextPreview(container, text, choice, zoom = 2) {
  const r = await api('POST', '/api/text/preview', { text, choice });
  await ensureGlyphs(r.pages.flat(2));
  container.innerHTML = '';
  r.pages.forEach((page, pi) => {
    const z = zoom;
    const cv = h('canvas', { width: 160 * z, height: 40 * z, title: `ページ ${pi + 1}` });
    const x = cv.getContext('2d');
    x.fillStyle = `rgb(${GB[0]})`; x.fillRect(0, 0, cv.width, cv.height);
    const tiles = [[0, 0, 0], [19, 0, 2], [0, 4, 5], [19, 4, 7]];
    for (let i = 1; i < 19; i++) tiles.push([i, 0, 1], [i, 4, 6]);
    for (let j = 1; j < 4; j++) tiles.push([0, j, 3], [19, j, 4]);
    tiles.forEach(([tx, ty, t]) => drawTile(x, tx * 8, ty * 8, BORDER[t], z));
    page.forEach((row, ly) => row.forEach((ch, cx) => { const g = S.font[ch]; if (g) drawTile(x, 8 + cx * 8, 8 + ly * 8, g, z); }));
    if (choice && pi === r.pages.length - 1) {
      const n = page.length;
      if (n >= 2) { const g = S.font['▶']; if (g) drawTile(x, 8, 8 + (n - 2) * 8, g, z); }
    }
    container.append(cv);
  });
  if (r.missing.length) container.append(h('div', { class: 'warn' }, 'フォントに無い文字: ' + r.missing.join(' ')));
  return r;
}

async function openTexts() {
  S.texts = await api('GET', '/api/texts');
  S.textsLoaded = true; S.textsDirty = false;
  if (!S.textSel || !(S.textSel in S.texts)) S.textSel = Object.keys(S.texts).sort()[0] || null;
  renderTextList();
  showText();
  markSaved();
}
function renderTextList() {
  const ul = $('#textList');
  ul.innerHTML = '';
  Object.keys(S.texts).sort().forEach((k) => ul.append(h('li', { class: k === S.textSel ? 'sel' : '', onclick: () => { S.textSel = k; renderTextList(); showText(); } }, k)));
}
let textTimer = null;
function showText() {
  const t = S.texts[S.textSel];
  $('#textKey').value = S.textSel || '';
  $('#textBody').value = t ? t.text : '';
  $('#textChoice').checked = t ? t.choice : false;
  $('#textBody').disabled = !t;
  updateTextPreview();
}
function updateTextPreview() {
  clearTimeout(textTimer);
  textTimer = setTimeout(async () => {
    const t = S.texts[S.textSel];
    if (!t) { $('#textPreview').innerHTML = ''; $('#textInfo').textContent = ''; return; }
    const r = await renderTextPreview($('#textPreview'), t.text, t.choice, 2);
    $('#textInfo').textContent = `${r.pages.length} ページ / スクリプトでは TEXT_${S.textSel.toUpperCase()}`;
  }, 150);
}
function setupTextEditor() {
  $('#textBody').addEventListener('input', (e) => { if (!S.texts[S.textSel]) return; S.texts[S.textSel].text = e.target.value; S.textsDirty = true; markSaved(); updateTextPreview(); });
  $('#textChoice').addEventListener('change', (e) => { if (!S.texts[S.textSel]) return; S.texts[S.textSel].choice = e.target.checked; S.textsDirty = true; markSaved(); updateTextPreview(); });
  $('#textKey').addEventListener('change', (e) => {
    const nk = e.target.value.trim();
    if (!S.textSel || nk === S.textSel) return;
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(nk) || nk in S.texts) { log('ID は半角英数字で、重複しない名前にしてください', 'w'); e.target.value = S.textSel; return; }
    S.texts[nk] = S.texts[S.textSel]; delete S.texts[S.textSel]; S.textSel = nk;
    S.textsDirty = true; markSaved(); renderTextList();
    log('テキスト ID を変更しました。アクター・スクリプトの参照も更新してください', 'w');
  });
  $('#btnTextNew').onclick = () => {
    let k = prompt('テキスト ID (半角英数字)', 'text' + (Object.keys(S.texts).length + 1));
    if (!k) return;
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(k) || k in S.texts) { log('ID が不正か重複しています', 'w'); return; }
    S.texts[k] = { text: 'ここに ぶんしょうを かきます。', choice: false };
    S.textSel = k; S.textsDirty = true; markSaved(); renderTextList(); showText();
  };
  $('#btnTextDel').onclick = () => {
    if (!S.textSel || !confirm(`テキスト ${S.textSel} を削除しますか？`)) return;
    delete S.texts[S.textSel]; S.textSel = Object.keys(S.texts).sort()[0] || null;
    S.textsDirty = true; markSaved(); renderTextList(); showText();
  };
  $('#btnTextSave').onclick = () => saveAll();
}

// ---------------------------------------------------------------- アセット
async function openAsset(kind, name) {
  S.asset = { kind, name };
  if (kind === 'res') {
    $('#assetTitle').textContent = name;
    const body = $('#assetBody');
    body.innerHTML = '';
    const stem = name.split('/').pop().replace(/\.png$/, '');
    const img = await loadImage('/asset/' + name.replace(/^res\/([a-z]+)\//, 'res_$1/'));
    const c = h('canvas', { width: img.width * 3, height: img.height * 3 });
    const x = c.getContext('2d'); x.imageSmoothingEnabled = false; x.drawImage(img, 0, 0, c.width, c.height);
    const kindTxt = name.includes('/sprites/') ? `16×16 メタスプライト (8×16 モード)。コード: #include "${stem}.h" → set_sprite_data(0, ${stem}_TILE_COUNT, ${stem}_tiles); move_metasprite_ex(${stem}_metasprites[0], 0, 0, 0, x, y);`
      : `背景マップ。コード: #include "${stem}.h" → set_bkg_data(0, ${stem}_TILE_COUNT, ${stem}_tiles); set_bkg_tiles(0, 0, ${stem}_WIDTH / 8, ${stem}_HEIGHT / 8, ${stem}_map);`;
    body.append(h('div', { class: 'hint' }, 'ビルド時に GBDK の png2asset で自動変換されます。' + kindTxt), c);
    return;
  }
  $('#assetTitle').textContent = (kind === 'sprite' ? 'スプライト: ' : '背景: ') + name;
  const body = $('#assetBody');
  body.innerHTML = '';
  if (kind === 'sprite') {
    const si = spriteInfo(name);
    const img = await gbCanvas(`/asset/sprites/${name}.png`, 'sprite');
    if (si.error) body.append(h('div', { class: 'errtext' }, si.error));
    const row = h('div', { class: 'frame-row' });
    const labels = si.frames === 4 ? ['下', '上', '左', '右'] : si.frames === 8 ? ['下1', '下2', '上1', '上2', '左1', '左2', '右1', '右2'] : [];
    for (let f = 0; f < si.frames; f++) {
      const c = h('canvas', { width: 64, height: 64 });
      const x = c.getContext('2d'); x.imageSmoothingEnabled = false;
      x.drawImage(img, f * 16, 0, 16, 16, 0, 0, 64, 64);
      row.append(h('figure', {}, c, h('figcaption', {}, `#${f} ${labels[f] || ''}`)));
    }
    body.append(h('div', { class: 'hint' }, `${si.frames} フレーム。1 フレーム 16×16、横に並べた PNG。透明部分は透過。色は 白 / 濃い灰 / 黒 の 3 色に変換されます。`), row);
  } else {
    const bg = S.proj.backgrounds.find((b) => b.file === name);
    const img = await gbCanvas(`/asset/backgrounds/${name}`, 'bg');
    const c = h('canvas', { width: img.width * 2, height: img.height * 2 });
    const x = c.getContext('2d'); x.imageSmoothingEnabled = false; x.drawImage(img, 0, 0, c.width, c.height);
    body.append(h('div', { class: 'hint' }, `${bg.width}×${bg.height}px (${bg.width / 8}×${bg.height / 8} タイル)。4 階調に変換したプレビュー。ユニークタイル 128 個まで。`), c);
  }
}
async function deleteAsset() {
  const a = S.asset;
  if (!a || !confirm(`${a.name} を削除しますか？`)) return;
  try {
    if (a.kind === 'res') await api('DELETE', '/api/res?path=' + encodeURIComponent(a.name));
    else await api('DELETE', `/api/asset/${a.kind}/${encodeURIComponent(a.name)}`);
    clearImageCache();
    const t = S.active; await loadProject(); closeTab(t);
  } catch (e) { fail(e); }
}

// ---------------------------------------------------------------- 設定
function openSettings() {
  const s = S.proj.settings, f = $('#settingsForm');
  const ss = f.elements['start_scene']; ss.innerHTML = '';
  S.proj.scenes.forEach((n) => ss.append(h('option', { selected: n === s.start_scene }, n)));
  const fs = f.elements['font']; fs.innerHTML = '';
  S.proj.fonts.forEach((n) => fs.append(h('option', { value: n, selected: n === s.font }, { misaki_gothic: '美咲ゴシック', misaki_mincho: '美咲明朝' }[n] || n)));
  $$('.engine-only', f).forEach((e) => e.classList.toggle('hidden', S.proj.type === 'c'));
  $$('.c-only', f).forEach((e) => e.classList.toggle('hidden', S.proj.type !== 'c'));
  for (const el of f.elements) {
    if (!el.name || el.name === 'start_scene' || el.name === 'font' || el.name === 'flasher.port') continue;
    const [a, b] = el.name.split('.');
    const v = b ? s[a][b] : s[a];
    if (el.type === 'checkbox') el.checked = !!v;
    else if (el.name === 'variables') el.value = (v || []).join('\n');
    else el.value = v ?? '';
  }
  refreshPorts();
}
async function refreshPorts() {
  const sel = $('#settingsForm').elements['flasher.port'];
  const cur = S.proj.settings.flasher.port;
  const ports = await api('GET', '/api/ports').catch(() => []);
  sel.innerHTML = '';
  sel.append(h('option', { value: '' }, '(自動検出)'));
  ports.forEach((p) => sel.append(h('option', { value: p.device, selected: p.device === cur }, (p.likely ? '★ ' : '') + p.device + ' ' + p.description)));
  if (cur && !ports.some((p) => p.device === cur)) sel.append(h('option', { value: cur, selected: true }, cur + ' (未接続)'));
}
async function saveSettings() {
  const f = $('#settingsForm'), out = { build: {}, flasher: {}, jpfont: {}, ide: {} };
  for (const el of f.elements) {
    if (!el.name || el.closest('.hidden')) continue;
    const [a, b] = el.name.split('.');
    let v = el.type === 'checkbox' ? el.checked : el.value;
    if (el.name === 'variables') v = v.split(/\s+/).filter(Boolean);
    if (el.name === 'flasher.baud') v = parseInt(v, 10);
    if (b) out[a][b] = v; else out[a] = v;
  }
  try {
    await api('PUT', '/api/project', out);
    log('設定を保存しました', 'o');
    S.font = {};
    await loadProject();
  } catch (e) { fail(e); }
}

// ---------------------------------------------------------------- ビルド / 実行 / 書き込み
let busy = false;
/* GBDK が未セットアップならダウンロードの確認を取る。続行するなら true */
async function confirmGbdk(body) {
  await refreshStatus();
  const tc = S.proj?.settings?.build?.toolchain || 'gbdk';
  if (tc !== 'gbdk' || S.status?.gbdk?.installed) return true;
  if (!confirm('コンパイラ GBDK-2020 (約 7MB) がまだありません。\nGitHub の公式リリースからダウンロードして自動セットアップしますか？\n(チェックサムで検証し、ユーザーフォルダの .gbforge に保存します)')) return false;
  body.install_gbdk = true;
  return true;
}

async function runJob(url, body, title) {
  if (busy) { log('別の処理が実行中です', 'w'); return null; }
  if (url !== '/api/gbdk/install' && !(await confirmGbdk(body))) return null;
  busy = true;
  await saveAll(true);
  const showModal = !!title;
  if (showModal) {
    $('#modal').classList.remove('hidden');
    $('#modalTitle').textContent = title; $('#modalBar').style.width = '0';
    $('#modalText').textContent = '準備中…'; $('#modalClose').classList.add('hidden');
  }
  try {
    let job = await api('POST', url, body);
    let since = 0;
    while (true) {
      job = await api('GET', `/api/job/${job.id}?since=${since}`);
      job.log.forEach((l) => log(l));
      since = job.log_len;
      if (showModal) {
        const p = job.progress;
        if (p.total) { $('#modalBar').style.width = (100 * p.done / p.total) + '%'; $('#modalText').textContent = `${p.label} ${p.done}/${p.total}`; }
        else if (job.log.length) $('#modalText').textContent = job.log[job.log.length - 1];
      }
      if (job.done) break;
      await new Promise((r) => setTimeout(r, 250));
    }
    if (showModal) {
      $('#modalText').textContent = job.ok ? '完了しました！' : '失敗しました。コンソールを確認してください';
      $('#modalBar').style.width = job.ok ? '100%' : $('#modalBar').style.width;
      $('#modalClose').classList.remove('hidden');
    }
    if (job.ok && job.result && job.result.glyphs) log(`ROM ${job.result.rom_size / 1024}KB / 使用文字 ${job.result.glyphs} / データ ${job.result.data_bytes} バイト`, 'o');
    return job;
  } catch (e) {
    fail(e);
    if (showModal) { $('#modalText').textContent = String(e.message || e); $('#modalClose').classList.remove('hidden'); }
    return null;
  } finally {
    busy = false;
    if (S.status?.project) loadProject();
    refreshStatus();
  }
}

// ---------------------------------------------------------------- 状態表示 / スタート画面
async function refreshStatus() {
  try { S.status = await api('GET', '/api/status'); } catch (e) { return; }
  const st = S.status;
  const g = $('#chipGbdk'), w = $('#chipWriter');
  if (st.gbdk.installed) { g.className = 'chip ok'; g.textContent = '✔ GBDK'; g.title = `GBDK-2020: ${st.gbdk.home} (${st.gbdk.source})`; }
  else { g.className = 'chip ng'; g.textContent = '✘ GBDK 未セットアップ'; g.title = 'クリックで GBDK-2020 を自動セットアップ'; }
  if (st.writer.length) { w.className = 'chip ok'; w.textContent = '✔ ロムライタ ' + st.writer[0].device; w.title = st.writer.map((p) => p.device + ' ' + p.description).join('\n'); }
  else { w.className = 'chip idle'; w.textContent = '○ ロムライタ未接続'; w.title = 'USB で接続すると自動で検出します (FTDI)'; }
  const hs = $('#homeStatus');
  if (hs) {
    hs.innerHTML = '';
    hs.append(g.cloneNode(true), w.cloneNode(true), h('span', { class: 'chip ' + (st.emulator ? 'ok' : 'idle') }, st.emulator ? '✔ エミュレータ ' + st.emulator : '○ エミュレータなし'));
    hs.firstChild.onclick = g.onclick;
  }
}
async function installGbdk() {
  if (S.status?.gbdk?.installed) return;
  if (!confirm('GBDK-2020 を GitHub の公式リリースからダウンロードしてセットアップします (約 7MB)。よろしいですか？')) return;
  await runJob('/api/gbdk/install', {}, 'GBDK-2020 をセットアップ中');
}

async function showHome() {
  $('#home').classList.remove('hidden');
  const r = await api('GET', '/api/home');
  const ul = $('#recentList');
  ul.innerHTML = '';
  if (!r.recent.length) ul.append(h('li', { class: 'muted' }, 'まだありません'));
  r.recent.forEach((it) => ul.append(h('li', { onclick: () => openProject(it.path), title: it.path }, it.name || it.path, h('small', {}, it.path))));
  if (!$('#newPath').value) $('#newPath').value = r.default_dir + (r.default_dir.includes('\\') ? '\\' : '/') + 'MyGame';
  refreshStatus();
}
async function openProject(path) {
  try {
    const r = await api('POST', '/api/open', { path });
    if (r.needs_adopt) { EXT.showAdopt(r.info); return; }
    location.reload();
  } catch (e) { alert(e.message); }
}

// ---------------------------------------------------------------- 起動
function setup() {
  setupSceneEvents();
  setupScriptEditor();
  setupTextEditor();
  $('#btnBuild').onclick = () => runJob('/api/build', { run: false });
  $('#btnPlay').onclick = () => runJob('/api/build', { run: true });
  $('#btnFlash').onclick = () => {
    if (!confirm('ビルドしてロムライタ (CUBIC STYLE / GB Cart Flasher) でカートリッジに書き込みます。よろしいですか？')) return;
    runJob('/api/flash', {}, 'カートリッジに書き込み中');
  };
  $('#modalClose').onclick = () => $('#modal').classList.add('hidden');
  $('#chipGbdk').onclick = installGbdk;
  $('#btnIde').onclick = () => openInIde(S.active?.kind === 'file' ? S.active.name : S.active?.kind === 'script' ? `scripts/${S.active.name}.c` : null);
  $('#newType').onchange = () => { const c = $('#newType').value === 'c'; $('#newTargetRow').classList.toggle('hidden', !c); $('#newSampleRow').classList.toggle('hidden', c); };
  $('#btnClose').onclick = async () => { await saveAll(true); await api('POST', '/api/close'); location.reload(); };
  $('#btnOpen').onclick = () => openProject($('#openPath').value.trim());
  $('#btnCreate').onclick = async () => {
    try {
      await api('POST', '/api/create', { path: $('#newPath').value.trim(), name: $('#newName').value.trim(), sample: $('#newSample').checked,
        type: $('#newType').value, target: $('#newTarget').value });
      location.reload();
    } catch (e) { alert(e.message); }
  };
  $('#btnClearLog').onclick = () => { $('#log').innerHTML = ''; };
  $('#btnSettingsSave').onclick = saveSettings;
  $('#btnPorts').onclick = refreshPorts;
  $('#btnAssetDel').onclick = deleteAsset;
  $('#btnCartInfo').onclick = async () => {
    $('#cartInfo').textContent = '読み込み中…';
    try {
      const port = $('#settingsForm').elements['flasher.port'].value;
      const i = await api('POST', '/api/cart/info', port ? { port } : {});
      $('#cartInfo').textContent = `FW ${i.firmware} / ${i.flash_maker} ${i.flash_device_id} / ${i.title || '(空)'} ${i.cart_type}`;
    } catch (e) { $('#cartInfo').textContent = ''; fail(e); }
  };
  window.addEventListener('keydown', (e) => {
    const mod = e.ctrlKey || e.metaKey;
    if (mod && e.key === 's') { e.preventDefault(); saveAll(); }
    else if (mod && e.key === 'b') { e.preventDefault(); $('#btnBuild').click(); }
    else if (e.key === 'F5') { e.preventDefault(); $('#btnPlay').click(); }
    else if (!mod && S.active?.kind === 'scene' && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) {
      if (e.key === 'v') setTool('select');
      if (e.key === 'c') setTool('collision');
      if (e.key === 'a') setTool('actor');
      if ((e.key === 'Delete' || e.key === 'Backspace') && S.sel >= 0) {
        S.scene.actors.splice(S.sel, 1); S.sel = -1; dirtyScene(); renderHierarchy(); renderInspector(); drawScene();
      }
    }
  });
  window.addEventListener('beforeunload', (e) => { if (S.sceneDirty || S.scriptDirty || S.textsDirty) { e.preventDefault(); e.returnValue = ''; } });
}

(async () => {
  setup();
  await refreshStatus();
  setInterval(refreshStatus, 4000);
  if (!S.status || !S.status.project) { await showHome(); if (S.status?.pending_adopt) EXT.showAdopt(S.status.pending_adopt); return; }
  try {
    await loadProject();
    log(`GBForge エディタ v${S.proj.version} — ${S.proj.root}`, 'c');
    if (S.proj.type === 'c') {
      const main = S.proj.sources.find((f) => f === 'main.c' || f.endsWith('/main.c')) || S.proj.sources[0];
      if (main) await openTab('file', main);
    } else {
      const start = S.proj.settings.start_scene || S.proj.scenes[0];
      if (start) await openTab('scene', start);
    }
  } catch (e) { fail(e); }
})();

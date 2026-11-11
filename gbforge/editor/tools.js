/* GBForge エディタ拡張: ステージエディタ / C データ / 変換ツール / バージョン / 既存フォルダの取り込み
 * app.js より先に読み込まれ、app.js の関数 ($, h, api, log, openTab ...) は実行時に参照する。 */
'use strict';

const EXT = {
  views: { stage: 'stageView', carray: 'carrayView', convert: 'convertView', versions: 'versionsView', image: 'assetView' },
  open: {}, inspect: {}, arrays: null,
};
window.EXT = EXT;

const KIND_ICON = { tiles: '▦', palette: '🎨', map: '🗺', data: '#' };
const ST = { name: null, data: null, imgs: {}, tileCount: 0, sel: 0, selEnd: 0, pal: 0, tool: 'paint', selObj: -1, undo: [], drag: null };

// ---------------------------------------------------------------- 共通
EXT.ensureArrays = async (force) => {
  if (EXT.arrays && !force) return EXT.arrays;
  try { EXT.arrays = await api('GET', '/api/carrays'); } catch (e) { EXT.arrays = []; }
  return EXT.arrays;
};
const arrKey = (a) => `${a.file}#${a.name}`;
const arrOf = (key) => (EXT.arrays || []).find((a) => arrKey(a) === key);
const qs = (o) => Object.entries(o).filter(([, v]) => v !== undefined && v !== null && v !== '').map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join('&');

function loadImg(src) {
  return new Promise((res, rej) => { const im = new Image(); im.onload = () => res(im); im.onerror = rej; im.src = src; });
}

/* スクリプト方式 (build.bat) のとき、新しい .c をビルドスクリプトへ追加するボタン */
async function scriptAddBox(files) {
  const box = h('div', {});
  if (S.proj.build_mode !== 'script' || !files.length) return box;
  try {
    const r = await api('GET', '/api/build_script/has?files=' + encodeURIComponent(files.join(',')));
    const missing = files.filter((f) => !r.has[f]);
    if (!missing.length) return box;
    box.append(h('div', { class: 'warn small' }, `${missing.join(', ')} がビルドスクリプトに入っていません`),
      h('button', { onclick: async () => {
        const res = await api('POST', '/api/build_script/add', { files: missing });
        log(`ビルドスクリプトに追加しました: ${res.changed.join(', ')} (元のファイルは .bak に保存)`, 'o');
        box.innerHTML = '';
      } }, '⊕ ビルドスクリプト (build.bat など) に追加'));
  } catch (e) { /* */ }
  return box;
}

// ---------------------------------------------------------------- ツリー
EXT.tree = (t, isSel) => {
  const p = S.proj;
  const item = (kind, name, label, extra) => h('div', { class: 'tree-item' + (isSel(kind, name) ? ' sel' : ''), onclick: () => openTab(kind, name), title: name }, label, extra || null);
  t.append(treeSection('🗺 ステージ (シーン)', (p.stages || []).map((n) => item('stage', n, n)), newStage));
  const imgs = (p.images || []).map((f) => item('image', f, h('img', { class: 'thumb', src: '/file?path=' + encodeURIComponent(f) }), h('span', {}, f)));
  t.append(treeSection('🎨 アセット (画像)', imgs.length ? imgs : [h('div', { class: 'tree-item muted' }, '(PNG なし)')]));
  if (!EXT.arrays) { EXT.ensureArrays().then(() => renderTree()); }
  const arrs = (EXT.arrays || []).filter((a) => a.kind !== 'data' || a.count >= 8);
  t.append(treeSection('🧩 C データ (配列)', arrs.length ? arrs.map((a) => item('carray', arrKey(a),
    `${KIND_ICON[a.kind] || '#'} ${a.name}`, h('span', { class: 'badge' }, `${a.file} · ${a.kind === 'tiles' ? a.count / 16 + '枚' : a.count}`)))
    : [h('div', { class: 'tree-item muted' }, EXT.arrays ? '(配列なし)' : '読み込み中…')]));
  t.append(treeSection('🧰 ツール', [item('convert', 'convert', '画像 → スプライト / タイル / マップ 変換'), item('versions', 'versions', 'バージョン管理')]));
};

// ---------------------------------------------------------------- 取り込み (既存フォルダ)
EXT.showAdopt = (info) => {
  $('#home').classList.add('hidden');
  const m = $('#adopt');
  m.classList.remove('hidden');
  $('#adoptInfo').innerHTML = '';
  $('#adoptInfo').append(h('div', {}, h('b', {}, info.name)), h('div', { class: 'muted' }, info.path),
    h('div', {}, `C ファイル ${info.c_files} / ヘッダー ${info.h_files} / PNG ${info.png_files} / ROM: ${info.roms.join(', ') || 'なし'}`),
    h('div', {}, info.gbdk ? `GBDK を発見: ${info.gbdk}` : h('span', { class: 'warn' }, 'GBDK が見つかりません (同梱/自動セットアップを使います)')));
  $('#adWin').value = info.build_script_windows || '';
  $('#adUnix').value = info.build_script_unix || '';
  $('#adOut').value = info.output || '';
  $('#adGbdk').value = info.gbdk || '';
  $('#adTarget').value = info.target || 'dmg';
  $('#adCancel').onclick = () => { m.classList.add('hidden'); $('#home').classList.remove('hidden'); };
  $('#adOk').onclick = async () => {
    try {
      await api('POST', '/api/adopt', { path: info.path, build_script_windows: $('#adWin').value, build_script_unix: $('#adUnix').value,
        output: $('#adOut').value, gbdk: $('#adGbdk').value, target: $('#adTarget').value });
      location.reload();
    } catch (e) { alert(e.message); }
  };
};

// ---------------------------------------------------------------- 保存
EXT.save = async (quiet) => {
  if (S.stageDirty && ST.name) {
    const r = await api('PUT', '/api/stage/' + encodeURIComponent(ST.name), ST.data);
    S.stageDirty = false;
    if (!quiet) { log(`ステージ ${ST.name} を保存しました`, 'o'); (r.log || []).forEach((l) => log(l, 'o')); }
    else (r.log || []).forEach((l) => log(l, 'c'));
  }
};

// ================================================================ ステージエディタ
async function newStage() {
  const name = prompt('ステージ (シーン) の名前 (半角英数字)', 'stage' + ((S.proj.stages || []).length + 1));
  if (!name) return;
  await EXT.ensureArrays(true);
  const maps = EXT.arrays.filter((a) => a.kind === 'map' && a.parsed);
  const tiles = EXT.arrays.find((a) => a.kind === 'tiles' && a.parsed);
  const pals = EXT.arrays.find((a) => a.kind === 'palette' && a.parsed);
  const tileset = tiles ? { kind: 'carray', file: tiles.file, symbol: tiles.name, palette_file: pals ? pals.file : '', palette_symbol: pals ? pals.name : '' }
    : (S.proj.images[0] ? { kind: 'png', path: S.proj.images[0] } : {});
  let body;
  if (maps.length && confirm(`既存のマップ配列を編集しますか？\n${maps.map((a, i) => `${i + 1}: ${a.name} (${a.file}, ${a.count})`).join('\n')}\n\nOK = 配列を編集 / キャンセル = 新しく作る`)) {
    const i = parseInt(prompt('番号を入力', '1'), 10) - 1;
    if (!maps[i]) return;
    const w = prompt('マップの幅 (タイル数) 空欄 = 自動', '');
    body = { from: 'array', file: maps[i].file, symbol: maps[i].name, width: w ? parseInt(w, 10) : null, tileset };
  } else {
    const sz = prompt('幅,高さ (タイル数)  例: 20,18 / 縦スクロール 20,128', '20,32');
    if (!sz) return;
    const [w, hh] = sz.split(/[,x×\s]+/).map((v) => parseInt(v, 10));
    body = { from: 'new', width: w || 20, height: hh || 18, tileset };
  }
  try {
    await api('POST', '/api/stage/' + encodeURIComponent(name), body);
    await loadProject();
    openTab('stage', name);
  } catch (e) { fail(e); }
}

EXT.open.stage = async (name) => {
  ST.name = name;
  ST.data = await api('GET', '/api/stage/' + encodeURIComponent(name));
  ST.imgs = {}; ST.undo = []; ST.selObj = -1;
  S.stageDirty = false;
  $('#stageName').textContent = '🗺 ' + name + (ST.data.export?.mode === 'array' ? `  (→ ${ST.data.export.file} の ${ST.data.export.map_symbol})` : '  (→ stage_data.c)');
  const ps = $('#stPal');
  ps.innerHTML = '';
  for (let i = 0; i < 8; i++) ps.append(h('option', { value: i }, String(i)));
  ps.value = ST.pal;
  await loadTileset();
  drawStage();
  markSaved();
};

async function loadTileset() {
  ST.imgs = {};
  const r = await fetch(`/api/stage/${encodeURIComponent(ST.name)}/tileset.png?pal=0&t=${Date.now()}`);
  ST.tileCount = parseInt(r.headers.get('X-Tile-Count') || '0', 10);
  ST.imgs[0] = await loadImg(URL.createObjectURL(await r.blob()));
  drawTileset();
}
async function tilesetFor(pal) {
  const ts = ST.data.tileset || {};
  if (!pal || ts.kind !== 'carray' || !ts.palette_symbol) return ST.imgs[0];
  if (!ST.imgs[pal]) {
    ST.imgs[pal] = 'loading';
    loadImg(`/api/stage/${encodeURIComponent(ST.name)}/tileset.png?pal=${pal}&t=${Date.now()}`).then((im) => { ST.imgs[pal] = im; drawStage(); drawTileset(); });
  }
  return ST.imgs[pal] === 'loading' ? ST.imgs[0] : ST.imgs[pal];
}

function drawTileset() {
  const im = ST.imgs[ST.pal] && ST.imgs[ST.pal] !== 'loading' ? ST.imgs[ST.pal] : ST.imgs[0];
  const cv = $('#tilesetCanvas'), z = 2;   /* 16 列 × 2 倍 = 256px */
  if (!im) return;
  cv.width = im.width * z; cv.height = im.height * z;
  const x = cv.getContext('2d');
  x.imageSmoothingEnabled = false;
  x.drawImage(im, 0, 0, cv.width, cv.height);
  x.strokeStyle = 'rgba(255,255,255,.12)';
  for (let gx = 0; gx <= 16; gx++) { x.beginPath(); x.moveTo(gx * 8 * z + .5, 0); x.lineTo(gx * 8 * z + .5, cv.height); x.stroke(); }
  for (let gy = 0; gy <= cv.height / (8 * z); gy++) { x.beginPath(); x.moveTo(0, gy * 8 * z + .5); x.lineTo(cv.width, gy * 8 * z + .5); x.stroke(); }
  x.lineWidth = 2; x.strokeStyle = '#ffd34d';
  x.strokeRect((ST.sel % 16) * 8 * z, Math.floor(ST.sel / 16) * 8 * z, 8 * z, 8 * z);
  x.lineWidth = 1;
  $('#stSelInfo').textContent = `タイル ${ST.sel} (マップの値 ${ST.sel + (ST.data.tile_origin || 0)}) / 全 ${ST.tileCount} 枚 / パレット ${ST.pal}`;
}

async function drawStage() {
  const d = ST.data;
  if (!d) return;
  const z = +$('#stZoom').value, cv = $('#stageCanvas');
  cv.width = d.width * 8 * z; cv.height = d.height * 8 * z;
  const x = cv.getContext('2d');
  x.imageSmoothingEnabled = false;
  x.fillStyle = '#000'; x.fillRect(0, 0, cv.width, cv.height);
  const imgs = {};
  for (let i = 0; i < d.tiles.length; i++) {
    const pal = d.attrs ? d.attrs[i] & 7 : 0;
    if (!imgs[pal]) imgs[pal] = await tilesetFor(pal);
    const im = imgs[pal], t = d.tiles[i];
    const dx = (i % d.width) * 8 * z, dy = Math.floor(i / d.width) * 8 * z;
    if (im && t < ST.tileCount) x.drawImage(im, (t % 16) * 8, Math.floor(t / 16) * 8, 8, 8, dx, dy, 8 * z, 8 * z);
    else { x.fillStyle = '#802050'; x.fillRect(dx, dy, 8 * z, 8 * z); }
  }
  if ($('#stAttr').checked && d.attrs) {
    x.font = `${Math.max(8, 4 * z)}px monospace`; x.fillStyle = 'rgba(255,255,0,.9)';
    d.attrs.forEach((a, i) => x.fillText(String(a & 7), (i % d.width) * 8 * z + 1, Math.floor(i / d.width) * 8 * z + 4 * z + 2));
  }
  if ($('#stGrid').checked) {
    x.strokeStyle = 'rgba(255,255,255,.10)';
    for (let gx = 0; gx <= d.width; gx++) { x.beginPath(); x.moveTo(gx * 8 * z + .5, 0); x.lineTo(gx * 8 * z + .5, cv.height); x.stroke(); }
    for (let gy = 0; gy <= d.height; gy++) { x.beginPath(); x.moveTo(0, gy * 8 * z + .5); x.lineTo(cv.width, gy * 8 * z + .5); x.stroke(); }
  }
  if ($('#stScreen').checked) {
    x.setLineDash([6, 4]); x.strokeStyle = '#3fb950'; x.lineWidth = 2;
    for (let sy = 0; sy < d.height * 8; sy += 144) x.strokeRect(1, sy * z + 1, Math.min(160, d.width * 8) * z - 2, Math.min(144, d.height * 8 - sy) * z - 2);
    x.setLineDash([]); x.lineWidth = 1;
  }
  const types = S.proj.object_types || [];
  (d.objects || []).forEach((o, i) => {
    const t = types.find((tt) => tt.name === o.type) || { color: '#ccc' };
    const ox = o.x * z, oy = o.y * z;
    x.fillStyle = t.color + '99'; x.fillRect(ox, oy, 16 * z, 16 * z);
    x.strokeStyle = i === ST.selObj ? '#ffd34d' : t.color; x.lineWidth = i === ST.selObj ? 3 : 2;
    x.strokeRect(ox + 1, oy + 1, 16 * z - 2, 16 * z - 2); x.lineWidth = 1;
    x.fillStyle = '#fff'; x.font = `bold ${Math.max(9, 4 * z)}px sans-serif`;
    x.fillText(o.type, ox + 2, oy + 5 * z);
  });
  if (ST.drag && ST.drag.rect) {
    const r = ST.drag.rect;
    x.strokeStyle = '#ffd34d'; x.lineWidth = 2;
    x.strokeRect(Math.min(r.x0, r.x1) * 8 * z, Math.min(r.y0, r.y1) * 8 * z, (Math.abs(r.x1 - r.x0) + 1) * 8 * z, (Math.abs(r.y1 - r.y0) + 1) * 8 * z);
    x.lineWidth = 1;
  }
}

function stagePos(ev) {
  const cv = $('#stageCanvas'), r = cv.getBoundingClientRect(), z = +$('#stZoom').value;
  const px = (ev.clientX - r.left) / z, py = (ev.clientY - r.top) / z;
  return { px, py, tx: Math.floor(px / 8), ty: Math.floor(py / 8) };
}
function pushUndo() {
  ST.undo.push(JSON.stringify({ tiles: ST.data.tiles, attrs: ST.data.attrs, objects: ST.data.objects }));
  if (ST.undo.length > 60) ST.undo.shift();
}
function stageDirty() { S.stageDirty = true; markSaved(); }
function setCell(tx, ty) {
  const d = ST.data;
  if (tx < 0 || ty < 0 || tx >= d.width || ty >= d.height) return false;
  const i = ty * d.width + tx;
  if (ST.tool === 'attr') {
    if (!d.attrs) d.attrs = new Array(d.tiles.length).fill(0);
    if ((d.attrs[i] & 7) === ST.pal) return false;
    d.attrs[i] = (d.attrs[i] & ~7) | ST.pal;
    return true;
  }
  if (d.tiles[i] === ST.sel) return false;
  d.tiles[i] = ST.sel;
  if (d.attrs && ST.data.tileset?.palette_symbol) d.attrs[i] = (d.attrs[i] & ~7) | ST.pal;
  return true;
}
function objAt(px, py) {
  const os = ST.data.objects || [];
  for (let i = os.length - 1; i >= 0; i--) if (px >= os[i].x && px < os[i].x + 16 && py >= os[i].y && py < os[i].y + 16) return i;
  return -1;
}
function setStageTool(t) {
  ST.tool = t;
  $$('#stageTools button').forEach((b) => b.classList.toggle('active', b.dataset.tool === t));
}

function setupStage() {
  const cv = $('#stageCanvas');
  cv.addEventListener('contextmenu', (e) => e.preventDefault());
  cv.addEventListener('mousedown', (e) => {
    if (!ST.data) return;
    const p = stagePos(e);
    const d = ST.data;
    if (e.altKey || ST.tool === 'pick' || e.button === 2) {        // スポイト
      const i = p.ty * d.width + p.tx;
      if (d.tiles[i] !== undefined) { ST.sel = d.tiles[i]; if (d.attrs) { ST.pal = d.attrs[i] & 7; $('#stPal').value = ST.pal; } drawTileset(); }
      return;
    }
    if (ST.tool === 'object') {
      const i = objAt(p.px, p.py);
      if (i >= 0) { ST.selObj = i; pushUndo(); ST.drag = { obj: i, dx: p.px - d.objects[i].x, dy: p.py - d.objects[i].y }; }
      else {
        const types = S.proj.object_types || [];
        if (!types.length) { log('先にインスペクターで「オブジェクトの種類」を追加してください', 'w'); return; }
        pushUndo();
        const t = types[0];
        d.objects.push({ type: ST.lastType || t.name, x: Math.max(0, Math.floor((p.px - 8) / 8) * 8), y: Math.max(0, Math.floor((p.py - 8) / 8) * 8), props: {} });
        ST.selObj = d.objects.length - 1;
        stageDirty();
      }
      drawStage(); renderInspector();
      return;
    }
    pushUndo();
    if (ST.tool === 'rect') { ST.drag = { rect: { x0: p.tx, y0: p.ty, x1: p.tx, y1: p.ty } }; drawStage(); return; }
    ST.drag = { paint: true };
    if (setCell(p.tx, p.ty)) { stageDirty(); drawStage(); }
  });
  window.addEventListener('mousemove', (e) => {
    if (!ST.drag || !ST.data) return;
    const p = stagePos(e);
    if (ST.drag.paint) { if (setCell(p.tx, p.ty)) { stageDirty(); drawStage(); } }
    else if (ST.drag.rect) { ST.drag.rect.x1 = p.tx; ST.drag.rect.y1 = p.ty; drawStage(); }
    else if (ST.drag.obj !== undefined) {
      const o = ST.data.objects[ST.drag.obj];
      const nx = Math.max(0, Math.round((p.px - ST.drag.dx) / (e.shiftKey ? 1 : 8)) * (e.shiftKey ? 1 : 8));
      const ny = Math.max(0, Math.round((p.py - ST.drag.dy) / (e.shiftKey ? 1 : 8)) * (e.shiftKey ? 1 : 8));
      if (nx !== o.x || ny !== o.y) { o.x = nx; o.y = ny; stageDirty(); drawStage(); }
    }
  });
  window.addEventListener('mouseup', () => {
    if (ST.drag && ST.drag.rect) {
      const r = ST.drag.rect;
      for (let y = Math.min(r.y0, r.y1); y <= Math.max(r.y0, r.y1); y++)
        for (let x = Math.min(r.x0, r.x1); x <= Math.max(r.x0, r.x1); x++) setCell(x, y);
      stageDirty(); ST.drag = null; drawStage(); return;
    }
    if (ST.drag && ST.drag.obj !== undefined) renderInspector();
    ST.drag = null;
  });
  $('#tilesetCanvas').addEventListener('click', (e) => {
    const r = e.target.getBoundingClientRect();
    const t = Math.floor((e.clientY - r.top) / 16) * 16 + Math.floor((e.clientX - r.left) / 16);
    if (t < ST.tileCount) { ST.sel = t; if (ST.tool === 'pick' || ST.tool === 'object' || ST.tool === 'attr') setStageTool('paint'); drawTileset(); }
  });
  $('#stPal').addEventListener('change', async (e) => { ST.pal = +e.target.value; await tilesetFor(ST.pal); drawTileset(); });
  $$('#stageTools button').forEach((b) => b.addEventListener('click', () => setStageTool(b.dataset.tool)));
  ['stGrid', 'stAttr', 'stScreen', 'stZoom'].forEach((id) => $('#' + id).addEventListener('change', drawStage));
  $('#btnStageSave').onclick = () => saveAll();
  $('#btnStageUndo').onclick = undoStage;
  window.addEventListener('keydown', (e) => {
    if (S.active?.kind !== 'stage' || /INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) return;
    const mod = e.ctrlKey || e.metaKey;
    if (mod && e.key === 'z') { e.preventDefault(); undoStage(); return; }
    if (mod) return;
    const map = { b: 'paint', r: 'rect', i: 'pick', p: 'attr', o: 'object' };
    if (map[e.key]) setStageTool(map[e.key]);
    if ((e.key === 'Delete' || e.key === 'Backspace') && ST.selObj >= 0) {
      pushUndo(); ST.data.objects.splice(ST.selObj, 1); ST.selObj = -1; stageDirty(); drawStage(); renderInspector();
    }
  });
}
function undoStage() {
  const u = ST.undo.pop();
  if (!u) return;
  Object.assign(ST.data, JSON.parse(u));
  ST.selObj = -1; stageDirty(); drawStage(); renderInspector();
}

function resizeStage(w, hh) {
  const d = ST.data;
  pushUndo();
  const nt = new Array(w * hh).fill(0), na = d.attrs ? new Array(w * hh).fill(0) : null;
  for (let y = 0; y < Math.min(hh, d.height); y++) for (let x = 0; x < Math.min(w, d.width); x++) {
    nt[y * w + x] = d.tiles[y * d.width + x];
    if (na) na[y * w + x] = d.attrs[y * d.width + x];
  }
  Object.assign(d, { width: w, height: hh, tiles: nt, attrs: na });
  stageDirty(); drawStage(); renderInspector();
}

EXT.inspect.stage = async (el) => {
  const d = ST.data;
  if (!d) return;
  await EXT.ensureArrays();
  const tiles = EXT.arrays.filter((a) => a.kind === 'tiles'), pals = EXT.arrays.filter((a) => a.kind === 'palette');
  const ts = d.tileset || {};
  const wIn = h('input', { type: 'number', value: d.width, min: 1, max: 1024 }), hIn = h('input', { type: 'number', value: d.height, min: 1, max: 1024 });
  const tsKind = select([['carray', 'C 配列のタイル'], ['png', 'PNG 画像']], ts.kind || 'carray', async (v) => { d.tileset = { kind: v }; stageDirty(); renderInspector(); });
  const tsSrc = ts.kind === 'png'
    ? prop('画像', select(S.proj.images, ts.path, async (v) => { d.tileset.path = v; stageDirty(); await saveAll(true); await loadTileset(); drawStage(); }, '(選択)'))
    : h('div', {},
      prop('タイル配列', select(tiles.map((a) => [arrKey(a), `${a.name} (${a.file}, ${a.count / 16}枚)`]), ts.file ? `${ts.file}#${ts.symbol}` : '', async (v) => {
        const a = arrOf(v); d.tileset = Object.assign(d.tileset || {}, { kind: 'carray', file: a.file, symbol: a.name }); stageDirty(); await saveAll(true); await loadTileset(); drawStage();
      }, '(選択)')),
      prop('パレット配列', select(pals.map((a) => [arrKey(a), `${a.name} (${a.file})`]), ts.palette_symbol ? `${ts.palette_file}#${ts.palette_symbol}` : '', async (v) => {
        const a = v ? arrOf(v) : null; Object.assign(d.tileset, { palette_file: a ? a.file : '', palette_symbol: a ? a.name : '' }); stageDirty(); await saveAll(true); await loadTileset(); drawStage();
      }, '(初代の 4 階調)')));
  el.append(h('div', { class: 'comp' }, h('div', { class: 'comp-title' }, '🗺 ステージ: ' + ST.name),
    prop('大きさ', h('div', { class: 'row' }, wIn, '×', hIn, h('button', { onclick: () => resizeStage(+wIn.value, +hIn.value) }, '変更'))),
    h('div', { class: 'hint' }, `${d.width * 8}×${d.height * 8} px / 画面 ${Math.ceil(d.width / 20)}×${Math.ceil(d.height / 18)} 枚分`),
    prop('タイルの元', tsKind), tsSrc,
    prop('タイル番号のずれ', num(d.tile_origin || 0, (v) => { d.tile_origin = v; stageDirty(); drawTileset(); }, 0, 255), 'マップに書く値 = タイル番号 + この値 (VRAM の途中に読み込む場合)'),
    prop('CGB 属性', h('input', { type: 'checkbox', checked: !!d.attrs, onchange: (e) => { d.attrs = e.target.checked ? new Array(d.tiles.length).fill(0) : null; stageDirty(); drawStage(); } }), 'タイルごとのパレット番号を保存 (カラー用)'),
    h('div', { class: 'hint' }, d.export?.mode === 'array'
      ? `保存すると ${d.export.file} の配列 ${d.export.map_symbol} を書き換えます (他の部分はそのまま)。`
      : `保存すると ${S.proj.settings.stages_output || 'stage_data'}.c / .h に出力。C からは #include "${S.proj.settings.stages_output || 'stage_data'}.h" → stage_${ST.name}.map / .objects`),
    await scriptAddBox((d.export?.mode !== 'array' || (d.objects || []).length) ? [(S.proj.settings.stages_output || 'stage_data') + '.c'] : []),
    h('button', { class: 'danger', onclick: async () => { if (!confirm(`ステージ ${ST.name} を削除しますか？ (C ソースは消えません)`)) return; await api('DELETE', '/api/stage/' + ST.name); S.stageDirty = false; const t = S.active; await loadProject(); closeTab(t); } }, 'ステージを削除')));

  // オブジェクトの種類 (プレハブ)
  const types = S.proj.object_types || [];
  const typeBox = h('div', { class: 'comp' }, h('div', { class: 'comp-title' }, '◆ オブジェクトの種類'));
  const saveTypes = async () => { await api('PUT', '/api/object_types', S.proj.object_types); stageDirty(); drawStage(); };
  types.forEach((t, i) => typeBox.append(h('div', { class: 'obj-type' },
    h('input', { value: t.name, class: 'mono', onchange: (e) => { const old = t.name; t.name = e.target.value.replace(/\W/g, '_'); d.objects.forEach((o) => { if (o.type === old) o.type = t.name; }); saveTypes(); } }),
    h('input', { type: 'color', value: t.color || '#cccccc', onchange: (e) => { t.color = e.target.value; saveTypes(); } }),
    h('input', { value: (t.fields || []).join(','), placeholder: '値の名前 (例: speed,lane)', title: '最大 4 個', onchange: (e) => { t.fields = e.target.value.split(/[,\s]+/).filter(Boolean).slice(0, 4); saveTypes(); renderInspector(); } }),
    h('button', { class: 'tiny', onclick: () => { types.splice(i, 1); saveTypes(); renderInspector(); } }, '×'))));
  typeBox.append(h('button', { onclick: () => { types.push({ name: 'obj' + (types.length + 1), color: '#4c8bf5', fields: [] }); S.proj.object_types = types; saveTypes(); renderInspector(); } }, '＋ 種類を追加'),
    h('div', { class: 'hint' }, 'C では OBJ_名前 の番号と、p[0]〜p[3] に値が入った stage_object_t の配列になります'));
  el.append(typeBox);

  const o = d.objects[ST.selObj];
  if (o) {
    const t = types.find((tt) => tt.name === o.type);
    const box = h('div', { class: 'comp' }, h('div', { class: 'comp-title' }, `選択中のオブジェクト #${ST.selObj}`),
      prop('種類', select(types.map((tt) => tt.name), o.type, (v) => { o.type = v; ST.lastType = v; stageDirty(); drawStage(); renderInspector(); })),
      prop('X (px)', num(o.x, (v) => { o.x = v; stageDirty(); drawStage(); })),
      prop('Y (px)', num(o.y, (v) => { o.y = v; stageDirty(); drawStage(); })));
    ((t && t.fields) || []).forEach((f) => box.append(prop(f, num((o.props || {})[f] || 0, (v) => { o.props = o.props || {}; o.props[f] = v; stageDirty(); }))));
    box.append(h('button', { class: 'danger', onclick: () => { pushUndo(); d.objects.splice(ST.selObj, 1); ST.selObj = -1; stageDirty(); drawStage(); renderInspector(); } }, '削除 (Delete)'));
    el.append(box);
  }
  el.append(h('div', { class: 'hint' }, '操作: B=タイル R=矩形 I=スポイト(右クリック/Alt) P=パレット O=オブジェクト (Shift でドット単位移動) / Ctrl+Z 元に戻す'));
};

// ================================================================ C データ (配列)
EXT.open.carray = async (key) => {
  const [file, name] = key.split('#');
  await EXT.ensureArrays();
  const a = await api('GET', `/api/carray?${qs({ file, name })}`);
  $('#carrayTitle').textContent = `${KIND_ICON[a.kind] || '#'} ${a.name}`;
  $('#carrayInfo').textContent = `  ${a.file}:${a.line} / ${a.ctype} / ${a.count} 要素${a.kind === 'tiles' ? ` (${a.count / 16} タイル)` : ''}`;
  const body = $('#carrayBody');
  body.innerHTML = '';
  if (!a.parsed) body.append(h('div', { class: 'warn' }, '数値以外 (マクロなど) を含むため、表示・編集できない要素があります'));
  const pals = EXT.arrays.filter((x) => x.kind === 'palette');
  if (a.kind === 'tiles') {
    const st = EXT.tileView || (EXT.tileView = { pal: pals[0] ? arrKey(pals[0]) : '', idx: 0, order: 'tiles', scale: 4, cols: 16 });
    const img = h('img', { class: 'pix' });
    const params = () => { const p = st.pal ? arrOf(st.pal) : null; return qs({ file, name, pal_file: p?.file, pal_name: p?.name, pal_index: st.idx, order: st.order, cols: st.cols, scale: st.scale }); };
    const refresh = () => { img.src = `/api/carray/png?${params()}&t=${Date.now()}`; dl.href = `/api/carray/png?${params().replace(/scale=\d+/, 'scale=1')}&download=1`; };
    const dl = h('a', { class: 'btnlike' }, h('button', {}, '⤓ PNG で書き出し'));
    const fi = h('input', { type: 'file', accept: 'image/png', class: 'hidden', onchange: async () => {
      const f = fi.files[0]; if (!f) return;
      if (!confirm(`${f.name} の内容で ${name} を書き換えます。よろしいですか？ (パレットの色に一番近い色に変換)`)) return;
      try {
        const p = st.pal ? arrOf(st.pal) : null;
        const r = await api('POST', `/api/carray/png?${qs({ file, name, pal_file: p?.file, pal_name: p?.name, pal_index: st.idx })}`, undefined, await f.arrayBuffer());
        log(`${name} を PNG から更新しました (${r.tiles} タイル)`, 'o'); EXT.arrays = null; refresh();
      } catch (e) { fail(e); }
    } });
    body.append(h('div', { class: 'row' },
      h('label', {}, 'パレット ', select(pals.map((x) => [arrKey(x), x.name]), st.pal, (v) => { st.pal = v || ''; refresh(); }, '初代 4 階調')),
      h('label', {}, ' 番号 ', select([0, 1, 2, 3, 4, 5, 6, 7].map((i) => [String(i), String(i)]), String(st.idx), (v) => { st.idx = +v; refresh(); })),
      h('label', {}, ' 並び ', select([['tiles', '8x8 タイル順'], ['sprite8x16', '8x16 スプライト (16x16)']], st.order, (v) => { st.order = v; refresh(); })),
      h('label', {}, ' 列 ', num(st.cols, (v) => { st.cols = Math.max(1, v); refresh(); }, 1, 64)),
      h('label', {}, ' 拡大 ', select(['2', '3', '4', '6', '8'], String(st.scale), (v) => { st.scale = +v; refresh(); }))),
      h('div', {}, img),
      h('div', { class: 'row' }, dl, h('button', { onclick: () => fi.click() }, '⤒ PNG から読み込んで書き換え'), fi),
      h('div', { class: 'hint' }, '書き出した PNG をお好みのドット絵ツールで編集し、「PNG から読み込んで書き換え」で C ソースの配列に戻せます (ソースの他の部分は変更しません)。'));
    refresh();
  } else if (a.kind === 'palette') {
    const vals = a.values ? a.values.slice() : [];
    const toHex = (v) => '#' + [v & 31, (v >> 5) & 31, (v >> 10) & 31].map((c) => ((c << 3) | (c >> 2)).toString(16).padStart(2, '0')).join('');
    const fromHex = (s) => { const n = parseInt(s.slice(1), 16); return ((n >> 19) & 31) | (((n >> 11) & 31) << 5) | (((n >> 3) & 31) << 10); };
    const box = h('div', {});
    for (let p = 0; p < vals.length; p += 4) {
      const row = h('div', { class: 'pal-edit' }, h('span', { class: 'muted' }, `パレット ${p / 4}`));
      for (let i = p; i < Math.min(p + 4, vals.length); i++)
        row.append(h('input', { type: 'color', value: toHex(vals[i]), title: `RGB(${vals[i] & 31}, ${(vals[i] >> 5) & 31}, ${(vals[i] >> 10) & 31})`, onchange: (e) => { vals[i] = fromHex(e.target.value); } }));
      box.append(row);
    }
    body.append(box, h('button', { class: 'play', onclick: async () => { try { await api('PUT', `/api/carray?${qs({ file, name })}`, { values: vals }); log(`${name} を保存しました`, 'o'); } catch (e) { fail(e); } } }, 'パレットを保存 (C ソースに書き戻し)'),
      h('div', { class: 'hint' }, 'ゲームボーイカラーの色は各 5 ビット (RGB 0〜31) です。'));
  } else {
    const ta = h('textarea', { rows: 14, class: 'mono', style: 'width:100%' }, (a.values || []).join(', '));
    body.append(
      a.kind === 'map' ? h('div', { class: 'row' }, h('button', { class: 'play', onclick: async () => {
        const sname = prompt('ステージ名 (この配列をステージエディタで編集します)', name.replace(/\W/g, '_'));
        if (!sname) return;
        const w = prompt('幅 (タイル数) 空欄 = 自動推定', '');
        const t = EXT.arrays.find((x) => x.kind === 'tiles'), p = EXT.arrays.find((x) => x.kind === 'palette');
        try {
          await api('POST', '/api/stage/' + encodeURIComponent(sname), { from: 'array', file, symbol: name, width: w ? +w : null,
            tileset: t ? { kind: 'carray', file: t.file, symbol: t.name, palette_file: p?.file || '', palette_symbol: p?.name || '' } : {} });
          await loadProject(); openTab('stage', sname);
        } catch (e) { fail(e); }
      } }, '🗺 ステージエディタで編集')) : null,
      ta, h('button', { onclick: async () => {
        const vals = ta.value.split(/[\s,]+/).filter(Boolean).map((v) => parseInt(v, v.startsWith('0x') ? 16 : 10));
        if (vals.some((v) => Number.isNaN(v))) { log('数値以外が含まれています', 'w'); return; }
        try { await api('PUT', `/api/carray?${qs({ file, name })}`, { values: vals }); log(`${name} を保存しました`, 'o'); EXT.arrays = null; } catch (e) { fail(e); }
      } }, '値を保存 (C ソースに書き戻し)'));
  }
};
EXT.inspect.carray = (el) => el.append(h('div', { class: 'hint' }, 'C ソースの配列を直接表示・編集しています。書き戻しは初期化子 { … } の中だけで、コメントや他のコードはそのままです。'));

// ================================================================ 画像 (アセット)
EXT.open.image = async (path) => {
  S.asset = { kind: 'image', name: path };
  $('#assetTitle').textContent = '🖼 ' + path;
  const body = $('#assetBody');
  body.innerHTML = '';
  const im = await loadImg('/file?path=' + encodeURIComponent(path) + '&t=' + Date.now());
  const cv = h('canvas', { width: im.width * 4, height: im.height * 4 });
  const x = cv.getContext('2d'); x.imageSmoothingEnabled = false; x.drawImage(im, 0, 0, cv.width, cv.height);
  body.append(h('div', { class: 'hint' }, `${im.width}×${im.height}px`), cv,
    h('div', {}, h('button', { class: 'play', onclick: () => { EXT.cvPreset = path; openTab('convert', 'convert'); } }, '🧰 変換ツールで開く (スプライト / タイル / マップへ)')));
};
EXT.inspect.image = (el) => el.append(h('div', { class: 'hint' }, '「変換ツールで開く」で GB / GBC 用の C データ (.c / .h) に変換できます。'));

// ================================================================ 変換ツール
let cvUpload = null;
EXT.open.convert = async () => {
  const sel = $('#cvSrc');
  sel.innerHTML = '';
  sel.append(h('option', { value: '' }, '(選択)'));
  (S.proj.images || []).forEach((f) => sel.append(h('option', { value: f }, f)));
  if (EXT.cvPreset) { sel.value = EXT.cvPreset; cvUpload = null; $('#cvName').value = EXT.cvPreset.split('/').pop().replace(/\.png$/i, '').replace(/\W/g, '_'); $('#cvOrig').src = '/file?path=' + encodeURIComponent(EXT.cvPreset); EXT.cvPreset = null; }
  $('#cvTarget').value = S.proj.target === 'dmg' ? 'dmg' : 'cgb';
};
async function runConvert(save) {
  const src = $('#cvSrc').value;
  if (!src && !cvUpload) { log('画像を選んでください', 'w'); return; }
  const params = qs({ src: cvUpload ? '' : src, mode: $('#cvMode').value, target: $('#cvTarget').value, sw: $('#cvSw').value, sh: $('#cvSh').value,
    origin: $('#cvOrigin').value, bank: $('#cvBank').value, dedupe: $('#cvDedupe').checked ? 1 : 0, name: $('#cvName').value.trim() });
  try {
    const r = await api('POST', `/api/convert${save ? '/save' : ''}?${params}`, undefined, cvUpload || undefined);
    $('#cvPrev').src = 'data:image/png;base64,' + r.preview;
    $('#cvStats').innerHTML = '';
    $('#cvStats').append(h('div', {}, `タイル ${r.tile_count} 枚` + (r.map_w ? ` / マップ ${r.map_w}×${r.map_h}` : '') + (r.frames ? ` / ${r.frames} フレーム` : '') + ` / パレット ${r.palettes.length} 本`),
      ...r.warnings.map((w) => h('div', { class: 'warn' }, w)));
    const pals = $('#cvPals');
    pals.innerHTML = '';
    pals.append(h('div', { class: 'swatches' }, ...r.palettes.map((p, i) => h('div', { title: `パレット ${i}`, class: 'swatch-group' }, ...p.map((c) => h('span', { style: `background:rgb(${c.join(',')})` }))))));
    $('#cvCode').textContent = r.h + '\n/* ---- .c ---- */\n' + r.c.slice(0, 3000) + (r.c.length > 3000 ? '\n…' : '');
    if (save) {
      log(`保存しました: ${r.files.join(', ')}`, 'o'); await loadProject(); EXT.arrays = null; renderTree();
      $('#cvStats').append(await scriptAddBox(r.files.filter((f) => f.endsWith('.c'))));
    }
  } catch (e) { fail(e); }
}
function setupConvert() {
  $('#cvSrc').addEventListener('change', (e) => {
    cvUpload = null; $('#cvFile').value = '';
    if (e.target.value) { $('#cvOrig').src = '/file?path=' + encodeURIComponent(e.target.value); $('#cvName').value = e.target.value.split('/').pop().replace(/\.png$/i, '').replace(/\W/g, '_'); }
  });
  $('#cvFile').addEventListener('change', async (e) => {
    const f = e.target.files[0]; if (!f) return;
    cvUpload = await f.arrayBuffer(); $('#cvSrc').value = '';
    $('#cvOrig').src = URL.createObjectURL(f); $('#cvName').value = f.name.replace(/\.png$/i, '').replace(/\W/g, '_');
  });
  $('#btnCvPreview').onclick = () => runConvert(false);
  $('#btnCvSave').onclick = () => runConvert(true);
}
EXT.inspect.convert = (el) => el.append(h('div', { class: 'hint' },
  'ゲームボーイカラーでは 8×8 (スプライト 8x16 では 8×16) ごとの色から BG/OBJ パレット (最大 8 本 × 4 色) を自動で組み立てます。スプライトは透明 + 3 色。保存すると .c / .h ができ、ビルドに自動で含まれます (スクリプト方式の場合は build.bat に追加してください)。'));

// ================================================================ バージョン
EXT.open.versions = async () => {
  const r = await api('GET', '/api/versions');
  const sug = $('#verSuggest');
  sug.innerHTML = '';
  r.suggest.forEach((s) => sug.append(h('option', { value: s }, s)));
  sug.onchange = () => { $('#verName').value = sug.value; };
  $('#verName').value = r.suggest[0] || '';
  const tb = $('#verTable');
  tb.innerHTML = '';
  r.versions.slice().reverse().forEach((v) => tb.append(h('tr', { class: v.current ? 'cur' : '' },
    h('td', {}, v.current ? '▶ ' : '', v.name), h('td', { class: 'muted' }, v.version ? 'v' + v.version : ''),
    h('td', { class: 'muted' }, new Date(v.mtime * 1000).toLocaleString()),
    h('td', {}, v.current ? '(開いています)' : h('button', { onclick: () => openProject(v.path) }, '開く')))));
  $('#btnVerSave').onclick = async () => {
    const name = $('#verName').value.trim();
    if (!name) return;
    await saveAll(true);
    try {
      const res = await api('POST', '/api/versions', { name, zip: $('#verZip').checked, open: $('#verOpen').checked });
      log(`新しいバージョンを作りました: ${res.path}${res.zip ? ' / ' + res.zip : ''}`, 'o');
      if ($('#verOpen').checked) location.reload(); else EXT.open.versions();
    } catch (e) { fail(e); }
  };
};
EXT.inspect.versions = (el) => el.append(h('div', { class: 'hint' }, '同じフォルダにある「名前_v番号」のフォルダを同じプロジェクトの版として一覧します。'));

document.addEventListener('DOMContentLoaded', () => { setupStage(); setupConvert(); });

/* GB Media Studio — 写真・動画を GB / GBC 用に変換し、タイムラインで演出を付ける。
 * tools.js の後、app.js の前に読み込まれる。app.js の関数 ($, h, api, log, openTab ...) は実行時に参照する。
 * プレビューの画面は必ずサーバーが「書き出すデータ」を再生ライブラリと同じ規則で再生した結果 (/api/media/frames)。 */
'use strict';

const MEDIA = { list: null };
window.MEDIA = MEDIA;
const GBFPS = 59.7275;
const f2s = (f) => f / GBFPS;
const s2f = (s) => Math.round(s * GBFPS);
const M = {
  path: null, proj: null, info: null, undo: [], redo: [], scene: 0, sel: null,
  frame: 0, playing: false, frames: null, screens: {}, stale: true, zoom: 3,
  building: false, rebuild: false, buildTimer: null, saveTimer: null, dirty: false,
  report: null, diags: [], probe: {}, prevTimer: null, origKey: null, lastTs: 0,
};
const DMG_SHADES = { dmg_green: [[224, 248, 208], [136, 192, 112], [52, 104, 86], [8, 24, 32]], dmg_gray: [[255, 255, 255], [170, 170, 170], [85, 85, 85], [0, 0, 0]] };
const FX_COLOR = { fade_in_black: '#3b5b8c', fade_out_black: '#2c3e66', fade_in_white: '#8c8c5b', fade_out_white: '#6e6e45', flash_white: '#b0a040', flash_black: '#555', flicker: '#8c4b3b', film: '#7a5a3a', random: '#5a3a7a', horror: '#8c2b2b' };
const TIP = {
  preset: '写真らしさを残すための設定のまとめ。選ぶと下の値がその設定になります (個別に変えた値はそのプリセットの上書きになります)',
  fit: 'fill = 画面いっぱいにしてはみ出しを切る / fit = 全体を入れて余白を足す / stretch = 縦横比を無視して引き伸ばす',
  zoom: '拡大して一部を切り出す (1 = そのまま)', crop_x: '切り出す位置 (横)。0 = 左端、1 = 右端', crop_y: '切り出す位置 (縦)。0 = 上、1 = 下',
  pad: 'fit のときの余白の色', brightness: '明るさ (-100〜100)', contrast: 'コントラスト (-100〜100)。上げると白黒がはっきりする',
  gamma: 'ガンマ。1 より大きいと中間の明るさが明るくなる (暗い写真に)', saturation: '彩度 (カラーのみ)',
  black_point: 'これより暗い所を黒にする (レベル補正)', white_point: 'これより明るい所を白にする (レベル補正)',
  sharpen: '輪郭の強調。小さな画面では少し強めると見やすい', denoise: 'ノイズ除去。ざらつきを減らすとタイル数も減る',
  grayscale: 'カラーでも白黒にする', dither: 'ディザリング (少ない色で中間の明るさを点の混ぜ方で表す)。none はベタ塗り。Bayer は規則的な模様で動画のちらつきが少ない',
  dither_strength: 'ディザの強さ。弱くすると模様が減り、タイル数と動画の転送量も減る',
  palettes: 'カラー: 使う BG パレットの数 (1〜8)。8x8 ドットごとに 1 本のパレット (4 色) を使う',
  tile_limit: '固有タイル数の上限 (0 = 自動: 初代 256 / カラー 512)。超えると似たタイルを統合する',
  fps: '目標 FPS。ゲームボーイの画面は約 59.73 回/秒なので、1 枚を何フレーム表示するかに丸めます (例: 15 → 4 フレームずつ = 14.93 FPS)',
  start: '動画のどこから使うか (秒)', end: 'どこまで使うか (秒、0 = 最後まで)', speed: '再生速度 (2 = 倍速)',
  threshold: '差分しきい値。前のフレームとの違いがこれ以下のタイルは送らない (上げると転送量・ROM が減るが、細かな動きが消える)',
  frame_tile_limit: '動画 1 フレームで新しく送るタイルの上限 (0 = 自動)', loop: '動画を最後まで再生したら最初に戻る',
  strict_fps: 'オン: 目標 FPS を必ず守る (間に合わない時は画質を大きく落とす)。オフ: 画質を落としすぎる時は表示時間を延ばす (FPS が下がる)',
};

// ---------------------------------------------------------------- 登録
EXT.views.media = 'mediaView';
const prevSave = EXT.save;
EXT.save = async (quiet) => { if (prevSave) await prevSave(quiet); await mdSave(true); };

MEDIA.tree = (t, isSel) => {
  if (!MEDIA.list) { MEDIA.list = []; api('GET', '/api/media/list').then((l) => { MEDIA.list = l; renderTree(); }).catch(() => {}); }
  const items = MEDIA.list.map((m) => h('div', { class: 'tree-item' + (isSel('media', m.path) ? ' sel' : ''), onclick: () => openTab('media', m.path), title: m.error || m.path },
    `🎞 ${m.name}`, h('span', { class: 'badge' }, `${m.target === 'cgb' ? 'カラー' : '初代'} · ${m.scenes || 0}シーン`)));
  t.append(treeSection('🎬 メディア (写真・動画)', items.length ? items : [h('div', { class: 'tree-item muted' }, '(GB Media Studio)')], newMedia));
};

async function newMedia() {
  const name = prompt('メディアプロジェクトの名前 (例: opening)', 'opening');
  if (!name) return;
  try {
    const r = await api('POST', '/api/media/new', { name });
    MEDIA.list = null;
    await openTab('media', r.path);
  } catch (e) { fail(e); }
}

// ---------------------------------------------------------------- 開く・保存・元に戻す
EXT.open.media = async (path) => {
  $('#mdEmpty').classList.add('hidden');
  if (M.path !== path) {
    stopPlay();
    M.path = path; M.proj = await api('GET', '/api/media/project?path=' + encodeURIComponent(path));
    M.undo = []; M.redo = []; M.scene = 0; M.sel = null; M.frame = 0; M.frames = null; M.screens = {}; M.report = null; M.diags = []; M.probe = {};
    M.dirty = false;
  }
  if (!M.info) M.info = await api('GET', '/api/media/info');
  renderMedia();
  scheduleBuild(10);
};

const sc = () => M.proj.scenes[M.scene];
const assetOf = (id) => M.proj.assets.find((a) => a.id === id);
const uid = (p) => p + Math.random().toString(36).slice(2, 8);

function snapshot() {
  M.undo.push(JSON.stringify(M.proj));
  if (M.undo.length > 200) M.undo.shift();
  M.redo = [];
}
function change(fn, opts = {}) {
  snapshot();
  fn();
  changed(opts);
}
function changed(opts = {}) {
  M.dirty = true; markSaved();
  M.stale = true; $('#mdStale').classList.remove('hidden');
  if (!opts.noRender) renderMedia();
  clearTimeout(M.saveTimer); M.saveTimer = setTimeout(() => mdSave(true), 800);
  if (!opts.noBuild) scheduleBuild(1200);
}
function undo(redo) {
  const from = redo ? M.redo : M.undo, to = redo ? M.undo : M.redo;
  if (!from.length) return;
  to.push(JSON.stringify(M.proj));
  M.proj = JSON.parse(from.pop());
  if (M.scene >= M.proj.scenes.length) M.scene = 0;
  M.sel = null;
  changed();
}
async function mdSave(quiet) {
  if (!M.path || !M.dirty) return;
  M.dirty = false;
  try {
    await api('PUT', '/api/media/project?path=' + encodeURIComponent(M.path), M.proj);
    if (!quiet) log('保存しました: ' + M.path, 'o');
    markSaved();
  } catch (e) { M.dirty = true; fail(e); }
}

// ---------------------------------------------------------------- 変換 (ジョブ) → プレビュー用フレーム
async function pollJob(job, quiet) {
  let since = 0;
  while (true) {
    job = await api('GET', `/api/job/${job.id}?since=${since}`);
    if (!quiet) job.log.forEach((l) => log(l));
    since = job.log_len;
    if (job.done) return job;
    await new Promise((r) => setTimeout(r, 250));
  }
}
function scheduleBuild(ms) { clearTimeout(M.buildTimer); M.buildTimer = setTimeout(mdBuild, ms); }
async function mdBuild(verbose) {
  if (!M.path) return;
  if (M.building) { M.rebuild = true; return; }
  M.building = true; M.rebuild = false;
  $('#mdStale').textContent = '変換中…'; $('#mdStale').classList.remove('hidden');
  const path = M.path, scene = M.scene;
  try {
    await mdSave(true);
    const job = await pollJob(await api('POST', '/api/media/build?path=' + encodeURIComponent(path), {}), !verbose);
    if (path !== M.path) return;
    if (!job.ok) job.log.filter((l) => /エラー|✖/.test(l)).forEach((l) => log(l, 'e'));
    if (job.result && job.result.report) { M.report = job.result.report; M.diags = job.result.diagnostics; }
    if (job.ok || (job.result && job.result.report)) {
      const fr = await api('GET', `/api/media/frames?path=${encodeURIComponent(path)}&scene=${scene}`);
      if (path === M.path && scene === M.scene) { M.frames = fr; M.screens = {}; }
    }
    M.stale = M.rebuild;
  } catch (e) { fail(e); } finally {
    M.building = false;
    if (M.path === path) {
      $('#mdStale').classList.toggle('hidden', !M.stale);
      $('#mdStale').textContent = '変更あり — 変換中…';
      renderReport(); drawPreview(); renderTimeline();
    }
    if (M.rebuild) scheduleBuild(50);
  }
}

// ---------------------------------------------------------------- 描画: 全体
function renderMedia() {
  if (!M.proj) return;
  const p = M.proj, s = sc();
  $('#mdName').textContent = p.name;
  $('#mdTarget').value = p.target;
  $('#mdFast').checked = p.cgb_fast !== false;
  $$('.md-cgb').forEach((e) => e.classList.toggle('hidden', p.target !== 'cgb'));
  const sel = $('#mdScene'); sel.innerHTML = '';
  p.scenes.forEach((x, i) => sel.append(h('option', { value: i }, x.name)));
  sel.value = M.scene;
  $('#mdLen').value = f2s(s.length).toFixed(2);
  $('#mdLoop').checked = !!s.loop; $('#mdFadeObj').checked = !!s.fade_obj;
  const fx = $('#mdFxType');
  if (!fx.options.length) Object.entries(M.info.fx_types).forEach(([k, v]) => fx.append(h('option', { value: k }, v)));
  renderAssets(); renderTimeline(); drawPreview(); renderReport(); renderInspector();
}

function renderAssets() {
  const ul = $('#mdAssetList'); ul.innerHTML = '';
  for (const a of M.proj.assets) {
    const used = M.proj.scenes.some((x) => x.media.some((c) => c.asset === a.id));
    const li = h('li', { class: (M.sel && M.sel.type === 'asset' && M.sel.id === a.id ? 'sel ' : '') + 'md-asset', draggable: 'true', title: a.path,
      onclick: () => { M.sel = { type: 'asset', id: a.id }; renderAssets(); renderInspector(); },
      ondragstart: (e) => { e.dataTransfer.setData('text/gbm-asset', a.id); e.dataTransfer.effectAllowed = 'copy'; } },
    h('img', { src: `/api/media/thumb?path=${encodeURIComponent(M.path)}&asset_path=${encodeURIComponent(a.path)}&w=64` }),
    h('div', {}, h('div', {}, (a.kind === 'image' ? '🖼 ' : '🎞 ') + (a.name || a.path)),
      h('div', { class: 'muted small' }, a.kind === 'image' ? '画像' : `動画 ${(a.video && a.video.fps) || 10}fps`, used ? '' : ' · 未使用')));
    ul.append(li);
  }
  if (!M.proj.assets.length) ul.append(h('li', { class: 'muted small' }, '「＋ 取り込み」で写真や動画を追加'));
}

// ---------------------------------------------------------------- プレビュー
function screenOf(i) {
  if (!M.screens[i]) {
    const b = atob(M.frames.screens[i]); const a = new Uint8Array(b.length);
    for (let k = 0; k < b.length; k++) a[k] = b.charCodeAt(k);
    M.screens[i] = a;
  }
  return M.screens[i];
}
function drawPreview() {
  const cv = $('#mdCanvas'), ctx = cv.getContext('2d');
  applyScale();
  const len = sc() ? sc().length : 0;
  $('#mdTime').textContent = `${f2s(M.frame).toFixed(2)} 秒 / ${M.frame} f  (全 ${f2s(len).toFixed(2)} 秒)`;
  if (!M.frames) { ctx.fillStyle = '#000'; ctx.fillRect(0, 0, 160, 144); drawOrig(); return; }
  const F = M.frames, fi = Math.min(M.frame, F.frames.length - 1);
  const [sid, pid] = F.frames[fi];
  const scr = screenOf(sid), pal = F.palettes[pid];
  const mode = $('#mdMode').value;
  const img = ctx.createImageData(160, 144), d = img.data;
  let lut;
  if (F.target === 'dmg') {
    const shades = DMG_SHADES[mode === 'dmg_gray' ? 'dmg_gray' : 'dmg_green'];
    lut = [0, 1, 2, 3].map((c) => shades[(pal[0] >> (2 * c)) & 3]);
  } else {
    lut = pal.map((w) => { const c = [w & 31, (w >> 5) & 31, (w >> 10) & 31].map((v) => (v << 3) | (v >> 2)); return c; });
    if (mode !== 'auto') {
      const shades = DMG_SHADES[mode];
      lut = lut.map((c) => { const y = (c[0] * 299 + c[1] * 587 + c[2] * 114) / 1000; return shades[y > 212 ? 0 : y > 127 ? 1 : y > 42 ? 2 : 3]; });
    }
  }
  for (let i = 0; i < 23040; i++) {
    const v = scr[i], c = F.target === 'dmg' ? lut[v & 3] : lut[v];
    d[i * 4] = c[0]; d[i * 4 + 1] = c[1]; d[i * 4 + 2] = c[2]; d[i * 4 + 3] = 255;
  }
  ctx.putImageData(img, 0, 0);
  drawOrig();
}
function applyScale() {
  const v = $('#mdScale').value, wrap = $('.md-screens');
  let z = Number(v);
  if (v === 'fit') {
    const n = $('#mdCompare').checked ? 2 : 1;
    z = Math.max(1, Math.min((wrap.clientWidth - 24 * n) / (160 * n), (wrap.clientHeight - 24) / 144));
    z = z >= 2 ? Math.floor(z) : Math.max(1, Math.floor(z * 2) / 2);
  }
  for (const c of [$('#mdCanvas'), $('#mdOrig')]) { c.style.width = 160 * z + 'px'; c.style.height = 144 * z + 'px'; }
  $('#mdOrig').parentElement.classList.toggle('hidden', !$('#mdCompare').checked);
}
/* 再生位置にある素材の元画像 (明るさ等の調整と切り抜き後、減色前)。再生中の動画は停止時に更新 */
function drawOrig() {
  if (!$('#mdCompare').checked || !M.proj) return;
  const s = sc(), clip = [...s.media].sort((a, b) => a.start - b.start).filter((c) => c.start <= M.frame && M.frame < c.start + c.duration).pop();
  const ctx = $('#mdOrig').getContext('2d');
  if (!clip) { ctx.fillStyle = '#111'; ctx.fillRect(0, 0, 160, 144); $('#mdOrigLabel').textContent = '元の素材 (この位置には素材がありません)'; M.origKey = null; return; }
  const a = assetOf(clip.asset); if (!a) return;
  let t = 0;
  if (a.kind !== 'image') {
    const vp = a.video || {}, hold = Math.max(1, Math.round(GBFPS / (vp.fps || 10)));
    const fi = M.frames ? Math.min(M.frame, M.frames.frames.length - 1) : -1;
    const vf = fi >= 0 && M.frames.frames[fi][2] >= 0 ? M.frames.frames[fi][2] : Math.floor((M.frame - clip.start) / hold);
    t = Math.max(0, vf) * hold / GBFPS * (vp.speed || 1);
    if (M.playing) return;
  }
  const key = `${a.id}|${t.toFixed(3)}|${JSON.stringify(a.params)}|${JSON.stringify(a.video)}`;
  if (key === M.origKey) return;
  M.origKey = key;
  $('#mdOrigLabel').textContent = `元の素材: ${a.name || a.path}` + (a.kind === 'image' ? '' : ` (${t.toFixed(2)} 秒)`);
  loadImg(`/api/media/thumb?path=${encodeURIComponent(M.path)}&asset=${encodeURIComponent(a.id)}&asset_path=${encodeURIComponent(a.path)}&t=${t}&adjusted=1&v=${encodeURIComponent(key)}`)
    .then((im) => { if (M.origKey === key) { ctx.imageSmoothingEnabled = false; ctx.drawImage(im, 0, 0, 160, 144); } }).catch(() => {});
}

function play() {
  if (M.playing) return stopPlay();
  if (!M.frames) { log('プレビューを準備中です (変換が終わると再生できます)', 'w'); return; }
  M.playing = true; $('#mdPlay').textContent = '⏸'; M.lastTs = performance.now(); M.acc = 0;
  const step = (ts) => {
    if (!M.playing) return;
    M.acc += (ts - M.lastTs) / 1000 * GBFPS; M.lastTs = ts;
    const n = Math.floor(M.acc);
    if (n > 0) {
      M.acc -= n; M.frame += n;
      const len = sc().length;
      if (M.frame >= len) {
        if ($('#mdLoopPlay').checked) M.frame = sc().loop ? 1 + (M.frame - len) % Math.max(1, len) : 0;
        else { M.frame = len; stopPlay(); }
      }
      drawPreview(); movePlayhead();
    }
    requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}
function stopPlay() { M.playing = false; const b = $('#mdPlay'); if (b) b.textContent = '▶'; if (M.proj) drawOrig(); }
function seek(f) { M.frame = Math.max(0, Math.min(sc().length, Math.round(f))); drawPreview(); movePlayhead(); }

// ---------------------------------------------------------------- ハードウェアの制約・診断
function renderReport() {
  const el = $('#mdReport'); el.innerHTML = '';
  if (!M.report) { el.append(h('div', { class: 'muted small' }, M.building ? '変換中…' : 'まだ変換していません')); return; }
  const r = M.report;
  const mode = { dmg: '初代', cgb: 'カラー (等速)', cgb_fast: 'カラー (倍速)' }[r.mode];
  el.append(h('div', { class: 'md-rep-head' },
    h('span', { title: 'ROM の使用量の目安 (再生ライブラリを含む)。MBC5 は最大 8MB' }, `ROM 約 ${Math.ceil(r.rom_bytes / 1024)} KB (${r.rom_banks} バンク)`),
    h('span', { title: '1 フレームに VRAM へ送るバイト数。LCD を止めずに送れる量を PyBoy で測って決めた値' }, `${mode}: 転送 ${r.bytes_per_update} バイト/フレーム`),
    h('span', { title: 'タイルを置ける場所の数' }, `タイル枠 ${r.vram_slots}`)));
  for (const a of r.assets) {
    if (a.kind === 'image') {
      el.append(h('div', { class: 'md-rep-row' }, `🖼 ${a.name}`,
        h('span', { class: a.merged_tiles ? 'warn' : '' }, ` タイル ${a.tiles}/${a.tile_limit}` + (a.merged_tiles ? ` (似たタイル ${a.merged_tiles} 枚を統合)` : '')),
        r.target === 'cgb' ? ` · パレット ${a.palettes}/8` : '', ` · VRAM ${a.vram_bytes} B · 読み込み ${a.load_frames} フレーム (${f2s(a.load_frames).toFixed(2)} 秒) · ROM ${(a.rom_bytes / 1024).toFixed(1)} KB`));
    } else {
      const ok = a.feasible;
      const row = h('div', { class: 'md-rep-row' }, `🎞 ${a.name}: ${a.frames} 枚 · `,
        h('b', { class: ok ? 'ok' : 'warn', title: '目標 FPS と、転送が間に合うかを考えた実際の FPS (プレビューと ROM で同じ)' }, `目標 ${a.target_fps} FPS → 実際 ${a.achieved_fps} FPS`),
        ` · 1 枚平均 ${a.avg_frame_bytes} B (最大 ${a.max_frame_bytes} B) · `,
        h('span', { title: '1 フレームの時間のうち VRAM 転送に使う割合の目安 (再生ライブラリの処理時間)。残りがゲームの処理に使える' }, `CPU 最大 ${a.max_cpu_percent}% / 平均 ${a.avg_cpu_percent}%`),
        ` · ROM ${(a.rom_bytes / 1024).toFixed(1)} KB`,
        a.raised_frames ? h('span', { class: 'warn' }, ` · 画質を下げた枚数 ${a.raised_frames} (しきい値 ${a.threshold}→${a.max_used_threshold})`) : '');
      el.append(row);
      el.append(bytesChart(a, r));
    }
  }
  if (M.frames) el.append(h('div', { class: 'md-rep-row small' }, `プレビューでの遅れた動画フレーム: ${M.frames.late} (最大 ${M.frames.max_lag} フレーム)`,
    h('span', { class: 'muted' }, ' — 実機での実測は「✔ ROM 検証」')));
  for (const d of M.diags) {
    el.append(h('div', { class: 'md-diag ' + d.level }, (d.level === 'error' ? '✖ ' : d.level === 'warn' ? '⚠ ' : 'ℹ ') + d.text,
      d.fix ? h('div', { class: 'muted small' }, '→ ' + d.fix) : null));
  }
}
function bytesChart(a, r) {
  const n = a.per_frame_bytes.length, W = Math.min(600, Math.max(120, n * 4)), H = 40;
  const cv = h('canvas', { width: W, height: H, class: 'md-chart', title: '動画 1 枚ごとの転送量 (棒) と、表示時間内に送れる量 (線)。赤 = 間に合わない枚、灰色 = 先頭 (再生前に読み込む)' });
  const ctx = cv.getContext('2d');
  // 先頭フレームは再生前に読み込むので灰色で表示し、縦軸の基準からも外す
  const cap = Math.max(...a.per_frame_bytes.map((b, i) => i === 0 ? 0 : Math.max(b, r.bytes_per_update * a.per_frame_hold[i])), 1);
  const bw = W / n;
  a.per_frame_bytes.forEach((b, i) => {
    const lim = r.bytes_per_update * a.per_frame_hold[i];
    ctx.fillStyle = i === 0 ? '#8b8e96' : b > lim ? '#f85149' : '#4c8bf5';
    const hh = Math.max(1, Math.min(H - 2, b / cap * (H - 2)));
    ctx.fillRect(i * bw, H - hh, Math.max(1, bw - 1), hh);
    ctx.fillStyle = '#d29922'; ctx.fillRect(i * bw, H - lim / cap * (H - 2) - 1, Math.max(1, bw), 1);
  });
  return cv;
}

// ---------------------------------------------------------------- タイムライン
const TL = { head: 74, ruler: 22, media: 40, fxLane: 20, pal: 56 };
function fxLanes(fxs) {
  const lanes = [], out = {};
  for (const f of [...fxs].sort((a, b) => a.start - b.start)) {
    let l = lanes.findIndex((end) => end <= f.start);
    if (l < 0) { l = lanes.length; lanes.push(0); }
    lanes[l] = f.start + f.duration; out[f.id] = l;
  }
  return { map: out, n: Math.max(1, lanes.length) };
}
function renderTimeline() {
  const box = $('#mdTracks'); if (!M.proj) return;
  const s = sc(), z = M.zoom, W = Math.max(s.length * z + 40, box.clientWidth - TL.head);
  const lanes = fxLanes(s.fx);
  const fxH = lanes.n * TL.fxLane + 4;
  const sl = box.scrollLeft;
  box.innerHTML = '';
  const inner = h('div', { class: 'md-tl-inner', style: `width:${W + TL.head}px` });
  // 目盛り
  const ruler = h('div', { class: 'md-ruler', style: `left:${TL.head}px;width:${W}px` });
  const stepSec = z >= 6 ? 0.25 : z >= 2 ? 0.5 : z >= 1 ? 1 : 2;
  for (let t = 0; s2f(t) <= s.length + 1; t += stepSec) {
    const major = Math.abs(t - Math.round(t)) < 1e-6;
    ruler.append(h('div', { class: 'tick' + (major ? ' major' : ''), style: `left:${s2f(t) * z}px` }, major ? `${t}s` : ''));
  }
  ruler.addEventListener('mousedown', (e) => scrub(e, ruler));
  inner.append(h('div', { class: 'md-tl-label', style: `top:0;height:${TL.ruler}px` }, ''), ruler);
  let y = TL.ruler;
  // 映像
  const mediaRow = h('div', { class: 'md-row', style: `top:${y}px;height:${TL.media}px;left:${TL.head}px;width:${W}px` });
  inner.append(h('div', { class: 'md-tl-label', style: `top:${y}px;height:${TL.media}px`, title: '写真・動画のクリップ。素材を左の一覧からドラッグして置きます' }, '映像'), mediaRow);
  for (const c of s.media) {
    const a = assetOf(c.asset);
    const el = h('div', { class: 'md-clip' + (isSelItem('clip', c.id) ? ' sel' : ''), style: `left:${c.start * z}px;width:${Math.max(4, c.duration * z)}px`,
      title: `${a ? a.name : '(素材なし)'}  ${f2s(c.start).toFixed(2)}〜${f2s(c.start + c.duration).toFixed(2)} 秒` },
    a ? h('img', { src: `/api/media/thumb?path=${encodeURIComponent(M.path)}&asset_path=${encodeURIComponent(a.path)}&w=48` }) : null,
    h('span', {}, a ? a.name || a.path : '?'), h('div', { class: 'grip' }));
    el.addEventListener('mousedown', (e) => dragItem(e, 'clip', c, el));
    mediaRow.append(el);
  }
  mediaRow.addEventListener('dragover', (e) => { if (e.dataTransfer.types.includes('text/gbm-asset')) { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; } });
  mediaRow.addEventListener('drop', (e) => {
    const id = e.dataTransfer.getData('text/gbm-asset'); if (!id) return;
    e.preventDefault();
    const x = e.clientX - mediaRow.getBoundingClientRect().left;
    addClip(id, Math.max(0, Math.round(x / z)));
  });
  y += TL.media;
  // 演出
  const fxRow = h('div', { class: 'md-row', style: `top:${y}px;height:${fxH}px;left:${TL.head}px;width:${W}px` });
  inner.append(h('div', { class: 'md-tl-label', style: `top:${y}px;height:${fxH}px`, title: 'フェード・フラッシュなどの演出 (パレットだけで行うので LCD は止めません)' }, '演出'), fxRow);
  for (const f of s.fx) {
    const el = h('div', { class: 'md-fx' + (isSelItem('fx', f.id) ? ' sel' : ''), style: `left:${f.start * z}px;width:${Math.max(4, f.duration * z)}px;top:${lanes.map[f.id] * TL.fxLane + 2}px;background:${FX_COLOR[f.type] || '#555'}`,
      title: `${M.info.fx_types[f.type]} ${f.duration} フレーム (${f2s(f.duration).toFixed(2)} 秒)` }, h('span', {}, M.info.fx_types[f.type]), h('div', { class: 'grip' }));
    el.addEventListener('mousedown', (e) => dragItem(e, 'fx', f, el));
    fxRow.append(el);
  }
  y += fxH;
  // パレット (明るさ)
  const palRow = h('div', { class: 'md-row md-pal', style: `top:${y}px;height:${TL.pal}px;left:${TL.head}px;width:${W}px` });
  inner.append(h('div', { class: 'md-tl-label', style: `top:${y}px;height:${TL.pal}px`, title: '明るさ (上 = 白 +31、真ん中 = 元の色 0、下 = 黒 -31)。線は演出とキーフレームを合わせた結果 (最後に変換した時点)。ダブルクリックでキーフレーム追加' }, 'パレット'), palRow);
  const cv = h('canvas', { width: Math.min(W, 16000), height: TL.pal });
  palRow.append(cv);
  drawLevels(cv, z);
  for (const k of s.palette) {
    const el = h('div', { class: 'md-key' + (isSelItem('key', k.id) ? ' sel' : ''), style: `left:${k.frame * z - 6}px;top:${levelY(k.level) - 6}px`, title: `明るさ ${k.level} (${k.interp === 'linear' ? '直線で変化' : '段階'})` });
    el.addEventListener('mousedown', (e) => dragItem(e, 'key', k, el));
    palRow.append(el);
  }
  palRow.addEventListener('dblclick', (e) => {
    if (e.target.classList.contains('md-key')) return;
    const r = palRow.getBoundingClientRect();
    const lv = Math.max(-31, Math.min(31, Math.round(31 - (e.clientY - r.top) / TL.pal * 62)));
    change(() => { const k = { id: uid('k'), frame: Math.round((e.clientX - r.left) / z), level: lv, interp: 'linear' }; sc().palette.push(k); M.sel = { type: 'key', id: k.id }; });
  });
  y += TL.pal;
  inner.style.height = y + 'px';
  inner.append(h('div', { id: 'mdHead', class: 'md-playhead', style: `left:${TL.head + M.frame * z}px;height:${y}px` }));
  box.append(inner);
  box.scrollLeft = sl;
  const pin = () => $$('.md-tl-label', inner).forEach((l) => { l.style.left = box.scrollLeft + 'px'; });
  box.onscroll = pin; pin();
}
const levelY = (lv) => (31 - lv) / 62 * (TL.pal - 2) + 1;
function drawLevels(cv, z) {
  const ctx = cv.getContext('2d');
  ctx.strokeStyle = '#444'; ctx.beginPath(); ctx.moveTo(0, levelY(0)); ctx.lineTo(cv.width, levelY(0)); ctx.stroke();
  const lv = M.frames && M.frames.levels;
  if (!lv) return;
  ctx.strokeStyle = M.stale ? '#8b8e96' : '#3fb950'; ctx.beginPath();
  lv.forEach((v, f) => { const x = f * z, yy = levelY(v); if (f === 0) ctx.moveTo(x, yy); else ctx.lineTo(x, yy); });
  ctx.stroke();
}
function movePlayhead() { const el = $('#mdHead'); if (el) el.style.left = (TL.head + M.frame * M.zoom) + 'px'; }
const isSelItem = (type, id) => M.sel && M.sel.type === type && M.sel.id === id;

function scrub(e, ruler) {
  const r = ruler.getBoundingClientRect();
  const mv = (ev) => seek((ev.clientX - r.left) / M.zoom);
  mv(e);
  const up = () => { window.removeEventListener('mousemove', mv); window.removeEventListener('mouseup', up); };
  window.addEventListener('mousemove', mv); window.addEventListener('mouseup', up);
}

/* クリップ・演出・キーフレームのドラッグ (移動 / 右端で長さ変更)。吸着: 他の端・再生位置 (Shift で無効) */
function dragItem(e, type, item, el) {
  e.stopPropagation(); e.preventDefault();
  M.sel = { type, id: item.id };
  const z = M.zoom, x0 = e.clientX;
  const resize = type !== 'key' && e.target.classList.contains('grip');
  const orig = type === 'key' ? { frame: item.frame } : { start: item.start, duration: item.duration };
  let moved = false;
  const s = sc();
  const edges = [0, M.frame, s.length];
  for (const c of [...s.media, ...s.fx]) if (c !== item) edges.push(c.start, c.start + c.duration);
  const snap = (f, ev) => {
    if (ev.shiftKey) return f;
    for (const g of edges) if (Math.abs(g - f) * z <= 6) return g;
    return f;
  };
  const mv = (ev) => {
    const df = Math.round((ev.clientX - x0) / z);
    if (!moved && Math.abs(ev.clientX - x0) < 3) return;
    if (!moved) { snapshot(); moved = true; }
    if (type === 'key') { item.frame = Math.max(0, snap(orig.frame + df, ev)); el.style.left = (item.frame * z - 6) + 'px'; }
    else if (resize) { item.duration = Math.max(1, snap(orig.start + orig.duration + df, ev) - item.start); el.style.width = Math.max(4, item.duration * z) + 'px'; }
    else {
      let st = Math.max(0, snap(orig.start + df, ev));
      const endSnap = snap(st + item.duration, ev) - item.duration;
      if (endSnap !== st && !ev.shiftKey && Math.abs(endSnap - st) * z <= 6) st = Math.max(0, endSnap);
      item.start = st; el.style.left = (item.start * z) + 'px';
    }
  };
  const up = () => {
    window.removeEventListener('mousemove', mv); window.removeEventListener('mouseup', up);
    if (moved) changed(); else { renderTimeline(); renderInspector(); renderAssets(); }
  };
  window.addEventListener('mousemove', mv); window.addEventListener('mouseup', up);
}

async function addClip(assetId, start) {
  const a = assetOf(assetId); if (!a) return;
  let dur = s2f(3);
  if (a.kind !== 'image') {
    const pr = await probeAsset(a);
    if (pr && pr.duration) {
      const vp = a.video || {};
      const len = ((vp.end || pr.duration) - (vp.start || 0)) / (vp.speed || 1);
      if (len > 0) dur = Math.max(1, s2f(len));
    }
  }
  change(() => {
    const c = { id: uid('c'), asset: assetId, start, duration: dur };
    sc().media.push(c);
    if (start + dur > sc().length) sc().length = start + dur;
    M.sel = { type: 'clip', id: c.id };
  });
}
async function probeAsset(a) {
  if (M.probe[a.id]) return M.probe[a.id];
  try { M.probe[a.id] = await api('GET', `/api/media/probe?path=${encodeURIComponent(M.path)}&asset_path=${encodeURIComponent(a.path)}`); } catch (e) { return null; }
  return M.probe[a.id];
}

function deleteSel() {
  const t = M.sel; if (!t) return;
  if (t.type === 'asset') {
    const a = assetOf(t.id);
    const n = M.proj.scenes.reduce((k, x) => k + x.media.filter((c) => c.asset === t.id).length, 0);
    if (!confirm(`素材「${a.name || a.path}」をこのメディアプロジェクトから外しますか？` + (n ? `\n(タイムライン上のクリップ ${n} 個も消えます)` : '') + '\n元のファイルは消しません')) return;
    change(() => { M.proj.assets = M.proj.assets.filter((x) => x.id !== t.id); M.proj.scenes.forEach((x) => { x.media = x.media.filter((c) => c.asset !== t.id); }); M.sel = null; });
    return;
  }
  const key = { clip: 'media', fx: 'fx', key: 'palette' }[t.type];
  change(() => { sc()[key] = sc()[key].filter((x) => x.id !== t.id); M.sel = null; });
}

// ---------------------------------------------------------------- 取り込み
async function importFiles(files) {
  for (const file of files) {
    try {
      log(`取り込み中: ${file.name} (${Math.round(file.size / 1024)} KB)`);
      const r = await api('POST', `/api/media/import?path=${encodeURIComponent(M.path)}&name=${encodeURIComponent(file.name)}`, undefined, file);
      const stem = file.name.replace(/\.[^.]+$/, '');
      const a = { id: uid('a'), name: stem, path: r.path, kind: r.kind === 'image' ? 'image' : 'video', params: { preset: 'standard' } };
      if (a.kind === 'video') {
        a.video = { fps: 10, start: 0, end: 0, speed: 1, threshold: 8, tile_limit: 0, loop: false, strict_fps: false };
        a.params.dither = 'bayer4';
      }
      change(() => { M.proj.assets.push(a); M.sel = { type: 'asset', id: a.id }; });
      if (a.kind === 'video') {
        const pr = await probeAsset(a);
        if (pr && pr.error) log(pr.error, 'w');
        else if (pr) log(`動画: ${pr.width}x${pr.height} ${pr.fps.toFixed(2)}fps ${pr.duration.toFixed(2)} 秒 (${pr.backend})`);
      }
    } catch (e) { fail(e); }
  }
}

// ---------------------------------------------------------------- インスペクター
EXT.inspect.media = (el) => {
  if (!M.proj) return;
  const t = M.sel;
  if (t && t.type === 'asset' && assetOf(t.id)) return inspectAsset(el, assetOf(t.id));
  const s = sc();
  if (t && t.type === 'clip') { const c = s.media.find((x) => x.id === t.id); if (c) return inspectClip(el, c); }
  if (t && t.type === 'fx') { const f = s.fx.find((x) => x.id === t.id); if (f) return inspectFx(el, f); }
  if (t && t.type === 'key') { const k = s.palette.find((x) => x.id === t.id); if (k) return inspectKey(el, k); }
  inspectProject(el);
};

/* 入力欄 (ラベルにツールチップ) */
function fld(label, tip, input) { return h('label', { class: 'md-fld', title: tip || '' }, h('span', {}, label), input); }
function rng(value, min, max, step, onset, fmt) {
  const out = h('span', { class: 'mono small md-val' }, fmt ? fmt(value) : String(value));
  const i = h('input', { type: 'range', min, max, step, value });
  let first = true;
  i.addEventListener('input', () => { out.textContent = fmt ? fmt(Number(i.value)) : i.value; if (first) { snapshot(); first = false; } onset(Number(i.value), true); });
  i.addEventListener('change', () => { first = true; onset(Number(i.value), false); });
  return h('span', { class: 'md-rng' }, i, out);
}
function sel(options, value, onset) {
  const s = h('select', { onchange: () => onset(s.value) }, ...Object.entries(options).map(([k, v]) => h('option', { value: k }, v)));
  s.value = value; return s;
}
function numIn(value, step, onset, min, max) {
  const i = h('input', { type: 'number', value, step, min, max, style: 'width:80px', onchange: () => onset(Number(i.value)) });
  return i;
}
function chk(value, onset) { const c = h('input', { type: 'checkbox', onchange: () => onset(c.checked) }); c.checked = !!value; return c; }

function inspectAsset(el, a) {
  const cgb = M.proj.target === 'cgb';
  const P = M.info.preset_params, D = M.info.defaults;
  const p = a.params || (a.params = {});
  const cur = Object.assign({}, D, P[p.preset] || {}, p);
  const setP = (k, v, live) => {
    if (!live) snapshot();
    if (k === 'preset') { a.params = { preset: v }; if (a.kind !== 'image') a.params.dither = (P[v] || {}).dither || 'bayer4'; }
    else a.params[k] = v;
    changed({ noRender: true, noBuild: live });
    if (k === 'preset') renderInspector(); else quickPreview(a);
  };
  el.append(h('div', { class: 'insp-title' }, (a.kind === 'image' ? '🖼 ' : '🎞 ') + (a.name || a.path)));
  el.append(fld('名前', '一覧とタイムラインに出る名前', h('input', { value: a.name || '', onchange: (e) => change(() => { a.name = e.target.value; }) })));
  el.append(h('div', { class: 'muted small' }, a.path));
  const pv = h('div', { id: 'mdQuick', class: 'md-quick' }, h('div', { class: 'muted small' }, '変換プレビューを作っています…'));
  el.append(pv);
  el.append(fld('プリセット', TIP.preset, sel(M.info.presets, p.preset || 'standard', (v) => setP('preset', v))));
  const sec = (title) => el.append(h('div', { class: 'md-sec' }, title));
  sec('切り抜き (160×144)');
  el.append(fld('合わせ方', TIP.fit, sel({ fill: 'いっぱい (はみ出しを切る)', fit: '全体 (余白を足す)', stretch: '引き伸ばす' }, cur.fit, (v) => setP('fit', v))));
  el.append(fld('拡大', TIP.zoom, rng(cur.zoom, 1, 4, 0.05, (v, l) => setP('zoom', v, l), (v) => v.toFixed(2) + 'x')));
  el.append(fld('位置 (横)', TIP.crop_x, rng(cur.crop_x, 0, 1, 0.01, (v, l) => setP('crop_x', v, l), (v) => v.toFixed(2))));
  el.append(fld('位置 (縦)', TIP.crop_y, rng(cur.crop_y, 0, 1, 0.01, (v, l) => setP('crop_y', v, l), (v) => v.toFixed(2))));
  if (cur.fit === 'fit') el.append(fld('余白', TIP.pad, sel({ black: '黒', white: '白' }, cur.pad, (v) => setP('pad', v))));
  sec('調整');
  el.append(fld('明るさ', TIP.brightness, rng(cur.brightness, -100, 100, 1, (v, l) => setP('brightness', v, l))));
  el.append(fld('コントラスト', TIP.contrast, rng(cur.contrast, -100, 100, 1, (v, l) => setP('contrast', v, l))));
  el.append(fld('ガンマ', TIP.gamma, rng(cur.gamma, 0.3, 3, 0.05, (v, l) => setP('gamma', v, l), (v) => v.toFixed(2))));
  el.append(fld('黒レベル', TIP.black_point, rng(cur.black_point, 0, 254, 1, (v, l) => setP('black_point', v, l))));
  el.append(fld('白レベル', TIP.white_point, rng(cur.white_point, 1, 255, 1, (v, l) => setP('white_point', v, l))));
  el.append(fld('シャープ', TIP.sharpen, rng(cur.sharpen, 0, 3, 0.1, (v, l) => setP('sharpen', v, l), (v) => v.toFixed(1))));
  el.append(fld('ノイズ除去', TIP.denoise, rng(cur.denoise, 0, 3, 1, (v, l) => setP('denoise', v, l))));
  if (cgb) {
    el.append(fld('彩度', TIP.saturation, rng(cur.saturation, -100, 100, 1, (v, l) => setP('saturation', v, l))));
    el.append(fld('白黒にする', TIP.grayscale, chk(cur.grayscale, (v) => setP('grayscale', v))));
  }
  sec('減色');
  el.append(fld('ディザ', TIP.dither, sel({ none: 'なし', fs: 'Floyd–Steinberg', atkinson: 'Atkinson', bayer2: 'Bayer 2x2', bayer4: 'Bayer 4x4', bayer8: 'Bayer 8x8' }, cur.dither, (v) => setP('dither', v))));
  el.append(fld('ディザの強さ', TIP.dither_strength, rng(cur.dither_strength, 0, 1, 0.05, (v, l) => setP('dither_strength', v, l), (v) => v.toFixed(2))));
  if (cgb) el.append(fld('パレット数', TIP.palettes, rng(cur.palettes, 1, 8, 1, (v, l) => setP('palettes', v, l))));
  el.append(fld('タイル上限', TIP.tile_limit, numIn(cur.tile_limit, 1, (v) => setP('tile_limit', v), 0, cgb ? 512 : 256)));
  if (a.kind !== 'image') {
    const vp = a.video || (a.video = {});
    const setV = (k, v) => change(() => { vp[k] = v; }, { noRender: true });
    sec('動画');
    const pr = M.probe[a.id];
    const info = h('div', { class: 'muted small' }, pr ? (pr.error || `${pr.width}x${pr.height} · ${pr.fps.toFixed(2)}fps · ${pr.duration.toFixed(2)} 秒`) : '');
    if (!pr) probeAsset(a).then((r) => { if (r) info.textContent = r.error || `${r.width}x${r.height} · ${r.fps.toFixed(2)}fps · ${r.duration.toFixed(2)} 秒`; });
    el.append(info);
    const hold = Math.max(1, Math.round(GBFPS / (vp.fps || 10)));
    el.append(fld('目標 FPS', TIP.fps, h('span', {}, numIn(vp.fps || 10, 1, (v) => setV('fps', Math.max(1, Math.min(30, v))), 1, 30),
      h('span', { class: 'muted small' }, ` → 1 枚 ${hold} フレーム = ${(GBFPS / hold).toFixed(2)} FPS`))));
    el.append(fld('開始 (秒)', TIP.start, numIn(vp.start || 0, 0.1, (v) => setV('start', Math.max(0, v)), 0)));
    el.append(fld('終了 (秒)', TIP.end, numIn(vp.end || 0, 0.1, (v) => setV('end', Math.max(0, v)), 0)));
    el.append(fld('速度', TIP.speed, numIn(vp.speed || 1, 0.25, (v) => setV('speed', Math.max(0.1, v)), 0.1, 8)));
    el.append(fld('差分しきい値', TIP.threshold, rng(vp.threshold ?? 8, 0, 60, 1, (v, l) => { if (!l) setV('threshold', v); })));
    el.append(fld('1 枚の新タイル上限', TIP.frame_tile_limit, numIn(vp.tile_limit || 0, 1, (v) => setV('tile_limit', Math.max(0, v)), 0)));
    el.append(fld('ループ', TIP.loop, chk(vp.loop, (v) => setV('loop', v))));
    el.append(fld('FPS を必ず守る', TIP.strict_fps, chk(vp.strict_fps, (v) => setV('strict_fps', v))));
  }
  el.append(h('div', { class: 'row' }, h('button', { onclick: () => addClip(a.id, M.frame), title: '再生位置にこの素材のクリップを置く' }, '＋ 再生位置に置く'),
    h('button', { class: 'danger', onclick: deleteSel }, '素材を外す')));
  quickPreview(a, 0);
}
function quickPreview(a, delay = 350) {
  clearTimeout(M.prevTimer);
  M.prevTimer = setTimeout(async () => {
    const box = $('#mdQuick'); if (!box) return;
    try {
      const r = await api('POST', `/api/media/preview?path=${encodeURIComponent(M.path)}`, { project: M.proj, asset: a.id, t: 0 });
      if (!$('#mdQuick') || !M.sel || M.sel.id !== a.id) return;
      box.innerHTML = '';
      box.append(h('div', { class: 'row' }, h('div', {}, h('div', { class: 'muted small' }, '調整後'), h('img', { class: 'pix', src: 'data:image/png;base64,' + r.source })),
        h('div', {}, h('div', { class: 'muted small' }, '変換結果'), h('img', { class: 'pix', src: 'data:image/png;base64,' + r.converted }))));
      box.append(h('div', { class: 'small' + (r.merged ? ' warn' : '') }, `タイル ${r.tiles}` + (r.merged ? ` (統合 ${r.merged})` : '') + ` · VRAM ${r.vram_bytes} B · 読み込み ${r.load_frames} フレーム`));
      if (r.palettes) {
        const sw = h('div', { class: 'md-pals', title: 'カラーの BG パレット (8 本 x 4 色)' });
        r.palettes.forEach((pal) => sw.append(h('span', { class: 'md-pal' }, ...pal.map((c) => h('i', { style: `background:rgb(${c[0] * 8},${c[1] * 8},${c[2] * 8})` })))));
        box.append(sw);
      }
      r.warnings.forEach((w) => box.append(h('div', { class: 'warn small' }, w)));
    } catch (e) { box.innerHTML = ''; box.append(h('div', { class: 'warn small' }, String(e.message || e))); }
  }, delay);
}

function timeFields(el, item, keys) {
  const s = sc();
  for (const [k, label, tip] of keys) {
    el.append(fld(label, tip, h('span', {}, numIn(f2s(item[k]).toFixed(2), 0.05, (v) => change(() => { item[k] = Math.max(k === 'duration' ? 1 : 0, s2f(v)); })),
      h('span', { class: 'muted small' }, ` 秒 = ${item[k]} フレーム `),
      h('button', { class: 'tiny', title: '1 フレーム減らす', onclick: () => change(() => { item[k] = Math.max(k === 'duration' ? 1 : 0, item[k] - 1); }) }, '−'),
      h('button', { class: 'tiny', title: '1 フレーム増やす', onclick: () => change(() => { item[k] += 1; if (item.start + (item.duration || 0) > s.length) s.length = item.start + (item.duration || 0); }) }, '＋'))));
  }
}
function inspectClip(el, c) {
  const a = assetOf(c.asset);
  el.append(h('div', { class: 'insp-title' }, '映像クリップ'));
  el.append(fld('素材', '表示する写真・動画', sel(Object.fromEntries(M.proj.assets.map((x) => [x.id, x.name || x.path])), c.asset, (v) => change(() => { c.asset = v; }))));
  timeFields(el, c, [['start', '開始', 'この時刻に画面が切り替わる (読み込みはその前から始める)'], ['duration', '長さ', '表示する時間']]);
  if (a && a.kind !== 'image') el.append(h('button', { onclick: async () => {
    const pr = await probeAsset(a); if (!pr || !pr.duration) return;
    const vp = a.video || {};
    change(() => { c.duration = Math.max(1, s2f(((vp.end || pr.duration) - (vp.start || 0)) / (vp.speed || 1))); });
  } }, '動画の長さに合わせる'));
  el.append(h('div', { class: 'hint small' }, '前の絵が見えているうちに切り替えると、読み込み中にタイルが重なって乱れることがあります。暗転中に切り替えると確実です。'));
  el.append(h('div', { class: 'row' }, h('button', { onclick: () => { M.sel = { type: 'asset', id: c.asset }; renderAssets(); renderInspector(); } }, '素材の変換設定'), h('button', { class: 'danger', onclick: deleteSel }, '削除')));
}
function inspectFx(el, f) {
  el.append(h('div', { class: 'insp-title' }, '演出'));
  el.append(fld('種類', 'パレットだけで行う演出 (LCD は止めない)。初代は BGP の 4 階調、カラーは 31 段階', sel(M.info.fx_types, f.type, (v) => change(() => { f.type = v; }))));
  timeFields(el, f, [['start', '開始', ''], ['duration', '長さ', 'フェードにかけるフレーム数']]);
  if (['flash_white', 'flash_black'].includes(f.type)) el.append(fld('戻りのフレーム数', '最後の何フレームで元の明るさに戻すか (0 = すぐ戻る)', numIn(f.decay || 0, 1, (v) => change(() => { f.decay = Math.max(0, v); }), 0)));
  if (['flicker', 'film', 'random', 'horror'].includes(f.type)) {
    el.append(fld('強さ', '明るさの変化の大きさ (1〜31)', rng(f.intensity ?? 16, 1, 31, 1, (v, l) => { f.intensity = v; changed({ noRender: true, noBuild: l }); })));
    el.append(fld('間隔 (フレーム)', '何フレームごとに明るさを変えるか', numIn(f.period ?? 4, 1, (v) => change(() => { f.period = Math.max(1, v); }), 1)));
    if (f.type !== 'flicker') el.append(fld('乱数の種', '変えると違う揺らぎ方になる (プレビューと ROM は同じ)', numIn(f.seed ?? 1, 1, (v) => change(() => { f.seed = v; }))));
  }
  if (M.proj.target === 'dmg') el.append(h('div', { class: 'hint small' }, '初代は 4 階調なので、明るさは 31/3 ごとの段階で変わります (パレット段階変化)。'));
  el.append(h('button', { class: 'danger', onclick: deleteSel }, '削除'));
}
function inspectKey(el, k) {
  el.append(h('div', { class: 'insp-title' }, 'パレットのキーフレーム'));
  el.append(fld('フレーム', 'このフレームでこの明るさになる', numIn(k.frame, 1, (v) => change(() => { k.frame = Math.max(0, v); }), 0)));
  el.append(fld('明るさ', '-31 = 真っ黒、0 = 元の色、+31 = 真っ白', rng(k.level, -31, 31, 1, (v, l) => { k.level = v; changed({ noBuild: l }); })));
  el.append(fld('次までの変化', 'linear = 次のキーフレームまで少しずつ変える / step = 次まで同じ', sel({ linear: '少しずつ (linear)', step: 'そのまま (step)' }, k.interp || 'linear', (v) => change(() => { k.interp = v; }))));
  el.append(h('button', { class: 'danger', onclick: deleteSel }, '削除'));
}
function inspectProject(el) {
  const p = M.proj, out = p.output || (p.output = {}), st = p.settings || (p.settings = {});
  el.append(h('div', { class: 'insp-title' }, '🎞 ' + p.name));
  el.append(h('div', { class: 'hint small' }, 'クリップ・演出・キーフレームを選ぶとここで編集できます。素材は左の一覧から選びます。'));
  el.append(h('div', { class: 'md-sec' }, '書き出し'));
  el.append(fld('出力先', 'C ソースを書き出すフォルダ (ソースの場所からの相対パス)。GBForge のビルドでは自動的にコンパイルされます',
    h('input', { class: 'mono', value: out.dir || 'gbmedia', onchange: (e) => change(() => { out.dir = e.target.value.trim() || 'gbmedia'; }) })));
  el.append(fld('バンク配置', 'auto = #pragma bank 255 (lcc -autobank でリンク。GBForge のビルドはこれ) / fixed = 決まったバンク番号 (-autobank を使わない build.bat 用)',
    sel({ auto: '自動 (-autobank)', fixed: '固定バンク' }, out.bank_mode || 'auto', (v) => change(() => { out.bank_mode = v; }))));
  if (out.bank_mode === 'fixed') el.append(fld('最初のバンク', 'データを置き始めるバンク番号 (ゲームの他のデータと重ならない番号に)', numIn(out.first_bank || 2, 1, (v) => change(() => { out.first_bank = Math.max(1, v); }), 1, 511)));
  el.append(h('div', { class: 'md-sec' }, 'ツール'));
  el.append(fld('ffmpeg', `動画の読み込みに使う ffmpeg.exe の場所 (空 = 自動で探す)。現在: ${M.info.ffmpeg || '見つかりません — winget install Gyan.FFmpeg'}`,
    h('input', { class: 'mono', value: st.ffmpeg || '', placeholder: M.info.ffmpeg || 'C:\\ffmpeg\\bin\\ffmpeg.exe', onchange: (e) => change(() => { st.ffmpeg = e.target.value.trim(); }, { noRender: true }) })));
  el.append(fld('エミュレーター', 'ROM プレビューで起動するエミュレーター (空 = プロジェクト設定のエミュレーター)。例: C:\\SameBoy\\sameboy.exe / C:\\bgb\\bgb.exe / "C:\\mGBA\\mGBA.exe" {rom}',
    h('input', { class: 'mono', value: st.emulator || '', placeholder: 'sameboy.exe / bgb.exe / mGBA.exe {rom}', onchange: (e) => change(() => { st.emulator = e.target.value.trim(); }, { noRender: true, noBuild: true }) })));
  el.append(h('div', { class: 'md-sec' }, '使い方 (C)'));
  const scn = sc().name.replace(/\W/g, '_').toUpperCase();
  el.append(h('pre', { class: 'code-box small' }, `#include "${(out.dir || 'gbmedia')}/gbmedia.h"\n#include "${(out.dir || 'gbmedia')}/gbmedia_data.h"\n\ngbmedia_init();\ngbmedia_load_scene(GBM_SCENE_${scn});\ngbmedia_play();   // 最後まで再生`));
}

// ---------------------------------------------------------------- 書き出し・ROM プレビュー
async function mdExport() {
  await mdSave(true);
  log('GB Media Studio: 書き出し中…');
  try {
    const job = await pollJob(await api('POST', '/api/media/export?path=' + encodeURIComponent(M.path), {}));
    if (job.ok) {
      log('書き出しました。プロジェクトをビルドすると一緒にコンパイルされます', 'o');
      MEDIA.list = null; if (window.EXT && EXT.ensureArrays) EXT.ensureArrays(true);
      await loadProject();
      const box = await scriptAddBox(job.result.c_files || []);
      if (box.childNodes.length) $('#mdReport').prepend(box);
    }
  } catch (e) { fail(e); }
}
async function mdDemo(verify) {
  await mdSave(true);
  const job = await runJob('/api/media/demo?path=' + encodeURIComponent(M.path), { scene: M.scene, run: !verify, verify }, verify ? 'ROM 検証 (PyBoy)' : 'ROM プレビュー');
  if (job && job.result && job.result.verify) {
    const r = job.result.verify;
    const ok = r.matched === r.compared && r.cut === 0 && r.overruns === 0 && r.late === r.sim_late;
    $('#mdReport').prepend(h('div', { class: 'md-diag ' + (ok ? 'ok' : 'warn') },
      `${ok ? '✔' : '⚠'} ROM 検証 (シーン「${sc().name}」): ${r.matched}/${r.compared} フレームがプレビューと一致 · 遅れた動画フレーム ${r.late} (最大 ${r.max_lag}) · 転送の打ち切り ${r.cut} · フレーム落ち ${r.overruns}`));
  }
}

// ---------------------------------------------------------------- 初期化
function mediaSetup() {
  $('#mdTarget').addEventListener('change', (e) => change(() => { M.proj.target = e.target.value; }));
  $('#mdFast').addEventListener('change', (e) => change(() => { M.proj.cgb_fast = e.target.checked; }));
  $('#mdScene').addEventListener('change', (e) => { stopPlay(); M.scene = Number(e.target.value); M.frame = 0; M.frames = null; M.sel = null; renderMedia(); scheduleBuild(10); });
  $('#mdSceneAdd').addEventListener('click', () => {
    const name = prompt('シーンの名前 (英数字。C から GBM_SCENE_名前 で呼びます)', 'scene' + (M.proj.scenes.length + 1));
    if (!name) return;
    change(() => { M.proj.scenes.push({ name, length: s2f(5), loop: false, fade_obj: false, media: [], fx: [], palette: [] }); M.scene = M.proj.scenes.length - 1; M.frame = 0; M.frames = null; });
  });
  $('#mdSceneRen').addEventListener('click', () => { const n = prompt('シーンの名前', sc().name); if (n) change(() => { sc().name = n; }); });
  $('#mdSceneDel').addEventListener('click', () => {
    if (M.proj.scenes.length < 2) { log('シーンは 1 つ以上必要です', 'w'); return; }
    if (!confirm(`シーン「${sc().name}」を削除しますか？ (元に戻すで戻せます)`)) return;
    change(() => { M.proj.scenes.splice(M.scene, 1); M.scene = 0; M.frames = null; });
  });
  $('#mdLen').addEventListener('change', (e) => change(() => { sc().length = Math.max(1, s2f(Number(e.target.value) || 1)); }));
  $('#mdLoop').addEventListener('change', (e) => change(() => { sc().loop = e.target.checked; }));
  $('#mdFadeObj').addEventListener('change', (e) => change(() => { sc().fade_obj = e.target.checked; }));
  $('#mdUndo').addEventListener('click', () => undo(false));
  $('#mdRedo').addEventListener('click', () => undo(true));
  $('#mdSave').addEventListener('click', () => { M.dirty = true; mdSave(false); });
  $('#mdBuild').addEventListener('click', () => mdBuild(true));
  $('#mdDemo').addEventListener('click', () => mdDemo(false));
  $('#mdVerify').addEventListener('click', () => mdDemo(true));
  $('#mdExport').addEventListener('click', mdExport);
  $('#mdImport').addEventListener('click', () => $('#mdFile').click());
  $('#mdFile').addEventListener('change', (e) => { importFiles([...e.target.files]); e.target.value = ''; });
  const drop = $('#mediaView');
  drop.addEventListener('dragover', (e) => { if (e.dataTransfer.types.includes('Files')) e.preventDefault(); });
  drop.addEventListener('drop', (e) => { if (e.dataTransfer.files.length && M.path) { e.preventDefault(); importFiles([...e.dataTransfer.files]); } });
  $('#mdPlay').addEventListener('click', play);
  $('#mdFirst').addEventListener('click', () => seek(0));
  $('#mdLast').addEventListener('click', () => seek(sc().length));
  $('#mdPrev').addEventListener('click', () => { stopPlay(); seek(M.frame - 1); });
  $('#mdNext').addEventListener('click', () => { stopPlay(); seek(M.frame + 1); });
  for (const id of ['#mdScale', '#mdMode', '#mdCompare']) $(id).addEventListener('change', () => { M.origKey = null; drawPreview(); });
  window.addEventListener('resize', () => { if (S.active && S.active.kind === 'media') { applyScale(); renderTimeline(); } });
  $('#mdZoom').addEventListener('input', (e) => { M.zoom = Number(e.target.value); renderTimeline(); });
  $('#mdFxAdd').addEventListener('click', () => change(() => {
    const type = $('#mdFxType').value;
    const f = { id: uid('f'), type, start: M.frame, duration: type.startsWith('flash') ? 12 : ['flicker', 'film', 'random', 'horror'].includes(type) ? 60 : 30 };
    if (['flicker', 'film', 'random', 'horror'].includes(type)) Object.assign(f, { intensity: 16, period: type === 'flicker' ? 4 : 3, seed: 1 });
    sc().fx.push(f); M.sel = { type: 'fx', id: f.id };
    if (f.start + f.duration > sc().length) sc().length = f.start + f.duration;
  }));
  $('#mdKeyAdd').addEventListener('click', () => change(() => {
    const lv = M.frames && M.frames.levels ? M.frames.levels[Math.min(M.frame, M.frames.levels.length - 1)] : 0;
    const k = { id: uid('k'), frame: M.frame, level: lv, interp: 'linear' }; sc().palette.push(k); M.sel = { type: 'key', id: k.id };
  }));
  $('#mdDelSel').addEventListener('click', deleteSel);
  document.addEventListener('keydown', (e) => {
    if (!S.active || S.active.kind !== 'media' || !M.proj) return;
    const typing = /INPUT|TEXTAREA|SELECT/.test(e.target.tagName);
    const k = e.key.toLowerCase();
    if ((e.ctrlKey || e.metaKey) && k === 'z' && !typing) { e.preventDefault(); undo(e.shiftKey); return; }
    if ((e.ctrlKey || e.metaKey) && k === 'y' && !typing) { e.preventDefault(); undo(true); return; }
    if (typing) return;
    if (e.key === ' ') { e.preventDefault(); play(); }
    else if (e.key === 'ArrowLeft') { e.preventDefault(); stopPlay(); seek(M.frame - (e.shiftKey ? 10 : 1)); }
    else if (e.key === 'ArrowRight') { e.preventDefault(); stopPlay(); seek(M.frame + (e.shiftKey ? 10 : 1)); }
    else if (e.key === 'Home') seek(0);
    else if (e.key === 'End') seek(sc().length);
    else if (e.key === 'Delete' || e.key === 'Backspace') { e.preventDefault(); deleteSel(); }
  }, true);
}
document.addEventListener('DOMContentLoaded', mediaSetup);

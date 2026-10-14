// ODLK1 Visualizer: 2.5D Isometric Backtracking Pillars (10x10)
const N = 10;
const CELLS = N * N;
const EMPTY = 255;

// ODLK Logo Multi-Color Palette (yellow replaced with crimson/red)
const ODLK_COLORS = [
  '#ff00ff', // 0: magenta
  '#c83c00', // 1: rust red-orange
  '#ff1744', // 2: crimson red (replaces yellow)
  '#ff7500', // 3: vivid orange
  '#a8e400', // 4: lime green
  '#00ff00', // 5: pure green
  '#009b8e', // 6: teal cyan
  '#bc00ab', // 7: deep purple
  '#3a7bff', // 8: royal blue
  '#ff0000'  // 9: pure red
];

function hexToRgb(hex) {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function hexToRgba(hex, alpha) {
  const [r, g, b] = hexToRgb(hex);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

let selectedDigit = -1; // -1 = All digits, 0..9 = highlight specific digit

// 2.5D Isometric camera controls (drag to rotate/tilt, wheel to zoom)
let isoYawDeg = 0;
let isoPitch = 1.0;
let isoZoom = 1.0;
let isDragging = false;
let dragMoved = false;
let lastMouseX = 0;
let lastMouseY = 0;

// ---------- Canvas ----------
const leftPane = document.getElementById('left-pane');
const ringCanvas = document.getElementById('rings');
const rctx = ringCanvas.getContext('2d');

function resizeCanvas() {
  const dpr = Math.min(devicePixelRatio || 1, 2);
  const w = leftPane ? leftPane.clientWidth : Math.floor(innerWidth / 2);
  const h = leftPane ? leftPane.clientHeight : innerHeight;
  ringCanvas.width = w * dpr;
  ringCanvas.height = h * dpr;
  ringCanvas.style.width = w + 'px';
  ringCanvas.style.height = h + 'px';
  rctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}
resizeCanvas();
addEventListener('resize', resizeCanvas);

ringCanvas.addEventListener('mousedown', (e) => {
  if (e.button !== 0) return;
  isDragging = true;
  dragMoved = false;
  lastMouseX = e.clientX;
  lastMouseY = e.clientY;
});

addEventListener('mousemove', (e) => {
  if (!isDragging) return;
  const dx = e.clientX - lastMouseX;
  const dy = e.clientY - lastMouseY;
  if (Math.abs(dx) > 2 || Math.abs(dy) > 2) dragMoved = true;
  isoYawDeg = (isoYawDeg - dx * 0.45) % 360;
  isoPitch = Math.max(0.55, Math.min(1.45, isoPitch + dy * 0.004));
  lastMouseX = e.clientX;
  lastMouseY = e.clientY;
});

addEventListener('mouseup', () => {
  isDragging = false;
});

ringCanvas.addEventListener('wheel', (e) => {
  e.preventDefault();
  const factor = e.deltaY < 0 ? 1.08 : 0.92;
  isoZoom = Math.max(0.65, Math.min(1.65, isoZoom * factor));
}, { passive: false });

// ---------- Theme ----------
let dark = false;
const themeBtn = document.getElementById('theme');

function applyTheme(d) {
  dark = d;
  document.body.classList.toggle('dark', d);
  themeBtn.textContent = d ? 'Light' : 'Dark';
  updateDigitButtons();
  updateMini();
}
themeBtn.addEventListener('click', () => applyTheme(!dark));

// ---------- Digit Selector Bar (All, 0..9) ----------
const digitsBar = document.getElementById('digits-bar');
const digitBtns = [];

function makeDigitBtn(label, val) {
  const b = document.createElement('button');
  b.type = 'button';
  b.textContent = label;
  b.addEventListener('click', () => {
    selectedDigit = (selectedDigit === val && val !== -1) ? -1 : val;
    updateDigitButtons();
    updateMini();
  });
  digitsBar.appendChild(b);
  digitBtns.push({ el: b, val });
}
makeDigitBtn('All', -1);
for (let d = 0; d < N; d++) makeDigitBtn(String(d), d);

function updateDigitButtons() {
  for (const item of digitBtns) {
    const active = item.val === selectedDigit;
    if (item.val === -1) {
      item.el.style.background = active ? '#ff0000' : (dark ? '#111111' : '#ffffff');
      item.el.style.color = active ? '#ffffff' : (dark ? '#ffffff' : '#000000');
    } else {
      const col = ODLK_COLORS[item.val];
      item.el.style.background = active ? col : (dark ? '#111827' : '#ffffff');
      item.el.style.color = active ? ((item.val === 4 || item.val === 5) ? '#000000' : '#ffffff') : (item.val === 4 ? '#558b00' : (item.val === 5 ? '#00a000' : col));
      item.el.style.outline = active ? (dark ? '1px solid #ffffff' : '1px solid #000000') : 'none';
    }
  }
}

// ---------- State & 2D Mini-Matrix ----------
const grid = new Uint8Array(CELLS).fill(EMPTY);
const animH = new Float32Array(CELLS);
const cellHeat = new Float32Array(CELLS);
let hasReceivedLiveFrame = false;

// Seed with a real 10x10 ODLK square before the first SSE frame arrives
const INITIAL_SQUARE = [
  0,3,5,9,2,7,8,6,4,1,
  2,1,8,6,7,4,3,9,0,5,
  6,5,2,0,1,8,7,3,9,4,
  8,6,7,3,9,2,4,1,5,0,
  5,0,1,2,4,6,9,8,3,7,
  4,9,6,1,8,5,0,2,7,3,
  1,7,0,5,3,9,6,4,2,8,
  3,8,9,4,0,1,5,7,6,2,
  9,2,4,7,5,3,1,0,8,6,
  7,4,3,8,6,0,2,5,1,9
];
for (let i = 0; i < CELLS; i++) {
  const v = INITIAL_SQUARE[i];
  grid[i] = v;
  animH[i] = v + 0.6;
}

const miniEl = document.getElementById('mini');
const miniCells = [];
for (let i = 0; i < CELLS; i++) {
  const d = document.createElement('div');
  const r = Math.floor(i / N), c = i % N;
  d.addEventListener('click', () => {
    const v = grid[r * N + c];
    if (v !== EMPTY) {
      selectedDigit = (selectedDigit === v) ? -1 : v;
      updateDigitButtons();
      updateMini();
    }
  });
  miniEl.appendChild(d);
  miniCells.push(d);
}
let miniDirty = true;

// ---------- Stream (/stream & /stats) ----------
function hexToBytes(hex) {
  const s = hex.trim();
  const out = new Uint8Array(s.length >> 1);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(s.substr(i * 2, 2), 16);
  return out;
}

const queue = [];
const es = new EventSource('/stream');
es.onmessage = (e) => {
  const bytes = hexToBytes(e.data);
  if (bytes.length < CELLS) return;
  if (queue.length > 16) queue.shift();
  queue.push(bytes);
};

const $ = (id) => document.getElementById(id);
const hms = (s) => {
  if (s == null) return '-';
  s = Math.max(0, Math.round(s));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  const p = (n) => String(n).padStart(2, '0');
  return `${p(h)}:${p(m)}:${p(sec)}`;
};
const num = (n) => n == null ? '-' : Number(n).toLocaleString('en-US');

es.addEventListener('stats', (e) => {
  const s = JSON.parse(e.data) || {};
  $('s-wu').textContent = s.wu ?? '-';
  $('s-wu').title = s.wu ?? '';
  $('s-progress').textContent = s.progress == null ? '-' : (s.progress * 100).toFixed(2) + ' %';
  $('s-bar').style.width = s.progress == null ? '0' : (s.progress * 100).toFixed(2) + '%';
  $('s-elapsed').textContent = hms(s.elapsed);
  $('s-remaining').textContent = hms(s.remaining);
  $('s-checked').textContent = num(s.checked);
  $('s-total').textContent = num(s.total);
});

function applyFrame(bytes) {
  const isFirstLive = !hasReceivedLiveFrame;
  hasReceivedLiveFrame = true;
  for (let i = 0; i < CELLS; i++) {
    let v = bytes[i];
    if (v > 9) v = EMPTY;
    const old = grid[i];
    if (!isFirstLive && v === old) continue;
    if (!isFirstLive) {
      cellHeat[i] = 1.0;
    }
    grid[i] = v;
    if (isFirstLive && v !== EMPTY) {
      animH[i] = v + 0.6;
    }
    miniDirty = true;
  }
}

function updateMini() {
  for (let i = 0; i < CELLS; i++) {
    const v = grid[i];
    const el = miniCells[i];
    const r = Math.floor(i / N);
    if (v === EMPTY) {
      el.textContent = '';
      el.style.background = dark ? '#0a0c10' : '#ffffff';
      el.style.outline = 'none';
      continue;
    }
    el.textContent = v;
    const col = ODLK_COLORS[v];
    const isMatch = (selectedDigit === -1 || selectedDigit === v);
    if (isMatch) {
      el.style.background = hexToRgba(col, selectedDigit === v ? 0.95 : (r <= 3 ? 0.75 : 0.92));
      el.style.color = (v === 4 || v === 5) ? '#000000' : '#ffffff';
      el.style.outline = 'none';
    } else {
      el.style.background = hexToRgba(col, 0.15);
      el.style.color = dark ? '#64748b' : '#94a3b8';
      el.style.outline = 'none';
    }
  }
  miniDirty = false;
}

// ---------- 2.5D Isometric Backtracking Pillars Renderer ----------
function isoProject(r, c, z, cx, cy, unit) {
  const rad = (isoYawDeg + 45) * Math.PI / 180;
  const cosA = Math.cos(rad);
  const sinA = Math.sin(rad);
  const xr = c - 4.5;
  const yr = r - 4.5;
  const rx = xr * cosA - yr * sinA;
  const ry = xr * sinA + yr * cosA;
  const scaleX = unit * 1.05 * isoZoom;
  const scaleY = unit * 0.55 * isoPitch * isoZoom;
  const zScale = unit * 0.24 * isoZoom;
  return {
    x: cx + rx * scaleX,
    y: cy + unit * 1.4 * isoZoom + ry * scaleY - z * zScale,
    depth: ry
  };
}

function renderPillars() {
  const W = leftPane ? leftPane.clientWidth : Math.floor(innerWidth / 2);
  const H = leftPane ? leftPane.clientHeight : innerHeight;
  rctx.clearRect(0, 0, W, H);

  const cx = W * 0.50;
  const cy = H * 0.46;
  const unit = Math.min(W, H) * 0.052;

  // Isometric base platform under the 10x10 pillars
  const b00 = isoProject(-0.55, -0.55, 0, cx, cy, unit);
  const b10 = isoProject(-0.55, 9.55, 0, cx, cy, unit);
  const b11 = isoProject(9.55, 9.55, 0, cx, cy, unit);
  const b01 = isoProject(9.55, -0.55, 0, cx, cy, unit);

  rctx.beginPath();
  rctx.moveTo(b00.x, b00.y);
  rctx.lineTo(b10.x, b10.y);
  rctx.lineTo(b11.x, b11.y);
  rctx.lineTo(b01.x, b01.y);
  rctx.closePath();
  rctx.fillStyle = dark ? 'rgba(15, 23, 42, 0.75)' : 'rgba(241, 245, 249, 0.85)';
  rctx.fill();
  rctx.lineWidth = 1.5;
  rctx.strokeStyle = dark ? 'rgba(255, 23, 68, 0.45)' : 'rgba(15, 23, 42, 0.35)';
  rctx.stroke();

  // Sort all 100 cells back-to-front by isometric depth
  const order = [];
  for (let r = 0; r < N; r++) {
    for (let c = 0; c < N; c++) {
      const p = isoProject(r, c, 0, cx, cy, unit);
      order.push({ r, c, depth: p.depth });
    }
  }
  order.sort((a, b) => a.depth - b.depth);

  const s = 0.42;
  const fontPx = Math.max(12, Math.round(unit * 0.56 * isoZoom));

  for (const item of order) {
    const { r, c } = item;
    const idx = r * N + c;
    const v = grid[idx];
    if (v === EMPTY) continue;

    const hVal = Math.max(0.4, animH[idx]);
    const hHeat = cellHeat[idx];
    const isDimmed = selectedDigit !== -1 && selectedDigit !== v;

    const [cr, cg, cb] = hexToRgb(ODLK_COLORS[v % 10]);
    const alpha = isDimmed ? 0.16 : 1.0;
    const topFill = `rgba(${cr},${cg},${cb},${alpha})`;
    const leftFill = `rgba(${Math.round(cr * 0.62)},${Math.round(cg * 0.62)},${Math.round(cb * 0.62)},${alpha})`;
    const rightFill = `rgba(${Math.round(cr * 0.40)},${Math.round(cg * 0.40)},${Math.round(cb * 0.40)},${alpha})`;
    const strokeCol = 'rgba(15, 23, 42, 0.45)';

    const p00 = isoProject(r - s, c - s, 0, cx, cy, unit);
    const p10 = isoProject(r - s, c + s, 0, cx, cy, unit);
    const p11 = isoProject(r + s, c + s, 0, cx, cy, unit);
    const p01 = isoProject(r + s, c - s, 0, cx, cy, unit);

    const t00 = isoProject(r - s, c - s, hVal, cx, cy, unit);
    const t10 = isoProject(r - s, c + s, hVal, cx, cy, unit);
    const t11 = isoProject(r + s, c + s, hVal, cx, cy, unit);
    const t01 = isoProject(r + s, c - s, hVal, cx, cy, unit);

    const centerX = (p00.x + p11.x) * 0.5;
    const walls = [
      { b0: p00, b1: p10, u1: t10, u0: t00 },
      { b0: p10, b1: p11, u1: t11, u0: t10 },
      { b0: p11, b1: p01, u1: t01, u0: t11 },
      { b0: p01, b1: p00, u1: t00, u0: t01 }
    ];
    for (const w of walls) {
      const midDepth = (w.b0.depth + w.b1.depth) * 0.5;
      if (midDepth <= item.depth) continue;
      const midX = (w.b0.x + w.b1.x) * 0.5;
      rctx.beginPath();
      rctx.moveTo(w.b0.x, w.b0.y);
      rctx.lineTo(w.b1.x, w.b1.y);
      rctx.lineTo(w.u1.x, w.u1.y);
      rctx.lineTo(w.u0.x, w.u0.y);
      rctx.closePath();
      rctx.fillStyle = (midX < centerX) ? leftFill : rightFill;
      rctx.fill();
    }

    // Top cap
    rctx.beginPath();
    rctx.moveTo(t00.x, t00.y);
    rctx.lineTo(t10.x, t10.y);
    rctx.lineTo(t11.x, t11.y);
    rctx.lineTo(t01.x, t01.y);
    rctx.closePath();
    rctx.fillStyle = topFill;
    rctx.fill();
    rctx.lineWidth = 0.8;
    rctx.strokeStyle = strokeCol;
    rctx.stroke();

    // Digit label on top cap
    const capX = (t00.x + t11.x) / 2;
    const capY = (t00.y + t11.y) / 2;
    rctx.save();
    rctx.font = `bold ${fontPx}px "Courier New", monospace`;
    rctx.textAlign = 'center';
    rctx.textBaseline = 'middle';
    if (isDimmed) {
      rctx.fillStyle = dark ? 'rgba(255,255,255,0.25)' : 'rgba(0,0,0,0.25)';
    } else if (v === 4 || v === 5) {
      rctx.fillStyle = '#0a0c10';
    } else {
      rctx.fillStyle = '#ffffff';
    }
    rctx.fillText(String(v), capX, capY);
    rctx.restore();
  }
}

// ---------- Main Loop ----------
let lastTime = performance.now();
let simAccum = 0;

function tick(now) {
  requestAnimationFrame(tick);
  const dt = Math.min((now - lastTime) / 1000, 0.1);
  lastTime = now;

  while (queue.length) applyFrame(queue.shift());

  // Gentle idle animation if no BOINC process is streaming yet
  if (!hasReceivedLiveFrame) {
    simAccum += dt;
    if (simAccum > 0.16) {
      simAccum = 0;
      const r = 4 + Math.floor(Math.random() * 6);
      const c1 = Math.floor(Math.random() * N);
      const c2 = (c1 + 1) % N;
      const t = grid[r * N + c1];
      grid[r * N + c1] = grid[r * N + c2];
      grid[r * N + c2] = t;
      cellHeat[r * N + c1] = 1.0;
      cellHeat[r * N + c2] = 1.0;
      miniDirty = true;
    }
  }

  for (let i = 0; i < CELLS; i++) {
    const v = grid[i] === EMPTY ? 0 : grid[i];
    const targetH = v + 0.6;
    animH[i] += (targetH - animH[i]) * Math.min(1, dt * 14);
    cellHeat[i] = Math.max(0, cellHeat[i] - dt * 2.2);
  }

  if (miniDirty) updateMini();
  renderPillars();
}

applyTheme(false);
requestAnimationFrame(tick);

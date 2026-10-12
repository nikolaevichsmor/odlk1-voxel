// ODLK1 Voxel Tensor - a 10x10 matrix shown as a 10x10x10 cube.
// Cell i (0..99): X = i % 10 (column), Y = i / 10 (row), Z = value (0..9).
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const N = 10;
const CELLS = N * N;
const VOX = N * N * N;
const EMPTY = 255;

// Fixed settings
const FADE_TIME = 0.25;   // trail fade-out, s
const VOXEL_SIZE = 0.30;  // fraction of the grid step
const FLASH = 1.6;        // brightness of a just-placed digit

// ---------- scene ----------
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.setSize(innerWidth, innerHeight);
document.getElementById('app').appendChild(renderer.domElement);

const scene = new THREE.Scene();
scene.background = new THREE.Color(0xffffff);

const camera = new THREE.PerspectiveCamera(45, innerWidth / innerHeight, 0.1, 500);
camera.position.set(16, 10, 26);

const controls = new OrbitControls(camera, renderer.domElement);
controls.target.set(0, -1.8, 0);
controls.enableDamping = true;
controls.autoRotate = true;
controls.autoRotateSpeed = 0.6;

addEventListener('resize', () => {
  camera.aspect = innerWidth / innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight);
});

const voxPos = (row, col, val) => new THREE.Vector3(col - 4.5, 4.5 - row, val - 4.5);

// ---------- voxels ----------
const intensity = new Float32Array(VOX);
const lit = new Uint8Array(VOX);
const shades = new Float32Array(VOX);

const boxGeo = new THREE.BoxGeometry(1, 1, 1);
const aIntensity = new THREE.InstancedBufferAttribute(intensity, 1);
aIntensity.setUsage(THREE.DynamicDrawUsage);
boxGeo.setAttribute('aIntensity', aIntensity);
boxGeo.setAttribute('aShade', new THREE.InstancedBufferAttribute(shades, 1));

const voxelMat = new THREE.ShaderMaterial({
  uniforms: { uScale: { value: VOXEL_SIZE }, uLight: { value: 1.0 } },
  vertexShader: /* glsl */`
    attribute float aIntensity;
    attribute float aShade;
    uniform float uScale;
    varying float vShade;
    varying float vI;
    varying vec3 vLocal;
    void main() {
      vI = aIntensity; vShade = aShade; vLocal = position;
      if (aIntensity < 0.004) { gl_Position = vec4(0.0, 0.0, 2.0, 1.0); return; }
      float s = uScale * mix(0.6, 1.0, clamp(aIntensity, 0.0, 1.0));
      gl_Position = projectionMatrix * modelViewMatrix * instanceMatrix * vec4(position * s, 1.0);
    }`,
  fragmentShader: /* glsl */`
    uniform float uLight;
    varying float vShade;
    varying float vI;
    varying vec3 vLocal;
    void main() {
      vec3 a = abs(vLocal) * 2.0;
      float mx = max(a.x, max(a.y, a.z));
      float mn = min(a.x, min(a.y, a.z));
      float mid = a.x + a.y + a.z - mx - mn;
      float edge = smoothstep(0.70, 0.90, mid);
      float flash = clamp(vI - 1.0, 0.0, 1.0);
      float i = clamp(vI, 0.0, 1.0);
      float g = vShade * (0.15 + 0.85 * edge) + 0.6 * flash;
      if (uLight > 0.5) gl_FragColor = vec4(vec3(1.0 - clamp(g * 1.5, 0.0, 1.0)), i);
      else gl_FragColor = vec4(vec3(g * i), 1.0);
    }`,
  transparent: true,
  depthWrite: false,
  blending: THREE.NormalBlending,
});

const voxels = new THREE.InstancedMesh(boxGeo, voxelMat, VOX);
voxels.frustumCulled = false;
{
  const m = new THREE.Matrix4();
  for (let row = 0; row < N; row++)
    for (let col = 0; col < N; col++)
      for (let val = 0; val < N; val++) {
        const k = (row * N + col) * N + val;
        const p = voxPos(row, col, val);
        voxels.setMatrixAt(k, m.makeTranslation(p.x, p.y, p.z));
        shades[k] = 0.4 + 0.6 * (val / (N - 1));   // gray level by Z
      }
  voxels.instanceMatrix.needsUpdate = true;
}
scene.add(voxels);

// ---------- frame, grid, nodes ----------
const frameMat = new THREE.LineBasicMaterial({ color: 0x000000, transparent: true, opacity: 0.6 });
scene.add(new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(N, N, N)), frameMat));

const gridMat = new THREE.LineBasicMaterial({ color: 0xc8c8c8 });
{
  const pts = [];
  const h = N / 2;
  for (let i = 1; i < N; i++) {
    const t = i - h;
    pts.push(-h, -h, t, h, -h, t, t, -h, -h, t, -h, h);
    pts.push(-h, t, -h, h, t, -h, t, -h, -h, t, h, -h);
    pts.push(-h, t, -h, -h, t, h, -h, -h, t, -h, h, t);
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.Float32BufferAttribute(pts, 3));
  scene.add(new THREE.LineSegments(g, gridMat));
}

const dotsMat = new THREE.PointsMaterial({ color: 0x909090, size: 0.05 });
{
  const p = new Float32Array(VOX * 3);
  let j = 0;
  for (let row = 0; row < N; row++) for (let col = 0; col < N; col++) for (let val = 0; val < N; val++) {
    const v = voxPos(row, col, val); p[j++] = v.x; p[j++] = v.y; p[j++] = v.z;
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(p, 3));
  scene.add(new THREE.Points(g, dotsMat));
}

// ---------- axis labels ----------
const labelMats = [];
function label(text, pos, dim = false, scale = 1) {
  const cv = document.createElement('canvas');
  const ctx = cv.getContext('2d');
  const fs = 48;
  const font = `${fs}px "Courier New", Courier, monospace`;
  ctx.font = font;
  cv.width = Math.ceil(ctx.measureText(text).width) + 8; cv.height = fs + 8;
  ctx.font = font;
  ctx.fillStyle = '#ffffff'; ctx.textBaseline = 'middle';
  ctx.fillText(text, 4, cv.height / 2);
  const tex = new THREE.CanvasTexture(cv);
  tex.colorSpace = THREE.SRGBColorSpace;
  const mat = new THREE.SpriteMaterial({ map: tex, transparent: true, depthWrite: false });
  mat.userData.dim = dim;
  labelMats.push(mat);
  const s = new THREE.Sprite(mat);
  const k = 0.011 * scale;
  s.scale.set(cv.width * k, cv.height * k, 1);
  s.position.copy(pos);
  scene.add(s);
}
label('X - column', new THREE.Vector3(0, -5.9, 5.6));
label('Y - row', new THREE.Vector3(-7.2, 0, 5.4));
label('Z - digit', new THREE.Vector3(6.2, -6.2, 7.2));
for (let v = 0; v < N; v++) label(String(v), new THREE.Vector3(5.5, -5.5, v - 4.5), true, 0.7);

// ---------- theme (light at startup) ----------
function applyTheme(dark) {
  document.body.classList.toggle('dark', dark);
  scene.background.set(dark ? 0x000000 : 0xffffff);
  frameMat.color.set(dark ? 0xffffff : 0x000000);
  gridMat.color.set(dark ? 0x404040 : 0xc8c8c8);
  dotsMat.color.set(dark ? 0x606060 : 0x909090);
  voxelMat.uniforms.uLight.value = dark ? 0.0 : 1.0;
  voxelMat.blending = dark ? THREE.AdditiveBlending : THREE.NormalBlending;
  voxelMat.needsUpdate = true;
  for (const m of labelMats) m.color.set(m.userData.dim ? (dark ? 0xc0c0c0 : 0x606060) : (dark ? 0xffffff : 0x000000));
  themeBtn.textContent = dark ? 'Light' : 'Dark';
}
const themeBtn = document.getElementById('theme');
let isDark = false;
themeBtn.addEventListener('click', () => { isDark = !isDark; applyTheme(isDark); });

// ---------- mini matrix ----------
const miniCells = [];
const miniEl = document.getElementById('mini');
for (let i = 0; i < CELLS; i++) { const d = document.createElement('div'); miniEl.appendChild(d); miniCells.push(d); }

// ---------- data feed (SSE from bridge.py) ----------
const grid = new Uint8Array(CELLS).fill(EMPTY);
const queue = [];
let miniDirty = true;

function hexToBytes(hex) {
  const out = new Uint8Array(hex.length >> 1);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(hex.substr(i * 2, 2), 16);
  return out;
}

const es = new EventSource('/stream');
es.onmessage = (e) => {
  queue.push(hexToBytes(e.data));
  if (queue.length > 16) queue.shift();
};

// ---------- BOINC task stats ----------
const $ = (id) => document.getElementById(id);
const hms = (s) => {
  if (s == null) return '-';
  s = Math.max(0, Math.round(s));
  const p = (n) => String(n).padStart(2, '0');
  return `${p(Math.floor(s / 3600))}:${p(Math.floor(s / 60) % 60)}:${p(s % 60)}`;
};
const num = (n) => (n == null ? '-' : n.toLocaleString('en-US'));

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
  for (let i = 0; i < CELLS; i++) {
    let v = bytes[i];
    if (v > 9) v = EMPTY;
    const old = grid[i];
    if (v === old) continue;
    if (old !== EMPTY) {
      const k = i * N + old;
      lit[k] = 0;
      intensity[k] = Math.min(intensity[k], 1);
    }
    if (v !== EMPTY) {
      const k = i * N + v;
      lit[k] = 1;
      intensity[k] = FLASH;
    }
    grid[i] = v;
    miniDirty = true;
  }
}

function updateMini() {
  for (let i = 0; i < CELLS; i++) miniCells[i].textContent = grid[i] === EMPTY ? '' : grid[i];
  miniDirty = false;
}

// ---------- loop ----------
const clock = new THREE.Clock();

function tick() {
  requestAnimationFrame(tick);
  const dt = Math.min(clock.getDelta(), 0.1);

  while (queue.length) applyFrame(queue.shift());

  const decay = dt / FADE_TIME;
  for (let k = 0; k < VOX; k++) {
    if (lit[k]) intensity[k] = Math.max(1, intensity[k] - dt * 4);
    else if (intensity[k] > 0) intensity[k] = Math.max(0, intensity[k] - decay);
  }
  aIntensity.needsUpdate = true;

  if (miniDirty) updateMini();
  controls.update();
  renderer.render(scene, camera);
}
tick();

applyTheme(false);

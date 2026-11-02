// The MKB Bull: a low-poly 3D bull head drawn on a 2D canvas.
// It turns its head and eyes toward the cursor, blinks, breathes, frowns when
// the cursor drops (bearish) and snorts when clicked. No WebGL, no libraries:
// a tiny flat-shaded renderer (rotate → light → depth-sort → paint).

const TAU = Math.PI * 2;
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const lerp = (a, b, t) => a + (b - a) * t;

// ---- Model -------------------------------------------------------------------
// x right, y down, z toward the viewer. Left half is defined; right half mirrors.
const CENTER = {
  C0: [0, -120, 20], C1: [0, -75, 48], C2: [0, -25, 60], C3: [0, 30, 66],
  C4: [0, 80, 74], C5: [0, 118, 84], C6: [0, 150, 60], C7: [0, 172, 30],
};
const SIDE = {
  L1: [-56, -118, 12], L2: [-98, -104, -6], L3: [-122, -72, -16], L4: [-64, -70, 40],
  L5: [-50, -32, 50], L6: [-104, -38, 18], L7: [-124, -4, -10], L8: [-90, 10, 22],
  L9: [-40, 4, 54], L10: [-106, 46, 0], L11: [-66, 52, 34], L12: [-35, 60, 58],
  L13: [-72, 96, 20], L14: [-52, 100, 52], L15: [-30, 108, 74], L16: [-63, 136, 40],
  L17: [-34, 148, 62], L18: [-44, 170, 22],
};
// [a, b, c, material]
const FACES_L = [
  ['C0', 'L1', 'L4', 'crown'], ['C0', 'L4', 'C1', 'crown'], ['L1', 'L2', 'L4', 'crown'],
  ['L2', 'L3', 'L4', 'head'], ['C1', 'L4', 'L5', 'head'], ['C1', 'L5', 'C2', 'head'],
  ['L4', 'L3', 'L6', 'head'], ['L4', 'L6', 'L5', 'brow'],
  ['L5', 'L6', 'L8', 'socket'], ['L5', 'L8', 'L9', 'socket'],
  ['L6', 'L3', 'L7', 'head'], ['L6', 'L7', 'L8', 'head'],
  ['C2', 'L5', 'L9', 'head'], ['C2', 'L9', 'C3', 'head'], ['L9', 'L12', 'C3', 'head'],
  ['L8', 'L7', 'L10', 'head'], ['L8', 'L10', 'L11', 'head'], ['L9', 'L8', 'L11', 'head'], ['L9', 'L11', 'L12', 'head'],
  ['C3', 'L12', 'L14', 'muzzle'], ['C3', 'L14', 'C4', 'muzzle'], ['L12', 'L11', 'L14', 'head'],
  ['L11', 'L10', 'L13', 'head'], ['L11', 'L13', 'L14', 'head'],
  ['C4', 'L14', 'L15', 'muzzle'], ['C4', 'L15', 'C5', 'muzzle'], ['L14', 'L13', 'L16', 'muzzle'],
  ['L14', 'L16', 'L15', 'muzzle'], ['L15', 'L16', 'L17', 'muzzle'], ['C5', 'L15', 'L17', 'muzzle'], ['C5', 'L17', 'C6', 'muzzle'],
  ['L16', 'L13', 'L18', 'jaw'], ['L16', 'L18', 'L17', 'jaw'], ['L17', 'L18', 'C7', 'jaw'], ['C6', 'L17', 'C7', 'jaw'],
];
const EAR = { E1: [-116, -64, -20], E2: [-124, -28, -22], E3: [-192, -58, -42], E4: [-174, -30, -36], E5: [-164, -70, -34] };
const EAR_FACES = [['E1', 'E5', 'E3', 'ear'], ['E1', 'E3', 'E4', 'ear'], ['E1', 'E4', 'E2', 'ear']];
const EAR_INNER = [[-126, -56, -16], [-176, -55, -30], [-132, -38, -17]];
const HORN = [[-84, -106, 0, 25], [-128, -114, -6, 20], [-168, -132, -4, 15], [-192, -166, 6, 9], [-190, -206, 18, 1.4]];
// Neck: a dark mass behind the head that grounds the silhouette.
const NECK = [[-118, -10, -40], [-158, 110, -70], [-190, 300, -90], [190, 300, -90], [158, 110, -70], [118, -10, -40]];
const EYE = { c: [-76, -14, 40], rx: 20, ry: 14 };
const NOSTRIL = { c: [-23, 126, 84], rx: 11, ry: 6.5 };

const MATERIALS = {
  crown: [22, 92, 70], head: [27, 110, 84], brow: [18, 80, 62], socket: [14, 58, 45],
  muzzle: [40, 190, 146], jaw: [20, 86, 66], ear: [22, 95, 73],
};

function buildModel() {
  const verts = {};
  for (const [k, v] of Object.entries(CENTER)) verts[k] = v;
  for (const [k, v] of Object.entries(SIDE)) { verts[k] = v; verts['R' + k.slice(1)] = [-v[0], v[1], v[2]]; }
  for (const [k, v] of Object.entries(EAR)) { verts['l' + k] = v; verts['r' + k] = [-v[0], v[1], v[2]]; }
  const mirror = (n) => (n.startsWith('L') ? 'R' + n.slice(1) : n);
  const faces = [];
  for (const [a, b, c, m] of FACES_L) {
    faces.push({ v: [a, b, c], m, part: 'head' });
    faces.push({ v: [mirror(a), mirror(b), mirror(c)], m, part: 'head' });
  }
  const ears = [];
  for (const [a, b, c, m] of EAR_FACES) {
    ears.push({ v: ['l' + a, 'l' + b, 'l' + c], m, part: 'ear' });
    ears.push({ v: ['r' + a, 'r' + b, 'r' + c], m, part: 'ear' });
  }
  // Outward normals in model space (pointing away from the head's core).
  const core = [0, 30, -60];
  for (const f of [...faces, ...ears]) {
    const [p, q, r] = f.v.map((n) => verts[n]);
    const u = [q[0] - p[0], q[1] - p[1], q[2] - p[2]];
    const w = [r[0] - p[0], r[1] - p[1], r[2] - p[2]];
    let n = [u[1] * w[2] - u[2] * w[1], u[2] * w[0] - u[0] * w[2], u[0] * w[1] - u[1] * w[0]];
    const len = Math.hypot(...n) || 1;
    n = n.map((x) => x / len);
    const cen = [(p[0] + q[0] + r[0]) / 3, (p[1] + q[1] + r[1]) / 3, (p[2] + q[2] + r[2]) / 3];
    const out = [cen[0] - core[0], cen[1] - core[1], cen[2] - core[2]];
    if (n[0] * out[0] + n[1] * out[1] + n[2] * out[2] < 0) n = n.map((x) => -x);
    f.n = n;
  }
  return { verts, faces, ears };
}

// ---- Renderer ----------------------------------------------------------------
export function mountBull(canvas, opts = {}) {
  const ctx = canvas.getContext('2d');
  const model = buildModel();
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const state = {
    w: 0, h: 0, dpr: 1, scale: 1, cx: 0, cy: 0,
    target: { x: 0, y: 0 }, gaze: { x: 0, y: 0 }, head: { yaw: 0, pitch: 0, roll: 0 },
    lastMove: -1e9, blink: 0, nextBlink: 1.5, snort: 0, puffs: [], mood: 0.6, moodShown: 0.6,
    t: 0, visible: true, raf: 0, last: performance.now(), pointerInside: false,
  };
  const listeners = new Set();

  function resize() {
    const r = canvas.getBoundingClientRect();
    state.dpr = Math.min(window.devicePixelRatio || 1, 2);
    state.w = r.width; state.h = r.height;
    canvas.width = Math.round(r.width * state.dpr);
    canvas.height = Math.round(r.height * state.dpr);
    state.scale = Math.min(r.width / 450, r.height / 480) * (opts.zoom || 0.98);
    state.cx = r.width / 2;
    state.cy = r.height / 2 + 12 * state.scale;
  }

  // Pointer → normalized target. The whole window counts, so the bull watches the cursor anywhere.
  function onPointer(e) {
    const r = canvas.getBoundingClientRect();
    const hx = r.left + state.cx, hy = r.top + state.cy - 40 * state.scale;
    const nx = clamp((e.clientX - hx) / (window.innerWidth * 0.45), -1, 1);
    const ny = clamp((e.clientY - hy) / (window.innerHeight * 0.5), -1, 1);
    state.target.x = nx; state.target.y = ny;
    state.lastMove = state.t;
    state.mood = clamp(1 - e.clientY / window.innerHeight, 0, 1);
  }
  function onClick(e) {
    const r = canvas.getBoundingClientRect();
    if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) return;
    snort();
  }
  function snort() {
    state.snort = 1;
    for (const side of [-1, 1]) {
      for (let i = 0; i < 14; i++) {
        state.puffs.push({ side, life: 1, x: 0, y: 0, vx: side * (0.4 + Math.random() * 1.2), vy: 0.6 + Math.random() * 1.4, r: 4 + Math.random() * 6 });
      }
    }
  }

  // Rotation + perspective
  function makeXf() {
    const { yaw, pitch, roll } = state.head;
    const cy = Math.cos(yaw), sy = Math.sin(yaw), cp = Math.cos(pitch), sp = Math.sin(pitch), cr = Math.cos(roll), sr = Math.sin(roll);
    const breathe = 1 + Math.sin(state.t * 1.6) * 0.006;
    const rot = (p) => {
      let [x, y, z] = p;
      y *= breathe;
      let x1 = x * cy + z * sy, z1 = -x * sy + z * cy;
      let y1 = y * cp - z1 * sp, z2 = y * sp + z1 * cp;
      let x2 = x1 * cr - y1 * sr, y2 = x1 * sr + y1 * cr;
      return [x2, y2, z2];
    };
    const rotN = (n) => {
      const [x, y, z] = n;
      let x1 = x * cy + z * sy, z1 = -x * sy + z * cy;
      let y1 = y * cp - z1 * sp, z2 = y * sp + z1 * cp;
      return [x1 * cr - y1 * sr, x1 * sr + y1 * cr, z2];
    };
    const F = 900;
    const proj = (p) => {
      const s = F / (F - p[2]);
      return [state.cx + p[0] * s * state.scale, state.cy + p[1] * s * state.scale, p[2]];
    };
    return { rot, rotN, proj };
  }

  function shade(base, n, light, rimDir, extra = 1) {
    const d = Math.max(0, n[0] * light[0] + n[1] * light[1] + n[2] * light[2]);
    const rim = Math.max(0, n[0] * rimDir[0] + n[1] * rimDir[1] + n[2] * rimDir[2]);
    const k = (0.38 + 0.78 * d) * extra;
    const r = Math.min(255, base[0] * k + 43 * rim * rim * 0.6);
    const g = Math.min(255, base[1] * k + 214 * rim * rim * 0.6);
    const b = Math.min(255, base[2] * k + 159 * rim * rim * 0.6);
    return `rgb(${r | 0},${g | 0},${b | 0})`;
  }

  function drawPoly(pts, fill, stroke, lw = 1) {
    ctx.beginPath();
    ctx.moveTo(pts[0][0], pts[0][1]);
    for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
    ctx.closePath();
    ctx.fillStyle = fill; ctx.fill();
    ctx.strokeStyle = stroke || fill; ctx.lineWidth = lw; ctx.stroke();
  }

  function drawHorn(xf, side, light, rimDir) {
    const pts = HORN.map(([x, y, z, r]) => ({ p: xf.proj(xf.rot([x * side, y, z])), r }));
    const left = [], right = [];
    for (let i = 0; i < pts.length; i++) {
      const a = pts[Math.max(0, i - 1)].p, b = pts[Math.min(pts.length - 1, i + 1)].p;
      let dx = b[0] - a[0], dy = b[1] - a[1];
      const l = Math.hypot(dx, dy) || 1; dx /= l; dy /= l;
      const rr = pts[i].r * state.scale;
      left.push([pts[i].p[0] - dy * rr, pts[i].p[1] + dx * rr]);
      right.push([pts[i].p[0] + dy * rr, pts[i].p[1] - dx * rr]);
    }
    const base = pts[0].p, tip = pts[pts.length - 1].p;
    const g = ctx.createLinearGradient(base[0], base[1], tip[0], tip[1]);
    const lit = 0.75 + 0.25 * Math.max(0, -side * light[0] + 0.3);
    g.addColorStop(0, `rgb(${31 * lit | 0},${122 * lit | 0},${92 * lit | 0})`);
    g.addColorStop(0.45, `rgb(${120 * lit | 0},${200 * lit | 0},${170 * lit | 0})`);
    g.addColorStop(1, `rgb(${228 * lit | 0},${248 * lit | 0},${238 * lit | 0})`);
    ctx.beginPath();
    ctx.moveTo(left[0][0], left[0][1]);
    for (let i = 1; i < left.length; i++) ctx.lineTo(left[i][0], left[i][1]);
    for (let i = right.length - 1; i >= 0; i--) ctx.lineTo(right[i][0], right[i][1]);
    ctx.closePath();
    ctx.fillStyle = g; ctx.fill();
    // Ridges: the low-poly bands on the horn
    ctx.strokeStyle = 'rgba(4,20,14,0.35)'; ctx.lineWidth = 1.2 * state.scale;
    for (let i = 1; i < pts.length - 1; i++) {
      ctx.beginPath(); ctx.moveTo(left[i][0], left[i][1]); ctx.lineTo(right[i][0], right[i][1]); ctx.stroke();
    }
    // Highlight down the lit side
    ctx.strokeStyle = 'rgba(255,255,255,0.35)'; ctx.lineWidth = 1.4 * state.scale;
    const hl = side < 0 ? left : right;
    ctx.beginPath(); ctx.moveTo(hl[1][0], hl[1][1]);
    for (let i = 2; i < hl.length; i++) ctx.lineTo(hl[i][0], hl[i][1]);
    ctx.stroke();
  }

  function eyeAxes(xf, side) {
    const c = [EYE.c[0] * side, EYE.c[1], EYE.c[2]];
    const pc = xf.proj(xf.rot(c));
    const px = xf.proj(xf.rot([c[0] + EYE.rx, c[1], c[2] + (side < 0 ? -4 : 4)]));
    const py = xf.proj(xf.rot([c[0], c[1] + EYE.ry, c[2] + 2]));
    return { c: pc, a: [px[0] - pc[0], px[1] - pc[1]], b: [py[0] - pc[0], py[1] - pc[1]] };
  }

  function drawEye(xf, side, lidColor) {
    const { c, a, b } = eyeAxes(xf, side);
    const mood = state.moodShown;
    ctx.save();
    ctx.setTransform(a[0] * state.dpr, a[1] * state.dpr, b[0] * state.dpr, b[1] * state.dpr, c[0] * state.dpr, c[1] * state.dpr);
    // socket shadow ring
    ctx.beginPath(); ctx.ellipse(0, 0, 1.22, 1.3, 0, 0, TAU); ctx.fillStyle = 'rgba(3,14,10,0.55)'; ctx.fill();
    // eyeball
    ctx.beginPath(); ctx.ellipse(0, 0, 1, 1, 0, 0, TAU);
    const sg = ctx.createRadialGradient(-0.3, -0.3, 0.1, 0, 0, 1.1);
    sg.addColorStop(0, '#F4FBF8'); sg.addColorStop(1, '#BFD9CE');
    ctx.fillStyle = sg; ctx.fill();
    ctx.save(); ctx.clip();
    // iris follows the gaze (the head already turned; eyes add the last bit)
    const ix = clamp(state.gaze.x * 0.5, -0.45, 0.45), iy = clamp(state.gaze.y * 0.38, -0.3, 0.36);
    const ig = ctx.createRadialGradient(ix - 0.15, iy - 0.15, 0.05, ix, iy, 0.62);
    ig.addColorStop(0, '#B8FBE4'); ig.addColorStop(0.45, '#2BD69F'); ig.addColorStop(1, '#0B3D2E');
    ctx.beginPath(); ctx.ellipse(ix, iy, 0.6, 0.62, 0, 0, TAU); ctx.fillStyle = ig; ctx.fill();
    ctx.beginPath(); ctx.ellipse(ix, iy, 0.28, 0.3, 0, 0, TAU); ctx.fillStyle = '#020806'; ctx.fill();
    ctx.beginPath(); ctx.ellipse(ix - 0.2, iy - 0.22, 0.12, 0.1, 0, 0, TAU); ctx.fillStyle = 'rgba(255,255,255,0.95)'; ctx.fill();
    ctx.beginPath(); ctx.ellipse(ix + 0.18, iy + 0.18, 0.05, 0.05, 0, 0, TAU); ctx.fillStyle = 'rgba(255,255,255,0.6)'; ctx.fill();
    // upper lid: lower when bearish, closes on blink. The inner corner dips for a stern look.
    const lidBase = lerp(0.02, -0.72, mood);
    const lid = lerp(lidBase, 1.05, state.blink);
    const tilt = 0.32 * (side < 0 ? 1 : -1) * (1.2 - mood);
    ctx.beginPath();
    ctx.moveTo(-1.3, -1.4); ctx.lineTo(1.3, -1.4);
    ctx.lineTo(1.3, lid + tilt); ctx.quadraticCurveTo(0, lid + 0.12, -1.3, lid - tilt);
    ctx.closePath(); ctx.fillStyle = lidColor; ctx.fill();
    ctx.beginPath(); ctx.moveTo(1.3, lid + tilt); ctx.quadraticCurveTo(0, lid + 0.12, -1.3, lid - tilt);
    ctx.strokeStyle = '#06261B'; ctx.lineWidth = 0.14; ctx.stroke();
    // lower lid
    const low = lerp(0.82, 1.05, state.blink);
    ctx.beginPath(); ctx.moveTo(-1.3, 1.4); ctx.lineTo(1.3, 1.4); ctx.lineTo(1.3, low); ctx.quadraticCurveTo(0, low - 0.1, -1.3, low); ctx.closePath();
    ctx.fillStyle = lidColor; ctx.fill();
    ctx.restore();
    ctx.beginPath(); ctx.ellipse(0, 0, 1, 1, 0, 0, TAU); ctx.strokeStyle = '#04150F'; ctx.lineWidth = 0.1; ctx.stroke();
    ctx.restore();
  }

  function drawBrow(xf, side) {
    const mood = state.moodShown;
    const outer = xf.proj(xf.rot([98 * side, -40 - 4 * mood, 16]));
    const inner = xf.proj(xf.rot([44 * side, -24 - 14 * mood, 54]));
    const mid = xf.proj(xf.rot([72 * side, -44 - 8 * mood, 40]));
    ctx.beginPath(); ctx.moveTo(outer[0], outer[1]); ctx.quadraticCurveTo(mid[0], mid[1], inner[0], inner[1]);
    ctx.strokeStyle = '#072519'; ctx.lineCap = 'round'; ctx.lineWidth = 9 * state.scale; ctx.stroke();
    ctx.strokeStyle = 'rgba(43,214,159,0.25)'; ctx.lineWidth = 1.4 * state.scale; ctx.stroke();
  }

  function drawNostril(xf, side) {
    const c = [NOSTRIL.c[0] * side, NOSTRIL.c[1], NOSTRIL.c[2]];
    const pc = xf.proj(xf.rot(c));
    const flare = 1 + Math.sin(state.t * 1.6) * 0.06 + state.snort * 0.35;
    const px = xf.proj(xf.rot([c[0] + NOSTRIL.rx * flare, c[1] - 3 * side * 0, c[2] - 3]));
    const py = xf.proj(xf.rot([c[0] + 2 * side, c[1] + NOSTRIL.ry * flare, c[2] - 2]));
    ctx.save();
    ctx.setTransform((px[0] - pc[0]) * state.dpr, (px[1] - pc[1]) * state.dpr, (py[0] - pc[0]) * state.dpr, (py[1] - pc[1]) * state.dpr, pc[0] * state.dpr, pc[1] * state.dpr);
    ctx.rotate(side * 0.35);
    ctx.beginPath(); ctx.ellipse(0, 0, 1, 1, 0, 0, TAU); ctx.fillStyle = '#03120C'; ctx.fill();
    ctx.restore();
    return pc;
  }

  function frame(now) {
    state.raf = requestAnimationFrame(frame);
    const dt = Math.min(0.05, (now - state.last) / 1000);
    state.last = now;
    if (!state.visible) return;
    state.t += dt;

    // Idle: wander when the pointer has been still for a while (and on touch screens).
    if (state.t - state.lastMove > 3.5) {
      state.target.x = Math.sin(state.t * 0.45) * 0.6 + Math.sin(state.t * 1.3) * 0.15;
      state.target.y = Math.sin(state.t * 0.33 + 1) * 0.35;
      state.mood = lerp(state.mood, 0.62, dt * 0.5);
    }
    const ease = reduced ? 1 : 1 - Math.pow(0.0009, dt);
    state.gaze.x = lerp(state.gaze.x, state.target.x, Math.min(1, ease * 1.6));
    state.gaze.y = lerp(state.gaze.y, state.target.y, Math.min(1, ease * 1.6));
    const yawT = state.target.x * 0.5;
    const pitchT = -state.target.y * 0.3 + state.snort * 0.18;
    state.head.yaw = lerp(state.head.yaw, yawT, ease);
    state.head.pitch = lerp(state.head.pitch, pitchT, ease);
    state.head.roll = lerp(state.head.roll, (yawT - state.head.yaw) * -0.35, ease);
    state.moodShown = lerp(state.moodShown, state.mood, ease * 0.6);
    state.snort = Math.max(0, state.snort - dt * 1.8);

    // Blink
    state.nextBlink -= dt;
    if (state.nextBlink <= 0) { state.blinkT = 0; state.nextBlink = 2.5 + Math.random() * 3.5; }
    if (state.blinkT !== undefined) {
      state.blinkT += dt;
      const p = state.blinkT / 0.18;
      state.blink = p < 1 ? p : p < 2 ? 2 - p : 0;
      if (p >= 2) state.blinkT = undefined;
    }

    render();
    for (const fn of listeners) fn(api.reading());
  }

  function render() {
    const { w, h, dpr } = state;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    const xf = makeXf();
    // Key light follows the cursor a little: the bull is lit by "the market".
    let light = [-0.45 + state.gaze.x * 0.35, -0.65 + state.gaze.y * 0.25, 0.75];
    const ll = Math.hypot(...light); light = light.map((v) => v / ll);
    const rimDir = [0.85, -0.25, -0.35];

    // Aura
    const g = ctx.createRadialGradient(state.cx, state.cy - 30 * state.scale, 10, state.cx, state.cy - 30 * state.scale, 260 * state.scale);
    g.addColorStop(0, 'rgba(43,214,159,0.30)'); g.addColorStop(0.55, 'rgba(43,214,159,0.07)'); g.addColorStop(1, 'rgba(43,214,159,0)');
    ctx.fillStyle = g; ctx.fillRect(0, 0, w, h);

    // Horns and ears sit behind the head.
    const items = [];
    const V = model.verts;
    for (const f of model.ears) items.push(f);
    for (const f of model.faces) items.push(f);
    const projected = {};
    const P = (n) => projected[n] || (projected[n] = xf.proj(xf.rot(V[n])));

    // Neck behind everything
    {
      const pts = NECK.map((p) => xf.proj(xf.rot(p)));
      const top = Math.min(...pts.map((p) => p[1])), bot = Math.max(...pts.map((p) => p[1]));
      const ng = ctx.createLinearGradient(0, top, 0, bot);
      ng.addColorStop(0, 'rgb(14,58,44)'); ng.addColorStop(0.55, 'rgba(10,42,31,0.85)'); ng.addColorStop(1, 'rgba(0,0,0,0)');
      ctx.beginPath(); ctx.moveTo(pts[0][0], pts[0][1]);
      for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
      ctx.closePath(); ctx.fillStyle = ng; ctx.fill();
      ctx.strokeStyle = 'rgba(43,214,159,0.10)'; ctx.lineWidth = 1; ctx.stroke();
    }
    // Back layer: far horn first, then ears, near horn.
    const nearSide = state.head.yaw > 0 ? 1 : -1;
    drawHorn(xf, -nearSide, light, rimDir);
    const earDraw = model.ears.map((f) => {
      const rn = xf.rotN(f.n); const pts = f.v.map(P);
      return { f, rn, pts, z: (pts[0][2] + pts[1][2] + pts[2][2]) / 3 };
    }).sort((a, b) => a.z - b.z);
    for (const e of earDraw) {
      const col = shade(MATERIALS.ear, e.rn, light, rimDir);
      drawPoly(e.pts, col, col, 0.8);
    }
    for (const side of [-1, 1]) {
      const pts = EAR_INNER.map(([x, y, z]) => xf.proj(xf.rot([x * side, y, z])));
      drawPoly(pts, 'rgba(6,30,22,0.85)', 'rgba(6,30,22,0.85)', 0.5);
    }
    drawHorn(xf, nearSide, light, rimDir);

    // Head faces: cull back faces, sort far → near, flat-shade.
    const draw = [];
    for (const f of model.faces) {
      const rn = xf.rotN(f.n);
      if (rn[2] < -0.08) continue;
      const pts = f.v.map(P);
      draw.push({ f, rn, pts, z: (pts[0][2] + pts[1][2] + pts[2][2]) / 3 });
    }
    draw.sort((a, b) => a.z - b.z);
    let lidColor = '#1B6E54';
    for (const d of draw) {
      const col = shade(MATERIALS[d.f.m], d.rn, light, rimDir);
      drawPoly(d.pts, col, col, 0.9);
      if (d.f.m === 'brow') lidColor = col;
    }
    // Digital wireframe sheen over the mesh
    ctx.lineWidth = 0.7; ctx.strokeStyle = 'rgba(43,214,159,0.13)';
    for (const d of draw) {
      ctx.beginPath(); ctx.moveTo(d.pts[0][0], d.pts[0][1]); ctx.lineTo(d.pts[1][0], d.pts[1][1]); ctx.lineTo(d.pts[2][0], d.pts[2][1]); ctx.closePath(); ctx.stroke();
    }

    // Face details
    for (const side of [-1, 1]) drawEye(xf, side, lidColor);
    for (const side of [-1, 1]) drawBrow(xf, side);
    const nos = [-1, 1].map((s) => drawNostril(xf, s));

    // Snort puffs
    for (const p of state.puffs) {
      const o = nos[p.side < 0 ? 0 : 1];
      p.x += p.vx * dt * 60; p.y += p.vy * dt * 60; p.life -= dt * 1.4; p.r += dt * 18;
      if (p.life <= 0) continue;
      ctx.beginPath(); ctx.arc(o[0] + p.x * state.scale * 2, o[1] + p.y * state.scale * 2, p.r * state.scale, 0, TAU);
      ctx.fillStyle = `rgba(214,245,233,${0.22 * p.life})`; ctx.fill();
    }
    state.puffs = state.puffs.filter((p) => p.life > 0);
  }

  const api = {
    // What the HUD shows: gaze angle and market mood.
    reading() {
      const deg = Math.round((state.head.yaw * 180) / Math.PI);
      const tilt = Math.round((state.head.pitch * 180) / Math.PI);
      return { yawDeg: deg, pitchDeg: tilt, mood: state.moodShown };
    },
    onReading(fn) { listeners.add(fn); return () => listeners.delete(fn); },
    setTarget(x, y, mood) { state.target.x = x; state.target.y = y; if (mood !== undefined) state.mood = mood; state.lastMove = state.t + 1e6; },
    snap() { state.gaze = { ...state.target }; state.head.yaw = state.target.x * 0.5; state.head.pitch = -state.target.y * 0.3; state.head.roll = 0; state.moodShown = state.mood; render(); },
    snort,
    destroy() {
      cancelAnimationFrame(state.raf); ro.disconnect(); io.disconnect();
      window.removeEventListener('pointermove', onPointer); window.removeEventListener('pointerdown', onClick);
    },
  };

  const ro = new ResizeObserver(resize); ro.observe(canvas);
  const io = new IntersectionObserver(([e]) => { state.visible = e.isIntersecting; }, { threshold: 0 });
  io.observe(canvas);
  window.addEventListener('pointermove', onPointer, { passive: true });
  window.addEventListener('pointerdown', onClick, { passive: true });
  resize();
  state.raf = requestAnimationFrame(frame);
  return api;
}

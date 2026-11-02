// Motion graphics used around the site: live-looking candlesticks (Trade),
// an abstract code editor (Build) and a partnership network (Scale).
// Each pauses off-screen and draws a single still frame for reduced-motion users.

const reduced = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;

export function animateCanvas(canvas, setup) {
  const ctx = canvas.getContext('2d');
  const st = { w: 0, h: 0, dpr: 1, t: 0, visible: true, mx: -1, my: -1 };
  let draw = null;
  const resize = () => {
    const r = canvas.getBoundingClientRect();
    st.dpr = Math.min(devicePixelRatio || 1, 2);
    st.w = Math.max(1, r.width); st.h = Math.max(1, r.height);
    canvas.width = Math.round(st.w * st.dpr); canvas.height = Math.round(st.h * st.dpr);
    draw = setup(ctx, st);
    if (reduced()) { ctx.setTransform(st.dpr, 0, 0, st.dpr, 0, 0); draw(0); }
  };
  const ro = new ResizeObserver(resize); ro.observe(canvas);
  const io = new IntersectionObserver(([e]) => { st.visible = e.isIntersecting; }); io.observe(canvas);
  canvas.addEventListener('pointermove', (e) => { const r = canvas.getBoundingClientRect(); st.mx = e.clientX - r.left; st.my = e.clientY - r.top; });
  canvas.addEventListener('pointerleave', () => { st.mx = -1; st.my = -1; });
  let last = performance.now(); let raf;
  const loop = (now) => {
    raf = requestAnimationFrame(loop);
    const dt = Math.min(0.05, (now - last) / 1000); last = now;
    if (!st.visible || !draw || reduced()) return;
    st.t += dt;
    ctx.setTransform(st.dpr, 0, 0, st.dpr, 0, 0);
    draw(dt);
  };
  resize();
  raf = requestAnimationFrame(loop);
  return { stop() { cancelAnimationFrame(raf); ro.disconnect(); io.disconnect(); } };
}

// Seeded random so covers look the same on every visit
export function rng(seed) {
  let h = 2166136261;
  for (const ch of String(seed)) h = Math.imul(h ^ ch.charCodeAt(0), 16777619);
  return () => { h += 0x6D2B79F5; let t = h; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
}

// ---- Candles: a random walk that keeps printing new bars ----
export function candles(canvas, { seed = 'mkb', speed = 1, bars = 42, grid = true, line = true, volume = true } = {}) {
  return animateCanvas(canvas, (ctx, st) => {
    const r = rng(seed);
    const data = [];
    let price = 100;
    const make = () => {
      const drift = (r() - 0.46) * 4.2;
      const o = price; const c = price + drift;
      const hi = Math.max(o, c) + r() * 2.2; const lo = Math.min(o, c) - r() * 2.2;
      price = c;
      return { o, c, h: hi, l: lo, v: 0.3 + r() * 0.7 };
    };
    for (let i = 0; i < bars + 2; i++) data.push(make());
    let offset = 0; let live = data[data.length - 1];
    return (dt) => {
      const { w, h } = st;
      ctx.clearRect(0, 0, w, h);
      offset += dt * speed * 0.9;
      // the last candle "ticks" before it closes
      live.c += (r() - 0.5) * 0.9 * speed; live.h = Math.max(live.h, live.c); live.l = Math.min(live.l, live.c);
      if (offset >= 1) { offset -= 1; data.shift(); live = make(); data.push(live); }
      let mn = Infinity, mx = -Infinity;
      for (const d of data) { mn = Math.min(mn, d.l); mx = Math.max(mx, d.h); }
      const pad = (mx - mn) * 0.15 + 0.001; mn -= pad; mx += pad;
      const bw = w / bars; const y = (v) => h * 0.86 - ((v - mn) / (mx - mn)) * h * 0.78;
      if (grid) {
        ctx.strokeStyle = 'rgba(43,214,159,0.06)'; ctx.lineWidth = 1;
        for (let gy = 0; gy < 6; gy++) { const yy = (h / 6) * gy + 0.5; ctx.beginPath(); ctx.moveTo(0, yy); ctx.lineTo(w, yy); ctx.stroke(); }
      }
      const pts = [];
      data.forEach((d, i) => {
        const x = (i - offset) * bw + bw / 2;
        const up = d.c >= d.o;
        if (volume) { ctx.fillStyle = up ? 'rgba(43,214,159,0.18)' : 'rgba(43,214,159,0.06)'; const vh = d.v * h * 0.12; ctx.fillRect(x - bw * 0.3, h - vh, bw * 0.6, vh); }
        ctx.strokeStyle = up ? '#2BD69F' : 'rgba(127,165,148,0.7)';
        ctx.lineWidth = 1.2;
        ctx.beginPath(); ctx.moveTo(x, y(d.h)); ctx.lineTo(x, y(d.l)); ctx.stroke();
        const top = y(Math.max(d.o, d.c)); const bh = Math.max(1.5, Math.abs(y(d.o) - y(d.c)));
        if (up) { ctx.fillStyle = '#2BD69F'; ctx.fillRect(x - bw * 0.32, top, bw * 0.64, bh); } else { ctx.fillStyle = '#000000'; ctx.fillRect(x - bw * 0.32, top, bw * 0.64, bh); ctx.strokeRect(x - bw * 0.32, top, bw * 0.64, bh); }
        pts.push([x, y(d.c)]);
      });
      if (line) {
        ctx.beginPath(); pts.forEach(([x, yy], i) => (i ? ctx.lineTo(x, yy) : ctx.moveTo(x, yy)));
        ctx.strokeStyle = 'rgba(43,214,159,0.55)'; ctx.lineWidth = 1.4; ctx.stroke();
        const [lx, ly] = pts[pts.length - 1];
        ctx.setLineDash([3, 4]); ctx.beginPath(); ctx.moveTo(0, ly); ctx.lineTo(w, ly); ctx.strokeStyle = 'rgba(43,214,159,0.35)'; ctx.stroke(); ctx.setLineDash([]);
        ctx.beginPath(); ctx.arc(lx, ly, 3.5, 0, Math.PI * 2); ctx.fillStyle = '#8EF5D2'; ctx.fill();
        ctx.beginPath(); ctx.arc(lx, ly, 3.5 + (st.t * 12) % 12, 0, Math.PI * 2); ctx.strokeStyle = `rgba(43,214,159,${1 - ((st.t * 12) % 12) / 12})`; ctx.stroke();
      }
    };
  });
}

// ---- Code: an abstract editor that keeps typing ----
export function code(canvas, { seed = 'build' } = {}) {
  return animateCanvas(canvas, (ctx, st) => {
    const r = rng(seed);
    const colors = ['#2BD69F', '#8EF5D2', '#7FA594', '#D9FBEA', '#1F7A5C'];
    const lineH = 22;
    const makeLine = () => {
      const indent = Math.floor(r() * 4);
      const tokens = []; let n = 1 + Math.floor(r() * 4);
      while (n--) tokens.push({ w: 18 + r() * 70, c: colors[Math.floor(r() * colors.length)] });
      return { indent, tokens, typed: 0 };
    };
    const lines = []; for (let i = 0; i < 40; i++) lines.push(makeLine());
    let cur = 0; let scroll = 0;
    return (dt) => {
      const { w, h } = st;
      ctx.clearRect(0, 0, w, h);
      const visible = Math.ceil(h / lineH) + 1;
      const L = lines[cur]; const total = L.tokens.reduce((a, t) => a + t.w + 8, 0);
      L.typed += dt * 260;
      if (L.typed >= total) { cur++; if (cur >= lines.length) lines.push(makeLine()); }
      const target = Math.max(0, (cur - visible + 3) * lineH);
      scroll += (target - scroll) * Math.min(1, dt * 6);
      const start = Math.max(0, Math.floor(scroll / lineH) - 1);
      for (let i = start; i <= Math.min(cur, start + visible); i++) {
        const ln = lines[i]; const yy = 50 + i * lineH - scroll;
        ctx.fillStyle = 'rgba(127,165,148,0.35)'; ctx.font = '500 10px JetBrains Mono, monospace'; ctx.fillText(String(i + 1).padStart(2, '0'), 12, yy + 4);
        let x = 44 + ln.indent * 18; let left = i < cur ? Infinity : ln.typed;
        for (const t of ln.tokens) {
          const tw = Math.min(t.w, Math.max(0, left)); left -= t.w + 8;
          if (tw <= 0) break;
          ctx.fillStyle = t.c; ctx.globalAlpha = 0.85; roundRect(ctx, x, yy - 4, tw, 8, 4); ctx.fill(); ctx.globalAlpha = 1;
          x += t.w + 8;
        }
        if (i === cur && Math.floor(st.t * 2) % 2 === 0) { ctx.fillStyle = '#8EF5D2'; ctx.fillRect(Math.min(x, w - 10), yy - 7, 2, 14); }
      }
      const g = ctx.createLinearGradient(0, 0, 0, h); g.addColorStop(0, 'rgba(0,0,0,0.9)'); g.addColorStop(0.15, 'rgba(0,0,0,0)'); g.addColorStop(0.85, 'rgba(0,0,0,0)'); g.addColorStop(1, 'rgba(0,0,0,0.9)');
      ctx.fillStyle = g; ctx.fillRect(0, 0, w, h);
    };
  });
}

// ---- Network: nodes that connect, with value travelling between partners ----
export function network(canvas, { seed = 'scale', count = 26 } = {}) {
  return animateCanvas(canvas, (ctx, st) => {
    const r = rng(seed);
    const nodes = []; for (let i = 0; i < count; i++) nodes.push({ x: r() * st.w, y: r() * st.h, vx: (r() - 0.5) * 14, vy: (r() - 0.5) * 14, s: 1.5 + r() * 3, hub: r() < 0.18 });
    const pulses = [];
    return (dt) => {
      const { w, h } = st;
      ctx.clearRect(0, 0, w, h);
      const maxD = Math.min(180, Math.max(110, w / 4));
      for (const n of nodes) {
        n.x += n.vx * dt; n.y += n.vy * dt;
        if (n.x < 0 || n.x > w) n.vx *= -1; if (n.y < 0 || n.y > h) n.vy *= -1;
        if (st.mx >= 0) { const dx = st.mx - n.x, dy = st.my - n.y, d = Math.hypot(dx, dy); if (d < 160) { n.x += (dx / d) * dt * 30; n.y += (dy / d) * dt * 30; } }
      }
      const edges = [];
      for (let i = 0; i < nodes.length; i++) for (let j = i + 1; j < nodes.length; j++) {
        const a = nodes[i], b = nodes[j]; const d = Math.hypot(a.x - b.x, a.y - b.y);
        if (d < maxD) { edges.push([a, b]); ctx.strokeStyle = `rgba(43,214,159,${0.35 * (1 - d / maxD)})`; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke(); }
      }
      if (edges.length && r() < dt * 3) { const e = edges[Math.floor(r() * edges.length)]; pulses.push({ a: e[0], b: e[1], p: 0 }); }
      for (const p of pulses) {
        p.p += dt * 0.9; const x = p.a.x + (p.b.x - p.a.x) * p.p, y = p.a.y + (p.b.y - p.a.y) * p.p;
        ctx.beginPath(); ctx.arc(x, y, 2.6, 0, Math.PI * 2); ctx.fillStyle = '#8EF5D2'; ctx.shadowColor = '#8EF5D2'; ctx.shadowBlur = 10; ctx.fill(); ctx.shadowBlur = 0;
      }
      for (let i = pulses.length - 1; i >= 0; i--) if (pulses[i].p >= 1) pulses.splice(i, 1);
      if (st.mx >= 0) { for (const n of nodes) { const d = Math.hypot(st.mx - n.x, st.my - n.y); if (d < 160) { ctx.strokeStyle = `rgba(43,214,159,${0.5 * (1 - d / 160)})`; ctx.beginPath(); ctx.moveTo(st.mx, st.my); ctx.lineTo(n.x, n.y); ctx.stroke(); } } }
      for (const n of nodes) {
        ctx.beginPath(); ctx.arc(n.x, n.y, n.hub ? n.s + 3 : n.s, 0, Math.PI * 2);
        ctx.fillStyle = n.hub ? '#2BD69F' : 'rgba(217,251,234,0.75)'; ctx.fill();
        if (n.hub) { ctx.beginPath(); ctx.arc(n.x, n.y, n.s + 8 + Math.sin(st.t * 2 + n.x) * 2, 0, Math.PI * 2); ctx.strokeStyle = 'rgba(43,214,159,0.35)'; ctx.stroke(); }
      }
    };
  });
}

// Pick the motion that fits a topic
export function artFor(canvas, topic, seed) {
  const t = String(topic || '').toLowerCase();
  if (/trad|forex|crypto|binary|market/.test(t)) return candles(canvas, { seed, bars: 26, speed: 0.8, volume: false });
  if (/business|grow|partner|brand|scale/.test(t)) return network(canvas, { seed, count: 16 });
  return code(canvas, { seed });
}

export function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath(); ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r); ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath();
}

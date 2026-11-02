#!/usr/bin/env node
// MKB website server. Zero dependencies (Node 18+).
//
//  - Serves the website in ../public
//  - Public API:  GET /api/content · POST /api/submissions · GET /api/track
//  - Admin API:   /api/admin/* (password login, signed tokens) to edit content,
//                 read proposals/orders, upload images and change the password
//  - Data lives in MKB_DATA_DIR (default ./data): content.json, submissions.json, auth.json, uploads/
//
// Run:  MKB_ADMIN_PASSWORD='a-long-password' node server/server.js

'use strict';
const http = require('http');
const fs = require('fs');
const fsp = fs.promises;
const path = require('path');
const crypto = require('crypto');

const ROOT = path.resolve(__dirname, '..');
const CFG = {
  port: Number(process.env.PORT || 8080),
  host: process.env.HOST || '0.0.0.0',
  publicDir: path.resolve(process.env.MKB_PUBLIC_DIR || path.join(ROOT, 'public')),
  dataDir: path.resolve(process.env.MKB_DATA_DIR || path.join(ROOT, 'data')),
  adminPassword: process.env.MKB_ADMIN_PASSWORD || '',
  trustProxy: process.env.MKB_TRUST_PROXY === '1' || !!process.env.RENDER || !!process.env.FLY_APP_NAME,
  webhook: process.env.MKB_NOTIFY_WEBHOOK || '',
  telegramToken: process.env.MKB_TELEGRAM_BOT_TOKEN || '',
  telegramChat: process.env.MKB_TELEGRAM_CHAT_ID || '',
  tokenHours: Number(process.env.MKB_SESSION_HOURS || 12),
};

const TYPES = { question: 'Question', partnership: 'Partnership proposal', idea: 'Business idea', hire: 'Project enquiry', order: 'Order', waitlist: 'Course waitlist' };
const STATUSES = ['new', 'reviewing', 'accepted', 'declined', 'done'];

// ---------------------------------------------------------------- storage
const file = (n) => path.join(CFG.dataDir, n);
const UPLOADS = () => file('uploads');
let writeQueue = Promise.resolve();
function writeJSON(name, data) {
  // Serialised, atomic writes: write a temp file, then rename over the old one.
  // A failed write must not block the ones queued after it.
  const job = writeQueue.catch(() => {}).then(async () => {
    const target = file(name);
    const tmp = `${target}.${process.pid}.${crypto.randomBytes(4).toString('hex')}.tmp`;
    await fsp.writeFile(tmp, JSON.stringify(data, null, 2));
    await fsp.rename(tmp, target);
  });
  writeQueue = job;
  return job;
}
async function readJSON(name, fallback) {
  try { return JSON.parse(await fsp.readFile(file(name), 'utf8')); } catch (e) { if (e.code === 'ENOENT') return fallback; throw e; }
}

const state = { content: null, submissions: [], auth: null };

async function init() {
  await fsp.mkdir(UPLOADS(), { recursive: true });
  await fsp.mkdir(file('backups'), { recursive: true });
  state.content = await readJSON('content.json', null);
  if (!state.content) {
    const seed = JSON.parse(await fsp.readFile(path.join(CFG.publicDir, 'content', 'content.json'), 'utf8'));
    state.content = seed;
    await writeJSON('content.json', seed);
  }
  state.submissions = await readJSON('submissions.json', []);
  state.auth = await readJSON('auth.json', null);
  if (!state.auth) { state.auth = { secret: crypto.randomBytes(32).toString('hex'), version: 1, hash: null, salt: null }; await writeJSON('auth.json', state.auth); }
}

// ---------------------------------------------------------------- auth
function hashPassword(pw, salt) { return crypto.scryptSync(pw, salt, 64).toString('hex'); }
function checkPassword(pw) {
  if (typeof pw !== 'string' || !pw) return false;
  if (state.auth.hash && state.auth.salt) {
    const a = Buffer.from(hashPassword(pw, state.auth.salt), 'hex'); const b = Buffer.from(state.auth.hash, 'hex');
    return a.length === b.length && crypto.timingSafeEqual(a, b);
  }
  if (!CFG.adminPassword) return false;
  const a = crypto.createHash('sha256').update(pw).digest(); const b = crypto.createHash('sha256').update(CFG.adminPassword).digest();
  return crypto.timingSafeEqual(a, b);
}
const adminEnabled = () => !!(state.auth.hash || CFG.adminPassword);
const b64u = (buf) => Buffer.from(buf).toString('base64url');
function sign(payload) {
  const body = b64u(JSON.stringify(payload));
  const mac = crypto.createHmac('sha256', state.auth.secret).update(body).digest('base64url');
  return `${body}.${mac}`;
}
function verify(token) {
  if (typeof token !== 'string' || !token.includes('.')) return null;
  const [body, mac] = token.split('.');
  const want = crypto.createHmac('sha256', state.auth.secret).update(body).digest('base64url');
  const a = Buffer.from(mac || ''); const b = Buffer.from(want);
  if (a.length !== b.length || !crypto.timingSafeEqual(a, b)) return null;
  try {
    const p = JSON.parse(Buffer.from(body, 'base64url').toString());
    if (p.exp < Date.now() || p.v !== state.auth.version) return null;
    return p;
  } catch { return null; }
}
function isAdmin(req) {
  const h = req.headers.authorization || '';
  return !!verify(h.startsWith('Bearer ') ? h.slice(7) : '');
}

// ---------------------------------------------------------------- rate limits
const buckets = new Map();
function limited(key, max, windowMs) {
  const now = Date.now();
  let b = buckets.get(key);
  if (!b || now - b.start > windowMs) { b = { start: now, n: 0 }; buckets.set(key, b); }
  b.n += 1;
  return b.n > max;
}
setInterval(() => { const now = Date.now(); for (const [k, b] of buckets) if (now - b.start > 3600_000) buckets.delete(k); }, 600_000).unref();
function clientIp(req) {
  if (CFG.trustProxy) { const xf = String(req.headers['x-forwarded-for'] || '').split(',')[0].trim(); if (xf) return xf; }
  return req.socket.remoteAddress || 'unknown';
}

// ---------------------------------------------------------------- http helpers
const SECURITY_HEADERS = {
  'X-Content-Type-Options': 'nosniff',
  'Referrer-Policy': 'strict-origin-when-cross-origin',
  'X-Frame-Options': 'SAMEORIGIN',
  'Permissions-Policy': 'camera=(), microphone=(), geolocation=()',
  'Content-Security-Policy': [
    "default-src 'self'",
    "script-src 'self' https://s3.tradingview.com https://js.paystack.co",
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src 'self' https://fonts.gstatic.com data:",
    "img-src 'self' data: blob: https:",
    "connect-src 'self' https:",
    'frame-src https:',
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'self'",
  ].join('; '),
};
function send(res, status, body, headers = {}) {
  res.writeHead(status, { ...SECURITY_HEADERS, ...headers });
  res.end(body);
}
function json(res, status, data) {
  send(res, status, JSON.stringify(data), { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' });
}
function readBody(req, limit) {
  return new Promise((resolve, reject) => {
    let size = 0; const chunks = [];
    req.on('data', (c) => { size += c.length; if (size > limit) { reject(Object.assign(new Error('Request too large'), { status: 413 })); req.destroy(); } else chunks.push(c); });
    req.on('end', () => resolve(Buffer.concat(chunks)));
    req.on('error', reject);
  });
}
async function readJSONBody(req, limit = 64 * 1024) {
  const ct = String(req.headers['content-type'] || '');
  if (!ct.includes('application/json')) throw Object.assign(new Error('Expected JSON'), { status: 415 });
  const buf = await readBody(req, limit);
  try { return JSON.parse(buf.toString('utf8') || '{}'); } catch { throw Object.assign(new Error('Invalid JSON'), { status: 400 }); }
}

// ---------------------------------------------------------------- validation
const str = (v, max = 500) => (typeof v === 'string' ? v.trim().slice(0, max) : typeof v === 'number' ? String(v) : '');
function cleanFields(obj) {
  const out = {};
  if (!obj || typeof obj !== 'object' || Array.isArray(obj)) return out;
  for (const [k, v] of Object.entries(obj).slice(0, 30)) {
    const key = str(k, 40).replace(/[^\w-]/g, '');
    if (!key) continue;
    if (Array.isArray(v)) out[key] = v.slice(0, 20).map((x) => str(x, 200)).filter(Boolean);
    else out[key] = str(v, 3000);
  }
  return out;
}
function newRef() {
  const a = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
  for (;;) {
    const bytes = crypto.randomBytes(6);
    let r = 'MKB-'; for (const b of bytes) r += a[b % a.length];
    if (!state.submissions.some((s) => s.ref === r)) return r;
  }
}
function validContent(c) {
  return c && typeof c === 'object' && !Array.isArray(c) && c.settings && typeof c.settings === 'object' && c.settings.contact &&
    ['skills', 'services', 'projects', 'packages', 'courses', 'qa'].every((k) => Array.isArray(c[k])) && c.trading && typeof c.trading === 'object' && c.about && typeof c.about === 'object';
}

// ---------------------------------------------------------------- notifications
async function notify(sub) {
  const lines = [`New ${TYPES[sub.type] || sub.type} — ${sub.ref}`, `From: ${sub.name || '—'} ${sub.phone ? '· ' + sub.phone : ''} ${sub.email ? '· ' + sub.email : ''}`.trim()];
  if (sub.title) lines.push(`Re: ${sub.title}`);
  if (sub.message) lines.push('', sub.message.slice(0, 1500));
  const text = lines.join('\n');
  const tasks = [];
  if (CFG.webhook) tasks.push(fetch(CFG.webhook, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text, content: text.slice(0, 1900), submission: sub }) }));
  if (CFG.telegramToken && CFG.telegramChat) tasks.push(fetch(`https://api.telegram.org/bot${CFG.telegramToken}/sendMessage`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ chat_id: CFG.telegramChat, text }) }));
  const results = await Promise.allSettled(tasks.map((t) => Promise.race([t, new Promise((_, rej) => setTimeout(() => rej(new Error('timeout')), 8000))])));
  for (const r of results) if (r.status === 'rejected') console.warn('[notify] failed:', r.reason && r.reason.message);
}

// ---------------------------------------------------------------- API
async function api(req, res, url) {
  const p = url.pathname;
  const m = req.method;
  const ip = clientIp(req);

  if (p === '/api/health') return json(res, 200, { ok: true, admin: adminEnabled() });
  if (p === '/api/content' && m === 'GET') return json(res, 200, state.content);

  if (p === '/api/submissions' && m === 'POST') {
    if (limited('sub:' + ip, 8, 10 * 60_000)) return json(res, 429, { error: 'Too many messages — please wait a few minutes or DM me directly.' });
    const b = await readJSONBody(req);
    if (b.website) return json(res, 200, { ok: true, ref: newRef() }); // honeypot: quietly drop bots
    const type = TYPES[b.type] ? b.type : null;
    if (!type) return json(res, 400, { error: 'Unknown submission type' });
    const name = str(b.name, 80);
    if (!name) return json(res, 400, { error: 'Please include your name.' });
    const email = str(b.email, 120);
    if (email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) return json(res, 400, { error: 'That email address looks wrong.' });
    const now = new Date().toISOString();
    const item = b.item && typeof b.item === 'object' ? { kind: str(b.item.kind, 20), id: str(b.item.id, 60), name: str(b.item.name, 160), price: Number(b.item.price) || 0, unit: str(b.item.unit, 20) } : null;
    const sub = {
      id: crypto.randomUUID(), ref: newRef(), type, status: 'new', name, phone: str(b.phone, 30), email, channel: str(b.channel, 20),
      title: str(b.title, 160), message: str(b.message, 6000), fields: cleanFields(b.fields), item, page: str(b.page, 120),
      notes: '', publicNote: '', createdAt: now, updatedAt: now,
    };
    state.submissions.unshift(sub);
    if (state.submissions.length > 5000) state.submissions.length = 5000;
    await writeJSON('submissions.json', state.submissions);
    notify(sub).catch(() => {});
    return json(res, 201, { ok: true, ref: sub.ref });
  }

  if (p === '/api/track' && m === 'GET') {
    if (limited('track:' + ip, 40, 10 * 60_000)) return json(res, 429, { error: 'Too many lookups' });
    const ref = str(url.searchParams.get('ref'), 12).toUpperCase();
    const s = state.submissions.find((x) => x.ref === ref);
    if (!s) return json(res, 404, { error: 'No submission with that reference' });
    return json(res, 200, { ref: s.ref, status: s.status, type: s.type, typeLabel: TYPES[s.type], createdAt: s.createdAt, updatedAt: s.updatedAt, publicNote: s.publicNote || '' });
  }

  // ---- admin
  if (p === '/api/admin/login' && m === 'POST') {
    if (!adminEnabled()) return json(res, 503, { error: 'Admin is disabled. Set MKB_ADMIN_PASSWORD on the server and restart.' });
    if (limited('login:' + ip, 8, 15 * 60_000)) return json(res, 429, { error: 'Too many attempts. Try again in 15 minutes.' });
    const b = await readJSONBody(req, 4096);
    if (!checkPassword(b.password)) { await new Promise((r) => setTimeout(r, 400)); return json(res, 401, { error: 'Wrong password' }); }
    const exp = Date.now() + CFG.tokenHours * 3600_000;
    return json(res, 200, { token: sign({ exp, v: state.auth.version, n: crypto.randomBytes(6).toString('hex') }), expiresAt: exp });
  }
  if (p.startsWith('/api/admin/')) {
    if (!isAdmin(req)) return json(res, 401, { error: 'Please sign in again' });
    if (p === '/api/admin/me') return json(res, 200, { ok: true, usingEnvPassword: !state.auth.hash });

    if (p === '/api/admin/content' && m === 'GET') return json(res, 200, state.content);
    if (p === '/api/admin/content' && m === 'PUT') {
      const b = await readJSONBody(req, 4 * 1024 * 1024);
      if (!validContent(b)) return json(res, 400, { error: 'Content is missing required sections' });
      const base = req.headers['if-match'];
      if (base && state.content.updatedAt && base !== state.content.updatedAt) return json(res, 409, { error: 'The content was changed in another tab. Reload to get the latest version.' });
      // keep a rolling backup of the previous version
      const stamp = new Date().toISOString().replace(/[:.]/g, '-');
      await writeJSON(`backups/content-${stamp}.json`, state.content);
      pruneBackups().catch(() => {});
      b.updatedAt = new Date().toISOString();
      state.content = b;
      await writeJSON('content.json', b);
      return json(res, 200, { ok: true, updatedAt: b.updatedAt });
    }

    if (p === '/api/admin/submissions' && m === 'GET') return json(res, 200, state.submissions);
    const sm = p.match(/^\/api\/admin\/submissions\/([\w-]{8,64})$/);
    if (sm) {
      const s = state.submissions.find((x) => x.id === sm[1]);
      if (!s) return json(res, 404, { error: 'Not found' });
      if (m === 'PATCH') {
        const b = await readJSONBody(req);
        if (b.status !== undefined) { if (!STATUSES.includes(b.status)) return json(res, 400, { error: 'Bad status' }); s.status = b.status; }
        if (b.notes !== undefined) s.notes = str(b.notes, 4000);
        if (b.publicNote !== undefined) s.publicNote = str(b.publicNote, 600);
        s.updatedAt = new Date().toISOString();
        await writeJSON('submissions.json', state.submissions);
        return json(res, 200, s);
      }
      if (m === 'DELETE') {
        state.submissions = state.submissions.filter((x) => x !== s);
        await writeJSON('submissions.json', state.submissions);
        return json(res, 200, { ok: true });
      }
    }

    if (p === '/api/admin/upload' && m === 'POST') {
      const b = await readJSONBody(req, 7 * 1024 * 1024);
      const mt = /^data:(image\/(png|jpeg|webp|gif));base64,([A-Za-z0-9+/=]+)$/.exec(String(b.dataUrl || ''));
      if (!mt) return json(res, 400, { error: 'Upload a PNG, JPG, WebP or GIF image' });
      const buf = Buffer.from(mt[3], 'base64');
      if (buf.length > 5 * 1024 * 1024) return json(res, 413, { error: 'Images must be under 5 MB' });
      const sig = buf.subarray(0, 12).toString('hex');
      const ok = { png: sig.startsWith('89504e47'), jpeg: sig.startsWith('ffd8ff'), gif: sig.startsWith('47494638'), webp: sig.startsWith('52494646') && sig.slice(16, 24) === '57454250' }[mt[2]];
      if (!ok) return json(res, 400, { error: 'That file is not a valid image' });
      const name = `${Date.now().toString(36)}-${crypto.randomBytes(5).toString('hex')}.${mt[2] === 'jpeg' ? 'jpg' : mt[2]}`;
      await fsp.writeFile(path.join(UPLOADS(), name), buf);
      return json(res, 201, { url: `uploads/${name}` });
    }

    if (p === '/api/admin/password' && m === 'POST') {
      const b = await readJSONBody(req, 4096);
      if (!checkPassword(b.current)) return json(res, 401, { error: 'Current password is wrong' });
      if (typeof b.next !== 'string' || b.next.length < 10) return json(res, 400, { error: 'Use at least 10 characters' });
      const salt = crypto.randomBytes(16).toString('hex');
      state.auth = { ...state.auth, salt, hash: hashPassword(b.next, salt), version: state.auth.version + 1 };
      await writeJSON('auth.json', state.auth);
      const exp = Date.now() + CFG.tokenHours * 3600_000;
      return json(res, 200, { ok: true, token: sign({ exp, v: state.auth.version, n: crypto.randomBytes(6).toString('hex') }), expiresAt: exp });
    }

    if (p === '/api/admin/export' && m === 'GET') {
      send(res, 200, JSON.stringify({ exportedAt: new Date().toISOString(), content: state.content, submissions: state.submissions }, null, 2), {
        'Content-Type': 'application/json; charset=utf-8', 'Content-Disposition': `attachment; filename="mkb-backup-${new Date().toISOString().slice(0, 10)}.json"`, 'Cache-Control': 'no-store',
      });
      return;
    }
  }
  return json(res, 404, { error: 'Not found' });
}

async function pruneBackups() {
  const dir = file('backups');
  const names = (await fsp.readdir(dir)).filter((n) => n.startsWith('content-')).sort();
  for (const n of names.slice(0, Math.max(0, names.length - 20))) await fsp.unlink(path.join(dir, n));
}

// ---------------------------------------------------------------- static files
const MIME = {
  '.html': 'text/html; charset=utf-8', '.css': 'text/css; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.mjs': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8', '.svg': 'image/svg+xml', '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp',
  '.gif': 'image/gif', '.ico': 'image/x-icon', '.txt': 'text/plain; charset=utf-8', '.xml': 'application/xml; charset=utf-8', '.webmanifest': 'application/manifest+json',
  '.woff2': 'font/woff2', '.csv': 'text/csv; charset=utf-8', '.pdf': 'application/pdf',
};
function safeJoin(base, rel) {
  let decoded;
  try { decoded = decodeURIComponent(rel); } catch { return null; }
  if (decoded.includes('\0')) return null;
  const full = path.resolve(base, '.' + path.posix.normalize('/' + decoded));
  return full === base || full.startsWith(base + path.sep) ? full : null;
}
async function serveFile(req, res, full, status = 200) {
  const st = await fsp.stat(full);
  const ext = path.extname(full).toLowerCase();
  const type = MIME[ext] || 'application/octet-stream';
  const etag = `W/"${st.size.toString(16)}-${Math.floor(st.mtimeMs).toString(16)}"`;
  const cache = ext === '.html' || ext === '.json' ? 'no-cache' : full.includes(`${path.sep}uploads${path.sep}`) ? 'public, max-age=31536000, immutable' : 'public, max-age=600';
  if (status === 200 && req.headers['if-none-match'] === etag) return send(res, 304, '', { ETag: etag, 'Cache-Control': cache });
  const headers = { 'Content-Type': type, 'Content-Length': st.size, ETag: etag, 'Cache-Control': cache };
  if (ext === '.svg') headers['Content-Security-Policy'] = "default-src 'none'; style-src 'unsafe-inline'";
  res.writeHead(status, { ...SECURITY_HEADERS, ...headers });
  if (req.method === 'HEAD') return res.end();
  fs.createReadStream(full).on('error', () => res.destroy()).pipe(res);
}
async function staticFiles(req, res, url) {
  if (!['GET', 'HEAD'].includes(req.method)) return send(res, 405, 'Method not allowed', { Allow: 'GET, HEAD' });
  let rel = url.pathname;
  let base = CFG.publicDir;
  if (rel.startsWith('/uploads/')) { base = UPLOADS(); rel = rel.slice('/uploads'.length); }
  let full = safeJoin(base, rel);
  if (!full) return send(res, 400, 'Bad path');
  try {
    let st = await fsp.stat(full).catch(() => null);
    if (st && st.isDirectory()) {
      if (!url.pathname.endsWith('/')) return send(res, 301, '', { Location: url.pathname + '/' + url.search });
      full = path.join(full, 'index.html'); st = await fsp.stat(full).catch(() => null);
    }
    if (!st && !path.extname(full) && base === CFG.publicDir) { // pretty URLs: /about → about.html
      const alt = full + '.html'; if (await fsp.stat(alt).catch(() => null)) { full = alt; st = true; }
    }
    if (!st) return serveFile(req, res, path.join(CFG.publicDir, '404.html'), 404).catch(() => send(res, 404, 'Not found'));
    if (path.basename(full).startsWith('.') || path.basename(full).startsWith('_')) return send(res, 404, 'Not found');
    return await serveFile(req, res, full);
  } catch (e) {
    console.error(e);
    return send(res, 500, 'Server error');
  }
}

// ---------------------------------------------------------------- server
function createServer() {
  return http.createServer(async (req, res) => {
    let url;
    try { url = new URL(req.url, 'http://localhost'); } catch { return send(res, 400, 'Bad request'); }
    try {
      if (url.pathname.startsWith('/api/')) return await api(req, res, url);
      return await staticFiles(req, res, url);
    } catch (e) {
      const status = e.status || 500;
      if (status >= 500) console.error(e);
      if (!res.headersSent) json(res, status, { error: status >= 500 ? 'Server error' : e.message });
    }
  });
}

async function start() {
  await init();
  const server = createServer();
  await new Promise((r) => server.listen(CFG.port, CFG.host, r));
  const addr = server.address();
  console.log(`MKB site on http://${CFG.host === '0.0.0.0' ? 'localhost' : CFG.host}:${addr.port}  (data: ${CFG.dataDir})`);
  if (!adminEnabled()) console.log('Admin is disabled until MKB_ADMIN_PASSWORD is set.');
  return server;
}

if (require.main === module) {
  start().catch((e) => { console.error(e); process.exit(1); });
  for (const sig of ['SIGINT', 'SIGTERM']) process.on(sig, () => { writeQueue.finally(() => process.exit(0)); });
}

module.exports = { start, CFG, state };

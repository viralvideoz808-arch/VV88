// Integration tests for server/server.js: run with `npm test` (Node 18+, no dependencies).
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const dataDir = mkdtempSync(join(tmpdir(), 'mkb-test-'));
process.env.PORT = '0';
process.env.HOST = '127.0.0.1';
process.env.MKB_DATA_DIR = dataDir;
process.env.MKB_ADMIN_PASSWORD = 'correct horse battery';
const { start } = require('../server/server.js');

let server; let base;
before(async () => { server = await start(); base = `http://127.0.0.1:${server.address().port}`; });
after(() => { server.close(); rmSync(dataDir, { recursive: true, force: true }); });

const post = (p, body, headers = {}) => fetch(base + p, { method: 'POST', headers: { 'Content-Type': 'application/json', ...headers }, body: typeof body === 'string' ? body : JSON.stringify(body) });
const auth = (t) => ({ Authorization: 'Bearer ' + t });
let token; let firstRef;

test('health and public content', async () => {
  const h = await (await fetch(base + '/api/health')).json();
  assert.equal(h.ok, true);
  assert.equal(h.admin, true);
  const r = await fetch(base + '/api/content');
  assert.equal(r.status, 200);
  const c = await r.json();
  assert.equal(c.settings.contact.email, 'mkbright888@gmail.com');
  assert.ok(Array.isArray(c.skills) && c.skills.length > 0);
  assert.ok(existsSync(join(dataDir, 'content.json')), 'seeds content into the data dir');
});

test('submissions: validation, honeypot and storage', async () => {
  let r = await post('/api/submissions', { type: 'partnership', name: 'Jane Wanjiru', phone: '0712 000 000', email: 'jane@example.com', title: 'Agri app JV', message: 'Let us partner', fields: { offer: 'Distribution to 200 farmers', needs: ['Tech build', 'Funding'] } });
  assert.equal(r.status, 201);
  const j = await r.json();
  assert.match(j.ref, /^MKB-[A-Z0-9]{6}$/);
  firstRef = j.ref;

  r = await post('/api/submissions', { type: 'spam', name: 'x' });
  assert.equal(r.status, 400);
  r = await post('/api/submissions', { type: 'question', name: '' });
  assert.equal(r.status, 400);
  r = await post('/api/submissions', { type: 'question', name: 'Bot', website: 'http://spam' });
  assert.equal(r.status, 200, 'honeypot looks like success to bots');
  r = await post('/api/submissions', 'name=x', { 'Content-Type': 'application/x-www-form-urlencoded' });
  assert.equal(r.status, 415);
});

test('submissions are rate limited per visitor', async () => {
  let last;
  for (let i = 0; i < 6; i++) last = await post('/api/submissions', { type: 'question', name: 'Repeat ' + i, message: 'hi' });
  assert.equal(last.status, 429);
});

test('track a reference', async () => {
  const r = await fetch(`${base}/api/track?ref=${firstRef}`);
  assert.equal(r.status, 200);
  const j = await r.json();
  assert.equal(j.status, 'new');
  assert.equal(j.typeLabel, 'Partnership proposal');
  assert.equal(j.name, undefined, 'tracking never exposes personal details');
  assert.equal((await fetch(`${base}/api/track?ref=MKB-ZZZZZZ`)).status, 404);
});

test('admin login and auth guard', async () => {
  assert.equal((await fetch(base + '/api/admin/me')).status, 401);
  assert.equal((await post('/api/admin/login', { password: 'nope' })).status, 401);
  const r = await post('/api/admin/login', { password: 'correct horse battery' });
  assert.equal(r.status, 200);
  token = (await r.json()).token;
  assert.equal((await fetch(base + '/api/admin/me', { headers: auth(token) })).status, 200);
  assert.equal((await fetch(base + '/api/admin/me', { headers: auth(token + 'x') })).status, 401, 'tampered token is rejected');
});

test('admin edits content (with conflict detection)', async () => {
  const c = await (await fetch(base + '/api/admin/content', { headers: auth(token) })).json();
  const bad = await fetch(base + '/api/admin/content', { method: 'PUT', headers: { ...auth(token), 'Content-Type': 'application/json' }, body: JSON.stringify({ settings: {} }) });
  assert.equal(bad.status, 400);
  c.skills.push({ id: 'sk-test', name: 'Rust', category: 'Development', level: 'Learning', addedAt: '2026-12-01' });
  const ok = await fetch(base + '/api/admin/content', { method: 'PUT', headers: { ...auth(token), 'Content-Type': 'application/json', 'If-Match': c.updatedAt }, body: JSON.stringify(c) });
  assert.equal(ok.status, 200);
  const pub = await (await fetch(base + '/api/content')).json();
  assert.ok(pub.skills.some((s) => s.name === 'Rust'), 'new skill is live for visitors');
  const stale = await fetch(base + '/api/admin/content', { method: 'PUT', headers: { ...auth(token), 'Content-Type': 'application/json', 'If-Match': c.updatedAt }, body: JSON.stringify(c) });
  assert.equal(stale.status, 409);
});

test('admin manages submissions', async () => {
  const list = await (await fetch(base + '/api/admin/submissions', { headers: auth(token) })).json();
  const s = list.find((x) => x.ref === firstRef);
  assert.ok(s);
  assert.deepEqual(s.fields.needs, ['Tech build', 'Funding']);
  const r = await fetch(`${base}/api/admin/submissions/${s.id}`, { method: 'PATCH', headers: { ...auth(token), 'Content-Type': 'application/json' }, body: JSON.stringify({ status: 'accepted', publicNote: 'Expect a call on Friday.' }) });
  assert.equal(r.status, 200);
  const t = await (await fetch(`${base}/api/track?ref=${firstRef}`)).json();
  assert.equal(t.status, 'accepted');
  assert.equal(t.publicNote, 'Expect a call on Friday.');
  const badStatus = await fetch(`${base}/api/admin/submissions/${s.id}`, { method: 'PATCH', headers: { ...auth(token), 'Content-Type': 'application/json' }, body: JSON.stringify({ status: 'hacked' }) });
  assert.equal(badStatus.status, 400);
});

test('image uploads are checked and served', async () => {
  const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==', 'base64');
  const r = await post('/api/admin/upload', { dataUrl: 'data:image/png;base64,' + png.toString('base64') }, auth(token));
  assert.equal(r.status, 201);
  const { url } = await r.json();
  const img = await fetch(`${base}/${url}`);
  assert.equal(img.status, 200);
  assert.equal(img.headers.get('content-type'), 'image/png');
  const fake = await post('/api/admin/upload', { dataUrl: 'data:image/png;base64,' + Buffer.from('<script>alert(1)</script>').toString('base64') }, auth(token));
  assert.equal(fake.status, 400);
  const svg = await post('/api/admin/upload', { dataUrl: 'data:image/svg+xml;base64,PHN2Zy8+' }, auth(token));
  assert.equal(svg.status, 400, 'SVG uploads are refused');
});

test('static files, pretty URLs, 404 and path traversal', async () => {
  const home = await fetch(base + '/');
  assert.equal(home.status, 200);
  assert.match(home.headers.get('content-type'), /text\/html/);
  assert.ok(home.headers.get('content-security-policy'));
  assert.equal(home.headers.get('x-content-type-options'), 'nosniff');
  assert.equal((await fetch(base + '/about')).status, 200);
  const missing = await fetch(base + '/does-not-exist');
  assert.equal(missing.status, 404);
  assert.match(await missing.text(), /Off the/);
  const admin = await fetch(base + '/admin', { redirect: 'manual' });
  assert.equal(admin.status, 301);
  for (const p of ['/../server/server.js', '/%2e%2e/server/server.js', '/..%2f..%2fserver/server.js', '/uploads/../auth.json', '/uploads/%2e%2e/auth.json']) {
    const r = await fetch(base + p);
    const body = await r.text();
    assert.ok(!body.includes('scryptSync') && !body.includes('"secret"'), `must not leak files via ${p}`);
  }
});

test('changing the password signs out old sessions', async () => {
  const short = await post('/api/admin/password', { current: 'correct horse battery', next: 'short' }, auth(token));
  assert.equal(short.status, 400);
  const r = await post('/api/admin/password', { current: 'correct horse battery', next: 'a much longer new password' }, auth(token));
  assert.equal(r.status, 200);
  const fresh = (await r.json()).token;
  assert.equal((await fetch(base + '/api/admin/me', { headers: auth(token) })).status, 401, 'old token revoked');
  assert.equal((await fetch(base + '/api/admin/me', { headers: auth(fresh) })).status, 200);
  assert.equal((await post('/api/admin/login', { password: 'correct horse battery' })).status, 401, 'env password no longer works');
  assert.equal((await post('/api/admin/login', { password: 'a much longer new password' })).status, 200);
});

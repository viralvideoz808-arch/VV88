// MKB admin dashboard.
// Server mode (node server): password login, edits go live instantly, inbox of proposals/orders.
// Static mode (plain hosting): edits are saved in this browser; export content.json and upload it to publish.
import { url, esc, ICONS, icon, toast, openModal, LS, markdown, $, $$ } from './core.js';

const API = (p) => url('api/' + p);
const app = $('#main');
const TOKEN_KEY = 'mkb.admin.token';
let MODE = 'static';
let ADMIN_ENABLED = false;
let C = null;         // working copy of the content
let SUBS = [];        // submissions (server mode)
let saving = Promise.resolve();

const today = () => new Date().toISOString().slice(0, 10);
const uid = (p) => `${p}-${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
const getPath = (obj, path) => path.split('.').reduce((o, k) => (o ? o[k] : undefined), obj);
const setPath = (obj, path, v) => { const ks = path.split('.'); const last = ks.pop(); const t = ks.reduce((o, k) => (o[k] ??= {}), obj); t[last] = v; };
const token = () => { try { return sessionStorage.getItem(TOKEN_KEY) || ''; } catch { return ''; } };
const setToken = (t) => { try { t ? sessionStorage.setItem(TOKEN_KEY, t) : sessionStorage.removeItem(TOKEN_KEY); } catch { /* ignore */ } };

async function apiFetch(path, opts = {}) {
  const r = await fetch(API(path), { ...opts, headers: { ...(opts.body ? { 'Content-Type': 'application/json' } : {}), Authorization: 'Bearer ' + token(), ...(opts.headers || {}) } });
  if (r.status === 401 && path !== 'admin/login') { setToken(''); renderLogin('Your session ended — please sign in again.'); throw new Error('Signed out'); }
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || 'Request failed (' + r.status + ')');
  return j;
}

// ------------------------------------------------------------------ schemas
const CATS = ['Development', 'Trading', 'Business', 'Design', 'Tools'];
const LEVELS = ['Learning', 'Intermediate', 'Advanced', 'Expert'];
const COLLECTIONS = {
  skills: {
    label: 'Skills', path: 'skills', prefix: 'sk', icon: 'sparkle', group: 'Profile',
    blurb: 'Add every new skill as you grow. New skills appear on the About page with a "New" badge for 60 days and in the homepage marquee.',
    title: (x) => x.name, sub: (x) => `${x.category} · ${x.level}${x.featured ? ' · featured' : ''}`,
    defaults: () => ({ category: 'Development', level: 'Learning', addedAt: today(), featured: false }),
    fields: [
      { k: 'name', label: 'Skill name', type: 'text', required: true, placeholder: 'e.g. Flutter, Smart Money Concepts, Negotiation' },
      { k: 'category', label: 'Category', type: 'select', options: CATS, custom: true },
      { k: 'level', label: 'Level', type: 'select', options: LEVELS },
      { k: 'note', label: 'One-line description', type: 'text', placeholder: 'What you do with it' },
      { k: 'addedAt', label: 'Date added', type: 'date' },
      { k: 'featured', label: 'Featured', type: 'bool' },
    ],
  },
  services: {
    label: 'IT services', path: 'services', prefix: 'sv', icon: 'code', group: 'Build',
    title: (x) => x.title, sub: (x) => `From KES ${Number(x.startingPrice || 0).toLocaleString()}${x.priceUnit || ''} · ${x.timeline || ''}`,
    defaults: () => ({ icon: 'globe', deliverables: [], stack: [], featured: false }),
    fields: [
      { k: 'title', label: 'Service name', type: 'text', required: true },
      { k: 'icon', label: 'Icon', type: 'select', options: ['globe', 'cart', 'grid', 'phone', 'chart', 'bolt', 'pen', 'shield'] },
      { k: 'summary', label: 'Summary', type: 'textarea', required: true },
      { k: 'deliverables', label: "What's included (one per line)", type: 'list' },
      { k: 'stack', label: 'Tech stack (comma separated)', type: 'tags' },
      { k: 'startingPrice', label: 'Starting price (KES)', type: 'number' },
      { k: 'priceUnit', label: 'Price unit (optional, e.g. /month)', type: 'text' },
      { k: 'timeline', label: 'Typical timeline', type: 'text', placeholder: '1–2 weeks' },
      { k: 'featured', label: 'Featured', type: 'bool' },
    ],
  },
  projects: {
    label: 'Projects', path: 'projects', prefix: 'pj', icon: 'briefcase', group: 'Build',
    blurb: 'Your portfolio. Projects marked "featured" appear on the homepage.',
    title: (x) => x.title, sub: (x) => `${x.category} · ${x.year || ''} · ${x.status || ''}${x.featured ? ' · featured' : ''}`,
    defaults: () => ({ category: 'Web', year: String(new Date().getFullYear()), status: 'Live', stack: [], results: [], featured: false, accent: '#29AB87' }),
    fields: [
      { k: 'title', label: 'Project name', type: 'text', required: true },
      { k: 'category', label: 'Category', type: 'select', options: ['Web', 'App', 'Trading', 'Automation', 'Brand'], custom: true },
      { k: 'year', label: 'Year', type: 'text' },
      { k: 'status', label: 'Status', type: 'select', options: ['Live', 'Built', 'In progress', 'Case study', 'Concept'] },
      { k: 'summary', label: 'Summary', type: 'textarea', required: true },
      { k: 'problem', label: 'The challenge', type: 'textarea' },
      { k: 'solution', label: 'What you built', type: 'textarea' },
      { k: 'results', label: 'Highlights / results (one per line)', type: 'list' },
      { k: 'stack', label: 'Stack (comma separated)', type: 'tags' },
      { k: 'image', label: 'Cover image (optional)', type: 'image' },
      { k: 'link', label: 'Live link', type: 'text', placeholder: 'https://' },
      { k: 'repo', label: 'Source code link', type: 'text', placeholder: 'https://github.com/…' },
      { k: 'accent', label: 'Accent colour', type: 'color' },
      { k: 'featured', label: 'Featured on homepage', type: 'bool' },
    ],
  },
  packages: {
    label: 'Packages', path: 'packages', prefix: 'pk', icon: 'star', group: 'Sell',
    blurb: 'Prices are in KES; the site converts to USD with the rate in Settings. Set price 0 and unit "custom" for "Let\'s talk" packages.',
    title: (x) => x.name, sub: (x) => `${x.group} · ${x.unit === 'custom' ? 'custom' : 'KES ' + Number(x.price || 0).toLocaleString() + ' ' + (x.unit || '')}${x.highlight ? ' · highlighted' : ''}`,
    defaults: () => ({ group: 'Build', unit: 'one-off', features: [], highlight: false }),
    fields: [
      { k: 'name', label: 'Package name', type: 'text', required: true },
      { k: 'group', label: 'Group', type: 'select', options: ['Build', 'Trade', 'Grow'], custom: true },
      { k: 'price', label: 'Price (KES)', type: 'number' },
      { k: 'unit', label: 'Price unit', type: 'select', options: ['one-off', 'from', '/month', '/session', '/week', 'custom'] },
      { k: 'summary', label: 'Summary', type: 'textarea' },
      { k: 'features', label: 'Features (one per line)', type: 'list' },
      { k: 'highlight', label: 'Highlight as "Most popular"', type: 'bool' },
    ],
  },
  courses: {
    label: 'Courses', path: 'courses', prefix: 'cr', icon: 'book', group: 'Sell',
    title: (x) => x.title, sub: (x) => `${x.category} · ${x.level} · KES ${Number(x.price || 0).toLocaleString()} · ${x.status}`,
    defaults: () => ({ category: 'IT', level: 'Beginner', status: 'coming-soon', outcomes: [], modules: [] }),
    fields: [
      { k: 'title', label: 'Course title', type: 'text', required: true },
      { k: 'category', label: 'Category', type: 'select', options: ['IT', 'Trading', 'Business'], custom: true },
      { k: 'level', label: 'Level', type: 'select', options: ['Beginner', 'Intermediate', 'Advanced', 'All levels'] },
      { k: 'duration', label: 'Duration', type: 'text', placeholder: '6 weeks' },
      { k: 'price', label: 'Price (KES)', type: 'number' },
      { k: 'status', label: 'Status', type: 'select', options: ['enrolling', 'coming-soon', 'closed'] },
      { k: 'summary', label: 'Summary', type: 'textarea', required: true },
      { k: 'outcomes', label: 'Students will… (one per line)', type: 'list' },
      { k: 'modules', label: 'Curriculum', type: 'modules' },
    ],
  },
  markets: {
    label: 'Markets', path: 'trading.markets', prefix: 'mk', icon: 'candles', group: 'Trading', fixed: true,
    blurb: 'The Forex, Binary and Crypto tabs on the Trading desk.',
    title: (x) => x.title, sub: (x) => x.tagline,
    fields: [
      { k: 'title', label: 'Market', type: 'text', required: true },
      { k: 'tagline', label: 'Tagline', type: 'text' },
      { k: 'summary', label: 'How you trade it', type: 'textarea' },
      { k: 'focus', label: 'Focus (comma separated)', type: 'tags' },
      { k: 'approach', label: 'Your rules (one per line)', type: 'list' },
    ],
  },
  creations: {
    label: 'Creations', path: 'trading.creations', prefix: 'cn', icon: 'bolt', group: 'Trading',
    blurb: 'Indicators, bots, scanners, templates and tools you have built.',
    title: (x) => x.title, sub: (x) => `${x.type} · ${x.platform || ''} · ${x.price || ''}`,
    defaults: () => ({ type: 'Indicator', status: 'Live', price: 'Free' }),
    fields: [
      { k: 'title', label: 'Name', type: 'text', required: true },
      { k: 'type', label: 'Type', type: 'select', options: ['Indicator', 'EA / Bot', 'Scanner', 'Tool', 'Template', 'Script', 'Strategy'], custom: true },
      { k: 'platform', label: 'Platform', type: 'text', placeholder: 'TradingView · MT5 · Python' },
      { k: 'summary', label: 'What it does', type: 'textarea', required: true },
      { k: 'link', label: 'Link (download, product page or #tool-position)', type: 'text' },
      { k: 'price', label: 'Price label', type: 'text', placeholder: 'Free / KES 5,000 / Private' },
      { k: 'status', label: 'Status', type: 'select', options: ['Live', 'Built', 'Beta', 'Coming soon'] },
    ],
  },
  lessons: {
    label: 'Lessons', path: 'trading.lessons', prefix: 'ls', icon: 'book', group: 'Trading',
    blurb: 'Free lessons in the Knowledge library. Formatting: "## Heading", "- bullet", **bold**.',
    title: (x) => x.title, sub: (x) => `${x.market} · ${x.level} · ${x.minutes || 3} min`,
    defaults: () => ({ market: 'General', level: 'Beginner', minutes: 5, body: '## Heading\nWrite your lesson here.\n\n- A bullet point\n- Another one' }),
    fields: [
      { k: 'title', label: 'Title', type: 'text', required: true },
      { k: 'market', label: 'Market', type: 'select', options: ['Forex', 'Crypto', 'Binary', 'General'], custom: true },
      { k: 'level', label: 'Level', type: 'select', options: ['Beginner', 'Intermediate', 'Advanced', 'All levels'] },
      { k: 'minutes', label: 'Reading time (minutes)', type: 'number' },
      { k: 'summary', label: 'One-line summary', type: 'text', required: true },
      { k: 'body', label: 'Lesson', type: 'markdown' },
    ],
  },
  qa: {
    label: 'Q&A', path: 'qa', prefix: 'qa', icon: 'question', group: 'Community',
    blurb: 'Answered questions shown on the Connect page and homepage. Answer inbox questions with "Publish as Q&A".',
    title: (x) => x.question, sub: (x) => `${x.topic || 'General'} · ${x.published ? 'published' : 'hidden'}`,
    defaults: () => ({ topic: 'Business', published: true }),
    fields: [
      { k: 'question', label: 'Question', type: 'textarea', required: true },
      { k: 'answer', label: 'Answer', type: 'textarea', required: true },
      { k: 'topic', label: 'Topic', type: 'select', options: ['Business', 'IT', 'Trading', 'Academy', 'Other'], custom: true },
      { k: 'published', label: 'Published', type: 'bool' },
    ],
  },
  testimonials: {
    label: 'Testimonials', path: 'testimonials', prefix: 'tm', icon: 'chat', group: 'Community',
    blurb: 'Only add real words from real clients or students, with their permission. They appear on the homepage once at least one is published.',
    title: (x) => x.name, sub: (x) => `${x.role || ''} · ${x.published ? 'published' : 'hidden'}`,
    defaults: () => ({ published: true }),
    fields: [
      { k: 'name', label: 'Name', type: 'text', required: true },
      { k: 'role', label: 'Role / company', type: 'text' },
      { k: 'quote', label: 'What they said', type: 'textarea', required: true },
      { k: 'published', label: 'Published', type: 'bool' },
    ],
  },
};

// ------------------------------------------------------------------ fields
function fieldHTML(f, v) {
  const id = 'fld-' + f.k;
  const req = f.required ? ' <span class="accent">*</span>' : '';
  const label = `<label for="${id}">${esc(f.label)}${req}</label>`;
  switch (f.type) {
    case 'textarea': return `<div class="field full">${label}<textarea class="textarea" id="${id}" data-f="${f.k}" ${f.required ? 'required' : ''}>${esc(v || '')}</textarea></div>`;
    case 'markdown': return `<div class="field full">${label}<div class="md-edit"><textarea class="textarea mono" id="${id}" data-f="${f.k}" style="min-height:300px;font-size:.85rem">${esc(v || '')}</textarea><div class="md-preview prose"></div></div></div>`;
    case 'number': return `<div class="field">${label}<input class="input" type="number" step="any" id="${id}" data-f="${f.k}" value="${esc(v ?? '')}"></div>`;
    case 'date': return `<div class="field">${label}<input class="input" type="date" id="${id}" data-f="${f.k}" value="${esc(v || '')}"></div>`;
    case 'color': return `<div class="field">${label}<input class="input" type="color" id="${id}" data-f="${f.k}" value="${esc(/^#[0-9a-f]{6}$/i.test(v || '') ? v : '#29AB87')}" style="padding:4px;height:50px"></div>`;
    case 'bool': return `<div class="field"><span class="label">&nbsp;</span><label class="switch"><input type="checkbox" id="${id}" data-f="${f.k}" ${v ? 'checked' : ''}>${esc(f.label)}</label></div>`;
    case 'list': return `<div class="field full">${label}<textarea class="textarea" id="${id}" data-f="${f.k}" style="min-height:120px">${esc((v || []).join('\n'))}</textarea></div>`;
    case 'tags': return `<div class="field full">${label}<input class="input" id="${id}" data-f="${f.k}" value="${esc((v || []).join(', '))}"></div>`;
    case 'select':
      if (f.custom) return `<div class="field">${label}<input class="input" id="${id}" data-f="${f.k}" list="${id}-dl" value="${esc(v || '')}"><datalist id="${id}-dl">${f.options.map((o) => `<option value="${esc(o)}">`).join('')}</datalist></div>`;
      return `<div class="field">${label}<select class="select" id="${id}" data-f="${f.k}">${f.options.map((o) => `<option ${o === v ? 'selected' : ''}>${esc(o)}</option>`).join('')}</select></div>`;
    case 'image': return `<div class="field full">${label}<div class="img-field"><div class="img-field__preview">${v ? `<img src="${esc(/^https?:/.test(v) ? v : url(v))}" alt="">` : '<span class="muted mono">No image</span>'}</div>
      <div style="flex:1;display:grid;gap:8px"><input class="input" id="${id}" data-f="${f.k}" value="${esc(v || '')}" placeholder="Image URL or upload">
      <div class="row">${MODE === 'server' ? `<label class="btn btn--sm">Upload image<input type="file" accept="image/png,image/jpeg,image/webp,image/gif" data-upload="${f.k}" hidden></label>` : '<span class="muted" style="font-size:.78rem">Paste an image URL (uploads need the server).</span>'}<button type="button" class="btn btn--sm" data-clear="${f.k}">Remove</button></div></div></div></div>`;
    case 'modules': return `<div class="field full"><span class="label">${esc(f.label)}</span><div class="modules" data-modules="${f.k}">${(v || []).map(moduleRow).join('')}</div><button type="button" class="btn btn--sm" data-add-module style="margin-top:8px">+ Add module</button></div>`;
    case 'pairs': return `<div class="field full">${label}<textarea class="textarea mono" id="${id}" data-f="${f.k}" style="min-height:140px">${esc((v || []).map((p) => `${p.label} | ${p.symbol}`).join('\n'))}</textarea>${f.hint ? `<span class="hint">${esc(f.hint)}</span>` : ''}</div>`;
    default: return `<div class="field ${f.wide === false ? '' : 'full'}">${label}<input class="input" id="${id}" data-f="${f.k}" value="${esc(v ?? '')}" placeholder="${esc(f.placeholder || '')}" ${f.required ? 'required' : ''}>${f.hint ? `<span class="hint">${esc(f.hint)}</span>` : ''}</div>`;
  }
}
const moduleRow = (m = { title: '', lessons: [] }) => `<div class="module"><input class="input" data-m="title" placeholder="Module title" value="${esc(m.title)}"><textarea class="textarea" data-m="lessons" placeholder="Lessons, one per line" style="min-height:90px">${esc((m.lessons || []).join('\n'))}</textarea><button type="button" class="icon-btn" data-del-module aria-label="Remove module">${ICONS.trash}</button></div>`;

function wireFields(root) {
  $$('[data-upload]', root).forEach((inp) => inp.addEventListener('change', async () => {
    const f = inp.files[0]; if (!f) return;
    if (f.size > 5 * 1024 * 1024) { toast('Images must be under 5 MB', 'err'); return; }
    const dataUrl = await new Promise((res, rej) => { const r = new FileReader(); r.onload = () => res(r.result); r.onerror = rej; r.readAsDataURL(f); });
    try {
      const j = await apiFetch('admin/upload', { method: 'POST', body: JSON.stringify({ dataUrl }) });
      const target = $(`[data-f="${inp.dataset.upload}"]`, root); target.value = j.url;
      target.closest('.img-field').querySelector('.img-field__preview').innerHTML = `<img src="${esc(url(j.url))}" alt="">`;
      toast('Image uploaded');
    } catch (e) { toast(e.message, 'err'); }
  }));
  $$('[data-clear]', root).forEach((b) => b.addEventListener('click', () => { const t = $(`[data-f="${b.dataset.clear}"]`, root); t.value = ''; t.closest('.img-field').querySelector('.img-field__preview').innerHTML = '<span class="muted mono">No image</span>'; }));
  $$('[data-add-module]', root).forEach((b) => b.addEventListener('click', () => { b.previousElementSibling.insertAdjacentHTML('beforeend', moduleRow()); wireModules(root); }));
  wireModules(root);
  $$('.md-edit', root).forEach((box) => { const ta = $('textarea', box); const pv = $('.md-preview', box); const up = () => { pv.innerHTML = markdown(ta.value); }; ta.addEventListener('input', up); up(); });
}
function wireModules(root) { $$('[data-del-module]', root).forEach((b) => { b.onclick = () => b.closest('.module').remove(); }); }

function collect(root, fields, base = {}) {
  const out = { ...base };
  for (const f of fields) {
    if (f.type === 'modules') {
      out[f.k] = $$('.module', $(`[data-modules="${f.k}"]`, root)).map((m) => ({ title: $('[data-m=title]', m).value.trim(), lessons: $('[data-m=lessons]', m).value.split('\n').map((s) => s.trim()).filter(Boolean) })).filter((m) => m.title);
      continue;
    }
    const el = $(`[data-f="${f.k}"]`, root); if (!el) continue;
    const v = el.value;
    if (f.type === 'bool') out[f.k] = el.checked;
    else if (f.type === 'number') out[f.k] = v === '' ? 0 : Number(v);
    else if (f.type === 'list') out[f.k] = v.split('\n').map((s) => s.trim()).filter(Boolean);
    else if (f.type === 'tags') out[f.k] = v.split(',').map((s) => s.trim()).filter(Boolean);
    else if (f.type === 'pairs') out[f.k] = v.split('\n').map((l) => l.split('|').map((s) => s.trim())).filter((p) => p[0] && p[1]).map(([label, symbol]) => ({ label, symbol }));
    else out[f.k] = v.trim();
  }
  return out;
}
function missing(fields, data) { return fields.filter((f) => f.required && !String(data[f.k] ?? '').trim()).map((f) => f.label); }

// ------------------------------------------------------------------ saving
function persist(label = 'Saved') {
  saving = saving.then(async () => {
    if (MODE === 'server') {
      try {
        const j = await apiFetch('admin/content', { method: 'PUT', headers: { 'If-Match': C.updatedAt || '' }, body: JSON.stringify(C) });
        C.updatedAt = j.updatedAt;
        toast(`${label} — live on the site`);
      } catch (e) { toast(e.message, 'err'); if (/changed in another tab/.test(e.message)) { await loadContent(); route(); } }
    } else {
      C.updatedAt = new Date().toISOString();
      LS.set('mkb.draft', C);
      toast(`${label} in this browser — export to publish`);
      updateDraftBadge();
    }
  });
  return saving;
}

async function loadContent() {
  if (MODE === 'server') { C = await apiFetch('admin/content'); return; }
  const draft = LS.get('mkb.draft', null);
  if (draft && draft.settings) { C = draft; return; }
  const r = await fetch(url('content/content.json'), { cache: 'no-cache' });
  C = await r.json();
}

// ------------------------------------------------------------------ shell
const NAV = [
  ['Dashboard', [['overview', 'Overview', 'grid'], ['inbox', 'Inbox', 'chat']]],
  ['Profile', [['skills', 'Skills', 'sparkle'], ['about', 'About & story', 'user']]],
  ['Build', [['services', 'IT services', 'code'], ['projects', 'Projects', 'briefcase']]],
  ['Sell', [['packages', 'Packages', 'star'], ['courses', 'Courses', 'book']]],
  ['Trading', [['markets', 'Markets', 'candles'], ['creations', 'Creations', 'bolt'], ['lessons', 'Lessons', 'book']]],
  ['Community', [['qa', 'Q&A', 'question'], ['testimonials', 'Testimonials', 'chat']]],
  ['Site', [['settings', 'Settings', 'shield'], ['backup', 'Backup & security', 'lock']]],
];
function shell() {
  app.innerHTML = `<div class="admin-shell">
    <aside class="admin-side">
      <a class="logo" href="${url('index.html')}" target="_blank" rel="noopener"><img src="${url('assets/brand/mkb-mark.svg')}" alt="" width="38" height="38"><span class="logo__text"><b>MKB</b><span>Admin</span></span></a>
      <span class="tag ${MODE === 'server' ? '' : 'tag--warn'}" style="align-self:flex-start">${MODE === 'server' ? '● Live server' : '● Local edit mode'}</span>
      <nav class="admin-nav">${NAV.map(([g, items]) => `<div class="admin-nav__group"><span>${g}</span>${items.map(([k, l, ic]) => `<a href="#${k}" data-nav="${k}">${icon(ic)}<span>${l}</span>${k === 'inbox' ? '<b class="badge" id="inbox-badge" hidden></b>' : ''}</a>`).join('')}</div>`).join('')}</nav>
      <div class="admin-side__foot">
        <a class="btn btn--sm btn--block" href="${url('index.html')}" target="_blank" rel="noopener">View site ${ICONS.arrowUR}</a>
        ${MODE === 'server' ? `<button class="btn btn--sm btn--block" id="signout">${ICONS.logout} Sign out</button>` : ''}
      </div>
    </aside>
    <section class="admin-main">
      <header class="admin-top"><button class="icon-btn admin-menu" aria-label="Menu">${ICONS.menu}</button><h1 class="h3" id="view-title"></h1><span class="spacer"></span><span id="draft-badge"></span></header>
      ${MODE === 'static' ? `<div class="notice" style="margin-bottom:18px">${ICONS.info}<span><b>Local edit mode:</b> no server was found, so changes are saved in this browser only. When you're done, go to <a href="#backup" style="text-decoration:underline">Backup</a> → <b>Export content.json</b> and replace <span class="kbd">public/content/content.json</span> on your host. Run the included Node server to edit live with a password instead.</span></div>` : ''}
      <div id="view"></div>
    </section>
  </div>`;
  $('#signout')?.addEventListener('click', () => { setToken(''); renderLogin('Signed out.'); });
  $('.admin-menu').addEventListener('click', () => $('.admin-side').classList.toggle('is-open'));
  $$('.admin-nav a').forEach((a) => a.addEventListener('click', () => $('.admin-side').classList.remove('is-open')));
  updateDraftBadge(); updateInboxBadge();
}
function updateDraftBadge() {
  const el = $('#draft-badge'); if (!el || MODE !== 'static') return;
  const has = !!LS.get('mkb.draft', null);
  el.innerHTML = has ? `<label class="switch" title="Show your unpublished edits on the public pages in this browser"><input type="checkbox" id="preview-toggle" ${LS.get('mkb.preview', false) ? 'checked' : ''}>Preview edits on site</label>` : '';
  $('#preview-toggle')?.addEventListener('change', (e) => { LS.set('mkb.preview', e.target.checked); toast(e.target.checked ? 'Your edits now show on the site in this browser' : 'Showing the published site'); });
}
function updateInboxBadge() {
  const b = $('#inbox-badge'); if (!b) return;
  const n = SUBS.filter((s) => s.status === 'new').length;
  b.hidden = !n; b.textContent = n;
}

function route() {
  const key = (location.hash || '#overview').slice(1);
  $$('.admin-nav a').forEach((a) => a.classList.toggle('is-active', a.dataset.nav === key));
  const view = $('#view');
  const views = { overview, inbox, about, settings, backup };
  if (COLLECTIONS[key]) collectionView(key, view);
  else (views[key] || overview)(view);
  view.scrollTop = 0; window.scrollTo(0, 0);
}
addEventListener('hashchange', () => { if (C) route(); });

// ------------------------------------------------------------------ views
function setTitle(t) { $('#view-title').textContent = t; document.title = `${t} · MKB Admin`; }

function overview(view) {
  setTitle('Overview');
  const count = (f) => SUBS.filter(f).length;
  const stats = [
    ['New messages', count((s) => s.status === 'new'), 'chat', '#inbox'],
    ['Open orders', count((s) => s.type === 'order' && ['new', 'reviewing'].includes(s.status)), 'cart', '#inbox'],
    ['Proposals & ideas', count((s) => ['partnership', 'idea'].includes(s.type)), 'handshake', '#inbox'],
    ['Skills listed', C.skills.length, 'sparkle', '#skills'],
  ];
  view.innerHTML = `
    <div class="admin-stats">${stats.map(([l, n, ic, h]) => `<a class="card admin-stat" href="${h}"><span class="icon-tile">${icon(ic)}</span><b>${MODE === 'static' && h === '#inbox' ? '—' : n}</b><span>${l}</span></a>`).join('')}</div>
    <div class="grid grid-2" style="margin-top:18px;align-items:start">
      <div class="card"><div class="row" style="justify-content:space-between;margin-bottom:12px"><h2 class="h4">Latest messages</h2><a class="link-arrow" href="#inbox">Inbox</a></div>
        ${MODE === 'static' ? '<p class="muted">The inbox needs the live server. On static hosting, messages go straight to your WhatsApp, DMs and email.</p>' : SUBS.length ? `<ul class="admin-list">${SUBS.slice(0, 6).map(subRow).join('')}</ul>` : '<p class="muted">No messages yet. They will appear here as visitors send proposals, questions and orders.</p>'}</div>
      <div class="card"><h2 class="h4" style="margin-bottom:12px">Quick actions</h2>
        <div class="quick">${[['skills', 'Add a new skill', 'sparkle'], ['projects', 'Add a project', 'briefcase'], ['courses', 'Add a course', 'book'], ['lessons', 'Write a lesson', 'book'], ['creations', 'Add a trading creation', 'bolt'], ['packages', 'Add a package', 'star']].map(([k, l, ic]) => `<button class="dm" data-quick="${k}"><i>${icon(ic)}</i><span>${l}</span><span class="go">${ICONS.plus}</span></button>`).join('')}</div>
      </div>
    </div>
    <div class="card" style="margin-top:18px"><h2 class="h4">Site at a glance</h2>
      <div class="admin-glance">${[['Services', C.services.length], ['Projects', C.projects.length], ['Packages', C.packages.length], ['Courses', C.courses.length], ['Lessons', C.trading.lessons.length], ['Creations', C.trading.creations.length], ['Q&A', C.qa.length], ['Last update', C.updatedAt ? new Date(C.updatedAt).toLocaleString() : '—']].map(([l, v]) => `<div><span class="muted">${l}</span><b>${esc(v)}</b></div>`).join('')}</div></div>`;
  $$('[data-quick]', view).forEach((b) => b.addEventListener('click', () => { location.hash = b.dataset.quick; setTimeout(() => editItem(b.dataset.quick, null), 50); }));
  $$('[data-sub]', view).forEach((li) => li.addEventListener('click', () => openSub(li.dataset.sub)));
}

function collectionView(key, view) {
  const S = COLLECTIONS[key];
  setTitle(S.label);
  const list = getPath(C, S.path) || [];
  view.innerHTML = `
    ${S.blurb ? `<p class="muted" style="margin:0 0 16px;max-width:70ch">${esc(S.blurb)}</p>` : ''}
    <div class="row" style="justify-content:space-between;margin-bottom:14px">
      <input class="input" id="coll-search" placeholder="Search ${esc(S.label.toLowerCase())}…" style="max-width:320px">
      ${S.fixed ? '' : `<button class="btn btn--primary" id="add-item">${ICONS.plus} Add ${esc(S.label.replace(/s$/, '').toLowerCase())}</button>`}
    </div>
    <ul class="admin-items" id="items"></ul>`;
  const draw = () => {
    const term = $('#coll-search').value.trim().toLowerCase();
    $('#items').innerHTML = list.map((x, i) => ({ x, i })).filter(({ x }) => !term || JSON.stringify(x).toLowerCase().includes(term)).map(({ x, i }) => `
      <li class="admin-item">
        <div class="admin-item__main" data-edit="${i}" role="button" tabindex="0"><b>${esc(S.title(x) || '(untitled)')}</b><span class="muted">${esc(S.sub(x) || '')}</span></div>
        <div class="admin-item__actions">
          <button class="icon-btn" data-up="${i}" aria-label="Move up" ${i === 0 ? 'disabled' : ''}><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 19V5M6 11l6-6 6 6"/></svg></button>
          <button class="icon-btn" data-edit="${i}" aria-label="Edit">${ICONS.edit}</button>
          ${S.fixed ? '' : `<button class="icon-btn" data-dup="${i}" aria-label="Duplicate">${ICONS.copy}</button><button class="icon-btn" data-del="${i}" aria-label="Delete">${ICONS.trash}</button>`}
        </div></li>`).join('') || `<li class="empty">Nothing here yet.${S.fixed ? '' : ' Add the first one.'}</li>`;
    $$('[data-edit]', view).forEach((b) => { b.onclick = () => editItem(key, Number(b.dataset.edit)); b.onkeydown = (e) => { if (e.key === 'Enter') editItem(key, Number(b.dataset.edit)); }; });
    $$('[data-up]', view).forEach((b) => { b.onclick = () => { const i = Number(b.dataset.up); if (i < 1) return; [list[i - 1], list[i]] = [list[i], list[i - 1]]; persist('Order saved'); draw(); }; });
    $$('[data-dup]', view).forEach((b) => { b.onclick = () => { const i = Number(b.dataset.dup); const copy = structuredClone(list[i]); copy.id = uid(S.prefix); const tk = Object.keys(copy).find((k) => ['name', 'title', 'question'].includes(k)); if (tk) copy[tk] += ' (copy)'; list.splice(i + 1, 0, copy); persist('Duplicated'); draw(); }; });
    $$('[data-del]', view).forEach((b) => { b.onclick = () => { const i = Number(b.dataset.del); if (!confirm(`Delete "${S.title(list[i])}"? This can't be undone.`)) return; list.splice(i, 1); persist('Deleted'); draw(); }; });
  };
  $('#coll-search').addEventListener('input', draw);
  $('#add-item')?.addEventListener('click', () => editItem(key, null));
  draw();
}

function editItem(key, index, preset) {
  const S = COLLECTIONS[key];
  const list = getPath(C, S.path) || (setPath(C, S.path, []), getPath(C, S.path));
  const item = index === null ? { ...(S.defaults ? S.defaults() : {}), ...(preset || {}) } : list[index];
  const m = openModal(`<span class="eyebrow">${esc(S.label)}</span><h2 style="margin:12px 0 22px">${index === null ? 'Add new' : 'Edit'}</h2>
    <form class="form-grid admin-form" novalidate>${S.fields.map((f) => fieldHTML(f, item[f.k])).join('')}
    <div class="full row" style="justify-content:flex-end;margin-top:8px"><button type="button" class="btn" data-cancel>Cancel</button><button class="btn btn--primary" type="submit">${index === null ? 'Add' : 'Save changes'}</button></div></form>`, { wide: true, label: `${index === null ? 'Add' : 'Edit'} ${S.label}` });
  const form = $('form', m.el);
  wireFields(form);
  $('[data-cancel]', form).addEventListener('click', m.close);
  form.addEventListener('submit', (e) => {
    e.preventDefault();
    const data = collect(form, S.fields, item);
    const miss = missing(S.fields, data);
    if (miss.length) { toast('Please fill in: ' + miss.join(', '), 'err'); return; }
    if (index === null) { data.id = data.id || uid(S.prefix); list.push(data); } else list[index] = data;
    m.close();
    persist(index === null ? `${S.label.replace(/s$/, '')} added` : 'Saved').then(() => { if (location.hash.slice(1) === key) route(); });
  });
}

// ---- single-object forms
function objectForm(view, title, sections, onSave) {
  setTitle(title);
  view.innerHTML = `<form class="admin-object" novalidate>${sections.map((s) => `<fieldset class="card"><legend class="h4">${esc(s.title)}</legend>${s.note ? `<p class="muted" style="margin:0 0 14px;font-size:.88rem">${esc(s.note)}</p>` : ''}<div class="form-grid">${s.fields.map((f) => fieldHTML(f, getPath(C, f.path || f.k))).join('')}</div></fieldset>`).join('')}
    <div class="admin-savebar"><button class="btn btn--primary btn--lg" type="submit">Save ${esc(title.toLowerCase())}</button></div></form>`;
  const form = $('form', view);
  wireFields(form);
  form.addEventListener('submit', (e) => {
    e.preventDefault();
    for (const s of sections) for (const f of s.fields) {
      const v = collect(form, [f])[f.k];
      if (v !== undefined) setPath(C, f.path || f.k, v);
    }
    onSave?.();
    persist(title + ' saved');
  });
}

function about(view) {
  objectForm(view, 'About & story', [
    { title: 'Headline & story', fields: [
      { k: 'h', path: 'about.headline', label: 'Headline', type: 'text' },
      { k: 'i', path: 'about.intro', label: 'Intro (top of the About page)', type: 'textarea' },
      { k: 'b', path: 'about.bio', label: 'Your story (one paragraph per line)', type: 'list' },
      { k: 'm', path: 'about.mission', label: 'Mission', type: 'textarea' },
      { k: 'v', path: 'about.vision', label: 'Vision', type: 'textarea' },
    ] },
  ]);
  view.insertAdjacentHTML('beforeend', `<div class="grid grid-2" style="margin-top:18px;align-items:start">
    <div class="card"><div class="row" style="justify-content:space-between"><h2 class="h4">Values</h2><button class="btn btn--sm" data-add="values">${ICONS.plus} Add</button></div><ul class="admin-items" id="values" style="margin-top:12px"></ul></div>
    <div class="card"><div class="row" style="justify-content:space-between"><h2 class="h4">Journey timeline</h2><button class="btn btn--sm" data-add="timeline">${ICONS.plus} Add</button></div><ul class="admin-items" id="timeline" style="margin-top:12px"></ul></div></div>`);
  const sub = {
    values: { path: 'about.values', fields: [{ k: 'title', label: 'Value', type: 'text', required: true }, { k: 'text', label: 'Explanation', type: 'textarea' }], t: (x) => x.title },
    timeline: { path: 'about.timeline', fields: [{ k: 'label', label: 'Label (e.g. Chapter 01, 2024)', type: 'text', required: true }, { k: 'title', label: 'Title', type: 'text', required: true }, { k: 'text', label: 'What happened', type: 'textarea' }], t: (x) => `${x.label} — ${x.title}` },
  };
  const draw = (k) => {
    const list = getPath(C, sub[k].path) || [];
    $('#' + k).innerHTML = list.map((x, i) => `<li class="admin-item"><div class="admin-item__main" data-e="${i}" role="button" tabindex="0"><b>${esc(sub[k].t(x))}</b></div><div class="admin-item__actions"><button class="icon-btn" data-e="${i}" aria-label="Edit">${ICONS.edit}</button><button class="icon-btn" data-d="${i}" aria-label="Delete">${ICONS.trash}</button></div></li>`).join('') || '<li class="empty">None yet.</li>';
    $$(`#${k} [data-e]`).forEach((b) => { b.onclick = () => edit(k, Number(b.dataset.e)); });
    $$(`#${k} [data-d]`).forEach((b) => { b.onclick = () => { if (confirm('Delete this item?')) { list.splice(Number(b.dataset.d), 1); persist('Deleted'); draw(k); } }; });
  };
  const edit = (k, i) => {
    const list = getPath(C, sub[k].path) || (setPath(C, sub[k].path, []), getPath(C, sub[k].path));
    const item = i === null ? {} : list[i];
    const m = openModal(`<h2 style="margin-bottom:18px">${i === null ? 'Add' : 'Edit'}</h2><form class="form-grid" novalidate>${sub[k].fields.map((f) => fieldHTML(f, item[f.k])).join('')}<div class="full row" style="justify-content:flex-end"><button class="btn btn--primary" type="submit">Save</button></div></form>`);
    $('form', m.el).addEventListener('submit', (e) => { e.preventDefault(); const d = collect($('form', m.el), sub[k].fields, item); const miss = missing(sub[k].fields, d); if (miss.length) { toast('Please fill in: ' + miss.join(', '), 'err'); return; } if (i === null) list.push(d); else list[i] = d; m.close(); persist('Saved'); draw(k); });
  };
  $$('[data-add]', view).forEach((b) => b.addEventListener('click', () => edit(b.dataset.add, null)));
  draw('values'); draw('timeline');
}

function settings(view) {
  objectForm(view, 'Settings', [
    { title: 'Brand', fields: [
      { k: 'fullName', path: 'settings.fullName', label: 'Full name', type: 'text', wide: false },
      { k: 'brandName', path: 'settings.brandName', label: 'Brand / initials', type: 'text', wide: false },
      { k: 'role', path: 'settings.role', label: 'Role line', type: 'text', wide: false },
      { k: 'location', path: 'settings.location', label: 'Location', type: 'text', wide: false },
      { k: 'portrait', path: 'settings.portrait', label: 'Your photo (About page — leave empty to show the bull)', type: 'image' },
      { k: 'seo', path: 'settings.seo.description', label: 'Site description (search engines & link previews)', type: 'textarea' },
    ] },
    { title: 'Availability & announcement', fields: [
      { k: 'availability', path: 'settings.availability', label: 'Availability', type: 'select', options: ['open', 'busy', 'closed'] },
      { k: 'availabilityNote', path: 'settings.availabilityNote', label: 'Availability note (hero chip)', type: 'text', wide: false },
      { k: 'responseTime', path: 'settings.responseTime', label: 'Response time', type: 'text', wide: false },
      { k: 'annOn', path: 'settings.announcement.enabled', label: 'Show announcement bar', type: 'bool' },
      { k: 'annText', path: 'settings.announcement.text', label: 'Announcement text', type: 'text' },
      { k: 'annLink', path: 'settings.announcement.link', label: 'Announcement link (e.g. academy.html)', type: 'text' },
    ] },
    { title: 'Contact & DMs', note: 'These power every "message me" button. WhatsApp must be in international format without + (e.g. 254710910301).', fields: [
      { k: 'email', path: 'settings.contact.email', label: 'Email', type: 'text', wide: false },
      { k: 'whatsapp', path: 'settings.contact.whatsapp', label: 'WhatsApp number (international)', type: 'text', wide: false },
      { k: 'phone', path: 'settings.contact.phone', label: 'Phone (for calls & SMS, e.g. +254710910301)', type: 'text', wide: false },
      { k: 'phoneDisplay', path: 'settings.contact.phoneDisplay', label: 'Phone as displayed', type: 'text', wide: false },
      { k: 'instagram', path: 'settings.contact.instagram', label: 'Instagram username', type: 'text', wide: false },
      { k: 'threads', path: 'settings.contact.threads', label: 'Threads username', type: 'text', wide: false },
      { k: 'x', path: 'settings.contact.x', label: 'X username', type: 'text', wide: false },
      { k: 'xUserId', path: 'settings.contact.xUserId', label: 'X numeric user ID (lets the X button open a DM with the message filled in)', type: 'text', wide: false },
    ] },
    { title: 'Prices & payments', note: 'Prices are entered in KES. Payments: M-Pesa details appear on every order confirmation; a Paystack public key adds a "Pay with M-Pesa or card" button (create one at paystack.com).', fields: [
      { k: 'usdRate', path: 'settings.currency.usdRate', label: 'KES per 1 USD', type: 'number' },
      { k: 'usdToggle', path: 'settings.currency.showUsdToggle', label: 'Show KES / USD switch', type: 'bool' },
      { k: 'mpesaType', path: 'settings.payments.mpesaType', label: 'M-Pesa type', type: 'select', options: ['', 'till', 'paybill', 'phone'] },
      { k: 'mpesaNumber', path: 'settings.payments.mpesaNumber', label: 'Till / Paybill / phone number', type: 'text', wide: false },
      { k: 'mpesaName', path: 'settings.payments.mpesaName', label: 'Name shown on M-Pesa', type: 'text', wide: false },
      { k: 'paystack', path: 'settings.payments.paystackPublicKey', label: 'Paystack public key (pk_live_… or pk_test_…)', type: 'text', wide: false },
      { k: 'payNote', path: 'settings.payments.note', label: 'Payment safety note', type: 'textarea' },
    ] },
    { title: 'Trading desk', fields: [
      { k: 'tIntro', path: 'trading.intro', label: 'Trading desk intro', type: 'textarea' },
      { k: 'wl', path: 'trading.watchlist.crypto', label: 'Crypto watchlist (CoinGecko ids, comma separated)', type: 'tags' },
      { k: 'tv', path: 'trading.watchlist.tvSymbols', label: 'Chart symbols (one per line: Label | EXCHANGE:SYMBOL)', type: 'pairs', hint: 'e.g. Gold | OANDA:XAUUSD' },
    ] },
    { title: 'Forms (optional)', note: 'Without the server, visitors still reach you through your DMs. To also keep a copy of every form, paste a Formspree / Web3Forms endpoint here.', fields: [
      { k: 'formEp', path: 'settings.forms.endpoint', label: 'Form endpoint URL (https://…)', type: 'text' },
    ] },
  ]);
}

function backup(view) {
  setTitle('Backup & security');
  view.innerHTML = `<div class="grid grid-2" style="align-items:start">
    <div class="card"><h2 class="h4">Export</h2><p class="muted">Download everything on the site as one file. ${MODE === 'static' ? 'This is how you publish in local edit mode: replace <span class="kbd">public/content/content.json</span> with this file.' : 'Keep a copy somewhere safe.'}</p>
      <div class="row"><button class="btn btn--primary" id="exp">${ICONS.download} Export content.json</button>${MODE === 'server' ? `<button class="btn" id="exp-all">${ICONS.download} Full backup (with inbox)</button><button class="btn" id="exp-csv">${ICONS.download} Inbox as CSV</button>` : ''}</div></div>
    <div class="card"><h2 class="h4">Import</h2><p class="muted">Restore from a content.json export. This replaces all current content.</p>
      <label class="btn">${ICONS.plus} Choose file<input type="file" accept="application/json,.json" id="imp" hidden></label></div>
    ${MODE === 'server' ? `<div class="card"><h2 class="h4">Change password</h2><form id="pw" class="form-grid" style="margin-top:12px">
        <div class="field full"><label for="pw-cur">Current password</label><input class="input" type="password" id="pw-cur" autocomplete="current-password" required></div>
        <div class="field"><label for="pw-new">New password</label><input class="input" type="password" id="pw-new" autocomplete="new-password" minlength="10" required></div>
        <div class="field"><label for="pw-new2">Repeat</label><input class="input" type="password" id="pw-new2" autocomplete="new-password" minlength="10" required></div>
        <div class="full"><button class="btn btn--primary" type="submit">Update password</button><p class="muted" style="font-size:.8rem;margin-top:8px">At least 10 characters. Other devices are signed out.</p></div></form></div>`
    : `<div class="card"><h2 class="h4">Local draft</h2><p class="muted">Your edits live in this browser until you export them. Discard them to start again from the published content.</p><button class="btn" id="reset">${ICONS.trash} Discard local edits</button></div>`}
  </div>`;
  const dl = (name, text, type = 'application/json') => { const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([text], { type })); a.download = name; document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 2000); };
  $('#exp').addEventListener('click', () => dl('content.json', JSON.stringify(C, null, 2)));
  $('#exp-all')?.addEventListener('click', async () => { try { const r = await fetch(API('admin/export'), { headers: { Authorization: 'Bearer ' + token() } }); if (!r.ok) throw new Error('Export failed'); dl(`mkb-backup-${today()}.json`, await r.text()); } catch (e) { toast(e.message, 'err'); } });
  $('#exp-csv')?.addEventListener('click', () => {
    const cols = ['ref', 'createdAt', 'type', 'status', 'name', 'phone', 'email', 'channel', 'title', 'message', 'notes'];
    const q = (v) => `"${String(v ?? '').replace(/"/g, '""')}"`;
    dl(`mkb-inbox-${today()}.csv`, '﻿' + [cols.join(','), ...SUBS.map((s) => cols.map((c) => q(s[c])).join(','))].join('\n'), 'text/csv');
  });
  $('#imp').addEventListener('change', async (e) => {
    const f = e.target.files[0]; if (!f) return;
    try {
      const data = JSON.parse(await f.text());
      const content = data.content && data.content.settings ? data.content : data;
      if (!content.settings || !Array.isArray(content.skills)) throw new Error('That file is not an MKB content export');
      if (!confirm('Replace all site content with this file?')) return;
      content.updatedAt = C.updatedAt; C = content; await persist('Imported');
    } catch (err) { toast(err.message, 'err'); }
  });
  $('#reset')?.addEventListener('click', async () => { if (!confirm('Discard all local edits?')) return; LS.del('mkb.draft'); LS.del('mkb.preview'); await loadContent(); toast('Local edits discarded'); route(); updateDraftBadge(); });
  $('#pw')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    if ($('#pw-new').value !== $('#pw-new2').value) { toast("The new passwords don't match", 'err'); return; }
    try { const j = await apiFetch('admin/password', { method: 'POST', body: JSON.stringify({ current: $('#pw-cur').value, next: $('#pw-new').value }) }); setToken(j.token); e.target.reset(); toast('Password updated'); } catch (err) { toast(err.message, 'err'); }
  });
}

// ------------------------------------------------------------------ inbox
const TYPE_LABEL = { question: 'Question', partnership: 'Partnership', idea: 'Idea', hire: 'Project', order: 'Order', waitlist: 'Waitlist' };
const STATUS = ['new', 'reviewing', 'accepted', 'declined', 'done'];
const subRow = (s) => `<li class="admin-sub" data-sub="${esc(s.id)}" role="button" tabindex="0"><span class="st st--${esc(s.status)}"></span><span class="tag tag--ghost">${esc(TYPE_LABEL[s.type] || s.type)}</span><span class="admin-sub__main"><b>${esc(s.name)}</b><span class="muted">${esc(s.title || s.message.slice(0, 90))}</span></span><span class="mono muted admin-sub__meta">${esc(s.ref)}<br>${new Date(s.createdAt).toLocaleDateString()}</span></li>`;
function inbox(view) {
  setTitle('Inbox');
  if (MODE !== 'server') {
    view.innerHTML = `<div class="card"><h2 class="h4">The inbox needs the live server</h2><p class="muted">Right now every proposal, question and order goes straight to your WhatsApp / Instagram / X / Threads / email — whichever the visitor picks — with a reference code. To also collect them here, track their status and let visitors check progress, run the Node server (see the README).</p></div>`;
    return;
  }
  view.innerHTML = `<div class="row" style="gap:10px;margin-bottom:14px">
      <input class="input" id="in-q" placeholder="Search name, ref, message…" style="max-width:300px">
      <select class="select" id="in-type" style="max-width:180px"><option value="">All types</option>${Object.entries(TYPE_LABEL).map(([k, l]) => `<option value="${k}">${l}</option>`).join('')}</select>
      <select class="select" id="in-status" style="max-width:180px"><option value="">All statuses</option>${STATUS.map((s) => `<option>${s}</option>`).join('')}</select>
      <span class="spacer"></span><button class="btn btn--sm" id="in-refresh">Refresh</button></div>
    <ul class="admin-list" id="in-list"></ul>`;
  const draw = () => {
    const q = $('#in-q').value.trim().toLowerCase(); const t = $('#in-type').value; const st = $('#in-status').value;
    const list = SUBS.filter((s) => (!t || s.type === t) && (!st || s.status === st) && (!q || JSON.stringify(s).toLowerCase().includes(q)));
    $('#in-list').innerHTML = list.map(subRow).join('') || '<li class="empty">No messages match.</li>';
    $$('[data-sub]', view).forEach((li) => { li.onclick = () => openSub(li.dataset.sub); li.onkeydown = (e) => { if (e.key === 'Enter') openSub(li.dataset.sub); }; });
  };
  ['#in-q', '#in-type', '#in-status'].forEach((s) => $(s).addEventListener('input', draw));
  $('#in-refresh').addEventListener('click', async () => { await loadSubs(); draw(); toast('Inbox refreshed'); });
  draw();
}
async function loadSubs() { if (MODE !== 'server') return; try { SUBS = await apiFetch('admin/submissions'); updateInboxBadge(); } catch { /* signed out */ } }
const waNumber = (p) => { let d = String(p || '').replace(/\D/g, ''); if (d.startsWith('0') && d.length === 10) d = '254' + d.slice(1); return d; };
function openSub(id) {
  const s = SUBS.find((x) => x.id === id); if (!s) return;
  const first = (s.name || '').split(' ')[0];
  const reply = `Hi ${first}, thanks for your ${(TYPE_LABEL[s.type] || 'message').toLowerCase()} (${s.ref}) on my website. `;
  const fields = Object.entries(s.fields || {}).filter(([, v]) => (Array.isArray(v) ? v.length : v));
  const m = openModal(`<span class="eyebrow">${esc(TYPE_LABEL[s.type] || s.type)} · ${esc(s.ref)} · ${new Date(s.createdAt).toLocaleString()}</span>
    <h2 style="margin:12px 0 6px;text-transform:none">${esc(s.name)}</h2>
    <p class="muted" style="margin:0 0 16px">${[s.phone, s.email, s.channel ? 'prefers ' + s.channel : ''].filter(Boolean).map(esc).join(' · ')}</p>
    <div class="row" style="margin-bottom:20px">
      ${s.phone ? `<a class="btn btn--primary btn--sm" target="_blank" rel="noopener" href="https://wa.me/${waNumber(s.phone)}?text=${encodeURIComponent(reply)}">${ICONS.whatsapp} WhatsApp</a><a class="btn btn--sm" href="tel:${esc(s.phone)}">${ICONS.phone} Call</a>` : ''}
      ${s.email ? `<a class="btn btn--sm" href="mailto:${esc(s.email)}?subject=${encodeURIComponent('Re: ' + (s.title || TYPE_LABEL[s.type]) + ' (' + s.ref + ')')}&body=${encodeURIComponent(reply)}">${ICONS.mail} Email</a>` : ''}
    </div>
    ${s.item ? `<div class="card" style="margin-bottom:14px"><span class="eyebrow eyebrow--plain">Item</span><p style="margin:8px 0 0"><b>${esc(s.item.name)}</b> · ${s.item.price ? 'KES ' + Number(s.item.price).toLocaleString() : 'custom'} ${esc(s.item.unit || '')}</p></div>` : ''}
    ${fields.length ? `<div class="table-wrap" style="margin-bottom:14px"><table class="table"><tbody>${fields.map(([k, v]) => `<tr><th style="width:34%">${esc(k.replace(/([A-Z])/g, ' $1').toLowerCase())}</th><td style="white-space:pre-wrap">${esc(Array.isArray(v) ? v.join(', ') : v)}</td></tr>`).join('')}</tbody></table></div>` : ''}
    ${s.message ? `<details class="card" style="margin-bottom:14px"><summary class="label" style="cursor:pointer">Full message</summary><pre class="mono" style="white-space:pre-wrap;font-size:.82rem;margin:12px 0 0">${esc(s.message)}</pre></details>` : ''}
    <form class="form-grid" id="sub-form">
      <div class="field"><label for="s-status">Status</label><select class="select" id="s-status">${STATUS.map((x) => `<option ${x === s.status ? 'selected' : ''}>${x}</option>`).join('')}</select></div>
      <div class="field"><label for="s-public">Note the visitor sees when tracking</label><input class="input" id="s-public" value="${esc(s.publicNote || '')}" maxlength="600" placeholder="e.g. Call booked for Friday 3pm"></div>
      <div class="field full"><label for="s-notes">Private notes</label><textarea class="textarea" id="s-notes">${esc(s.notes || '')}</textarea></div>
      <div class="full row" style="justify-content:space-between"><div class="row">${s.type === 'question' ? '<button type="button" class="btn btn--sm" data-publish>Publish as Q&amp;A</button>' : ''}<button type="button" class="btn btn--sm" data-delete>${ICONS.trash} Delete</button></div><button class="btn btn--primary" type="submit">Save</button></div>
    </form>`, { wide: true, label: 'Message ' + s.ref });
  $('#sub-form', m.el).addEventListener('submit', async (e) => {
    e.preventDefault();
    try {
      const j = await apiFetch('admin/submissions/' + s.id, { method: 'PATCH', body: JSON.stringify({ status: $('#s-status', m.el).value, publicNote: $('#s-public', m.el).value, notes: $('#s-notes', m.el).value }) });
      Object.assign(s, j); updateInboxBadge(); toast('Updated'); m.close(); route();
    } catch (err) { toast(err.message, 'err'); }
  });
  $('[data-delete]', m.el).addEventListener('click', async () => {
    if (!confirm(`Delete ${s.ref}? This can't be undone.`)) return;
    try { await apiFetch('admin/submissions/' + s.id, { method: 'DELETE' }); SUBS = SUBS.filter((x) => x !== s); updateInboxBadge(); m.close(); route(); toast('Deleted'); } catch (err) { toast(err.message, 'err'); }
  });
  $('[data-publish]', m.el)?.addEventListener('click', () => {
    m.close();
    const topic = { 'Tech / IT': 'IT' }[s.fields?.topic] || s.fields?.topic || 'Business';
    setTimeout(() => editItem('qa', null, { question: s.fields?.question || s.title || '', answer: '', topic, published: true }), 460);
  });
}

// ------------------------------------------------------------------ login & boot
function renderLogin(msg = '') {
  app.innerHTML = `<div class="admin-login">
    <form class="card" id="login">
      <img src="${url('assets/brand/mkb-logo-horizontal.svg')}" alt="MKB" style="height:52px;width:auto;margin-bottom:22px">
      <h1 class="h3">Admin sign in</h1>
      <p class="muted" style="margin:6px 0 20px">${ADMIN_ENABLED ? 'For Moses only. Manage skills, offers, courses and messages.' : 'Admin is switched off on this server. Set the <span class="kbd">MKB_ADMIN_PASSWORD</span> environment variable and restart.'}</p>
      ${msg ? `<div class="notice notice--green" style="margin-bottom:14px">${ICONS.info}<span>${esc(msg)}</span></div>` : ''}
      <div class="field"><label for="pw">Password</label><input class="input" type="password" id="pw" autocomplete="current-password" required ${ADMIN_ENABLED ? 'autofocus' : 'disabled'}></div>
      <button class="btn btn--primary btn--lg btn--block" style="margin-top:16px" ${ADMIN_ENABLED ? '' : 'disabled'}>Sign in ${ICONS.arrow}</button>
      <a class="link-arrow" style="margin-top:18px" href="${url('index.html')}">Back to the site</a>
    </form></div>`;
  $('#login').addEventListener('submit', async (e) => {
    e.preventDefault();
    const btn = $('#login button'); btn.disabled = true;
    try {
      const j = await apiFetch('admin/login', { method: 'POST', body: JSON.stringify({ password: $('#pw').value }) });
      setToken(j.token); await start();
    } catch (err) { btn.disabled = false; toast(err.message, 'err'); $('#pw').select(); }
  });
}

async function start() {
  await loadContent();
  await loadSubs();
  shell();
  route();
  if (MODE === 'server') setInterval(async () => { const before = SUBS.filter((s) => s.status === 'new').length; await loadSubs(); const after = SUBS.filter((s) => s.status === 'new').length; if (after > before) toast(`${after - before} new message${after - before > 1 ? 's' : ''}`); }, 60_000);
}

(async () => {
  try {
    const r = await fetch(API('health'), { cache: 'no-store' });
    const j = r.ok && (r.headers.get('content-type') || '').includes('json') ? await r.json() : null;
    if (j && j.ok) { MODE = 'server'; ADMIN_ENABLED = !!j.admin; }
  } catch { /* static hosting */ }
  if (MODE === 'server') {
    if (token()) { try { await apiFetch('admin/me'); await start(); return; } catch { /* fall through to login */ } }
    renderLogin();
  } else {
    await start();
  }
})();

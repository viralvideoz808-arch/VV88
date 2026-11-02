// MKB core: shared layout, content loading, contact links, motion helpers.
// Every page imports boot() from here.

export const ROOT = new URL('../../', import.meta.url);
export const url = (p) => new URL(p, ROOT).href;
const LS = {
  get(k, d = null) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* storage blocked */ } },
  del(k) { try { localStorage.removeItem(k); } catch { /* storage blocked */ } },
};
export { LS };
const SS = {
  get(k) { try { return sessionStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { sessionStorage.setItem(k, v); } catch { /* storage blocked */ } },
};

// ---------- Icons ----------
const s = (d, extra = '') => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" ${extra}>${d}</svg>`;
export const ICONS = {
  arrow: s('<path d="M5 12h14M13 6l6 6-6 6"/>', 'class="arrow"'),
  arrowUR: s('<path d="M7 17L17 7M8 7h9v9"/>'),
  arrowDown: s('<path d="M12 5v14M6 13l6 6 6-6"/>'),
  whatsapp: s('<path d="M3.5 20.5l1.4-4.1A8.5 8.5 0 1 1 8 19.3z"/><path d="M9.2 8.2c-.3.9 0 2.3 1.2 3.6 1.3 1.4 2.8 2 3.8 1.8l.9-1-1.7-1-.8.6c-.8-.3-1.6-1.1-2-2l.6-.8-.9-1.8z" fill="currentColor" stroke-width="1"/>'),
  instagram: s('<rect x="3" y="3" width="18" height="18" rx="5"/><circle cx="12" cy="12" r="4.2"/><circle cx="17.4" cy="6.6" r="1" fill="currentColor" stroke="none"/>'),
  x: '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z"/></svg>',
  threads: s('<path d="M16.6 11.2c-.3-2.6-2-4-4.5-4-2 0-3.4 1-4 2.5"/><path d="M16.8 11.6c2.2.9 3 2.6 2.6 4.6-.6 3-3.4 4.8-7.3 4.8-4.8 0-8.1-3.3-8.1-9s3.3-9 8.3-9c3.7 0 6.3 1.8 7.4 5"/><path d="M16.7 11.4c-1.4-.5-3.2-.6-4.6-.3-1.9.4-2.9 1.6-2.7 2.9.2 1.4 1.7 2.2 3.4 2 2.2-.3 3.6-2 3.9-4.6z"/>'),
  mail: s('<rect x="3" y="5" width="18" height="14" rx="3"/><path d="M4 7l8 6 8-6"/>'),
  phone: s('<path d="M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2z"/>'),
  sms: s('<path d="M21 12a8 8 0 0 1-11.6 7.1L4 20.5l1.4-4.6A8 8 0 1 1 21 12z"/><path d="M8.5 12h.01M12 12h.01M15.5 12h.01" stroke-width="2.6"/>'),
  chat: s('<path d="M4 5h16v11H9l-5 4z"/><path d="M8 10h8M8 13h5"/>'),
  close: s('<path d="M6 6l12 12M18 6L6 18"/>'),
  menu: s('<path d="M4 8h16M4 16h16"/>'),
  check: s('<path d="M5 12.5l4.5 4.5L19 7.5"/>'),
  globe: s('<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 2.6 3.8 5.6 3.8 9S14.5 18.4 12 21c-2.5-2.6-3.8-5.6-3.8-9S9.5 5.6 12 3z"/>'),
  cart: s('<path d="M3 4h2l2.4 11h11L21 8H6.2"/><circle cx="9" cy="19.5" r="1.3"/><circle cx="17" cy="19.5" r="1.3"/>'),
  grid: s('<rect x="3" y="3" width="7.5" height="7.5" rx="2"/><rect x="13.5" y="3" width="7.5" height="7.5" rx="2"/><rect x="3" y="13.5" width="7.5" height="7.5" rx="2"/><rect x="13.5" y="13.5" width="7.5" height="7.5" rx="2"/>'),
  device: s('<rect x="6" y="2.5" width="12" height="19" rx="3"/><path d="M10.5 18.5h3"/>'),
  chart: s('<path d="M4 20V4M4 20h16"/><path d="M8 16v-4M12 16V8M16 16v-6M20 16v-9" stroke-width="2.4"/>'),
  candles: s('<path d="M7 3v18M17 3v18"/><rect x="5" y="7" width="4" height="8" rx="1" fill="currentColor"/><rect x="15" y="10" width="4" height="6" rx="1"/>'),
  bolt: s('<path d="M13 2L4 14h7l-1 8 9-12h-7z"/>'),
  pen: s('<path d="M4 20l4-1L19 8l-3-3L5 16z"/><path d="M14 7l3 3"/>'),
  shield: s('<path d="M12 3l8 3v6c0 4.5-3.4 8.2-8 9-4.6-.8-8-4.5-8-9V6z"/><path d="M8.5 12l2.5 2.5 4.5-5"/>'),
  download: s('<path d="M12 4v11M7 10l5 5 5-5M5 20h14"/>'),
  copy: s('<rect x="8" y="8" width="12" height="12" rx="3"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/>'),
  info: s('<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5h.01"/>'),
  warn: s('<path d="M12 3l10 18H2z"/><path d="M12 10v5M12 18h.01"/>'),
  play: s('<path d="M8 5v14l11-7z" fill="currentColor"/>'),
  plus: s('<path d="M12 5v14M5 12h14"/>'),
  search: s('<circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/>'),
  user: s('<circle cx="12" cy="8" r="4"/><path d="M4 21c1.5-4 4.5-6 8-6s6.5 2 8 6"/>'),
  lock: s('<rect x="4" y="10" width="16" height="11" rx="3"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/>'),
  logout: s('<path d="M15 4h4v16h-4M10 8l-4 4 4 4M6 12h10"/>'),
  edit: s('<path d="M4 20h4L19 9l-4-4L4 16z"/>'),
  trash: s('<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/>'),
  eye: s('<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>'),
  star: s('<path d="M12 3l2.8 5.7 6.2.9-4.5 4.4 1 6.2L12 17.3 6.5 20.2l1-6.2L3 9.6l6.2-.9z"/>'),
  clock: s('<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>'),
  book: s('<path d="M4 5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2z"/><path d="M4 19V5M19 19v2H6"/>'),
  handshake: s('<path d="M2 12l4-4 4 2 3-2 3 1 6 3"/><path d="M6 8l-4 4 6 6 2-1 2 2 2-1 2 1 4-4"/><path d="M10 10l3 3"/>'),
  bulb: s('<path d="M9 18h6M10 21h4"/><path d="M12 3a6 6 0 0 0-3.5 10.9c.6.5 1 1.3 1 2.1h5c0-.8.4-1.6 1-2.1A6 6 0 0 0 12 3z"/>'),
  question: s('<circle cx="12" cy="12" r="9"/><path d="M9.5 9a2.6 2.6 0 0 1 5 1c0 1.8-2.5 2.2-2.5 4M12 17.5h.01"/>'),
  briefcase: s('<rect x="3" y="7" width="18" height="13" rx="2.5"/><path d="M9 7V5h6v2M3 13h18"/>'),
  code: s('<path d="M8 8l-4 4 4 4M16 8l4 4-4 4M14 5l-4 14"/>'),
  sparkle: s('<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/>'),
  sun: s('<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>'),
};
export const icon = (name) => ICONS[name] || ICONS.sparkle;

// ---------- Utilities ----------
export const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
export const html = (str) => { const t = document.createElement('template'); t.innerHTML = str.trim(); return t.content.firstElementChild; };
export const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
export const prefersReduced = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;
export const isTouch = () => window.matchMedia('(hover: none), (pointer: coarse)').matches;
export const safeUrl = (u) => {
  const v = String(u || '').trim();
  if (!v) return '';
  if (/^(https?:|mailto:|tel:|sms:)/i.test(v)) return v;
  if (/^[a-z][a-z0-9+.-]*:/i.test(v)) return ''; // javascript:, data: …
  return v; // relative link
};

// Tiny, safe markdown: ## headings, - lists, **bold**, `code`, paragraphs.
export function markdown(md) {
  const inline = (t) => esc(t).replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>').replace(/`(.+?)`/g, '<code>$1</code>').replace(/\*(.+?)\*/g, '<em>$1</em>');
  const out = [];
  let list = null;
  for (const raw of String(md || '').split('\n')) {
    const line = raw.trimEnd();
    if (/^\s*-\s+/.test(line)) { if (!list) { list = []; } list.push(`<li>${inline(line.replace(/^\s*-\s+/, ''))}</li>`); continue; }
    if (list) { out.push(`<ul>${list.join('')}</ul>`); list = null; }
    if (!line.trim()) continue;
    if (line.startsWith('## ')) out.push(`<h2>${inline(line.slice(3))}</h2>`);
    else out.push(`<p>${inline(line)}</p>`);
  }
  if (list) out.push(`<ul>${list.join('')}</ul>`);
  return out.join('');
}

// ---------- Money ----------
export function currency() { return LS.get('mkb.currency', 'KES'); }
export function setCurrency(c) { LS.set('mkb.currency', c); document.dispatchEvent(new CustomEvent('mkb:currency', { detail: c })); }
export function money(kes, C, cur = currency()) {
  const n = Number(kes) || 0;
  if (cur === 'USD') {
    const rate = Number(C?.settings?.currency?.usdRate) || 129;
    const usd = n / rate;
    return '$' + (usd >= 100 ? Math.round(usd).toLocaleString('en-US') : usd.toFixed(usd < 10 ? 2 : 0));
  }
  return 'KES ' + Math.round(n).toLocaleString('en-US');
}

// ---------- Content ----------
let CONTENT = null;
export let MODE = 'static';
async function fetchJSON(u, ms = 3500) {
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), ms);
  try {
    const r = await fetch(u, { signal: ctl.signal, headers: { Accept: 'application/json' }, cache: 'no-cache' });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const ct = r.headers.get('content-type') || '';
    if (!ct.includes('json')) throw new Error('not json');
    return await r.json();
  } finally { clearTimeout(t); }
}
export async function loadContent() {
  if (CONTENT) return CONTENT;
  try {
    const data = await fetchJSON(url('api/content'), 2500);
    if (data && data.settings) { MODE = 'server'; CONTENT = data; }
  } catch { /* no server: static hosting */ }
  if (!CONTENT) CONTENT = await fetchJSON(url('content/content.json'), 8000);
  // Admin preview of unpublished edits (static hosting only, this browser only)
  if (MODE === 'static' && LS.get('mkb.preview', false)) {
    const draft = LS.get('mkb.draft', null);
    if (draft && draft.settings) CONTENT = draft;
  }
  return CONTENT;
}

// ---------- Contact links ----------
export function links(C) {
  const c = C.settings.contact;
  const enc = encodeURIComponent;
  return {
    whatsapp: (text = '') => `https://wa.me/${c.whatsapp}${text ? '?text=' + enc(text) : ''}`,
    email: (subject = '', body = '') => `mailto:${c.email}?subject=${enc(subject)}&body=${enc(body)}`,
    x: (text = '') => (c.xUserId ? `https://x.com/messages/compose?recipient_id=${c.xUserId}${text ? '&text=' + enc(text) : ''}` : `https://x.com/${c.x}`),
    xProfile: () => `https://x.com/${c.x}`,
    instagram: () => `https://ig.me/m/${c.instagram}`,
    instagramProfile: () => `https://www.instagram.com/${c.instagram}/`,
    threads: () => `https://www.threads.net/@${c.threads}`,
    sms: (text = '') => `sms:${c.phone}${text ? '?body=' + enc(text) : ''}`,
    call: () => `tel:${c.phone}`,
  };
}

// Channels a visitor can use to reach Moses directly, with the message pre-filled where the app allows it.
export function channels(C, { text = '', subject = 'Hello from mkb website' } = {}) {
  const L = links(C);
  const c = C.settings.contact;
  return [
    { key: 'whatsapp', label: 'WhatsApp', sub: c.phoneDisplay, icon: 'whatsapp', href: L.whatsapp(text), prefill: true },
    { key: 'instagram', label: 'Instagram DM', sub: '@' + c.instagram, icon: 'instagram', href: L.instagram(), copy: !!text },
    { key: 'x', label: 'X (Twitter) DM', sub: '@' + c.x, icon: 'x', href: L.x(text), prefill: !!c.xUserId },
    { key: 'threads', label: 'Threads', sub: '@' + c.threads, icon: 'threads', href: L.threads(), copy: !!text },
    { key: 'email', label: 'Email', sub: c.email, icon: 'mail', href: L.email(subject, text), prefill: true },
    { key: 'sms', label: 'SMS', sub: c.phoneDisplay, icon: 'sms', href: L.sms(text), prefill: true, mobile: true },
    { key: 'call', label: 'Call', sub: c.phoneDisplay, icon: 'phone', href: L.call() },
  ];
}

export function channelList(C, opts = {}) {
  const list = channels(C, opts).filter((ch) => !opts.only || opts.only.includes(ch.key));
  return `<div class="dm-list">${list.map((ch) => `
    <a class="dm" href="${esc(ch.href)}" target="_blank" rel="noopener" data-channel="${ch.key}" ${ch.copy ? 'data-copy="1"' : ''}>
      <i>${icon(ch.icon)}</i><span>${esc(ch.label)}<small>${esc(ch.sub)}${ch.copy ? ' · message copied for you' : ch.prefill && opts.text ? ' · message ready' : ''}</small></span><span class="go">${ICONS.arrowUR}</span>
    </a>`).join('')}</div>`;
}

export async function copyText(text) {
  try { await navigator.clipboard.writeText(text); return true; } catch {
    const ta = document.createElement('textarea'); ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.appendChild(ta); ta.select();
    let ok = false; try { ok = document.execCommand('copy'); } catch { ok = false; }
    ta.remove(); return ok;
  }
}

// Wire a channel list so Instagram/Threads copy the composed message first.
export function wireChannels(root, text) {
  $$('[data-channel]', root).forEach((a) => a.addEventListener('click', async () => {
    if (a.dataset.copy && text) {
      const ok = await copyText(text);
      toast(ok ? 'Message copied — paste it into the chat.' : 'Opening chat…');
    }
    document.dispatchEvent(new CustomEvent('mkb:channel', { detail: a.dataset.channel }));
  }));
}

// ---------- Submissions ----------
export function makeRef() {
  const a = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
  let r = 'MKB-';
  const buf = new Uint32Array(6); crypto.getRandomValues(buf);
  for (const n of buf) r += a[n % a.length];
  return r;
}
export async function submit(C, payload) {
  const body = { ...payload, page: location.pathname, sentAt: new Date().toISOString() };
  let ref = null; let stored = false;
  if (MODE === 'server') {
    try {
      const r = await fetch(url('api/submissions'), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      const j = await r.json().catch(() => ({}));
      if (r.ok && j.ref) { ref = j.ref; stored = true; } else if (j.error) throw new Error(j.error);
    } catch (e) { if (e.message && !/fetch/i.test(e.message)) throw e; }
  }
  if (!ref) ref = makeRef();
  const ep = C.settings.forms?.endpoint;
  if (!stored && ep && /^https:\/\//.test(ep)) {
    try { await fetch(ep, { method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json' }, body: JSON.stringify({ ...body, ref }) }); stored = true; } catch { /* the DM is the fallback */ }
  }
  const hist = LS.get('mkb.sent', []);
  hist.unshift({ ref, type: payload.type, title: payload.title || payload.subject || '', at: body.sentAt });
  LS.set('mkb.sent', hist.slice(0, 20));
  return { ref, stored };
}

// ---------- UI: toast, modal ----------
export function toast(msg, type = 'ok') {
  let box = $('.toasts');
  if (!box) { box = html('<div class="toasts" role="status" aria-live="polite"></div>'); document.body.appendChild(box); }
  const t = html(`<div class="toast ${type === 'err' ? 'toast--err' : ''}">${type === 'err' ? icon('warn') : icon('check')}<span></span></div>`);
  t.querySelector('svg').style.width = '18px';
  t.querySelector('span').textContent = msg;
  box.appendChild(t);
  setTimeout(() => { t.style.transition = 'opacity .4s, transform .4s'; t.style.opacity = '0'; t.style.transform = 'translateY(10px)'; setTimeout(() => t.remove(), 400); }, 3600);
}

export function openModal(content, { wide = false, label = 'Dialog', onClose } = {}) {
  const m = html(`<div class="modal ${wide ? 'modal--wide' : ''}" role="dialog" aria-modal="true" aria-label="${esc(label)}">
    <div class="modal__backdrop"></div>
    <div class="modal__panel"><button class="icon-btn modal__close" aria-label="Close">${ICONS.close}</button><div class="modal__body"></div></div></div>`);
  const body = $('.modal__body', m);
  if (typeof content === 'string') body.innerHTML = content; else body.appendChild(content);
  document.body.appendChild(m);
  const prevFocus = document.activeElement;
  document.documentElement.style.overflow = 'hidden';
  requestAnimationFrame(() => requestAnimationFrame(() => m.classList.add('is-open')));
  const close = () => {
    m.classList.remove('is-open');
    document.removeEventListener('keydown', onKey);
    setTimeout(() => { m.remove(); if (!$('.modal')) document.documentElement.style.overflow = ''; prevFocus?.focus?.(); }, 450);
    onClose?.();
  };
  const onKey = (e) => {
    if (e.key === 'Escape') close();
    if (e.key === 'Tab') {
      const f = $$('a[href], button:not([disabled]), input:not([type=hidden]), select, textarea, [tabindex]:not([tabindex="-1"])', m).filter((el) => el.offsetParent !== null);
      if (!f.length) return;
      if (e.shiftKey && document.activeElement === f[0]) { e.preventDefault(); f[f.length - 1].focus(); } else if (!e.shiftKey && document.activeElement === f[f.length - 1]) { e.preventDefault(); f[0].focus(); }
    }
  };
  document.addEventListener('keydown', onKey);
  $('.modal__close', m).addEventListener('click', close);
  $('.modal__backdrop', m).addEventListener('click', close);
  setTimeout(() => $('.modal__close', m).focus(), 60);
  attachHover(m);
  return { el: m, body, close };
}

// ---------- Layout ----------
const NAV = [
  ['index.html', 'Home', 'home'], ['about.html', 'About', 'about'], ['services.html', 'Services', 'services'], ['work.html', 'Work', 'work'],
  ['trading.html', 'Trading', 'trading'], ['academy.html', 'Academy', 'academy'], ['packages.html', 'Packages', 'packages'], ['connect.html', 'Connect', 'connect'],
];

function headerHTML(C, page) {
  const st = C.settings;
  const statusText = { open: 'Available', busy: 'Limited availability', closed: 'Fully booked' }[st.availability] || 'Available';
  return `<header class="site-header" id="top">
    <div class="container">
      <a class="logo" href="${url('index.html')}" aria-label="MKB home">
        <img src="${url('assets/brand/mkb-mark.svg')}" alt="" width="42" height="42">
        <span class="logo__text"><b>MKB</b><span>${esc(st.fullName)}</span></span>
      </a>
      <nav class="nav glass" aria-label="Main">
        ${NAV.map(([href, label, key]) => `<a href="${url(href)}" ${key === page ? 'aria-current="page"' : ''}>${label}</a>`).join('')}
      </nav>
      <div class="header-cta">
        <span class="status-pill" data-status="${esc(st.availability)}" title="${esc(st.availabilityNote || '')}"><span class="dot"></span>${esc(statusText)}</span>
        <a class="btn btn--primary btn--sm" href="${url('connect.html')}" data-magnetic>Let's talk ${ICONS.arrow}</a>
        <button class="icon-btn menu-btn" aria-label="Open menu" aria-expanded="false" aria-controls="menu">${ICONS.menu}</button>
      </div>
    </div>
  </header>`;
}

function menuHTML(C, page) {
  const L = links(C);
  return `<div class="menu" id="menu" aria-hidden="true">
    <div class="menu__top">
      <a class="logo" href="${url('index.html')}"><img src="${url('assets/brand/mkb-mark.svg')}" alt="" width="42" height="42"><span class="logo__text"><b>MKB</b></span></a>
      <button class="icon-btn menu-close" aria-label="Close menu">${ICONS.close}</button>
    </div>
    <nav class="menu__links" aria-label="Mobile">
      ${NAV.map(([href, label, key], i) => `<a href="${url(href)}" style="transition-delay:${0.15 + i * 0.04}s" ${key === page ? 'aria-current="page"' : ''}><small>0${i + 1}</small>${label}</a>`).join('')}
    </nav>
    <div class="menu__foot">
      <div class="socials">${socialButtons(C)}</div>
      <a class="btn btn--primary" href="${L.whatsapp('Hi Moses, I found you on your website.')}" target="_blank" rel="noopener">WhatsApp me ${ICONS.arrow}</a>
    </div>
  </div>`;
}

export function socialButtons(C) {
  const L = links(C);
  const c = C.settings.contact;
  return [
    ['whatsapp', L.whatsapp(), 'WhatsApp'], ['instagram', L.instagramProfile(), 'Instagram @' + c.instagram], ['x', L.xProfile(), 'X @' + c.x],
    ['threads', L.threads(), 'Threads @' + c.threads], ['mail', L.email(), 'Email'], ['phone', L.call(), 'Call'],
  ].map(([ic, href, label]) => `<a class="icon-btn" href="${esc(href)}" target="_blank" rel="noopener" aria-label="${esc(label)}" title="${esc(label)}">${icon(ic)}</a>`).join('');
}

function footerHTML(C) {
  const st = C.settings; const c = st.contact; const L = links(C);
  const y = new Date().getFullYear();
  return `<footer class="site-footer">
    <div class="container">
      <div class="footer-grid">
        <div class="footer-brand">
          <a href="${url('index.html')}"><img src="${url('assets/brand/mkb-logo-horizontal.svg')}" alt="MKB — Moses Bright Kang'ara" width="300" height="74" style="height:58px;width:auto"></a>
          <p>${esc(st.seo?.description || '')}</p>
          <div class="socials">${socialButtons(C)}</div>
        </div>
        <div><h4>Explore</h4><ul>
          ${NAV.map(([href, label]) => `<li><a href="${url(href)}">${label}</a></li>`).join('')}
          <li><a href="${url('brand.html')}">Brand book</a></li>
        </ul></div>
        <div><h4>Offers</h4><ul>
          <li><a href="${url('services.html')}">Websites & apps</a></li>
          <li><a href="${url('trading.html#toolkit')}">Trading toolkit</a></li>
          <li><a href="${url('academy.html')}">Courses</a></li>
          <li><a href="${url('packages.html')}">Packages & pricing</a></li>
          <li><a href="${url('connect.html#propose')}">Propose a partnership</a></li>
          <li><a href="${url('connect.html#track')}">Track a proposal</a></li>
        </ul></div>
        <div><h4>Direct line</h4><ul>
          <li><a href="${L.whatsapp('Hi Moses!')}" target="_blank" rel="noopener">WhatsApp · ${esc(c.phoneDisplay)}</a></li>
          <li><a href="${L.call()}">Call · ${esc(c.phoneDisplay)}</a></li>
          <li><a href="${L.email()}">${esc(c.email)}</a></li>
          <li><a href="${L.instagramProfile()}" target="_blank" rel="noopener">Instagram · @${esc(c.instagram)}</a></li>
          <li><a href="${L.xProfile()}" target="_blank" rel="noopener">X · @${esc(c.x)}</a></li>
          <li><a href="${L.threads()}" target="_blank" rel="noopener">Threads · @${esc(c.threads)}</a></li>
        </ul></div>
      </div>
      <div class="footer-giant" aria-hidden="true"><span>M</span><span>K</span><span>B</span></div>
      <div class="footer-bottom">
        <span>© ${y} ${esc(st.fullName)} · MKB. All rights reserved.</span>
        <span>Official accounts are only the ones listed on this site.</span>
        <a href="${url('admin/')}" rel="nofollow">Admin</a>
      </div>
      <p class="risk-note"><b>Risk warning:</b> Trading forex, binary options and crypto involves a high risk of loss and is not suitable for everyone. Content on this site is education, not financial advice. Past results do not guarantee future results. MKB does not manage client funds or accept money for account management.</p>
    </div>
  </footer>`;
}

function dockHTML(C) {
  return `<div class="dock">
    <div class="dock__panel glass" role="dialog" aria-label="Contact Moses">
      <h3>Talk to Moses</h3>
      <p>Pick an app — your message opens straight in my DMs. ${esc(C.settings.responseTime || '')}.</p>
      ${channelList(C, { text: 'Hi Moses, I found you on your website. ', subject: 'Hello from your website' })}
      <a class="btn btn--sm btn--block" style="margin-top:12px" href="${url('connect.html')}">Send a proposal or question ${ICONS.arrow}</a>
    </div>
    <button class="dock__toggle" aria-label="Contact options" aria-expanded="false">
      <svg class="chat" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a8 8 0 0 1-11.6 7.1L4 20.5l1.4-4.6A8 8 0 1 1 21 12z"/></svg>
      <svg class="x" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>
    </button>
  </div>`;
}

function announceHTML(C) {
  const a = C.settings.announcement;
  if (!a?.enabled || !a.text) return '';
  if (SS.get('mkb.announce.closed') === a.text) return '';
  const link = safeUrl(a.link);
  return `<div class="announce" role="region" aria-label="Announcement"><span>${esc(a.text)}</span>${link ? `<a href="${esc(/^https?:/.test(link) ? link : url(link))}">Learn more</a>` : ''}<button class="announce__close" aria-label="Dismiss">${ICONS.close}</button></div>`;
}

function mountLayout(C, page) {
  document.body.insertAdjacentHTML('afterbegin', '<a class="skip" href="#main">Skip to content</a>' + announceHTML(C) + headerHTML(C, page) + menuHTML(C, page));
  const main = $('main');
  (main || document.body).insertAdjacentHTML('afterend', footerHTML(C));
  document.body.insertAdjacentHTML('beforeend', dockHTML(C) + '<div class="wipe" aria-hidden="true"><i></i><i></i><i></i><i></i><i></i></div>');

  // Announcement
  const an = $('.announce');
  an?.querySelector('.announce__close').addEventListener('click', () => { SS.set('mkb.announce.closed', C.settings.announcement.text); an.remove(); });

  // Menu
  const menu = $('#menu'); const mb = $('.menu-btn');
  const setMenu = (open) => {
    menu.classList.toggle('is-open', open); menu.setAttribute('aria-hidden', String(!open)); mb.setAttribute('aria-expanded', String(open));
    document.documentElement.style.overflow = open ? 'hidden' : '';
    if (open) setTimeout(() => $('.menu-close', menu).focus(), 100);
  };
  mb.addEventListener('click', () => setMenu(true));
  $('.menu-close', menu).addEventListener('click', () => { setMenu(false); mb.focus(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && menu.classList.contains('is-open')) setMenu(false); });

  // Dock
  const dock = $('.dock'); const dt = $('.dock__toggle', dock);
  dt.addEventListener('click', () => { const o = !dock.classList.contains('is-open'); dock.classList.toggle('is-open', o); dt.setAttribute('aria-expanded', String(o)); });
  document.addEventListener('click', (e) => { if (dock.classList.contains('is-open') && !dock.contains(e.target)) { dock.classList.remove('is-open'); dt.setAttribute('aria-expanded', 'false'); } });
  wireChannels(dock, 'Hi Moses, I found you on your website. ');

  // Header on scroll
  const header = $('.site-header');
  let lastY = scrollY;
  addEventListener('scroll', () => {
    const y = scrollY;
    header.classList.toggle('is-scrolled', y > 20);
    header.classList.toggle('is-hidden', y > 300 && y > lastY && !menu.classList.contains('is-open'));
    lastY = y;
  }, { passive: true });
}

// ---------- Motion ----------
let revealIO;
export function observeReveals(root = document) {
  if (!revealIO) {
    revealIO = new IntersectionObserver((entries) => {
      for (const e of entries) if (e.isIntersecting) { e.target.classList.add('is-in'); revealIO.unobserve(e.target); }
    }, { rootMargin: '0px 0px -8% 0px', threshold: 0.08 });
  }
  $$('[data-reveal]:not(.is-in), .split-line:not(.is-in)', root).forEach((el) => revealIO.observe(el));
}

// Split a heading's lines into masked spans for a staggered rise.
export function splitLines(el) {
  if (!el || el.dataset.split) return;
  el.dataset.split = '1';
  const parts = el.innerHTML.split(/<br\s*\/?>/i);
  el.innerHTML = parts.map((p, i) => `<span class="split-line" style="--d:${i * 90}"><span>${p}</span></span>`).join('');
}

let cursor, cursorDot;
function initCursor() {
  if (isTouch() || prefersReduced()) return;
  document.body.classList.add('has-cursor');
  cursor = html('<div class="cursor" aria-hidden="true"><span></span></div>');
  cursorDot = html('<div class="cursor-dot" aria-hidden="true"></div>');
  document.body.append(cursor, cursorDot);
  let x = innerWidth / 2, y = innerHeight / 2, cx = x, cy = y;
  addEventListener('pointermove', (e) => { x = e.clientX; y = e.clientY; cursorDot.style.transform = `translate(${x}px, ${y}px) translate(-50%, -50%)`; }, { passive: true });
  document.addEventListener('mouseleave', () => { cursor.classList.add('is-hidden'); cursorDot.classList.add('is-hidden'); });
  document.addEventListener('mouseenter', () => { cursor.classList.remove('is-hidden'); cursorDot.classList.remove('is-hidden'); });
  // Embedded charts (iframes) swallow pointer events: hide the custom cursor over them.
  document.addEventListener('pointerover', (e) => { const over = !!e.target.closest?.('.tv-wrap, iframe'); cursor.classList.toggle('is-hidden', over); cursorDot.classList.toggle('is-hidden', over); });
  const loop = () => { cx += (x - cx) * 0.18; cy += (y - cy) * 0.18; cursor.style.transform = `translate(${cx}px, ${cy}px) translate(-50%, -50%)`; requestAnimationFrame(loop); };
  loop();
  attachHover(document);
}
export function attachHover(root) {
  if (!cursor) return;
  $$('a, button, [data-cursor], .choice span, .filter', root).forEach((el) => {
    if (el.dataset.hoverBound) return; el.dataset.hoverBound = '1';
    el.addEventListener('mouseenter', () => {
      const label = el.dataset.cursor;
      if (label) { cursor.classList.add('is-label'); cursor.querySelector('span').textContent = label; } else cursor.classList.add('is-hover');
    });
    el.addEventListener('mouseleave', () => cursor.classList.remove('is-hover', 'is-label'));
  });
  $$('[data-magnetic]', root).forEach((el) => {
    if (el.dataset.magBound || isTouch()) return; el.dataset.magBound = '1';
    el.addEventListener('pointermove', (e) => {
      const r = el.getBoundingClientRect();
      el.style.transform = `translate(${(e.clientX - r.left - r.width / 2) * 0.25}px, ${(e.clientY - r.top - r.height / 2) * 0.35}px)`;
    });
    el.addEventListener('pointerleave', () => { el.style.transform = ''; });
  });
  $$('.card--glow', root).forEach((el) => {
    if (el.dataset.glowBound) return; el.dataset.glowBound = '1';
    el.addEventListener('pointermove', (e) => { const r = el.getBoundingClientRect(); el.style.setProperty('--mx', e.clientX - r.left + 'px'); el.style.setProperty('--my', e.clientY - r.top + 'px'); });
  });
  $$('[data-tilt]', root).forEach((el) => {
    if (el.dataset.tiltBound || isTouch() || prefersReduced()) return; el.dataset.tiltBound = '1';
    el.style.transformStyle = 'preserve-3d';
    el.addEventListener('pointermove', (e) => {
      const r = el.getBoundingClientRect(); const px = (e.clientX - r.left) / r.width - 0.5; const py = (e.clientY - r.top) / r.height - 0.5;
      el.style.transform = `perspective(900px) rotateY(${px * 8}deg) rotateX(${-py * 8}deg) translateY(-4px)`;
    });
    el.addEventListener('pointerleave', () => { el.style.transform = ''; });
  });
}

function initTransitions() {
  const wipe = $('.wipe');
  if (SS.get('mkb.wipe') === '1') {
    wipe.classList.add('is-on');
    requestAnimationFrame(() => requestAnimationFrame(() => { wipe.style.transformOrigin = 'top'; $$('i', wipe).forEach((i) => { i.style.transformOrigin = 'top'; }); wipe.classList.remove('is-on'); }));
    SS.set('mkb.wipe', '0');
  }
  if (prefersReduced()) return;
  document.addEventListener('click', (e) => {
    const a = e.target.closest('a');
    if (!a || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    if (a.target === '_blank' || a.hasAttribute('download')) return;
    const href = a.getAttribute('href') || '';
    if (!href || href.startsWith('#') || /^(mailto|tel|sms|javascript):/i.test(href)) return;
    const u = new URL(a.href, location.href);
    if (u.origin !== location.origin) return;
    if (u.pathname === location.pathname && u.hash) return;
    e.preventDefault();
    $$('i', wipe).forEach((i) => { i.style.transformOrigin = 'bottom'; });
    wipe.classList.add('is-on');
    SS.set('mkb.wipe', '1');
    setTimeout(() => { location.href = u.href; }, 520);
  });
  addEventListener('pageshow', (e) => { if (e.persisted) wipe.classList.remove('is-on'); });
}

function preloader() {
  if (!document.body.hasAttribute('data-preloader') || SS.get('mkb.loaded') || prefersReduced()) return Promise.resolve();
  SS.set('mkb.loaded', '1');
  const el = html(`<div class="preloader" aria-hidden="true"><div class="preloader__inner">
    <svg viewBox="0 0 120 120"><line x1="21" x2="21" y1="26" y2="104"/><line x1="40.5" x2="40.5" y1="33" y2="74"/><line x1="60" x2="60" y1="47" y2="90"/><line x1="79.5" x2="79.5" y1="33" y2="74"/><line x1="99" x2="99" y1="26" y2="104"/><rect class="fill" x="14" y="36" width="14" height="62" rx="3"/><rect class="fill" x="33.5" y="42" width="14" height="24" rx="3"/><rect x="53" y="56" width="14" height="24" rx="3"/><rect class="fill" x="72.5" y="42" width="14" height="24" rx="3"/><rect class="fill" x="92" y="36" width="14" height="62" rx="3"/></svg>
    <div class="preloader__count">000</div></div></div>`);
  document.body.appendChild(el);
  const count = $('.preloader__count', el);
  return new Promise((res) => {
    const t0 = performance.now();
    const tick = (t) => {
      const p = Math.min(1, (t - t0) / 1500);
      count.textContent = String(Math.round(p * 100)).padStart(3, '0') + ' · BUILD · TRADE · SCALE';
      if (p < 1) requestAnimationFrame(tick); else { el.classList.add('is-done'); setTimeout(() => el.remove(), 1000); res(); }
    };
    requestAnimationFrame(tick);
  });
}

// Animated number counters: <b data-count="12">0</b>
export function counters(root = document) {
  const io = new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (!e.isIntersecting) continue;
      io.unobserve(e.target);
      const el = e.target; const to = Number(el.dataset.count) || 0; const suffix = el.dataset.suffix || '';
      if (prefersReduced()) { el.textContent = to + suffix; continue; }
      const t0 = performance.now();
      const tick = (t) => { const p = Math.min(1, (t - t0) / 1400); const v = Math.round(to * (1 - Math.pow(1 - p, 3))); el.textContent = v + suffix; if (p < 1) requestAnimationFrame(tick); };
      requestAnimationFrame(tick);
    }
  }, { threshold: 0.4 });
  $$('[data-count]', root).forEach((el) => io.observe(el));
}

export function setMeta(C, { title, description } = {}) {
  if (title) document.title = title;
  const d = description || C.settings.seo?.description;
  let m = $('meta[name="description"]');
  if (!m) { m = document.createElement('meta'); m.name = 'description'; document.head.appendChild(m); }
  if (d && !m.content) m.content = d;
}

// ---------- Boot ----------
export async function boot() {
  const page = document.body.dataset.page || '';
  let C;
  try { C = await loadContent(); } catch (e) {
    document.body.insertAdjacentHTML('afterbegin', `<div class="announce">Content could not be loaded (${esc(e.message)}). Please refresh.</div>`);
    throw e;
  }
  mountLayout(C, page);
  initTransitions();
  initCursor();
  setMeta(C);
  await preloader();
  document.body.classList.add('is-ready');
  observeReveals();
  counters();
  return C;
}

// Shared renderers: project, plan and course cards plus their detail modals.
import { esc, icon, ICONS, money, currency, setCurrency, openModal, url, safeUrl, $, $$, attachHover, observeReveals } from './core.js';
import { artFor } from './fx.js';

// ---------- Currency toggle ----------
export function currencyToggle(C) {
  if (C.settings.currency?.showUsdToggle === false) return '';
  const cur = currency();
  return `<div class="seg" role="group" aria-label="Currency">
    <button type="button" data-cur="KES" aria-pressed="${cur === 'KES'}">KES</button>
    <button type="button" data-cur="USD" aria-pressed="${cur === 'USD'}">USD</button></div>`;
}
export function wireCurrency(root, onChange) {
  $$('[data-cur]', root).forEach((b) => b.addEventListener('click', () => setCurrency(b.dataset.cur)));
  document.addEventListener('mkb:currency', (e) => {
    $$('[data-cur]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.cur === e.detail)));
    onChange?.(e.detail);
  });
}

// ---------- Projects ----------
function coverArt(p) {
  const words = String(p.title || '').split(/[\s—-]+/).filter(Boolean);
  const glyph = (words[0] || 'MKB').slice(0, 8);
  const accent = /^#[0-9a-f]{3,8}$/i.test(p.accent || '') ? p.accent : '#2BD69F';
  return `<div class="cover-art" style="background:radial-gradient(120% 90% at 80% 10%, ${accent}33, transparent 60%), linear-gradient(160deg, #0B2219, #000000)">
    <div class="cover-art__grid"></div>
    <div class="cover-art__orb" style="background:${accent};right:-15%;top:-25%"></div>
    <canvas data-art="${esc(p.category)}" data-seed="${esc(p.id)}"></canvas>
    <span class="cover-art__glyph cover-art__glyph--sm">${esc(glyph)}</span></div>`;
}
export function projectCard(p, i = 0) {
  const img = safeUrl(p.image);
  return `<article class="project" data-reveal style="--d:${(i % 2) * 120}" data-project="${esc(p.id)}" tabindex="0" role="button" aria-label="Open case study: ${esc(p.title)}" data-cursor="View">
    <div class="project__cover">
      ${img ? `<img src="${esc(img)}" alt="" loading="lazy">` : coverArt(p)}
      <span class="tag project__badge">${esc(p.category)} · ${esc(p.status || '')}</span>
      <span class="project__open">${ICONS.arrowUR}</span>
    </div>
    <div class="project__meta">
      <div><h3>${esc(p.title)}</h3><p>${esc(p.summary)}</p></div>
      <span class="project__year">${esc(p.year || '')}</span>
    </div>
  </article>`;
}
export function openProject(C, p) {
  const link = safeUrl(p.link); const repo = safeUrl(p.repo);
  const resolve = (u) => (/^(https?:|mailto:)/.test(u) ? u : url(u));
  const m = openModal(`
    <span class="eyebrow">${esc(p.category)} · ${esc(p.year || '')} · ${esc(p.status || '')}</span>
    <h2 style="margin:16px 0 12px">${esc(p.title)}</h2>
    <p class="lead muted">${esc(p.summary)}</p>
    <div class="tags" style="margin:18px 0 26px">${(p.stack || []).map((s) => `<span class="tag">${esc(s)}</span>`).join('')}</div>
    <div class="grid grid-2">
      ${p.problem ? `<div class="card"><span class="eyebrow eyebrow--plain">The challenge</span><p style="margin:12px 0 0">${esc(p.problem)}</p></div>` : ''}
      ${p.solution ? `<div class="card"><span class="eyebrow eyebrow--plain">The build</span><p style="margin:12px 0 0">${esc(p.solution)}</p></div>` : ''}
    </div>
    ${(p.results || []).length ? `<h3 class="h4" style="margin:28px 0 12px">Highlights</h3><ul class="grid" style="gap:10px">${p.results.map((r) => `<li class="row" style="flex-wrap:nowrap;align-items:flex-start"><span class="icon-tile" style="width:32px;height:32px;border-radius:10px;flex-shrink:0">${ICONS.check}</span><span>${esc(r)}</span></li>`).join('')}</ul>` : ''}
    <div class="row" style="margin-top:30px">
      ${link ? `<a class="btn btn--primary" href="${esc(resolve(link))}" ${/^https?:/.test(link) ? 'target="_blank" rel="noopener"' : ''}>Open project ${ICONS.arrow}</a>` : ''}
      ${repo ? `<a class="btn" href="${esc(repo)}" target="_blank" rel="noopener">Source code ${ICONS.arrowUR}</a>` : ''}
      <a class="btn" href="${url('connect.html')}?type=hire&ref=${encodeURIComponent(p.title)}">Start a similar project ${ICONS.arrow}</a>
    </div>`, { label: p.title, wide: true });
  return m;
}
export function wireProjects(C, root, list) {
  const open = (id) => { const p = list.find((x) => x.id === id); if (p) openProject(C, p); };
  $$('[data-project]', root).forEach((el) => {
    el.addEventListener('click', () => open(el.dataset.project));
    el.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(el.dataset.project); } });
  });
  mountArt(root); attachHover(root); observeReveals(root);
  return open;
}

// ---------- Plans ----------
export function priceHTML(C, price, unit) {
  if (unit === 'custom' || !Number(price)) return `<div class="plan__price">Let's talk</div>`;
  const from = unit === 'from' ? '<span class="from">Starting from</span>' : '';
  const suffix = unit && unit !== 'one-off' && unit !== 'from' ? `<small>${esc(unit)}</small>` : '';
  return `<div class="plan__price">${from}<span data-price="${Number(price)}">${money(price, C)}</span>${suffix}</div>`;
}
export function planCard(C, pk, i = 0) {
  return `<article class="card card--glow plan ${pk.highlight ? 'plan--hl' : ''}" data-reveal style="--d:${i * 80}">
    ${pk.highlight ? `<span class="tag plan__flag" style="background:var(--jungle);color:var(--black)">Most popular</span>` : ''}
    <div><span class="eyebrow eyebrow--plain">${esc(pk.group)}</span><h3 style="margin-top:10px">${esc(pk.name)}</h3></div>
    ${priceHTML(C, pk.price, pk.unit)}
    <p>${esc(pk.summary)}</p>
    <ul>${(pk.features || []).map((f) => `<li>${ICONS.check}<span>${esc(f)}</span></li>`).join('')}</ul>
    <button class="btn ${pk.highlight ? 'btn--primary' : ''} btn--block" data-order="${esc(pk.id)}">${pk.unit === 'custom' ? 'Propose terms' : 'Get started'} ${ICONS.arrow}</button>
  </article>`;
}
export function refreshPrices(C, root = document) {
  $$('[data-price]', root).forEach((el) => { el.textContent = money(Number(el.dataset.price), C); });
}

// ---------- Courses ----------
const STATUS = { enrolling: ['Enrolling now', ''], 'coming-soon': ['Coming soon', 'tag--ghost'], closed: ['Closed', 'tag--ghost'] };
export function courseCard(C, cr, i = 0) {
  const [st, cls] = STATUS[cr.status] || STATUS.enrolling;
  return `<article class="card card--glow course" data-reveal style="--d:${(i % 3) * 90}" data-course="${esc(cr.id)}" tabindex="0" role="button" aria-label="Course details: ${esc(cr.title)}" data-cursor="Open">
    <div class="course__cover"><canvas data-art="${esc(cr.category)}" data-seed="${esc(cr.id)}"></canvas><span class="tag ${cls}">${esc(st)}</span></div>
    <div class="course__meta"><span>${esc(cr.category)}</span><span>${esc(cr.level)}</span><span>${esc(cr.duration)}</span></div>
    <h3>${esc(cr.title)}</h3>
    <p>${esc(cr.summary)}</p>
    <div class="course__foot"><span class="price" style="font-size:1.3rem"><span data-price="${Number(cr.price)}">${money(cr.price, C)}</span></span><span class="link-arrow">Details ${ICONS.arrowUR}</span></div>
  </article>`;
}
export function mountArt(root) {
  $$('canvas[data-art]', root).forEach((cv) => { if (!cv.dataset.mounted) { cv.dataset.mounted = '1'; artFor(cv, cv.dataset.art, cv.dataset.seed); } });
}
export function openCourse(C, cr, onEnroll) {
  const [st] = STATUS[cr.status] || STATUS.enrolling;
  const lessons = (cr.modules || []).reduce((a, m) => a + (m.lessons || []).length, 0);
  const m = openModal(`
    <span class="eyebrow">${esc(cr.category)} · ${esc(cr.level)} · ${esc(st)}</span>
    <h2 style="margin:16px 0 12px">${esc(cr.title)}</h2>
    <p class="lead muted">${esc(cr.summary)}</p>
    <div class="row" style="margin:20px 0 28px;gap:24px">
      <span class="price" style="font-size:2rem"><span data-price="${Number(cr.price)}">${money(cr.price, C)}</span></span>
      <span class="chip">${ICONS.clock.replace('<svg', '<svg width="16" height="16"')} ${esc(cr.duration)}</span>
      <span class="chip">${ICONS.book.replace('<svg', '<svg width="16" height="16"')} ${(cr.modules || []).length} modules · ${lessons} lessons</span>
    </div>
    <div class="grid grid-2" style="align-items:start">
      <div><h3 class="h4" style="margin-bottom:14px">You will</h3>
        <ul class="grid" style="gap:10px">${(cr.outcomes || []).map((o) => `<li class="row" style="flex-wrap:nowrap;align-items:flex-start"><span style="color:var(--jungle);width:20px;flex-shrink:0">${ICONS.check}</span><span>${esc(o)}</span></li>`).join('')}</ul></div>
      <div><h3 class="h4" style="margin-bottom:6px">Curriculum</h3>
        <div class="acc">${(cr.modules || []).map((md, i) => `<div class="acc__item"><button class="acc__btn" aria-expanded="${i === 0}"><span><span class="mono accent" style="font-size:.75rem;margin-right:10px">${String(i + 1).padStart(2, '0')}</span>${esc(md.title)}</span><span class="plus"></span></button>
          <div class="acc__panel"><div><ul style="display:grid;gap:6px">${(md.lessons || []).map((l) => `<li>— ${esc(l)}</li>`).join('')}</ul></div></div></div>`).join('')}</div></div>
    </div>
    <div class="row" style="margin-top:30px">
      ${cr.status === 'enrolling' ? `<button class="btn btn--primary btn--lg" data-enroll>Enroll now ${ICONS.arrow}</button>` : `<button class="btn btn--primary btn--lg" data-waitlist>Join the waitlist ${ICONS.arrow}</button>`}
      <a class="btn btn--lg" href="${url('connect.html')}?type=question&q=${encodeURIComponent('Question about the course: ' + cr.title)}">Ask a question</a>
    </div>`, { label: cr.title, wide: true });
  accordion(m.el);
  $('[data-enroll]', m.el)?.addEventListener('click', () => { m.close(); onEnroll?.(cr, 'enroll'); });
  $('[data-waitlist]', m.el)?.addEventListener('click', () => { m.close(); onEnroll?.(cr, 'waitlist'); });
  return m;
}

export function accordion(root) {
  $$('.acc__btn', root).forEach((b) => { if (b.dataset.accBound) return; b.dataset.accBound = '1'; b.addEventListener('click', () => b.setAttribute('aria-expanded', String(b.getAttribute('aria-expanded') !== 'true'))); });
}

export function serviceIcon(name) {
  return icon({ globe: 'globe', cart: 'cart', grid: 'grid', phone: 'device', chart: 'chart', bolt: 'bolt', pen: 'pen', shield: 'shield' }[name] || name);
}

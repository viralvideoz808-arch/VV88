import { boot, $, $$, esc, safeUrl, splitLines, observeReveals, attachHover, icon } from '../core.js';
import { mountBull } from '../bull.js';

const C = await boot();
const A = C.about;
const S = C.settings;

splitLines($('.page-hero .split'));
observeReveals();
$('#about-intro').textContent = A.intro;
$('#about-headline').textContent = A.headline;
$('#about-bio').innerHTML = (A.bio || []).map((p) => `<p>${esc(p)}</p>`).join('');

// Portrait: the owner's photo if one is set in admin, otherwise the bull.
const photo = safeUrl(S.portrait);
if (photo) {
  $('#portrait').innerHTML = `<img src="${esc(photo)}" alt="${esc(S.fullName)}"><span class="portrait__tag chip"><span class="dot pulse"></span>${esc(S.location || '')}</span>`;
} else {
  $('#portrait').innerHTML = `<canvas id="portrait-bull" role="img" aria-label="The MKB Bull"></canvas><span class="portrait__tag chip"><span class="dot pulse"></span>${esc(S.role)}</span>`;
  mountBull($('#portrait-bull'), { zoom: 0.92 });
}

$('#mission').innerHTML = [['Mission', A.mission, 'bolt'], ['Vision', A.vision, 'sun']].map(([t, v, ic], i) => `
  <div class="card card--glow" data-reveal style="--d:${i * 120};min-height:240px;display:flex;flex-direction:column;gap:18px">
    <span class="icon-tile">${icon(ic)}</span>
    <span class="eyebrow eyebrow--plain">${t}</span>
    <p class="h3" style="margin:0;color:var(--paper)">${esc(v)}</p>
  </div>`).join('');

$('#values').innerHTML = (A.values || []).map((v, i) => `
  <div class="card card--glow" data-reveal style="--d:${i * 90}">
    <span class="mono accent" style="font-size:.8rem">0${i + 1}</span>
    <h3 class="h4" style="margin:22px 0 10px;font-family:var(--f-display);font-size:1.3rem">${esc(v.title)}</h3>
    <p class="muted" style="margin:0">${esc(v.text)}</p>
  </div>`).join('');

$('#timeline').innerHTML = (A.timeline || []).map((t, i) => `
  <li class="timeline__item" data-reveal style="--d:${i * 100}">
    <span class="timeline__label mono">${esc(t.label)}</span>
    <h3 class="timeline__title">${esc(t.title)}</h3>
    <p class="muted">${esc(t.text)}</p>
  </li>`).join('');

// ---- Skills ----
const LEVELS = { Learning: 1, Intermediate: 2, Advanced: 3, Expert: 4 };
const launched = new Date(S.launchedAt || '2026-11-02').getTime();
const isNew = (s) => { const t = new Date(s.addedAt || 0).getTime(); return t > launched && Date.now() - t < 60 * 864e5; };
const cats = ['All', ...new Set(C.skills.map((s) => s.category))];
let active = 'All';
const renderSkills = () => {
  const list = C.skills.filter((s) => active === 'All' || s.category === active)
    .sort((a, b) => (isNew(b) - isNew(a)) || (LEVELS[b.level] || 0) - (LEVELS[a.level] || 0));
  $('#skills-grid').innerHTML = list.map((s, i) => `
    <article class="skill" data-reveal style="--d:${(i % 4) * 60}">
      <div class="row" style="justify-content:space-between;gap:8px"><span class="tag tag--ghost">${esc(s.category)}</span>${isNew(s) ? '<span class="tag">New</span>' : ''}</div>
      <h3>${esc(s.name)}</h3>
      <p>${esc(s.note || '')}</p>
      <div class="skill__level" aria-label="${esc(s.level)}"><span class="skill__bars">${[1, 2, 3, 4].map((n) => `<i class="${n <= (LEVELS[s.level] || 1) ? 'on' : ''}"></i>`).join('')}</span><span class="mono">${esc(s.level)}</span></div>
    </article>`).join('');
  observeReveals($('#skills-grid'));
};
$('#skill-filters').innerHTML = cats.map((c) => `<button class="filter ${c === 'All' ? 'is-active' : ''}" role="tab" aria-selected="${c === 'All'}" data-cat="${esc(c)}">${esc(c)}<sup>${c === 'All' ? C.skills.length : C.skills.filter((s) => s.category === c).length}</sup></button>`).join('');
$$('#skill-filters [data-cat]').forEach((b) => b.addEventListener('click', () => {
  active = b.dataset.cat;
  $$('#skill-filters [data-cat]').forEach((x) => { x.classList.toggle('is-active', x === b); x.setAttribute('aria-selected', String(x === b)); });
  renderSkills();
}));
renderSkills();
const newest = C.skills.filter(isNew);
if (newest.length) $('#skills-note').textContent = `Always learning — ${newest.length} new skill${newest.length > 1 ? 's' : ''} added recently: ${newest.map((s) => s.name).join(', ')}.`;

mountBull($('#bull-mini'), { zoom: 0.9 });
attachHover(document);
observeReveals();

import { boot, $, $$, esc, links, socialButtons, attachHover, observeReveals, splitLines, url, isTouch, counters } from '../core.js';
import { mountBull } from '../bull.js';
import { candles, code, network } from '../fx.js';
import { projectCard, wireProjects, planCard, currencyToggle, wireCurrency, refreshPrices, courseCard, mountArt, openCourse, accordion } from '../render.js';
import { crypto, fmtPrice, fmtChange } from '../markets.js';
import { startCheckout } from '../checkout.js';

const C = await boot();
const S = C.settings;

// ---- Hero ----
$('[data-bind="availabilityNote"]').textContent = S.availabilityNote || S.role;
$('#hero-socials').innerHTML = socialButtons(C).replaceAll('class="icon-btn"', 'class=""');
$('#cta-wa').href = links(C).whatsapp('Hi Moses, I have an idea I would like to discuss.');

const bull = mountBull($('#bull'));
const gazeDot = $('#hud-gaze'), gazeT = $('#hud-gaze-t'), moodDot = $('#hud-mood'), moodT = $('#hud-mood-t');
let lastHud = 0;
bull.onReading(({ yawDeg, mood }) => {
  const now = performance.now(); if (now - lastHud < 60) return; lastHud = now;
  gazeDot.style.left = `${50 + (yawDeg / 30) * 50}%`;
  gazeT.textContent = Math.abs(yawDeg) < 2 ? '0° centre' : `${Math.abs(yawDeg)}° ${yawDeg < 0 ? 'left' : 'right'}`;
  moodDot.style.left = `${mood * 100}%`;
  const pct = Math.round(mood * 100);
  moodT.textContent = pct >= 50 ? `Bullish ${pct}%` : `Bearish ${100 - pct}%`;
  moodT.style.color = pct >= 50 ? 'var(--mint)' : 'var(--bear)';
});
if (isTouch()) $('.hud span[style]').textContent = 'Tap the bull to make it snort';

const fx = { code, candles, network };
$$('canvas[data-fx]').forEach((cv, i) => fx[cv.dataset.fx](cv, { seed: 'home' + i }));

// ---- Skills marquee ----
const skills = (C.skills || []).map((s) => s.name);
const items = skills.concat(skills).map((n) => `<span class="marquee__item">${esc(n)}</span>`).join('');
$('#skills-marquee').innerHTML = items;

// ---- Pillars: tap/hover to expand ----
splitLines($('.split'));
$$('.pillar').forEach((p) => p.addEventListener('mouseenter', () => { $$('.pillar').forEach((x) => x.classList.toggle('is-active', x === p)); }));

// ---- Stats: counted from the real content ----
const tools = 7;
$('#stats').innerHTML = [
  [C.skills.length, 'skills & growing'], [C.projects.length, 'projects shipped'], [tools, 'free trading tools'], [C.courses.length, 'courses in the academy'],
].map(([n, l]) => `<div class="stat"><b data-count="${n}">0</b><span>${esc(l)}</span></div>`).join('');
counters($('#stats'));

// ---- Work ----
const featured = C.projects.filter((p) => p.featured).slice(0, 4);
$('#featured-projects').innerHTML = (featured.length ? featured : C.projects.slice(0, 4)).map(projectCard).join('');
wireProjects(C, $('#featured-projects'), C.projects);

// ---- Testimonials (only real ones added in admin) ----
const quotes = (C.testimonials || []).filter((t) => t.published && t.quote);
if (quotes.length) {
  $('#testimonials').hidden = false;
  $('#quotes').innerHTML = quotes.map((t, i) => `<figure class="card quote" data-reveal style="--d:${(i % 3) * 90};margin:0"><p>${esc(t.quote)}</p><footer><b>${esc(t.name)}</b>${esc(t.role || '')}</footer></figure>`).join('');
}

// ---- Live markets ----
(async () => {
  try {
    const rows = await crypto(C.trading?.watchlist?.crypto);
    $('#live-rows').innerHTML = rows.slice(0, 4).map((r) => `<div class="live-card__row"><span>${esc(r.symbol)}</span><b>${fmtPrice(r.price)}</b><span class="${r.change >= 0 ? 'up' : 'down'}" style="font-family:var(--f-mono);font-size:.75rem">${fmtChange(r.change)}</span></div>`).join('');
    const t = rows.map((r) => `<span class="ticker__item"><b>${esc(r.symbol)}/USD</b> ${fmtPrice(r.price)} <span class="${r.change >= 0 ? 'up' : 'down'}">${fmtChange(r.change)}</span></span>`).join('');
    $('#ticker .ticker__track').innerHTML = t + t + t + t;
  } catch {
    $('#live-rows').innerHTML = `<div class="live-card__row"><span class="muted">Live prices are unavailable right now.</span></div>`;
    $('#ticker').hidden = true;
  }
})();

// ---- Packages: one highlight per group ----
$('#home-currency').innerHTML = currencyToggle(C);
const groups = ['Build', 'Trade', 'Grow'];
const picks = groups.map((g) => C.packages.find((p) => p.group === g && p.highlight) || C.packages.find((p) => p.group === g)).filter(Boolean);
$('#home-plans').innerHTML = picks.map((p, i) => planCard(C, p, i)).join('');
wireCurrency(document, () => refreshPrices(C));
$$('[data-order]').forEach((b) => b.addEventListener('click', () => {
  const pk = C.packages.find((p) => p.id === b.dataset.order);
  if (pk.unit === 'custom') { location.href = url('connect.html') + '?type=partnership&ref=' + encodeURIComponent(pk.name); return; }
  startCheckout(C, { kind: 'package', id: pk.id, name: pk.name, price: pk.price, unit: pk.unit, group: pk.group });
}));

// ---- Courses ----
const courses = [...C.courses].sort((a, b) => (a.status === 'enrolling' ? -1 : 1) - (b.status === 'enrolling' ? -1 : 1)).slice(0, 3);
$('#home-courses').innerHTML = courses.map((c, i) => courseCard(C, c, i)).join('');
mountArt($('#home-courses'));
const enroll = (cr, kind) => startCheckout(C, { kind: kind === 'waitlist' ? 'waitlist' : 'course', id: cr.id, name: cr.title, price: cr.price, unit: 'one-off' });
$$('[data-course]').forEach((el) => {
  const open = () => openCourse(C, C.courses.find((c) => c.id === el.dataset.course), enroll);
  el.addEventListener('click', open);
  el.addEventListener('keydown', (e) => { if (e.key === 'Enter') open(); });
});

// ---- Ask ----
const sugg = ['Partnership offer', 'Website for my business', 'How do I start trading?'];
$('#ask-suggest').innerHTML = sugg.map((s) => `<button type="button" class="chip" data-s="${esc(s)}">${esc(s)}</button>`).join('');
$$('#ask-suggest [data-s]').forEach((b) => b.addEventListener('click', () => { $('#qa-input').value = b.dataset.s + ': '; $('#qa-input').focus(); }));
$('#quick-ask').addEventListener('submit', (e) => {
  e.preventDefault();
  const q = $('#qa-input').value.trim(); if (!q) return;
  const type = /partner/i.test(q) ? 'partnership' : /website|app|build/i.test(q) ? 'hire' : 'question';
  location.href = url('connect.html') + `?type=${type}&q=${encodeURIComponent(q)}#propose`;
});
$('#home-qa').innerHTML = C.qa.filter((q) => q.published).slice(0, 4).map((q, i) => `<div class="acc__item"><button class="acc__btn" aria-expanded="${i === 0}">${esc(q.question)}<span class="plus"></span></button><div class="acc__panel"><div><p>${esc(q.answer)}</p></div></div></div>`).join('');
accordion($('#home-qa'));

attachHover(document);
observeReveals();

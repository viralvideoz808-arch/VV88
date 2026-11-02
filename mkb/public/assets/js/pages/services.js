import { boot, $, $$, esc, money, url, splitLines, observeReveals, attachHover, toast } from '../core.js';
import { code } from '../fx.js';
import { serviceIcon, currencyToggle, wireCurrency, refreshPrices, accordion } from '../render.js';

const C = await boot();
splitLines($('.page-hero .split'));
observeReveals();
code($('.page-hero__art canvas'), { seed: 'services' });

// ---- Services ----
const priceSpan = (kes) => `<span data-price="${Number(kes)}">${money(kes, C)}</span>`;
$('#svc-currency').innerHTML = currencyToggle(C);
$('#services').innerHTML = C.services.map((s, i) => `
  <article class="card card--glow service" data-reveal style="--d:${(i % 3) * 90}" id="${esc(s.id)}">
    <div class="row" style="justify-content:space-between"><span class="icon-tile">${serviceIcon(s.icon)}</span><span class="mono muted" style="font-size:.75rem">${esc(s.timeline || '')}</span></div>
    <h3>${esc(s.title)}</h3>
    <p>${esc(s.summary)}</p>
    <ul>${(s.deliverables || []).map((d) => `<li>${esc(d)}</li>`).join('')}</ul>
    <div class="tags">${(s.stack || []).map((t) => `<span class="tag tag--ghost">${esc(t)}</span>`).join('')}</div>
    <div class="service__foot">
      <span class="price"><small>From</small> ${priceSpan(s.startingPrice)}<small>${esc(s.priceUnit || '')}</small></span>
      <a class="btn btn--sm" href="${url('connect.html')}?type=hire&service=${encodeURIComponent(s.id)}">Get a quote</a>
    </div>
  </article>`).join('');
wireCurrency(document, () => { refreshPrices(C); estimate(); });

// ---- Estimator ----
const svc = (id) => C.services.find((s) => s.id === id)?.startingPrice;
const TYPES = [
  { id: 'website', label: 'Business website', base: svc('sv-websites') || 25000, pages: 5, perPage: 2500, weeks: [1, 2] },
  { id: 'store', label: 'Online store', base: svc('sv-ecommerce') || 60000, pages: 8, perPage: 3000, weeks: [3, 5] },
  { id: 'system', label: 'Web app / system', base: svc('sv-webapps') || 80000, pages: 8, perPage: 6000, weeks: [4, 8] },
  { id: 'mobile', label: 'Mobile app', base: svc('sv-mobile') || 120000, pages: 10, perPage: 7000, weeks: [6, 10] },
  { id: 'tool', label: 'Trading tool / bot', base: svc('sv-trading') || 30000, pages: 3, perPage: 5000, weeks: [1, 4] },
];
const FEATURES = [
  ['mpesa', 'M-Pesa payments', 12000], ['cards', 'Card payments', 8000], ['booking', 'Bookings / appointments', 10000], ['accounts', 'User accounts & login', 15000],
  ['admin', 'Admin dashboard', 20000], ['cms', 'Blog / content manager', 8000], ['sms', 'SMS & WhatsApp alerts', 9000], ['multilang', 'Swahili + English', 7000],
  ['seo', 'SEO launch pack', 6000], ['copy', 'Copywriting', 7500], ['logo', 'Logo & brand kit', 15000], ['care', '3 months maintenance', 10500],
];
$('#est-type').innerHTML = TYPES.map((t, i) => `<label class="choice"><input type="radio" name="type" value="${t.id}" ${i === 0 ? 'checked' : ''}><span>${esc(t.label)}</span></label>`).join('');
$('#est-features').innerHTML = FEATURES.map(([id, l]) => `<label class="choice"><input type="checkbox" name="f" value="${id}"><span>${esc(l)}</span></label>`).join('');

let last = null;
function estimate() {
  const form = $('#est-form');
  const type = TYPES.find((t) => t.id === form.type.value) || TYPES[0];
  const pages = Number($('#est-pages').value);
  $('#est-pages-v').textContent = pages;
  const feats = $$('input[name=f]:checked', form).map((i) => FEATURES.find((f) => f[0] === i.value));
  const design = Number($('#est-design').value); const rush = Number($('#est-speed').value);
  const extraPages = Math.max(0, pages - type.pages);
  const lines = [[`${type.label} (base, ${type.pages} pages/screens)`, type.base], ...(extraPages ? [[`${extraPages} extra page${extraPages > 1 ? 's' : ''}`, extraPages * type.perPage]] : []), ...feats.map(([, l, p]) => [l, p])];
  const sub = lines.reduce((a, [, p]) => a + p, 0);
  const total = sub * design * rush;
  const lo = Math.round((total * 0.9) / 1000) * 1000; const hi = Math.round((total * 1.2) / 1000) * 1000;
  const wk = [type.weeks[0] + Math.floor(feats.length / 3), type.weeks[1] + Math.ceil(feats.length / 3) + Math.floor(extraPages / 10)];
  const fast = rush > 1 ? wk.map((w) => Math.max(1, Math.round(w * 0.7))) : wk;
  $('#est-range').innerHTML = `${money(lo, C)} <span class="muted">–</span> ${money(hi, C)}`;
  $('#est-time').textContent = `≈ ${fast[0]}–${fast[1]} weeks${rush > 1 ? ' (rush)' : ''}`;
  $('#est-lines').innerHTML = lines.map(([l, p]) => `<li><span>${esc(l)}</span><span class="mono">${money(p, C)}</span></li>`).join('') +
    (design !== 1 ? `<li><span>Design level</span><span class="mono">×${design}</span></li>` : '') + (rush !== 1 ? `<li><span>Rush delivery</span><span class="mono">×${rush}</span></li>` : '');
  last = { type: type.label, pages, features: feats.map((f) => f[1]), design: $('#est-design').selectedOptions[0].text, rush: rush > 1, lo, hi, weeks: fast };
}
$('#est-form').addEventListener('input', estimate);
$('#est-form').addEventListener('change', estimate);
$('#est-form').addEventListener('submit', (e) => e.preventDefault());
estimate();
$('#est-send').addEventListener('click', () => {
  if (!last) return;
  const summary = `Project estimate from your website:\n• Type: ${last.type}\n• Pages/screens: ${last.pages}\n• Features: ${last.features.join(', ') || 'none selected'}\n• Design: ${last.design}${last.rush ? '\n• Rush delivery' : ''}\n• Estimate: KES ${last.lo.toLocaleString()} – ${last.hi.toLocaleString()} (${last.weeks[0]}–${last.weeks[1]} weeks)`;
  try { sessionStorage.setItem('mkb.estimate', summary); } catch { /* ignore */ }
  toast('Estimate attached — tell me a bit more.');
  location.href = url('connect.html') + '?type=hire&estimate=1#propose';
});

// ---- Stack marquee ----
const stack = [...new Set(C.services.flatMap((s) => s.stack || []))];
$('#stack').innerHTML = stack.concat(stack).map((t) => `<span class="marquee__item" style="font-size:clamp(1.1rem,2vw,1.6rem)">${esc(t)}</span>`).join('');

// ---- FAQ ----
const FAQ = [
  ['How much does a website cost?', 'Most business websites fall between the Starter and Business Pro packages. Use the estimator above for a ballpark; you get a fixed quote after a short call.'],
  ['Who owns the website and code?', 'You do. Once the project is paid in full, the domain, content and code are yours, and I hand over all logins.'],
  ['Do you handle domain, hosting and email?', 'Yes. I can register your domain, set up fast hosting and business email, or work with what you already have.'],
  ['Can customers pay with M-Pesa?', 'Yes. I integrate M-Pesa (Daraja) STK push, Paybill and Till payments, plus card payments through providers like Paystack.'],
  ['How do payments work?', '50% to start and 50% at launch for most projects. Larger projects can be split into milestones.'],
  ['What happens after launch?', 'Every package includes free support for a period. After that, the Care & Maintenance plan covers updates, backups, security and small changes.'],
  ['Can you redesign my existing site?', 'Yes — I can rebuild it from scratch or improve what you have, keeping your search rankings intact.'],
];
$('#faq').innerHTML = FAQ.map(([q, a], i) => `<div class="acc__item"><button class="acc__btn" aria-expanded="${i === 0}">${esc(q)}<span class="plus"></span></button><div class="acc__panel"><div><p>${esc(a)}</p></div></div></div>`).join('');
accordion($('#faq'));

attachHover(document);
observeReveals();
if (location.hash) setTimeout(() => document.querySelector(location.hash)?.scrollIntoView({ behavior: 'smooth' }), 300);

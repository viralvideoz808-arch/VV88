import { boot, $, $$, esc, url, splitLines, observeReveals, attachHover } from '../core.js';
import { planCard, currencyToggle, wireCurrency, refreshPrices } from '../render.js';
import { startCheckout } from '../checkout.js';

const C = await boot();
splitLines($('.page-hero .split'));

const META = {
  Build: { title: 'Build', lead: 'Websites, stores and apps — from a sharp first site to a full product.', id: 'build' },
  Trade: { title: 'Trade', lead: 'One-on-one guidance, mentorship and custom tools for traders. Education, not signals.', id: 'trade' },
  Grow: { title: 'Grow', lead: 'Strategy, brand and partnerships for businesses ready to scale.', id: 'grow' },
};
const groups = [...new Set(C.packages.map((p) => p.group))];
$('#groups').innerHTML = groups.map((g, i) => `<button type="button" aria-pressed="${i === 0}" data-g="${esc((META[g] || { id: g.toLowerCase() }).id)}">${esc(g)}</button>`).join('');
$('#cur').innerHTML = currencyToggle(C);
$('#package-groups').innerHTML = groups.map((g) => {
  const m = META[g] || { title: g, lead: '', id: g.toLowerCase() };
  return `<section class="section" id="${esc(m.id)}" style="padding-top:30px">
    <div class="container">
      <div class="section-head"><div><span class="eyebrow">${esc(g)}</span><h2 class="h2">${esc(m.title)}</h2><p>${esc(m.lead)}</p></div></div>
      <div class="plans">${C.packages.filter((p) => p.group === g).map((p, i) => planCard(C, p, i)).join('')}</div>
    </div></section>`;
}).join('');

$$('#groups [data-g]').forEach((b) => b.addEventListener('click', () => {
  $$('#groups [data-g]').forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
  document.getElementById(b.dataset.g)?.scrollIntoView({ behavior: 'smooth' });
}));
const io = new IntersectionObserver((es) => es.forEach((e) => { if (e.isIntersecting) $$('#groups [data-g]').forEach((x) => x.setAttribute('aria-pressed', String(x.dataset.g === e.target.id))); }), { rootMargin: '-40% 0px -55% 0px' });
$$('#package-groups section').forEach((s) => io.observe(s));

wireCurrency(document, () => refreshPrices(C));
$$('[data-order]').forEach((b) => b.addEventListener('click', () => {
  const pk = C.packages.find((p) => p.id === b.dataset.order);
  if (pk.unit === 'custom') { location.href = url('connect.html') + '?type=partnership&ref=' + encodeURIComponent(pk.name) + '#propose'; return; }
  startCheckout(C, { kind: 'package', id: pk.id, name: pk.name, price: pk.price, unit: pk.unit, group: pk.group });
}));

const P = C.settings.payments || {};
const methods = [];
if (P.mpesaType && P.mpesaNumber) methods.push(`<span class="chip">M-Pesa ${esc({ till: 'Till', paybill: 'Paybill', phone: 'Send Money' }[P.mpesaType] || '')} · <b class="mono">${esc(P.mpesaNumber)}</b></span>`);
else methods.push('<span class="chip">M-Pesa</span>');
if (P.paystackPublicKey) methods.push('<span class="chip">Card &amp; M-Pesa via Paystack</span>');
methods.push('<span class="chip">Bank transfer on request</span>');
$('#pay-methods').innerHTML = `<div class="row" style="gap:8px">${methods.join('')}</div>`;

attachHover(document);
observeReveals();
if (location.hash) setTimeout(() => document.querySelector(location.hash)?.scrollIntoView({ behavior: 'smooth' }), 300);

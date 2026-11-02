import { boot, $, $$, esc, splitLines, observeReveals, attachHover } from '../core.js';
import { candles } from '../fx.js';
import { courseCard, mountArt, openCourse, currencyToggle, wireCurrency, refreshPrices, accordion } from '../render.js';
import { startCheckout } from '../checkout.js';

const C = await boot();
splitLines($('.page-hero .split'));
observeReveals();
candles($('.page-hero__art canvas'), { seed: 'academy', bars: 34 });

const enroll = (cr, kind) => startCheckout(C, { kind: kind === 'waitlist' ? 'waitlist' : 'course', id: cr.id, name: cr.title, price: cr.price, unit: 'one-off' });
const order = { enrolling: 0, 'coming-soon': 1, closed: 2 };
const all = [...C.courses].sort((a, b) => (order[a.status] ?? 3) - (order[b.status] ?? 3));
const cats = ['All', ...new Set(all.map((c) => c.category))];
let active = 'All';
const render = () => {
  const list = all.filter((c) => active === 'All' || c.category === active);
  $('#course-grid').innerHTML = list.map((c, i) => courseCard(C, c, i)).join('') || '<div class="empty">No courses here yet.</div>';
  mountArt($('#course-grid'));
  $$('#course-grid [data-course]').forEach((el) => {
    const go = () => openCourse(C, all.find((c) => c.id === el.dataset.course), enroll);
    el.addEventListener('click', go);
    el.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(); } });
  });
  attachHover($('#course-grid')); observeReveals($('#course-grid'));
};
$('#filters').innerHTML = cats.map((c) => `<button class="filter ${c === 'All' ? 'is-active' : ''}" role="tab" aria-selected="${c === 'All'}" data-cat="${esc(c)}">${esc(c)}<sup>${c === 'All' ? all.length : all.filter((x) => x.category === c).length}</sup></button>`).join('');
$$('#filters [data-cat]').forEach((b) => b.addEventListener('click', () => {
  active = b.dataset.cat;
  $$('#filters [data-cat]').forEach((x) => { x.classList.toggle('is-active', x === b); x.setAttribute('aria-selected', String(x === b)); });
  render();
}));
$('#cur').innerHTML = currencyToggle(C);
wireCurrency(document, () => refreshPrices(C));
render();

const FAQ = [
  ['How are classes delivered?', 'Live online sessions with recordings, a private group for questions, and assignments with feedback. You can learn from your phone or laptop.'],
  ['I am a complete beginner. Is that okay?', 'Yes. Beginner courses start from zero. If you are unsure which one fits, send me a message first and I will recommend one.'],
  ['How do I pay?', 'Enroll on this page to get a reference code, then pay by M-Pesa or card using only the details shown on this site. I confirm every payment.'],
  ['Will trading courses make me profitable?', 'No course can promise that, and you should be wary of anyone who does. The trading courses teach how markets work, risk management and a process — the skills that give you a fair chance.'],
  ['Do I get a certificate?', 'Yes, you receive an MKB certificate of completion after finishing the course project.'],
];
$('#faq').innerHTML = FAQ.map(([q, a], i) => `<div class="acc__item"><button class="acc__btn" aria-expanded="${i === 0}">${esc(q)}<span class="plus"></span></button><div class="acc__panel"><div><p>${esc(a)}</p></div></div></div>`).join('');
accordion($('#faq'));
attachHover(document);

const id = decodeURIComponent(location.hash.slice(1));
const deep = all.find((c) => c.id === id);
if (deep) setTimeout(() => openCourse(C, deep, enroll), 400);

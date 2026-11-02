import { boot, $, $$, esc, splitLines, observeReveals } from '../core.js';
import { network } from '../fx.js';
import { projectCard, wireProjects } from '../render.js';

const C = await boot();
splitLines($('.page-hero .split'));
observeReveals();
network($('.page-hero__art canvas'), { seed: 'work', count: 22 });

const all = C.projects;
const cats = ['All', ...new Set(all.map((p) => p.category))];
let active = 'All';
let open = () => {};
const render = () => {
  const list = all.filter((p) => active === 'All' || p.category === active);
  $('#projects').innerHTML = list.map(projectCard).join('') || '<div class="empty">No projects in this category yet.</div>';
  $('#count').textContent = `${list.length} project${list.length === 1 ? '' : 's'}`;
  open = wireProjects(C, $('#projects'), all);
};
$('#filters').innerHTML = cats.map((c) => `<button class="filter ${c === 'All' ? 'is-active' : ''}" role="tab" aria-selected="${c === 'All'}" data-cat="${esc(c)}">${esc(c)}<sup>${c === 'All' ? all.length : all.filter((p) => p.category === c).length}</sup></button>`).join('');
$$('#filters [data-cat]').forEach((b) => b.addEventListener('click', () => {
  active = b.dataset.cat;
  $$('#filters [data-cat]').forEach((x) => { x.classList.toggle('is-active', x === b); x.setAttribute('aria-selected', String(x === b)); });
  render();
}));
render();
// Deep link: work.html#pj-pumpscan opens that case study
const id = decodeURIComponent(location.hash.slice(1));
if (id && all.some((p) => p.id === id)) setTimeout(() => open(id), 400);

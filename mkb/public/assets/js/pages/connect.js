import { boot, $, $$, esc, url, splitLines, observeReveals, attachHover, ICONS, icon, toast, submit, channels, channelList, wireChannels, MODE, LS, copyText } from '../core.js';
import { network } from '../fx.js';
import { accordion } from '../render.js';

const C = await boot();
const S = C.settings;
splitLines($('.page-hero .split'));
network($('.page-hero__art canvas'), { seed: 'connect', count: 24 });
observeReveals();

// ---------- Intents ----------
const svcOptions = [...C.services.map((s) => s.title), 'Not sure yet'];
const BUDGET = ['Under KES 50,000', 'KES 50,000 – 250,000', 'KES 250,000 – 1M', 'Over KES 1M', 'Equity / revenue share', 'Not sure yet'];
const INTENTS = {
  question: {
    title: 'Ask me anything', icon: 'question', desc: 'Business, tech, trading or the academy — ask and get a straight answer.', label: 'Question',
    fields: [
      { k: 'topic', label: 'Topic', type: 'choice', options: ['Business', 'Tech / IT', 'Trading', 'Academy', 'Other'], required: true },
      { k: 'question', label: 'Your question', type: 'textarea', required: true, placeholder: 'Ask anything business-related…' },
    ],
  },
  partnership: {
    title: 'Propose a partnership', icon: 'handshake', desc: 'Joint ventures, investment, sponsorship, reselling or tech partnerships.', label: 'Partnership proposal',
    fields: [
      { k: 'organisation', label: 'Company / brand', type: 'text', placeholder: 'Who you represent' },
      { k: 'partnershipType', label: 'Type of partnership', type: 'choice', options: ['Joint venture', 'Investment', 'Reseller / affiliate', 'Sponsorship', 'Tech partner', 'Other'], required: true },
      { k: 'offer', label: 'What you bring', type: 'textarea', required: true, placeholder: 'Your audience, product, capital, distribution, skills…' },
      { k: 'ask', label: 'What you want from MKB', type: 'textarea', required: true, placeholder: 'Tech build, promotion, mentorship, capital, co-ownership…' },
      { k: 'budget', label: 'Budget / investment', type: 'select', options: BUDGET },
      { k: 'timeline', label: 'Timeline', type: 'select', options: ['As soon as possible', '1–3 months', '3–6 months', 'Just exploring'] },
      { k: 'link', label: 'Website or deck link (optional)', type: 'text', placeholder: 'https://' },
    ],
  },
  idea: {
    title: 'Pitch a business idea', icon: 'bulb', desc: "Have an idea that needs tech, a partner or a push? Let's see how far it can go.", label: 'Business idea',
    fields: [
      { k: 'ideaTitle', label: 'Idea in one line', type: 'text', required: true, placeholder: 'e.g. An app that connects farmers to restaurants' },
      { k: 'industry', label: 'Industry', type: 'select', options: ['Retail & e-commerce', 'Finance & fintech', 'Agriculture', 'Education', 'Health', 'Transport & logistics', 'Real estate', 'Entertainment & media', 'Trading & crypto', 'Other'] },
      { k: 'stage', label: 'Stage', type: 'choice', options: ['Just an idea', 'Research done', 'Prototype / MVP', 'Already selling'], required: true },
      { k: 'problem', label: 'The problem it solves', type: 'textarea', required: true, placeholder: 'Who has the problem, and how do they deal with it today?' },
      { k: 'needs', label: 'What you need', type: 'multi', options: ['Tech build', 'Funding', 'Mentorship', 'Marketing', 'Co-founder / partner'] },
      { k: 'budget', label: 'Budget available', type: 'select', options: BUDGET },
    ],
  },
  hire: {
    title: 'Hire me for a project', icon: 'briefcase', desc: 'Websites, apps, systems, trading tools or automation.', label: 'Project enquiry',
    fields: [
      { k: 'service', label: 'Service', type: 'select', options: svcOptions, required: true },
      { k: 'project', label: 'About the project', type: 'textarea', required: true, placeholder: 'What should it do? Who is it for? Any examples you like?' },
      { k: 'budget', label: 'Budget', type: 'select', options: BUDGET },
      { k: 'deadline', label: 'Deadline', type: 'select', options: ['Within 2 weeks', 'Within a month', '1–3 months', 'Flexible'] },
      { k: 'links', label: 'Links (optional)', type: 'text', placeholder: 'Current website, designs you like…' },
    ],
  },
};

// ---------- State ----------
const params = new URLSearchParams(location.search);
const st = { step: 1, intent: null, data: {}, you: { name: '', phone: '', email: '', channel: 'whatsapp' }, message: '', edited: false };
const startIntent = params.get('type');
if (INTENTS[startIntent]) { st.intent = startIntent; st.step = 2; }
const q = params.get('q');
if (q) { if (st.intent === 'question' || !st.intent) { st.intent = st.intent || 'question'; st.step = 2; st.data.question = q; } else if (st.intent === 'hire') st.data.project = q; else if (st.intent === 'partnership') st.data.offer = q; else if (st.intent === 'idea') st.data.ideaTitle = q; }
const svcId = params.get('service');
if (svcId) { const s = C.services.find((x) => x.id === svcId); if (s) st.data.service = s.title; }
const refTitle = params.get('ref');
if (refTitle) { if (st.intent === 'hire') st.data.project = `Something similar to "${refTitle}". `; if (st.intent === 'partnership') st.data.partnershipType = 'Other'; st.data.offer = st.data.offer || (st.intent === 'partnership' ? `Regarding: ${refTitle}. ` : ''); }
if (params.get('estimate')) { try { const est = sessionStorage.getItem('mkb.estimate'); if (est) { st.intent = 'hire'; st.step = 2; st.data.project = est + '\n\nMore about my project: '; } } catch { /* ignore */ } }
const saved = LS.get('mkb.you', null); if (saved) Object.assign(st.you, saved);

// ---------- Message ----------
function compose(ref) {
  const I = INTENTS[st.intent];
  if (!I) return '';
  const lines = [`Hi Moses — ${I.label.toLowerCase()} from your website.`];
  if (ref) lines.push(`Ref: ${ref}`);
  lines.push('');
  for (const f of I.fields) {
    const v = st.data[f.k];
    const val = Array.isArray(v) ? v.join(', ') : (v || '').trim();
    if (val) lines.push(f.type === 'textarea' ? `${f.label}:\n${val}` : `${f.label}: ${val}`);
  }
  lines.push('');
  if (st.you.name) lines.push(`From: ${st.you.name}`);
  if (st.you.phone) lines.push(`Phone/WhatsApp: ${st.you.phone}`);
  if (st.you.email) lines.push(`Email: ${st.you.email}`);
  return lines.join('\n').replace(/\n{3,}/g, '\n\n').trim();
}
const preview = () => { $('#preview').textContent = st.intent ? (st.edited ? st.message : compose()) : 'Pick what brings you here — your message builds itself as you go.'; };

// ---------- Rendering ----------
const fieldHTML = (f) => {
  const v = st.data[f.k];
  const id = 'f-' + f.k;
  const req = f.required ? ' <span class="accent">*</span>' : '';
  if (f.type === 'textarea') return `<div class="field full"><label for="${id}">${esc(f.label)}${req}</label><textarea class="textarea" id="${id}" data-k="${f.k}" maxlength="3000" placeholder="${esc(f.placeholder || '')}" ${f.required ? 'required' : ''}>${esc(v || '')}</textarea></div>`;
  if (f.type === 'text') return `<div class="field full"><label for="${id}">${esc(f.label)}${req}</label><input class="input" id="${id}" data-k="${f.k}" maxlength="300" value="${esc(v || '')}" placeholder="${esc(f.placeholder || '')}" ${f.required ? 'required' : ''}></div>`;
  if (f.type === 'select') return `<div class="field"><label for="${id}">${esc(f.label)}${req}</label><select class="select" id="${id}" data-k="${f.k}"><option value="">Choose…</option>${f.options.map((o) => `<option ${o === v ? 'selected' : ''}>${esc(o)}</option>`).join('')}</select></div>`;
  if (f.type === 'choice' || f.type === 'multi') {
    const multi = f.type === 'multi'; const sel = multi ? (v || []) : v;
    return `<div class="field full"><span class="label">${esc(f.label)}${req}</span><div class="choice-grid">${f.options.map((o) => `<label class="choice"><input type="${multi ? 'checkbox' : 'radio'}" name="${f.k}" data-k="${f.k}" value="${esc(o)}" ${(multi ? sel.includes(o) : sel === o) ? 'checked' : ''}><span>${esc(o)}</span></label>`).join('')}</div></div>`;
  }
  return '';
};

function render() {
  $$('#steps li').forEach((li) => { const n = Number(li.dataset.step); li.classList.toggle('is-current', n === st.step); li.classList.toggle('is-done', n < st.step); });
  const body = $('#panel-body');
  const I = INTENTS[st.intent];
  if (st.step === 1) {
    body.innerHTML = `<h2 class="h3">What brings you here?</h2><p class="muted" style="margin:6px 0 22px">Choose one — you can change it later.</p>
      <div class="intents">${Object.entries(INTENTS).map(([k, it]) => `<button type="button" class="intent ${st.intent === k ? 'is-on' : ''}" data-intent="${k}"><span class="icon-tile">${icon(it.icon)}</span><b>${esc(it.title)}</b><small>${esc(it.desc)}</small><span class="intent__go">${ICONS.arrow}</span></button>`).join('')}</div>`;
    $$('[data-intent]', body).forEach((b) => b.addEventListener('click', () => { if (st.intent !== b.dataset.intent) { st.edited = false; } st.intent = b.dataset.intent; st.step = 2; render(); }));
  } else if (st.step === 2) {
    body.innerHTML = `<div class="row" style="justify-content:space-between"><h2 class="h3">${esc(I.title)}</h2><button class="link-arrow" type="button" data-back>Change</button></div>
      <form class="form-grid" style="margin-top:22px" novalidate>${I.fields.map(fieldHTML).join('')}
      <div class="full row panel__nav"><button type="button" class="btn" data-back>Back</button><button class="btn btn--primary" type="submit">Continue ${ICONS.arrow}</button></div></form>`;
    wireFields(body);
    $('form', body).addEventListener('submit', (e) => {
      e.preventDefault();
      const missing = I.fields.filter((f) => f.required && !(Array.isArray(st.data[f.k]) ? st.data[f.k].length : String(st.data[f.k] || '').trim()));
      if (missing.length) { toast(`Please fill in: ${missing.map((f) => f.label).join(', ')}`, 'err'); return; }
      st.step = 3; render();
    });
  } else if (st.step === 3) {
    const list = channels(C).filter((c) => !c.mobile);
    body.innerHTML = `<h2 class="h3">About you</h2><p class="muted" style="margin:6px 0 22px">So I know who I'm talking to and how to reply.</p>
      <form class="form-grid" novalidate>
        <div class="field"><label for="y-name">Your name <span class="accent">*</span></label><input class="input" id="y-name" data-y="name" autocomplete="name" maxlength="80" value="${esc(st.you.name)}" required></div>
        <div class="field"><label for="y-phone">WhatsApp / phone</label><input class="input" id="y-phone" data-y="phone" autocomplete="tel" inputmode="tel" maxlength="30" value="${esc(st.you.phone)}" placeholder="07xx xxx xxx"></div>
        <div class="field full"><label for="y-email">Email</label><input class="input" id="y-email" data-y="email" type="email" autocomplete="email" maxlength="120" value="${esc(st.you.email)}"></div>
        <div class="field full"><span class="label">Send it to me on</span><div class="choice-grid">${list.map((c) => `<label class="choice"><input type="radio" name="channel" data-y="channel" value="${c.key}" ${st.you.channel === c.key ? 'checked' : ''}><span>${esc(c.label.replace(' DM', ''))}</span></label>`).join('')}</div></div>
        <input class="hp" name="website" tabindex="-1" autocomplete="off" aria-hidden="true">
        <div class="full row panel__nav"><button type="button" class="btn" data-prev>Back</button><button class="btn btn--primary" type="submit">Review ${ICONS.arrow}</button></div>
      </form>`;
    $$('[data-y]', body).forEach((el) => el.addEventListener('input', () => { if (el.type === 'radio') { if (el.checked) st.you.channel = el.value; } else st.you[el.dataset.y] = el.value; preview(); }));
    $$('[data-y][type=radio]', body).forEach((el) => el.addEventListener('change', () => { if (el.checked) st.you.channel = el.value; }));
    $('form', body).addEventListener('submit', (e) => {
      e.preventDefault();
      if ($('input[name=website]', body).value) return;
      if (!st.you.name.trim()) { toast('Please add your name.', 'err'); return; }
      if (st.you.email && !/^\S+@\S+\.\S+$/.test(st.you.email)) { toast('That email looks wrong.', 'err'); return; }
      LS.set('mkb.you', { name: st.you.name, phone: st.you.phone, email: st.you.email, channel: st.you.channel });
      st.step = 4; render();
    });
  } else if (st.step === 4) {
    if (!st.edited) st.message = compose();
    const ch = channels(C).find((c) => c.key === st.you.channel) || channels(C)[0];
    body.innerHTML = `<h2 class="h3">Review &amp; send</h2><p class="muted" style="margin:6px 0 18px">Edit anything you like. When you press send you'll get a reference code and ${esc(ch.label)} opens with this message ready.</p>
      <label class="sr-only" for="msg">Message</label><textarea class="textarea mono" id="msg" style="min-height:260px;font-size:.85rem">${esc(st.message)}</textarea>
      <div class="full row panel__nav" style="margin-top:16px"><button type="button" class="btn" data-prev>Back</button><button class="btn btn--primary btn--lg" type="button" data-send>${icon(ch.icon)} Send via ${esc(ch.label)}</button></div>
      <p class="muted" style="font-size:.78rem;margin-top:12px">${MODE === 'server' ? 'A copy is also saved to my inbox so nothing gets lost.' : 'Your message is sent through the app you chose.'} ${esc(S.responseTime || '')}.</p>`;
    $('#msg').addEventListener('input', (e) => { st.message = e.target.value; st.edited = true; preview(); });
    $('[data-send]', body).addEventListener('click', send);
  } else if (st.step === 5) {
    const ref = st.ref;
    const text = st.finalText;
    body.innerHTML = `<div class="sent"><span class="sent__check">${ICONS.check}</span><h2 class="h3">Ready — finish in your DMs</h2>
      <p class="muted">If the app didn't open, pick one below. Your message is already written${st.copied ? ' and copied' : ''}.</p>
      <div class="copy-row" style="margin:16px 0"><span>Reference: <b>${esc(ref)}</b></span><button class="btn btn--sm" data-copy-ref>Copy</button></div></div>
      ${channelList(C, { text, subject: `${INTENTS[st.intent].label} — ${ref}` })}
      <div class="row" style="margin-top:18px"><button class="btn" data-new>Send another</button><a class="btn" href="#track">Track it</a></div>`;
    wireChannels(body, text);
    $('[data-copy-ref]', body).addEventListener('click', async () => { await copyText(ref); toast('Reference copied'); });
    $('[data-new]', body).addEventListener('click', () => { Object.assign(st, { step: 1, intent: null, data: {}, edited: false, message: '' }); render(); });
    renderHistory();
  }
  $$('[data-back]', body).forEach((b) => b.addEventListener('click', () => { st.step = 1; render(); }));
  $$('[data-prev]', body).forEach((b) => b.addEventListener('click', () => { st.step -= 1; render(); }));
  attachHover(body);
  preview();
}

function wireFields(root) {
  $$('[data-k]', root).forEach((el) => {
    const k = el.dataset.k;
    const handler = () => {
      if (el.type === 'checkbox') st.data[k] = $$(`input[name="${k}"]:checked`, root).map((x) => x.value);
      else if (el.type === 'radio') { if (el.checked) st.data[k] = el.value; } else st.data[k] = el.value;
      st.edited = false; preview();
    };
    el.addEventListener('input', handler); el.addEventListener('change', handler);
  });
}

async function send() {
  const btn = $('[data-send]'); btn.disabled = true;
  const I = INTENTS[st.intent];
  const draft = st.message;
  let ref;
  try {
    ({ ref } = await submit(C, {
      type: st.intent, title: st.data.ideaTitle || st.data.organisation || st.data.service || (st.data.question || st.data.project || '').slice(0, 80),
      name: st.you.name.trim(), phone: st.you.phone.trim(), email: st.you.email.trim(), channel: st.you.channel,
      fields: st.data, message: draft,
    }));
  } catch (e) { btn.disabled = false; toast(e.message || 'Could not send — try a DM below.', 'err'); return; }
  // Put the reference in the message the visitor sends.
  const text = draft.includes('Ref:') ? draft : draft.replace(/^(.*\n)/, `$1Ref: ${ref}\n`);
  st.ref = ref; st.finalText = text;
  const ch = channels(C, { text, subject: `${I.label} — ${ref}` }).find((c) => c.key === st.you.channel);
  if (ch?.copy) st.copied = await copyText(text);
  st.step = 5; render();
  if (ch) {
    if (/^https?:/.test(ch.href)) { const w = window.open(ch.href, '_blank'); if (w) { try { w.opener = null; } catch { /* cross-origin */ } } else toast('Pop-up blocked — tap the platform below.', 'err'); }
    else location.href = ch.href;
  }
}

$$('[data-intent-link]').forEach((a) => a.addEventListener('click', () => { st.intent = a.dataset.intentLink; st.step = 2; st.edited = false; render(); }));
render();

// ---------- Direct DMs ----------
$('#response-time').textContent = `Tap a platform — it opens a chat with me straight away. ${S.responseTime || ''}.`;
$('#dm-grid').innerHTML = channels(C, { text: 'Hi Moses, I found you on your website. ' }).map((c, i) => `
  <a class="dm-card" href="${esc(c.href)}" target="_blank" rel="noopener" data-reveal style="--d:${i * 60}" data-channel="${c.key}" ${c.copy ? 'data-copy="1"' : ''} data-cursor="Open">
    <span class="dm-card__icon">${icon(c.icon)}</span><b>${esc(c.label)}</b><span class="mono muted">${esc(c.sub)}</span><span class="dm-card__go">${ICONS.arrowUR}</span>
  </a>`).join('');
wireChannels($('#dm-grid'), 'Hi Moses, I found you on your website. ');

// ---------- Tracker ----------
const STATUS_TEXT = { new: 'Received', reviewing: 'Under review', accepted: 'Accepted — expect a call', declined: 'Not a fit right now', done: 'Completed' };
function renderHistory() {
  const hist = LS.get('mkb.sent', []);
  $('#track-history').innerHTML = hist.length ? `<span class="label">Sent from this device</span><ul style="display:grid;gap:6px;margin-top:10px">${hist.slice(0, 5).map((h) => `<li class="row" style="justify-content:space-between;font-size:.85rem"><button class="mono accent" data-ref="${esc(h.ref)}">${esc(h.ref)}</button><span class="muted">${esc(h.type)} · ${new Date(h.at).toLocaleDateString()}</span></li>`).join('')}</ul>` : '';
  $$('#track-history [data-ref]').forEach((b) => b.addEventListener('click', () => { $('#track-ref').value = b.dataset.ref; track(); }));
}
async function track() {
  const ref = $('#track-ref').value.trim().toUpperCase();
  const out = $('#track-out');
  if (!/^MKB-[A-Z0-9]{6}$/.test(ref)) { out.innerHTML = `<div class="notice">${ICONS.info}<span>References look like MKB-7Q2K9P.</span></div>`; return; }
  if (MODE !== 'server') {
    out.innerHTML = `<div class="notice notice--green">${ICONS.info}<span>Live tracking isn't switched on for this site yet. Send your reference on WhatsApp and I'll update you directly.</span></div>
      <a class="btn btn--sm" style="margin-top:12px" target="_blank" rel="noopener" href="https://wa.me/${esc(S.contact.whatsapp)}?text=${encodeURIComponent('Hi Moses, checking on my submission ' + ref)}">Ask on WhatsApp</a>`;
    return;
  }
  out.innerHTML = '<div class="skeleton" style="min-height:80px"></div>';
  try {
    const r = await fetch(url('api/track') + '?ref=' + encodeURIComponent(ref));
    const j = await r.json();
    if (!r.ok) throw new Error(j.error || 'Not found');
    const order = ['new', 'reviewing', j.status === 'declined' ? 'declined' : j.status === 'done' ? 'done' : 'accepted'];
    const idx = order.indexOf(j.status);
    out.innerHTML = `<div class="tracker">${order.map((s, i) => `<div class="tracker__step ${i <= idx ? 'is-done' : ''} ${i === idx ? 'is-now' : ''}"><i></i><span>${esc(STATUS_TEXT[s])}</span></div>`).join('')}</div>
      <p class="muted" style="font-size:.85rem;margin:14px 0 0">${esc(j.typeLabel || '')} · sent ${new Date(j.createdAt).toLocaleDateString()} · updated ${new Date(j.updatedAt).toLocaleDateString()}</p>
      ${j.publicNote ? `<div class="notice notice--green" style="margin-top:12px">${ICONS.chat}<span>${esc(j.publicNote)}</span></div>` : ''}`;
  } catch (e) { out.innerHTML = `<div class="notice">${ICONS.warn}<span>${esc(e.message)}. Check the code, or ask me on WhatsApp.</span></div>`; }
}
$('#track-form').addEventListener('submit', (e) => { e.preventDefault(); track(); });
renderHistory();

// ---------- Q&A ----------
const qa = C.qa.filter((x) => x.published);
const qaTopics = ['All', ...new Set(qa.map((x) => x.topic).filter(Boolean))];
let qaTopic = 'All';
const renderQA = () => {
  const term = $('#qa-search').value.trim().toLowerCase();
  const list = qa.filter((x) => (qaTopic === 'All' || x.topic === qaTopic) && (!term || (x.question + ' ' + x.answer).toLowerCase().includes(term)));
  $('#qa-list').innerHTML = list.map((x, i) => `<div class="acc__item"><button class="acc__btn" aria-expanded="${i === 0 && !term ? 'true' : term ? 'true' : 'false'}"><span><span class="tag tag--ghost" style="margin-right:10px">${esc(x.topic || 'General')}</span>${esc(x.question)}</span><span class="plus"></span></button><div class="acc__panel"><div><p>${esc(x.answer)}</p></div></div></div>`).join('') || '<div class="empty">No answers match — ask your own question.</div>';
  accordion($('#qa-list'));
};
$('#qa-filters').innerHTML = qaTopics.map((t) => `<button class="filter ${t === 'All' ? 'is-active' : ''}" data-t="${esc(t)}">${esc(t)}</button>`).join('');
$$('#qa-filters [data-t]').forEach((b) => b.addEventListener('click', () => { qaTopic = b.dataset.t; $$('#qa-filters [data-t]').forEach((x) => x.classList.toggle('is-active', x === b)); renderQA(); }));
$('#qa-search').addEventListener('input', renderQA);
renderQA();

// ---------- Verify ----------
const c = S.contact;
$('#verify-list').innerHTML = [
  ['whatsapp', 'WhatsApp & calls', c.phoneDisplay], ['mail', 'Email', c.email], ['instagram', 'Instagram', '@' + c.instagram],
  ['x', 'X (Twitter)', '@' + c.x], ['threads', 'Threads', '@' + c.threads], ['globe', 'Website', location.host || 'this site'],
].map(([ic, l, v]) => `<li><span class="icon-tile" style="width:40px;height:40px;border-radius:12px">${icon(ic)}</span><span><small class="muted">${esc(l)}</small><b>${esc(v)}</b></span>${ICONS.check}</li>`).join('');

attachHover(document);
observeReveals();
if (location.hash && location.hash !== '#propose') setTimeout(() => document.querySelector(location.hash)?.scrollIntoView({ behavior: 'smooth' }), 300);
else if (st.step > 1 || location.hash === '#propose') setTimeout(() => $('#propose').scrollIntoView({ behavior: 'smooth' }), 300);

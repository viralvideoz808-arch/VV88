import { boot, $, $$, esc, url, splitLines, observeReveals, attachHover, toast, copyText, ICONS } from '../core.js';
import { mountBull } from '../bull.js';

const C = await boot();
const S = C.settings; const c = S.contact;
splitLines($('.page-hero .split'));

$('#essence').innerHTML = [
  ['Essence', 'Build. Trade. Scale.'], ['Promise', 'Technology that works and knowledge that\'s honest.'],
  ['Personality', 'Disciplined · Bold · Honest · Generous'], ['Audience', 'Business owners, new traders and founders with ideas.'],
].map(([k, v], i) => `<div class="card" data-reveal style="--d:${i * 80}"><span class="eyebrow eyebrow--plain">${k}</span><p class="h4" style="margin:14px 0 0;color:var(--paper);font-family:var(--f-display);font-size:1.25rem">${esc(v)}</p></div>`).join('');

// ---- Colours ----
const COLOURS = [
  ['MKB Black', '#040605', 'Backgrounds, the stage for everything'], ['Dark Green', '#0F3D2E', 'Surfaces, cards, depth'], ['Deep Green', '#0A2A1F', 'Gradients, badges'],
  ['Jungle Green', '#29AB87', 'Primary: buttons, highlights, the bull'], ['Mint', '#6FE3BF', 'Glows, links, live data'], ['Paper', '#ECF5F1', 'Text and light backgrounds'],
];
const rgb = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
const cmyk = ([r, g, b]) => { const k = 1 - Math.max(r, g, b) / 255; if (k >= 1) return [0, 0, 0, 100]; return [r, g, b].map((v) => Math.round(((1 - v / 255 - k) / (1 - k)) * 100)).concat(Math.round(k * 100)); };
$('#swatches').innerHTML = COLOURS.map(([n, h, use], i) => {
  const [r, g, b] = rgb(h); const light = (r * 299 + g * 587 + b * 114) / 1000 > 140;
  return `<button class="swatch" data-hex="${h}" data-reveal style="--d:${i * 60};background:${h};color:${light ? '#040605' : '#ECF5F1'}">
    <b>${n}</b><span class="mono">${h}</span><span class="mono">RGB ${r} ${g} ${b}</span><span class="mono">CMYK ${cmyk([r, g, b]).join(' ')}</span><small>${use}</small></button>`;
}).join('');
$$('.swatch').forEach((s) => s.addEventListener('click', async () => { await copyText(s.dataset.hex); toast(`${s.dataset.hex} copied`); }));

mountBull($('#bull-brand'), { zoom: 0.9 });

// ---- Voice ----
const VOICE = [
  ['Clear over clever', 'Say what it does and what it costs.', '"A website that takes M-Pesa payments, ready in two weeks."', '"Revolutionary next-gen digital synergy."'],
  ['Confident, not hype', 'Back claims with numbers or proof.', '"Risk 1% per trade. Here\'s why."', '"Make 300% a month guaranteed!"'],
  ['Honest about risk', 'Every trading message carries the risk.', '"Most traders lose money. These tools help you manage that risk."', '"Easy money from home."'],
  ['Warm and direct', 'Talk like a partner, not a corporation.', '"Got an idea? Let\'s see how far it can go."', '"Kindly submit your enquiry via the appropriate channel."'],
];
$('#voice-grid').innerHTML = VOICE.map(([t, d, yes, no], i) => `<div class="card" data-reveal style="--d:${(i % 2) * 90}"><h3 class="h4" style="font-family:var(--f-display);font-size:1.25rem">${t}</h3><p class="muted" style="margin:6px 0 16px">${d}</p>
  <div class="say say--yes">${ICONS.check}<span>${esc(yes)}</span></div><div class="say say--no">${ICONS.close}<span>${esc(no)}</span></div></div>`).join('');

const site = location.host ? location.origin.replace(/^https?:\/\//, '') : 'your-site';
const BIOS = [
  ['Instagram', 150, `Developer · Trader · Business Builder 🇰🇪\nWebsites & apps that sell\nForex · Binary · Crypto education\nDM to build, learn or partner 👇`],
  ['X (Twitter)', 160, `Building websites & apps that sell. Trading Forex, Binary & Crypto with discipline. Open to partnerships. Build. Trade. Scale. 🇰🇪`],
  ['Threads', 150, `Developer, trader & business builder. I share what I build, how I trade and what I learn. Build. Trade. Scale.`],
  ['WhatsApp About', 139, `MKB · Websites, apps & trading education. Build. Trade. Scale.`],
  ['LinkedIn headline', 220, `Founder, MKB · Web & App Developer · Forex, Binary & Crypto Trader and Educator · Helping businesses build, trade and scale`],
  ['Elevator pitch', 600, `I'm Moses Bright Kang'ara — MKB. I build websites and apps that help businesses sell, I trade Forex, Binary and Crypto with a risk-first approach and teach what I've learned, and I partner with people who have ideas worth building. Build. Trade. Scale.`],
];
$('#bios').innerHTML = BIOS.map(([n, max, text], i) => `<div class="card bio" data-reveal style="--d:${(i % 2) * 80}">
  <div class="row" style="justify-content:space-between"><b>${n}</b><span class="mono ${[...text].length > max ? 'down' : 'muted'}" style="font-size:.72rem">${[...text].length} / ${max}</span></div>
  <pre>${esc(text)}</pre><button class="btn btn--sm" data-copy="${i}">${ICONS.copy.replace('<svg', '<svg width="15" height="15"')} Copy</button></div>`).join('');
$$('[data-copy]').forEach((b) => b.addEventListener('click', async () => { await copyText(BIOS[Number(b.dataset.copy)][2]); toast('Bio copied'); }));

// ---- Kit ----
const KIT = [
  ['Profile picture', '1080 × 1080 · Instagram, X, Threads, WhatsApp', 'assets/kit/mkb-profile-1080.png', 'square'],
  ['X header', '1500 × 500 · X (Twitter) banner', 'assets/kit/mkb-x-header-1500x500.png', 'wide'],
  ['Instagram post', '1080 × 1350 · launch announcement', 'assets/kit/mkb-ig-post-1080x1350.png', 'tall'],
  ['Story / Status', '1080 × 1920 · IG story, WhatsApp status', 'assets/kit/mkb-story-1080x1920.png', 'story'],
  ['Business card · front', '1050 × 600 · 3.5 × 2 in at 300 dpi', 'assets/kit/mkb-card-front.png', 'card'],
  ['Business card · back', '1050 × 600 · contact side', 'assets/kit/mkb-card-back.png', 'card'],
  ['Link preview', '1200 × 630 · shared links (Open Graph)', 'assets/img/og-image.png', 'wide'],
  ['Logo · horizontal', 'Transparent PNG, 2400 px wide', 'assets/kit/mkb-logo-horizontal.png', 'wide'],
];
$('#kit-grid').innerHTML = KIT.map(([n, d, src, shape], i) => `<figure class="kit kit--${shape}" data-reveal style="--d:${(i % 4) * 70}">
  <div class="kit__img"><img src="${src}" alt="${esc(n)}" loading="lazy"></div>
  <figcaption><span><b>${esc(n)}</b><small>${esc(d)}</small></span><a class="icon-btn" href="${src}" download aria-label="Download ${esc(n)}">${ICONS.download}</a></figcaption></figure>`).join('');

// ---- Email signature ----
const abs = (p) => (location.protocol.startsWith('http') ? url(p) : p);
const sigHTML = `<table cellpadding="0" cellspacing="0" style="font-family:Arial,Helvetica,sans-serif;color:#0A2A1F"><tr>
  <td style="padding-right:14px;vertical-align:top"><img src="${abs('assets/kit/mkb-mark-1024.png')}" width="64" height="64" alt="MKB" style="display:block;border-radius:14px"></td>
  <td style="border-left:3px solid #29AB87;padding-left:14px;vertical-align:top">
    <div style="font-size:16px;font-weight:bold;color:#040605">${esc(S.fullName)}</div>
    <div style="font-size:12px;color:#1F7A5C;font-weight:bold;letter-spacing:1px;text-transform:uppercase">${esc(S.role)}</div>
    <div style="font-size:13px;margin-top:6px">📞 <a href="tel:${esc(c.phone)}" style="color:#040605;text-decoration:none">${esc(c.phoneDisplay)}</a> &nbsp;·&nbsp; 💬 <a href="https://wa.me/${esc(c.whatsapp)}" style="color:#040605;text-decoration:none">WhatsApp</a></div>
    <div style="font-size:13px">✉️ <a href="mailto:${esc(c.email)}" style="color:#040605;text-decoration:none">${esc(c.email)}</a></div>
    <div style="font-size:12px;margin-top:6px"><a href="https://www.instagram.com/${esc(c.instagram)}/" style="color:#29AB87">Instagram</a> · <a href="https://x.com/${esc(c.x)}" style="color:#29AB87">X</a> · <a href="https://www.threads.net/@${esc(c.threads)}" style="color:#29AB87">Threads</a>${location.protocol.startsWith('http') ? ` · <a href="${esc(location.origin)}" style="color:#29AB87">${esc(site)}</a>` : ''}</div>
    <div style="font-size:11px;color:#5B7068;margin-top:6px;letter-spacing:2px">BUILD · TRADE · SCALE</div>
  </td></tr></table>`;
$('#sig').innerHTML = sigHTML;
$('#copy-sig').addEventListener('click', async () => {
  try {
    await navigator.clipboard.write([new ClipboardItem({ 'text/html': new Blob([sigHTML], { type: 'text/html' }), 'text/plain': new Blob([`${S.fullName}\n${S.role}\n${c.phoneDisplay} · ${c.email}`], { type: 'text/plain' }) })]);
    toast('Signature copied — paste it into Gmail settings.');
  } catch {
    const r = document.createRange(); r.selectNode($('#sig')); const sel = getSelection(); sel.removeAllRanges(); sel.addRange(r);
    const ok = document.execCommand('copy'); sel.removeAllRanges();
    toast(ok ? 'Signature copied.' : 'Select the signature and copy it manually.', ok ? 'ok' : 'err');
  }
});

attachHover(document);
observeReveals();

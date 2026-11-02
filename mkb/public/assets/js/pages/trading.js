import { boot, $, $$, esc, markdown, url, splitLines, observeReveals, attachHover, openModal, ICONS, toast, LS } from '../core.js';
import { candles } from '../fx.js';
import { crypto, fx, fmtPrice, fmtChange, coinSymbol } from '../markets.js';
import { accordion } from '../render.js';

const C = await boot();
const T = C.trading;
const usdKes = Number(C.settings.currency?.usdRate) || 129;
splitLines($('.page-hero .split'));
$('#trade-intro').textContent = T.intro;
candles($('.trade-hero__bg'), { seed: 'desk', bars: 60, speed: 1.2 });
observeReveals();

const num = (v, d = 0) => { const n = parseFloat(String(v).replace(/,/g, '')); return Number.isFinite(n) ? n : d; };
const usd = (v, dp = 2) => (v < 0 ? '−$' : '$') + Math.abs(v).toLocaleString('en-US', { minimumFractionDigits: dp, maximumFractionDigits: dp });
const kes = (v) => 'KES ' + Math.round(v).toLocaleString('en-US');
const pct = (v, dp = 1) => (v * 100).toFixed(dp) + '%';

// ---------- Live data ----------
let RATES = null; let COINS = [];
const fxReady = fx().then((d) => { RATES = d; return d; });
const coinsReady = crypto(T.watchlist?.crypto).then((c) => { COINS = c; return c; }).catch(() => []);

coinsReady.then((rows) => {
  if (!rows.length) { $('#ticker').hidden = true; return; }
  const t = rows.map((r) => `<span class="ticker__item"><b>${esc(r.symbol)}/USD</b> ${fmtPrice(r.price)} <span class="${r.change >= 0 ? 'up' : 'down'}">${fmtChange(r.change)}</span></span>`).join('');
  $('#ticker .ticker__track').innerHTML = t + t + t + t;
});

// ---------- Markets ----------
const markets = T.markets || [];
$('#market-tabs').innerHTML = markets.map((m, i) => `<button type="button" role="tab" aria-pressed="${i === 0}" aria-selected="${i === 0}" data-m="${esc(m.key)}">${esc(m.title)}</button>`).join('');
async function marketWidget(key) {
  if (key === 'forex') {
    const d = await fxReady; const r = { USD: 1, ...d.rates };
    const pairs = [['EUR', 'USD'], ['GBP', 'USD'], ['USD', 'JPY'], ['USD', 'CHF'], ['AUD', 'USD'], ['USD', 'CAD'], ['EUR', 'GBP'], ['USD', 'ZAR']];
    const rows = pairs.filter(([b, q]) => r[b] && r[q]).map(([b, q]) => { const p = r[q] / r[b]; return `<tr><td><b>${b}/${q}</b></td><td class="mono">${p.toFixed(q === 'JPY' ? 2 : 4)}</td></tr>`; }).join('');
    return `<table class="table"><thead><tr><th>Pair</th><th>Reference rate</th></tr></thead><tbody>${rows}</tbody></table>
      <p class="muted" style="font-size:.75rem;margin:10px 0 0">${d.fallback ? 'Live rates unavailable — showing approximate values.' : `ECB reference rates, ${esc(d.date)}. Not for execution — use your broker's live price.`}</p>`;
  }
  if (key === 'binary') {
    const rows = [70, 75, 80, 85, 88, 90, 92, 95].map((p) => { const be = 1 / (1 + p / 100); return `<tr><td class="mono">${p}%</td><td class="mono"><b>${pct(be)}</b></td><td class="mono ${0.5 * (p / 100) - 0.5 >= 0 ? 'up' : 'down'}">${((0.5 * (p / 100) - 0.5) * 100).toFixed(1)}%</td></tr>`; }).join('');
    return `<table class="table"><thead><tr><th>Payout</th><th>Breakeven win rate</th><th>EV at 50% wins</th></tr></thead><tbody>${rows}</tbody></table>
      <p class="muted" style="font-size:.75rem;margin:10px 0 0">EV = expected profit per unit staked. A coin-flip strategy loses at every payout under 100%.</p>`;
  }
  const rows = await coinsReady;
  if (!rows.length) return '<p class="muted">Live crypto prices are unavailable right now.</p>';
  return `<table class="table"><thead><tr><th>Coin</th><th>Price</th><th>24h</th></tr></thead><tbody>${rows.map((r) => `<tr><td><b>${esc(r.symbol)}</b></td><td class="mono">${fmtPrice(r.price)}</td><td class="mono ${r.change >= 0 ? 'up' : 'down'}">${fmtChange(r.change)}</td></tr>`).join('')}</tbody></table>
    <p class="muted" style="font-size:.75rem;margin:10px 0 0">Live spot prices in USD.</p>`;
}
async function showMarket(key) {
  const m = markets.find((x) => x.key === key) || markets[0];
  if (!m) return;
  $$('#market-tabs [data-m]').forEach((b) => { const on = b.dataset.m === m.key; b.setAttribute('aria-pressed', String(on)); b.setAttribute('aria-selected', String(on)); });
  $('#market-panel').innerHTML = `
    <div class="market__info">
      <span class="eyebrow eyebrow--plain">${esc(m.title)}</span>
      <h3 class="h2" style="margin:14px 0">${esc(m.tagline)}</h3>
      <p class="lead muted">${esc(m.summary)}</p>
      <div class="tags" style="margin:18px 0 24px">${(m.focus || []).map((f) => `<span class="tag">${esc(f)}</span>`).join('')}</div>
      <ul class="market__rules">${(m.approach || []).map((a, i) => `<li><span class="mono accent">0${i + 1}</span>${esc(a)}</li>`).join('')}</ul>
    </div>
    <div class="market__live card"><div class="skeleton"></div></div>`;
  $('.market__live').innerHTML = await marketWidget(m.key);
}
$$('#market-tabs [data-m]').forEach((b) => b.addEventListener('click', () => showMarket(b.dataset.m)));
showMarket(markets[0]?.key);

// ---------- TradingView (loads on demand) ----------
function tvWidget(el, script, config) {
  el.innerHTML = '';
  const wrap = document.createElement('div'); wrap.className = 'tradingview-widget-container'; wrap.style.cssText = 'height:100%;width:100%';
  const inner = document.createElement('div'); inner.className = 'tradingview-widget-container__widget'; inner.style.cssText = 'height:100%;width:100%';
  const s = document.createElement('script'); s.src = `https://s3.tradingview.com/external-embedding/${script}`; s.async = true; s.type = 'text/javascript';
  s.textContent = JSON.stringify(config);
  wrap.append(inner, s); el.appendChild(wrap);
}
const syms = T.watchlist?.tvSymbols?.length ? T.watchlist.tvSymbols : [{ label: 'EUR/USD', symbol: 'FX:EURUSD' }];
let currentSym = syms[0].symbol;
const loadChart = () => tvWidget($('#tv-chart'), 'embed-widget-advanced-chart.js', {
  autosize: true, symbol: currentSym, interval: '60', timezone: 'Africa/Nairobi', theme: 'dark', style: '1', locale: 'en',
  backgroundColor: 'rgba(6, 10, 8, 1)', gridColor: 'rgba(111, 227, 191, 0.06)', allow_symbol_change: true, calendar: false, hide_volume: false, support_host: 'https://www.tradingview.com',
});
$('#tv-symbols').innerHTML = syms.map((s, i) => `<button class="filter ${i === 0 ? 'is-active' : ''}" data-sym="${esc(s.symbol)}">${esc(s.label)}</button>`).join('');
$$('#tv-symbols [data-sym]').forEach((b) => b.addEventListener('click', () => { currentSym = b.dataset.sym; $$('#tv-symbols .filter').forEach((x) => x.classList.toggle('is-active', x === b)); loadChart(); }));
const lazy = (el, fn) => { const io = new IntersectionObserver(([e]) => { if (e.isIntersecting) { io.disconnect(); fn(); } }, { rootMargin: '300px' }); io.observe(el); };
lazy($('#tv-chart'), loadChart);
lazy($('#tv-calendar'), () => tvWidget($('#tv-calendar'), 'embed-widget-events.js', { colorTheme: 'dark', isTransparent: true, width: '100%', height: '100%', locale: 'en', importanceFilter: '0,1', countryFilter: 'us,eu,gb,jp,au,ca,ch,cn,za' }));

// ---------- Session clock ----------
const SESSIONS = [
  { name: 'Sydney', tz: 'Australia/Sydney', open: 7, close: 16 },
  { name: 'Tokyo', tz: 'Asia/Tokyo', open: 9, close: 18 },
  { name: 'London', tz: 'Europe/London', open: 8, close: 17 },
  { name: 'New York', tz: 'America/New_York', open: 8, close: 17 },
];
const HOME_TZ = 'Africa/Nairobi';
function tzParts(date, tz) {
  const f = new Intl.DateTimeFormat('en-GB', { timeZone: tz, hour12: false, weekday: 'short', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' });
  const p = Object.fromEntries(f.formatToParts(date).map((x) => [x.type, x.value]));
  return { wd: p.weekday, h: Number(p.hour) % 24, m: Number(p.minute), s: Number(p.second), y: Number(p.year), mo: Number(p.month), d: Number(p.day) };
}
const tzOffsetMin = (date, tz) => { const p = tzParts(date, tz); return Math.round((Date.UTC(p.y, p.mo - 1, p.d, p.h, p.m, p.s) - date.getTime()) / 60000); };
const fmtDur = (min) => { const h = Math.floor(min / 60); const m = Math.round(min % 60); return h ? `${h}h ${m}m` : `${m}m`; };
function forexWeekend(now) {
  const p = tzParts(now, 'America/New_York');
  const hm = p.h + p.m / 60;
  return (p.wd === 'Fri' && hm >= 17) || p.wd === 'Sat' || (p.wd === 'Sun' && hm < 17);
}
function renderClock() {
  const now = new Date();
  const home = tzParts(now, HOME_TZ);
  const homeOff = tzOffsetMin(now, HOME_TZ);
  const weekend = forexWeekend(now);
  const nowH = home.h + home.m / 60;
  const rows = SESSIONS.map((s) => {
    const p = tzParts(now, s.tz); const hm = p.h + p.m / 60;
    const isWeekday = !['Sat', 'Sun'].includes(p.wd);
    const open = !weekend && isWeekday && hm >= s.open && hm < s.close;
    const shift = (tzOffsetMin(now, s.tz) - homeOff) / 60; // hours this city is ahead of Nairobi
    const startH = (((s.open - shift) % 24) + 24) % 24;
    const len = s.close - s.open;
    let note;
    if (open) note = `closes in ${fmtDur((s.close - hm) * 60)}`;
    else { let until = s.open - hm; if (until <= 0) until += 24; note = weekend ? 'weekend — market closed' : `opens in ${fmtDur(until * 60)}`; }
    const segs = startH + len > 24 ? [[startH, 24 - startH], [0, startH + len - 24]] : [[startH, len]];
    return `<div class="session ${open ? 'is-open' : ''}">
      <div class="session__name"><b>${esc(s.name)}</b><span class="mono">${String(p.h).padStart(2, '0')}:${String(p.m).padStart(2, '0')} local</span></div>
      <div class="session__bar">${segs.map(([a, l]) => `<i style="left:${(a / 24) * 100}%;width:${(l / 24) * 100}%"></i>`).join('')}<b class="session__now" style="left:${(nowH / 24) * 100}%"></b></div>
      <div class="session__state"><span class="tag ${open ? '' : 'tag--ghost'}">${open ? 'Open' : 'Closed'}</span><span class="mono muted">${note}</span></div>
    </div>`;
  }).join('');
  const openCount = SESSIONS.filter((s) => { const p = tzParts(now, s.tz); const hm = p.h + p.m / 60; return !weekend && !['Sat', 'Sun'].includes(p.wd) && hm >= s.open && hm < s.close; }).length;
  $('#session-clock').innerHTML = `
    <div class="sessions__head">
      <div><span class="eyebrow eyebrow--plain">Nairobi time (EAT)</span><div class="sessions__clock mono">${String(home.h).padStart(2, '0')}:${String(home.m).padStart(2, '0')}<small>:${String(home.s).padStart(2, '0')}</small></div></div>
      <div class="sessions__status">${weekend ? '<span class="tag tag--warn">Forex market closed for the weekend</span>' : openCount >= 2 ? `<span class="tag">${esc(SESSIONS.filter((s) => { const p = tzParts(now, s.tz); const hm = p.h + p.m / 60; return !['Sat', 'Sun'].includes(p.wd) && hm >= s.open && hm < s.close; }).map((s) => s.name).join(' + '))} overlap</span>` : openCount ? '<span class="tag">Market open</span>' : '<span class="tag tag--ghost">Quiet hours</span>'}</div>
    </div>
    <div class="session__scale mono">${[0, 3, 6, 9, 12, 15, 18, 21, 24].map((h) => `<span style="left:${(h / 24) * 100}%">${String(h).padStart(2, '0')}</span>`).join('')}</div>
    ${rows}
    <p class="muted" style="font-size:.75rem;margin:14px 0 0">Approximate session hours in each city's local time, adjusted for daylight saving automatically.</p>`;
}
renderClock();
setInterval(renderClock, 1000);

// ---------- Toolkit ----------
const INSTR = [
  { id: 'EURUSD', label: 'EUR/USD', base: 'EUR', quote: 'USD', pip: 0.0001, size: 100000 },
  { id: 'GBPUSD', label: 'GBP/USD', base: 'GBP', quote: 'USD', pip: 0.0001, size: 100000 },
  { id: 'AUDUSD', label: 'AUD/USD', base: 'AUD', quote: 'USD', pip: 0.0001, size: 100000 },
  { id: 'NZDUSD', label: 'NZD/USD', base: 'NZD', quote: 'USD', pip: 0.0001, size: 100000 },
  { id: 'USDJPY', label: 'USD/JPY', base: 'USD', quote: 'JPY', pip: 0.01, size: 100000 },
  { id: 'USDCHF', label: 'USD/CHF', base: 'USD', quote: 'CHF', pip: 0.0001, size: 100000 },
  { id: 'USDCAD', label: 'USD/CAD', base: 'USD', quote: 'CAD', pip: 0.0001, size: 100000 },
  { id: 'EURGBP', label: 'EUR/GBP', base: 'EUR', quote: 'GBP', pip: 0.0001, size: 100000 },
  { id: 'EURJPY', label: 'EUR/JPY', base: 'EUR', quote: 'JPY', pip: 0.01, size: 100000 },
  { id: 'GBPJPY', label: 'GBP/JPY', base: 'GBP', quote: 'JPY', pip: 0.01, size: 100000 },
  { id: 'XAUUSD', label: 'Gold XAU/USD', base: 'XAU', quote: 'USD', pip: 0.1, size: 100, note: 'Gold: 1 lot = 100 oz; here 1 pip = a $0.10 price move. Brokers differ — check yours.' },
];
const field = (id, label, value, attrs = '', addon = '') => `<div class="field"><label for="${id}">${label}</label><div class="input-group"><input class="input" id="${id}" value="${value}" inputmode="decimal" ${attrs}>${addon ? `<span class="addon">${addon}</span>` : ''}</div></div>`;
const result = (label, value, cls = '') => `<div class="result ${cls}"><span>${label}</span><b>${value}</b></div>`;

const TOOLS = {
  position: {
    title: 'Position size', icon: 'chart',
    html: () => `<div class="tool__grid"><form class="tool__form form-grid" onsubmit="return false">
      <div class="field full"><label for="ps-inst">Instrument</label><select class="select" id="ps-inst">${INSTR.map((i) => `<option value="${i.id}">${i.label}</option>`).join('')}</select></div>
      ${field('ps-bal', 'Account balance', '1000', '', 'USD')}${field('ps-risk', 'Risk per trade', '1', '', '%')}
      ${field('ps-stop', 'Stop loss', '20', '', 'pips')}${field('ps-lev', 'Leverage', '100', '', ':1')}
      ${field('ps-price', 'Current price', '', 'placeholder="auto"', '')}
      <p class="muted full" id="ps-note" style="font-size:.78rem;margin:0"></p>
    </form><div class="tool__out" id="ps-out"></div></div>`,
    run() {
      const inst = INSTR.find((i) => i.id === $('#ps-inst').value);
      const r = { USD: 1, ...(RATES?.rates || {}) };
      const priceEl = $('#ps-price');
      const autoPrice = r[inst.base] && r[inst.quote] ? r[inst.quote] / r[inst.base] : 0;
      if (!priceEl.dataset.touched) priceEl.value = autoPrice ? autoPrice.toFixed(inst.quote === 'JPY' ? 3 : 5) : '';
      const price = num(priceEl.value);
      const bal = num($('#ps-bal').value); const risk = num($('#ps-risk').value); const stop = num($('#ps-stop').value); const lev = Math.max(1, num($('#ps-lev').value, 100));
      const q2u = inst.quote === 'USD' ? 1 : r[inst.quote] ? 1 / r[inst.quote] : 0;
      $('#ps-note').textContent = (inst.note ? inst.note + ' ' : '') + (RATES?.fallback ? 'Using approximate FX rates.' : RATES ? `FX conversion uses ECB rates from ${RATES.date}.` : '');
      if (!bal || !risk || !stop || !q2u) { $('#ps-out').innerHTML = '<p class="muted">Enter your balance, risk and stop loss.</p>'; return; }
      const pipVal = inst.pip * inst.size * q2u;
      const riskUsd = (bal * risk) / 100;
      const lots = riskUsd / (stop * pipVal);
      const lotsR = Math.floor(lots * 100 + 1e-9) / 100;
      const actual = lotsR * stop * pipVal;
      const margin = price ? (lotsR * inst.size * price * q2u) / lev : 0;
      $('#ps-out').innerHTML = `
        <div class="result result--hero"><span>Position size</span><b>${lotsR.toFixed(2)} lots</b><small class="mono">${(lotsR * inst.size).toLocaleString('en-US')} units · exact ${lots.toFixed(3)}</small></div>
        ${lotsR <= 0 ? `<div class="notice">${ICONS.warn}<span>Your stop is too wide for this risk. The smallest size (0.01 lots) would risk ${usd(0.01 * stop * pipVal)}.</span></div>` : ''}
        <div class="result-grid">
          ${result('Money at risk', `${usd(actual)}<small>${kes(actual * usdKes)}</small>`)}
          ${result('Risk of balance', pct(actual / bal, 2))}
          ${result('Pip value / lot', usd(pipVal))}
          ${result('Pip value at your size', usd(pipVal * lotsR))}
          ${result('Margin needed', margin ? usd(margin) : '—')}
          ${result('Free margin after', margin ? usd(bal - margin) : '—', margin > bal ? 'is-bad' : '')}
        </div>`;
    },
    wire() {
      $('#ps-price').addEventListener('input', (e) => { e.target.dataset.touched = '1'; });
      $('#ps-inst').addEventListener('change', () => { delete $('#ps-price').dataset.touched; });
    },
  },
  rr: {
    title: 'Risk : reward', icon: 'candles',
    html: () => `<div class="tool__grid"><form class="tool__form form-grid" onsubmit="return false">
      <div class="field full"><span class="label">Direction</span><div class="seg" id="rr-dir"><button type="button" aria-pressed="true" data-d="long">Buy / Long</button><button type="button" aria-pressed="false" data-d="short">Sell / Short</button></div></div>
      ${field('rr-entry', 'Entry price', '1.0850')}${field('rr-stop', 'Stop loss', '1.0830')}
      ${field('rr-tp', 'Take profit', '1.0900')}${field('rr-risk', 'Money at risk (optional)', '10', '', 'USD')}
    </form><div class="tool__out" id="rr-out"></div></div>`,
    run() {
      const dir = $('#rr-dir [aria-pressed="true"]').dataset.d;
      const e = num($('#rr-entry').value), s = num($('#rr-stop').value), t = num($('#rr-tp').value), money = num($('#rr-risk').value);
      const risk = dir === 'long' ? e - s : s - e; const reward = dir === 'long' ? t - e : e - t;
      if (!e || risk <= 0 || reward <= 0) {
        $('#rr-out').innerHTML = `<div class="notice">${ICONS.warn}<span>${dir === 'long' ? 'For a buy, the stop goes below entry and the target above it.' : 'For a sell, the stop goes above entry and the target below it.'}</span></div>`; return;
      }
      const rr = reward / risk; const be = 1 / (1 + rr);
      const total = risk + reward;
      $('#rr-out').innerHTML = `<div class="rr">
        <div class="rr__visual ${dir}"><div class="rr__tp" style="flex:${reward / total}"><span>TP +${reward.toPrecision(4)}</span></div><div class="rr__entry"><span>Entry</span></div><div class="rr__sl" style="flex:${risk / total}"><span>SL −${risk.toPrecision(4)}</span></div></div>
        <div style="flex:1;display:grid;gap:12px;align-content:start">
          <div class="result result--hero"><span>Reward : risk</span><b>1 : ${rr.toFixed(2)}</b><small class="mono">${rr >= 2 ? 'Meets the 1:2 minimum' : rr >= 1 ? 'Below my 1:2 minimum' : 'Risking more than you can make'}</small></div>
          <div class="result-grid">
            ${result('Breakeven win rate', pct(be))}
            ${result('Win 4 of 10 trades', `${(4 * rr - 6).toFixed(1)}R`, 4 * rr - 6 < 0 ? 'is-bad' : '')}
            ${money ? result('Potential profit', usd(money * rr)) : ''}
            ${money ? result('Potential loss', usd(-money), 'is-bad') : ''}
          </div></div></div>`;
    },
    wire() { $$('#rr-dir [data-d]').forEach((b) => b.addEventListener('click', () => { $$('#rr-dir [data-d]').forEach((x) => x.setAttribute('aria-pressed', String(x === b))); TOOLS.rr.run(); })); },
  },
  binary: {
    title: 'Binary expectancy', icon: 'bolt',
    html: () => `<div class="tool__grid"><form class="tool__form form-grid" onsubmit="return false">
      ${field('bo-stake', 'Stake per trade', '500', '', 'KES')}${field('bo-pay', 'Payout on a win', '85', '', '%')}
      ${field('bo-win', 'Your measured win rate', '55', '', '%')}${field('bo-n', 'Trades per day', '10')}
      ${field('bo-bank', 'Starting balance', '10000', '', 'KES')}
      <p class="muted full" style="font-size:.78rem;margin:0">Use a win rate you've actually measured over 100+ demo trades — not one you hope for.</p>
    </form><div class="tool__out"><div id="bo-out"></div><canvas id="bo-chart" class="sim-chart" aria-label="Simulated balance paths over 100 trades"></canvas></div></div>`,
    run() {
      const stake = num($('#bo-stake').value), pay = num($('#bo-pay').value) / 100, w = Math.min(1, num($('#bo-win').value) / 100), n = num($('#bo-n').value), bank = num($('#bo-bank').value);
      if (!stake || !pay || !bank) { $('#bo-out').innerHTML = '<p class="muted">Fill in stake, payout and balance.</p>'; return; }
      const ev = stake * (w * pay - (1 - w)); const be = 1 / (1 + pay);
      // Monte Carlo: 100 trades, 2,000 runs
      const RUNS = 2000, N = 100; let below = 0, halved = 0; const finals = []; const paths = [];
      for (let k = 0; k < RUNS; k++) {
        let b = bank; let hit = false; const path = k < 40 ? [b] : null;
        for (let i = 0; i < N; i++) { if (b < stake) { hit = true; if (path) path.push(b); continue; } b += Math.random() < w ? stake * pay : -stake; if (b <= bank / 2) hit = true; if (path) path.push(b); }
        if (b < bank) below++; if (hit) halved++; finals.push(b); if (path) paths.push(path);
      }
      finals.sort((a, b) => a - b);
      const median = finals[Math.floor(RUNS / 2)];
      $('#bo-out').innerHTML = `
        <div class="result result--hero ${ev < 0 ? 'is-bad' : ''}"><span>Expected result per trade</span><b>${ev >= 0 ? '+' : '−'}KES ${Math.abs(ev).toFixed(0)}</b><small class="mono">${w > be ? 'Your win rate beats breakeven' : 'Your win rate is below breakeven — this loses over time'}</small></div>
        <div class="result-grid">
          ${result('Breakeven win rate', pct(be))}
          ${result('Expected per day', `${ev * n >= 0 ? '+' : '−'}${kes(Math.abs(ev * n))}`, ev < 0 ? 'is-bad' : '')}
          ${result('Chance of being down after 100 trades', pct(below / RUNS, 0), below / RUNS > 0.5 ? 'is-bad' : '')}
          ${result('Chance of losing half your balance', pct(halved / RUNS, 0), halved / RUNS > 0.2 ? 'is-bad' : '')}
          ${result('Median balance after 100', kes(median))}
        </div>`;
      drawPaths($('#bo-chart'), paths, bank);
    },
  },
  compound: {
    title: 'Compounding', icon: 'sparkle',
    html: () => `<div class="tool__grid"><form class="tool__form form-grid" onsubmit="return false">
      ${field('cp-start', 'Starting balance', '50000', '', 'KES')}${field('cp-rate', 'Return per month', '3', '', '%')}
      ${field('cp-months', 'Months', '24')}${field('cp-add', 'Added each month', '5000', '', 'KES')}
      <p class="muted full" style="font-size:.78rem;margin:0">Even professionals rarely make steady monthly returns. Try 1–3% and compare it with a losing month or two.</p>
    </form><div class="tool__out"><div id="cp-out"></div><canvas id="cp-chart" class="sim-chart" aria-label="Account growth chart"></canvas></div></div>`,
    run() {
      const start = num($('#cp-start').value), r = num($('#cp-rate').value) / 100, m = Math.min(600, Math.round(num($('#cp-months').value))), add = num($('#cp-add').value);
      let b = start; const series = [b]; const dep = [start];
      for (let i = 0; i < m; i++) { b = b * (1 + r) + add; series.push(b); dep.push(start + add * (i + 1)); }
      const invested = start + add * m;
      $('#cp-out').innerHTML = `
        <div class="result result--hero"><span>Balance after ${m} months</span><b>${kes(b)}</b><small class="mono">≈ ${usd(b / usdKes, 0)}</small></div>
        <div class="result-grid">${result('You put in', kes(invested))}${result('Growth', kes(b - invested), b - invested < 0 ? 'is-bad' : '')}${result('Yearly equivalent', pct(Math.pow(1 + r, 12) - 1, 1))}</div>`;
      drawLines($('#cp-chart'), [{ data: series, color: '#29AB87', fill: true }, { data: dep, color: 'rgba(143,168,157,0.7)', dash: true }]);
    },
  },
  drawdown: {
    title: 'Drawdown recovery', icon: 'shield',
    html: () => `<div class="tool__grid"><form class="tool__form" onsubmit="return false" style="display:grid;gap:22px">
      <div class="field"><label for="dd-loss">Account loss <b class="mono accent" id="dd-loss-v">20%</b></label><input type="range" class="range" id="dd-loss" min="1" max="90" value="20"></div>
      <div class="form-grid">${field('dd-risk', 'Risk per trade', '2', '', '%')}${field('dd-streak', 'Losing streak', '8', '', 'trades')}</div>
    </form><div class="tool__out" id="dd-out"></div></div>`,
    run() {
      const L = num($('#dd-loss').value) / 100; $('#dd-loss-v').textContent = Math.round(L * 100) + '%';
      const need = L / (1 - L);
      const r = num($('#dd-risk').value) / 100, n = Math.round(num($('#dd-streak').value));
      const left = Math.pow(1 - r, n); const streakDD = 1 - left;
      $('#dd-out').innerHTML = `
        <div class="result result--hero ${need > 0.5 ? 'is-bad' : ''}"><span>Gain needed to recover a ${pct(L, 0)} loss</span><b>+${pct(need, 1)}</b></div>
        <div class="dd-bars"><div><span>Loss</span><i style="width:${Math.min(100, L * 100)}%"></i><b class="mono">−${pct(L, 0)}</b></div><div><span>Recovery</span><i class="up" style="width:${Math.min(100, need * 100 / 9)}%"></i><b class="mono">+${pct(need, 0)}</b></div></div>
        <div class="result-grid">
          ${result(`${n} losses in a row at ${pct(r, 1)}`, `−${pct(streakDD, 1)}`, streakDD > 0.2 ? 'is-bad' : '')}
          ${result('Balance left', pct(left, 1))}
          ${result('Gain to recover that streak', `+${pct(streakDD / (1 - streakDD), 1)}`)}
        </div>`;
    },
  },
  dca: {
    title: 'Crypto average', icon: 'grid',
    html: () => `<div class="tool__grid"><form class="tool__form" onsubmit="return false" style="display:grid;gap:16px">
      <div class="form-grid"><div class="field"><label for="dca-coin">Coin</label><select class="select" id="dca-coin"></select></div>${field('dca-now', 'Current price', '', 'placeholder="auto"', 'USD')}</div>
      <div><span class="label">Your buys</span><div id="dca-rows" style="display:grid;gap:8px;margin-top:8px"></div>
      <button type="button" class="btn btn--sm" id="dca-add" style="margin-top:10px">+ Add buy</button></div>
      <p class="muted" style="font-size:.78rem;margin:0">Example values are filled in — replace them with your own.</p>
    </form><div class="tool__out" id="dca-out"></div></div>`,
    rows: [{ amt: 100, px: 95000 }, { amt: 100, px: 82000 }, { amt: 100, px: 70000 }],
    drawRows() {
      $('#dca-rows').innerHTML = this.rows.map((r, i) => `<div class="dca-row"><div class="input-group"><input class="input" data-i="${i}" data-k="amt" value="${r.amt}" inputmode="decimal" aria-label="Amount spent"><span class="addon">USD</span></div><span class="muted">at</span><div class="input-group"><input class="input" data-i="${i}" data-k="px" value="${r.px}" inputmode="decimal" aria-label="Price paid"><span class="addon">USD</span></div><button type="button" class="icon-btn" data-del="${i}" aria-label="Remove buy">${ICONS.close}</button></div>`).join('');
      $$('#dca-rows input').forEach((inp) => inp.addEventListener('input', () => { this.rows[inp.dataset.i][inp.dataset.k] = num(inp.value); this.run(); }));
      $$('#dca-rows [data-del]').forEach((b) => b.addEventListener('click', () => { this.rows.splice(Number(b.dataset.del), 1); this.drawRows(); this.run(); }));
    },
    run() {
      const coin = $('#dca-coin').value; const live = COINS.find((c) => c.id === coin);
      const nowEl = $('#dca-now'); if (!nowEl.dataset.touched && live) nowEl.value = live.price;
      const now = num(nowEl.value);
      const rows = this.rows.filter((r) => r.amt > 0 && r.px > 0);
      if (!rows.length) { $('#dca-out').innerHTML = '<p class="muted">Add at least one buy.</p>'; return; }
      const spent = rows.reduce((a, r) => a + r.amt, 0); const qty = rows.reduce((a, r) => a + r.amt / r.px, 0);
      const avg = spent / qty; const value = qty * now; const pl = value - spent;
      $('#dca-out').innerHTML = `
        <div class="result result--hero"><span>Average buy price</span><b>${fmtPrice(avg)}</b><small class="mono">${qty.toPrecision(5)} ${esc(coinSymbol(coin))}</small></div>
        <div class="result-grid">${result('Total spent', usd(spent))}${result('Value now', now ? usd(value) : '—')}
          ${now ? result('Profit / loss', `${usd(pl)} <small>${(pl / spent * 100).toFixed(1)}%</small>`, pl < 0 ? 'is-bad' : '') : ''}
          ${now ? result('Price vs your average', `${((now / avg - 1) * 100).toFixed(1)}%`, now < avg ? 'is-bad' : '') : ''}</div>`;
    },
    wire() {
      const ids = T.watchlist?.crypto || ['bitcoin'];
      $('#dca-coin').innerHTML = ids.filter((id) => !['tether', 'usd-coin'].includes(id)).map((id) => `<option value="${esc(id)}">${esc(coinSymbol(id))}</option>`).join('');
      $('#dca-coin').addEventListener('change', () => { delete $('#dca-now').dataset.touched; this.run(); });
      $('#dca-now').addEventListener('input', (e) => { e.target.dataset.touched = '1'; });
      $('#dca-add').addEventListener('click', () => { this.rows.push({ amt: 100, px: 0 }); this.drawRows(); });
      this.drawRows();
      coinsReady.then(() => this.run());
    },
  },
};

function chartCtx(cv) {
  const r = cv.getBoundingClientRect(); const dpr = Math.min(devicePixelRatio || 1, 2);
  cv.width = Math.round(r.width * dpr); cv.height = Math.round(r.height * dpr);
  const ctx = cv.getContext('2d'); ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, r.width, r.height);
  return { ctx, w: r.width, h: r.height };
}
function drawPaths(cv, paths, start) {
  const { ctx, w, h } = chartCtx(cv); if (!w) return;
  const all = paths.flat(); const mn = Math.min(...all, 0), mx = Math.max(...all, start * 1.2);
  const X = (i) => 8 + (i / 100) * (w - 16); const Y = (v) => h - 12 - ((v - mn) / (mx - mn || 1)) * (h - 24);
  ctx.strokeStyle = 'rgba(111,227,191,0.25)'; ctx.setLineDash([4, 4]); ctx.beginPath(); ctx.moveTo(0, Y(start)); ctx.lineTo(w, Y(start)); ctx.stroke(); ctx.setLineDash([]);
  for (const p of paths) {
    const end = p[p.length - 1];
    ctx.strokeStyle = end >= start ? 'rgba(41,171,135,0.45)' : 'rgba(255,122,112,0.32)'; ctx.lineWidth = 1;
    ctx.beginPath(); p.forEach((v, i) => (i ? ctx.lineTo(X(i), Y(v)) : ctx.moveTo(X(i), Y(v)))); ctx.stroke();
  }
  ctx.fillStyle = 'rgba(143,168,157,0.8)'; ctx.font = '500 10px JetBrains Mono, monospace'; ctx.fillText('start', 10, Y(start) - 6); ctx.fillText('40 simulated runs · 100 trades', 10, 14);
}
function drawLines(cv, series) {
  const { ctx, w, h } = chartCtx(cv); if (!w) return;
  const all = series.flatMap((s) => s.data); const mn = Math.min(...all), mx = Math.max(...all); const n = series[0].data.length - 1 || 1;
  const X = (i) => 8 + (i / n) * (w - 16); const Y = (v) => h - 12 - ((v - mn) / (mx - mn || 1)) * (h - 24);
  for (const s of series) {
    ctx.beginPath(); s.data.forEach((v, i) => (i ? ctx.lineTo(X(i), Y(v)) : ctx.moveTo(X(i), Y(v))));
    ctx.setLineDash(s.dash ? [4, 4] : []); ctx.strokeStyle = s.color; ctx.lineWidth = s.dash ? 1.2 : 2; ctx.stroke(); ctx.setLineDash([]);
    if (s.fill) { ctx.lineTo(X(n), h); ctx.lineTo(X(0), h); ctx.closePath(); const g = ctx.createLinearGradient(0, 0, 0, h); g.addColorStop(0, 'rgba(41,171,135,0.35)'); g.addColorStop(1, 'rgba(41,171,135,0)'); ctx.fillStyle = g; ctx.fill(); }
  }
  ctx.fillStyle = 'rgba(143,168,157,0.8)'; ctx.font = '500 10px JetBrains Mono, monospace'; ctx.fillText('— balance   - - money put in', 10, 14);
}

const toolKeys = Object.keys(TOOLS);
$('#tool-tabs').innerHTML = toolKeys.map((k, i) => `<button type="button" role="tab" id="tab-${k}" aria-selected="${i === 0}" data-tool="${k}">${ICONS[TOOLS[k].icon] || ''}<span>${esc(TOOLS[k].title)}</span></button>`).join('');
function showTool(k) {
  const t = TOOLS[k]; if (!t) return;
  $$('#tool-tabs [data-tool]').forEach((b) => b.setAttribute('aria-selected', String(b.dataset.tool === k)));
  $('#tool-panel').innerHTML = `<div class="tool__head"><h3 class="h3">${esc(t.title)}</h3><span class="tag">Free tool</span></div>${t.html()}`;
  $('#tool-panel').setAttribute('aria-labelledby', 'tab-' + k);
  t.wire?.call(t);
  const form = $('.tool__form', $('#tool-panel'));
  form.addEventListener('input', () => t.run.call(t));
  form.addEventListener('change', () => t.run.call(t));
  t.run.call(t);
  LS.set('mkb.tool', k);
}
$$('#tool-tabs [data-tool]').forEach((b) => b.addEventListener('click', () => showTool(b.dataset.tool)));
const startTool = { '#tool-position': 'position', '#tool-binary': 'binary', '#tool-rr': 'rr', '#tool-compound': 'compound', '#tool-drawdown': 'drawdown', '#tool-dca': 'dca' }[location.hash] || LS.get('mkb.tool', 'position');
showTool(TOOLS[startTool] ? startTool : 'position');
fxReady.then(() => { if ($('#ps-inst')) TOOLS.position.run(); });
addEventListener('resize', () => { const k = $('#tool-tabs [aria-selected="true"]')?.dataset.tool; if (['binary', 'compound'].includes(k)) TOOLS[k].run.call(TOOLS[k]); });

// ---------- Downloads ----------
function downloadJournal() {
  const cols = ['Date', 'Time (EAT)', 'Market', 'Instrument', 'Direction', 'Session', 'Setup', 'Timeframe', 'Entry', 'Stop loss', 'Take profit', 'Position size', 'Risk ($)', 'Risk (%)', 'Exit', 'Result ($)', 'R multiple', 'Followed plan? (Y/N)', 'Emotion before', 'Emotion after', 'Lesson', 'Screenshot link'];
  const csv = '﻿' + cols.join(',') + '\n';
  const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' })); a.download = 'MKB-Trading-Journal.csv';
  document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  toast('Journal downloaded — open it in Excel or Google Sheets.');
}
const CHECKLIST = ['Is the higher-timeframe trend or structure clear?', 'Is price at a key level I marked before the session?', 'Is this a setup from my written plan — not a feeling?', 'Where is my stop: the price that proves me wrong?', 'Is the reward at least 2× the risk?', 'Is my position size calculated for 1% risk or less?', 'Is there high-impact news in the next 30 minutes?', 'Am I still inside my daily loss limit?', 'Am I calm — not bored, chasing or revenging a loss?', 'Have I logged this trade in my journal?'];
function openChecklist() {
  const m = openModal(`<span class="eyebrow">Free template</span><h2 style="margin:14px 0 8px">Pre-trade checklist</h2><p class="muted">Answer all ten with a yes before you enter. One "no" means no trade.</p>
    <ol class="checklist">${CHECKLIST.map((q, i) => `<li><label><input type="checkbox"><span><b class="mono accent">${String(i + 1).padStart(2, '0')}</b> ${esc(q)}</span></label></li>`).join('')}</ol>
    <div class="row" style="margin-top:20px"><button class="btn btn--primary" data-print>Print / save as PDF</button><span class="mono muted" data-score>0 / 10</span></div>`, { label: 'Pre-trade checklist' });
  const boxes = $$('input[type=checkbox]', m.el);
  boxes.forEach((b) => b.addEventListener('change', () => { const n = boxes.filter((x) => x.checked).length; $('[data-score]', m.el).textContent = n === 10 ? '10 / 10 — clear to trade' : `${n} / 10`; }));
  $('[data-print]', m.el).addEventListener('click', () => {
    const w = window.open('', '_blank');
    if (!w) { toast('Allow pop-ups to print the checklist.', 'err'); return; }
    w.document.write(`<!doctype html><title>MKB Pre-Trade Checklist</title><style>body{font:16px/1.5 system-ui,sans-serif;max-width:640px;margin:40px auto;color:#06100b}h1{font-size:24px}li{margin:12px 0;padding-left:6px}li::marker{font-weight:700}.b{display:inline-block;width:14px;height:14px;border:2px solid #0F3D2E;border-radius:3px;margin-right:10px;vertical-align:-2px}footer{margin-top:32px;font-size:12px;color:#555}</style><h1>MKB · Pre-Trade Checklist</h1><p>All ten must be YES before you enter. One NO means no trade.</p><ol>${CHECKLIST.map((q) => `<li><span class="b"></span>${esc(q)}</li>`).join('')}</ol><footer>Moses Bright Kang'ara · MKB — education, not financial advice.</footer>`);
    w.document.close(); w.focus();
    setTimeout(() => w.print(), 250); // printed from here: the site's security policy blocks inline scripts
  });
}

// ---------- Creations ----------
$('#creation-grid').innerHTML = (T.creations || []).map((c, i) => `
  <article class="card card--glow creation" data-reveal style="--d:${(i % 3) * 80}">
    <div class="row" style="justify-content:space-between"><span class="tag">${esc(c.type)}</span><span class="mono muted" style="font-size:.72rem">${esc(c.status || '')}</span></div>
    <h3 class="h3" style="margin:18px 0 8px">${esc(c.title)}</h3>
    <p class="muted" style="margin:0 0 18px">${esc(c.summary)}</p>
    <div class="row" style="justify-content:space-between;margin-top:auto"><span class="mono muted" style="font-size:.75rem">${esc(c.platform || '')} · ${esc(c.price || '')}</span>
    ${c.link ? `<a class="link-arrow" href="${esc(c.link.startsWith('#') || /^https?:/.test(c.link) ? c.link : url(c.link))}" data-creation="${esc(c.link)}" ${/^https?:/.test(c.link) ? 'target="_blank" rel="noopener"' : ''}>${c.link.startsWith('#download') ? 'Download' : 'Open'} ${ICONS.arrowUR}</a>` : ''}</div>
  </article>`).join('');
$$('[data-creation]').forEach((a) => a.addEventListener('click', (e) => {
  const l = a.dataset.creation;
  if (l === '#download-journal') { e.preventDefault(); downloadJournal(); }
  else if (l === '#download-checklist') { e.preventDefault(); openChecklist(); }
  else if (l.startsWith('#tool-')) { e.preventDefault(); const k = { '#tool-position': 'position', '#tool-binary': 'binary', '#tool-rr': 'rr', '#tool-compound': 'compound', '#tool-drawdown': 'drawdown', '#tool-dca': 'dca' }[l]; if (k) showTool(k); $('#toolkit').scrollIntoView({ behavior: 'smooth' }); }
}));

// ---------- Library ----------
const lessons = T.lessons || [];
const topics = ['All', ...new Set(lessons.map((l) => l.market))];
let topic = 'All';
const TOOL_FOR = { Binary: 'binary', Forex: 'position', Crypto: 'dca', General: 'rr' };
const renderLessons = () => {
  const list = lessons.filter((l) => topic === 'All' || l.market === topic);
  $('#lesson-grid').innerHTML = list.map((l, i) => `
    <article class="card card--glow lesson" data-reveal style="--d:${(i % 3) * 80}" data-lesson="${esc(l.id)}" tabindex="0" role="button" data-cursor="Read">
      <div class="row" style="justify-content:space-between"><span class="tag">${esc(l.market)}</span><span class="mono muted" style="font-size:.72rem">${esc(l.level)} · ${Number(l.minutes) || 3} min</span></div>
      <h3 class="h3" style="margin:18px 0 8px;font-size:1.25rem">${esc(l.title)}</h3>
      <p class="muted" style="margin:0">${esc(l.summary)}</p>
      <span class="link-arrow" style="margin-top:auto;padding-top:18px">Read lesson ${ICONS.arrowUR}</span>
    </article>`).join('') || '<div class="empty">No lessons yet.</div>';
  $$('#lesson-grid [data-lesson]').forEach((el) => {
    const go = () => openLesson(el.dataset.lesson);
    el.addEventListener('click', go); el.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(); } });
  });
  attachHover($('#lesson-grid')); observeReveals($('#lesson-grid'));
};
function openLesson(id) {
  const l = lessons.find((x) => x.id === id); if (!l) return;
  const tool = TOOL_FOR[l.market];
  const m = openModal(`<span class="eyebrow">${esc(l.market)} · ${esc(l.level)} · ${Number(l.minutes) || 3} min read</span>
    <h2 style="margin:14px 0 18px">${esc(l.title)}</h2>
    <div class="prose">${markdown(l.body)}</div>
    <div class="row" style="margin-top:28px">${tool ? `<button class="btn btn--primary" data-go-tool="${tool}">Try the ${esc(TOOLS[tool].title)} tool</button>` : ''}<a class="btn" href="${url('connect.html')}?type=question&q=${encodeURIComponent('Question about the lesson: ' + l.title)}">Ask a question</a></div>
    <p class="muted" style="font-size:.75rem;margin-top:18px">Education only — not financial advice.</p>`, { label: l.title });
  $('[data-go-tool]', m.el)?.addEventListener('click', (e) => { m.close(); showTool(e.currentTarget.dataset.goTool); setTimeout(() => $('#toolkit').scrollIntoView({ behavior: 'smooth' }), 300); });
  history.replaceState(null, '', '#' + l.id);
}
$('#lesson-filters').innerHTML = topics.map((t) => `<button class="filter ${t === 'All' ? 'is-active' : ''}" role="tab" aria-selected="${t === 'All'}" data-t="${esc(t)}">${esc(t)}<sup>${t === 'All' ? lessons.length : lessons.filter((l) => l.market === t).length}</sup></button>`).join('');
$$('#lesson-filters [data-t]').forEach((b) => b.addEventListener('click', () => { topic = b.dataset.t; $$('#lesson-filters [data-t]').forEach((x) => { x.classList.toggle('is-active', x === b); x.setAttribute('aria-selected', String(x === b)); }); renderLessons(); }));
renderLessons();
const deep = lessons.find((l) => '#' + l.id === location.hash);
if (deep) setTimeout(() => openLesson(deep.id), 500);
else if (location.hash === '#download-journal') downloadJournal();
else if (location.hash === '#download-checklist') setTimeout(openChecklist, 400);

attachHover(document);
observeReveals();
accordion(document);

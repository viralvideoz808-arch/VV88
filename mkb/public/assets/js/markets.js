// Live market data from free public APIs that allow browser access.
// Crypto: CoinGecko (spot price + 24h change). FX: Frankfurter (ECB daily reference rates).

const SYMBOLS = { bitcoin: 'BTC', ethereum: 'ETH', solana: 'SOL', binancecoin: 'BNB', ripple: 'XRP', tether: 'USDT', cardano: 'ADA', dogecoin: 'DOGE', tron: 'TRX', toncoin: 'TON', 'usd-coin': 'USDC', litecoin: 'LTC', chainlink: 'LINK', avalanche: 'AVAX' };
export const coinSymbol = (id) => SYMBOLS[id] || String(id).slice(0, 5).toUpperCase();

function cached(key, ttlMs) {
  try { const v = JSON.parse(sessionStorage.getItem(key) || 'null'); if (v && Date.now() - v.t < ttlMs) return v.d; } catch { /* ignore */ }
  return null;
}
function store(key, d) { try { sessionStorage.setItem(key, JSON.stringify({ t: Date.now(), d })); } catch { /* ignore */ } }

async function getJSON(u, ms = 8000) {
  const ctl = new AbortController(); const t = setTimeout(() => ctl.abort(), ms);
  try { const r = await fetch(u, { signal: ctl.signal }); if (!r.ok) throw new Error('HTTP ' + r.status); return await r.json(); } finally { clearTimeout(t); }
}

// → [{ id, symbol, price, change }]
export async function crypto(ids) {
  const list = (ids && ids.length ? ids : ['bitcoin', 'ethereum', 'solana']).filter((x) => /^[a-z0-9-]+$/.test(x)).slice(0, 20);
  const key = 'mkb.cg.' + list.join(',');
  const hit = cached(key, 60_000);
  if (hit) return hit;
  let out = [];
  // Binance's public market-data mirror first (no key, generous limits); CoinGecko covers coins Binance doesn't list.
  try { out = await binance(list); } catch { out = []; }
  if (out.length < list.filter((id) => !['tether', 'usd-coin'].includes(id)).length) {
    try {
      const j = await getJSON(`https://api.coingecko.com/api/v3/simple/price?ids=${list.join(',')}&vs_currencies=usd&include_24hr_change=true`);
      const cg = list.filter((id) => j[id]).map((id) => ({ id, symbol: coinSymbol(id), price: j[id].usd, change: j[id].usd_24h_change ?? 0 }));
      if (cg.length >= out.length) out = cg;
    } catch { /* keep what Binance gave */ }
  }
  if (!out.length) throw new Error('no prices');
  store(key, out);
  return out;
}

async function binance(ids) {
  const pairs = ids.map((id) => [id, coinSymbol(id)]).filter(([, s]) => !['USDT', 'USDC'].includes(s)).map(([id, s]) => [id, s + 'USDT']);
  if (!pairs.length) return [];
  const q = encodeURIComponent(JSON.stringify(pairs.map(([, p]) => p)));
  const j = await getJSON(`https://data-api.binance.vision/api/v3/ticker/24hr?symbols=${q}`);
  const by = Object.fromEntries(j.map((t) => [t.symbol, t]));
  return pairs.filter(([, p]) => by[p]).map(([id, p]) => ({ id, symbol: coinSymbol(id), price: Number(by[p].lastPrice), change: Number(by[p].priceChangePercent) }));
}

// USD-based FX rates: { date, rates: { EUR: 0.89, JPY: 157.9, ... } }
const FALLBACK = { date: null, rates: { EUR: 0.89, GBP: 0.745, JPY: 158, CHF: 0.82, CAD: 1.4, AUD: 1.43, NZD: 1.62, ZAR: 17.5 }, fallback: true };
export async function fx() {
  const hit = cached('mkb.fx', 3600_000);
  if (hit) return hit;
  try {
    const j = await getJSON('https://api.frankfurter.dev/v1/latest?base=USD');
    const d = { date: j.date, rates: j.rates, fallback: false };
    store('mkb.fx', d);
    return d;
  } catch { return FALLBACK; }
}

export function fmtPrice(p) {
  if (p >= 1000) return '$' + p.toLocaleString('en-US', { maximumFractionDigits: 0 });
  if (p >= 1) return '$' + p.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return '$' + p.toPrecision(3);
}
export function fmtChange(c) { const v = Number(c) || 0; return `${v >= 0 ? '▲' : '▼'} ${Math.abs(v).toFixed(2)}%`; }

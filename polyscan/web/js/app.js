// Dashboard: renders the in-page engine's state every ~1.5 s. The scan, the council and the demo account all run
// inside this tab (polyscan is a static site; there is no server).
import { Engine } from "./engine.js";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const safeImg = (u) => (typeof u === "string" && /^https:\/\//.test(u)) ? esc(u) : "";
const cents = (p) => p == null ? "–" : `${(p * 100).toFixed(1)}¢`;
const usd = (x) => x == null ? "–" : (x < 0 ? "-$" : "$") + Math.abs(x).toFixed(2);
const usdk = (x) => x == null ? "–" : x >= 1e6 ? "$" + (x / 1e6).toFixed(1) + "M" : x >= 1e3 ? "$" + (x / 1e3).toFixed(0) + "k" : "$" + x.toFixed(0);
const dur = (h) => h == null ? "–" : h < 0 ? "ended" : h < 48 ? h.toFixed(0) + "h" : (h / 24).toFixed(0) + "d";
const signedPct = (x, d = 1) => { if (x == null || !Number.isFinite(x)) return "–"; if (Math.abs(x) < 0.5 / 10 ** d) x = 0; return `<span class="${x >= 0 ? "pos" : "neg"}">${x > 0 ? "+" : ""}${x.toFixed(d)}%</span>`; };
const signedC = (x) => { if (x == null || !Number.isFinite(x)) return "–"; const c = x * 100; return `<span class="${c >= 0 ? "pos" : "neg"}">${c >= 0 ? "+" : ""}${c.toFixed(1)}¢</span>`; };

const engine = new Engine();
window.polyscan = { engine };
let S = null, selected = null, seenSignals = null, rulesInit = false;

$("toggle").onclick = () => {
  if (!S) return;
  if (!S.running && "Notification" in window && Notification.permission === "default") Notification.requestPermission();
  if (S.running) engine.stop(); else engine.start();
  refresh();
};
$("minAgree").oninput = () => $("minAgreeV").textContent = $("minAgree").value;
$("saveRules").onclick = () => {
  engine.setRules({ min_buy_votes: Math.round(+$("minAgree").value), min_edge: Math.max(0, (+$("minEdge").value || 2) / 100), max_resolve_hours: Math.max(1, Math.min(168, +$("maxHours").value || 24)) });
  rulesInit = false; refresh();
};

function beep() {
  try {
    const a = new (window.AudioContext || window.webkitAudioContext)();
    [0, .18, .36].forEach((t, i) => { const o = a.createOscillator(), g = a.createGain(); o.frequency.value = [660, 880, 1100][i]; o.connect(g); g.connect(a.destination); g.gain.setValueAtTime(.15, a.currentTime + t); g.gain.exponentialRampToValueAtTime(.001, a.currentTime + t + .16); o.start(a.currentTime + t); o.stop(a.currentTime + t + .17); });
  } catch (e) {}
}
function notifyNew(signals) {
  const ids = new Set(signals.map((s) => s.id));
  if (seenSignals) {
    const fresh = signals.filter((s) => !seenSignals.has(s.id) && !s.outcome_result && s.kind !== "exploratory");
    if (fresh.length) { beep(); const s = fresh[0]; if ("Notification" in window && Notification.permission === "granted") new Notification(s.kind === "arbitrage" ? "RISK-FREE SET" : `SIGNAL: ${s.outcome}`, { body: s.headline.slice(0, 120) }); }
  }
  seenSignals = ids;
}

function renderHeader() {
  const t = $("toggle"); t.textContent = S.running ? "STOP" : "START"; t.className = S.running ? "stop" : "start";
  $("sources").innerHTML = Object.entries(S.sources).map(([k, v]) => {
    const cls = /error|crash/.test(v) ? "err" : /connecting|rate/.test(v) ? "wait" : /stopped/.test(v) ? "" : "ok";
    return `<span title="${esc(v)}"><i class="dot ${cls}"></i>${esc(k)}</span>`;
  }).join("");
}

function renderVerdict() {
  const v = $("verdict"), sig = S.active_signal;
  if (!S.running && !sig) {
    v.className = ""; v.innerHTML = `<div class="v-title muted">Press START to turn the 25 agents loose on Polymarket.</div>
      <p class="v-sub">They scan the <b>fast-resolving</b> markets (games and short-dated questions that settle within hours), price every order book, and look for two things:
      a <b>risk-free set</b> (buy every outcome of a complete question for under $1) and the rare market where the odds are genuinely mispriced after fees.
      The scan runs right here in this tab — keep it open while you want it working.</p>`;
    return;
  }
  if (!sig) {
    const closest = (S.arb_watch || [])[0];
    v.className = ""; v.innerHTML = `<div class="v-title muted ${S.running ? "pulse" : ""}">${S.running ? "Scanning" : "Stopped"} · ${S.stats.markets} fast markets (≤${S.rules.max_resolve_hours}h), ${S.stats.complete_sets} complete sets · no signal yet</div>
      <p class="v-sub">No risk-free set is below $1 and no directional edge has cleared the strict bar — on efficient markets that is normal and rare.
      ${closest && closest.cost ? `Closest set: <b>${esc(closest.title)}</b> costs ${usd(closest.cost)} for a $1 payout (${closest.edge >= 0 ? "arb!" : (-closest.edge * 100).toFixed(1) + "¢ short"}).` : ""}
      ${S.paper.settings.aggressive ? `<br><b>Aggressive demo mode is on:</b> the account is taking the council's best fast picks (${S.paper.positions.length} open) — they settle within hours. These are exploratory and pay the spread; turn it off for vetted signals only.` : `<b>Tip:</b> turn on "aggressive (explore)" in the demo account, or use the Demo buy buttons, to make it trade now.`}</p>`;
    return;
  }
  const isArb = sig.kind === "arbitrage";
  v.className = isArb ? "arb" : "signal";
  if (isArb) {
    v.innerHTML = `<div class="v-title arb">RISK-FREE SET · ${esc(sig.eventTitle || sig.question)}</div>
      <div class="v-sub">Buy one share of every outcome. Exactly one pays $1 at resolution.</div>
      <div class="plan">
        <div><span>Set cost</span><b>${usd(sig.arb.cost)}</b></div>
        <div><span>Guaranteed payout</span><b class="pos">$1.00</b></div>
        <div><span>Edge</span><b class="pos">${(sig.arb.edge * 100).toFixed(2)}¢ / set</b></div>
        <div><span>Outcomes in set</span><b>${sig.arb.members}</b></div>
      </div>
      <h2>Why the council agrees</h2>
      <ul class="reasons">${sig.reasons.slice(0, 8).map((r) => `<li><b>${esc(r.agent)}:</b> ${esc(r.reason)}</li>`).join("")}</ul>
      <p class="links" style="margin-top:12px"><a href="${esc(sig.links.polymarket)}" target="_blank" rel="noopener">Open on Polymarket</a></p>`;
    return;
  }
  v.innerHTML = `
    <div class="v-title buy">BUY ${esc(sig.outcome)} @ ${cents(sig.ask)}</div>
    <div class="v-sub">${esc(sig.question)} ${sig.eventTitle ? "· " + esc(sig.eventTitle) : ""} · <b>${sig.agree}/${sig.total_agents}</b> agents · conviction ${sig.conviction.toFixed(0)}%</div>
    <div class="plan">
      <div><span>Entry (ask)</span><b>${cents(sig.ask)}</b></div>
      <div><span>Fair estimate</span><b>${cents(sig.fair)}</b></div>
      <div><span>Net edge</span><b class="pos">${signedC(sig.edge)}/share</b></div>
      <div><span>Fee</span><b>${cents(sig.fee_per_share)}/share</b></div>
      <div><span>Resolves</span><b>${dur(sig.end_ms ? (sig.end_ms - S.now) / 3.6e6 : null)}</b></div>
    </div>
    <h2>Why the council says buy</h2>
    <ul class="reasons">${sig.reasons.map((r) => `<li><b>${esc(r.agent)}:</b> ${esc(r.reason)}</li>`).join("")}</ul>
    ${sig.cautions.length ? `<h2 style="margin-top:14px">Dissent</h2><ul class="reasons cautions">${sig.cautions.map((r) => `<li><b>${esc(r.agent)} (${r.stance}):</b> ${esc(r.reason)}</li>`).join("")}</ul>` : ""}
    <p class="links" style="margin-top:12px"><a href="${esc(sig.links.polymarket)}" target="_blank" rel="noopener">Open on Polymarket</a>
      <span class="muted">· speculative: a view the market may not share</span></p>`;
}

function renderStats() {
  const st = S.stats;
  const items = [
    ["Fast markets", st.markets], ["With order books", st.with_books], ["Complete sets", st.complete_sets],
    ["Analyzed", st.analyzed], ["Council passes", st.passes], ["Pass time", st.last_pass_ms + " ms"],
    ["Events", st.events], ["Uptime", S.uptime_s + "s"],
  ];
  $("stats").innerHTML = items.map(([k, v]) => `<div><span>${k}</span><b>${esc(v)}</b></div>`).join("");
}

function renderArbWatch() {
  const rows = S.arb_watch || [];
  $("arbwatch").innerHTML = rows.length ? rows.map((a) => {
    const cost = a.cost ?? a.sum_ask;
    const short = 1 - cost; // positive = arb
    const fill = Math.max(0, Math.min(1, 1 - Math.abs(short) / 0.1)); // how close to $1
    const label = short >= 0 ? `<b class="pos">arb +${(short * 100).toFixed(1)}¢</b>` : `<span class="muted">${(-short * 100).toFixed(1)}¢ over $1</span>`;
    return `<div class="arbrow"><div style="min-width:0;flex:1"><div style="overflow:hidden;text-overflow:ellipsis">${esc(a.title)}</div><div class="sym">${a.outcomes} outcomes · ${usdk(a.vol24)} 24h${a.priced ? "" : " · pricing…"}</div></div>
      <div style="text-align:right;min-width:92px">${usd(cost)} / $1<br><span style="font-size:11px">${label}</span></div>
      <div class="arbbar" style="max-width:90px"><i style="width:${(fill * 100).toFixed(0)}%"></i></div></div>`;
  }).join("") : `<p class="muted">${S.running ? "Loading complete outcome sets…" : "Not running."}</p>`;
}

function renderPaper() {
  const P = S.paper;
  const settle = P.settled ? `· settled ${P.settled} (${P.settled_wins}W)` : "";
  const cards = [
    ["Equity", usd(P.equity), signedPct(P.pnl_pct) + " vs $" + P.start_usd.toFixed(0)],
    ["Cash", usd(P.cash), "free to trade"],
    ["In positions", usd(P.open_value), `${P.positions.length} open ${settle}`],
    ["Fees paid", usd(P.totals.fees), "taker fees"],
  ];
  const posRows = P.positions.length ? P.positions.map((p) => `<tr><td>${p.kind === "arbitrage" ? '<span class="tag arb">arb</span> ' : ""}${esc((p.question || "").slice(0, 34))}<div class="sym">${esc(p.outcome)}</div></td>
      <td>${p.shares.toFixed(0)} sh</td><td>${cents(p.avg)}</td><td>${cents(p.mark)}</td><td>${signedPct(p.pnl_pct)}</td>
      <td>${p.resolves_in_h == null ? "–" : dur(p.resolves_in_h)}</td>
      <td>${p.kind === "arbitrage" ? "" : `<button class="tag" data-sellm="${esc(p.marketId)}" data-sells="${p.side}">sell</button>`}</td></tr>`).join("")
    : `<tr><td colspan="7" class="muted">No open positions.</td></tr>`;
  const trades = P.trades.slice(0, 8).map((t) => {
    const cls = t.status === "win" || t.status === "filled" ? "pos" : t.status === "loss" ? "neg" : "muted";
    return `<div class="item"><span>${esc(t.side)} <b>${esc((t.question || "").slice(0, 28))}</b> ${esc(t.outcome || "")}</span><span class="${cls}">${esc(t.status)}${t.pnl != null ? " " + usd(t.pnl) : ""}</span></div>`;
  }).join("");
  $("paper").innerHTML = `
    <div class="stats" style="grid-template-columns:repeat(auto-fit,minmax(110px,1fr))">${cards.map(([k, v, s]) => `<div><span>${k}</span><b>${v}</b><span style="text-transform:none">${s}</span></div>`).join("")}</div>
    <div class="table-wrap" style="margin-top:10px"><table><thead><tr><th>Position</th><th>Shares</th><th>Avg</th><th>Mark</th><th>P&amp;L</th><th>Resolves</th><th></th></tr></thead><tbody id="posbody">${posRows}</tbody></table></div>
    ${trades ? `<div style="margin-top:8px">${trades}</div>` : ""}
    <div class="row" style="margin-top:10px">
      <label class="muted" style="display:flex;gap:6px;align-items:center"><input type="checkbox" id="autoTrade" ${P.settings.autoTrade ? "checked" : ""}> auto-trade</label>
      <label class="muted" style="display:flex;gap:6px;align-items:center" title="Also take the council's best lean each cycle, not just vetted signals. Exploratory — expected to lose the spread + fee on efficient markets."><input type="checkbox" id="aggressive" ${P.settings.aggressive ? "checked" : ""}> aggressive (explore)</label>
      <label class="muted" style="display:flex;gap:6px;align-items:center">size <input type="number" id="sizePct" min="2" max="100" step="1" value="${P.settings.sizePct}">% </label>
      <button id="resetPaper">Reset to $10</button>
    </div>`;
  $("autoTrade").onchange = (e) => engine.paper.setSettings({ autoTrade: e.target.checked });
  $("aggressive").onchange = (e) => engine.paper.setSettings({ aggressive: e.target.checked });
  $("sizePct").onchange = (e) => { const v = +e.target.value; if (v >= 2 && v <= 100) engine.paper.setSettings({ sizePct: v }); };
  $("resetPaper").onclick = () => { if (confirm("Reset the demo account to $10? Positions and history are deleted.")) { engine.paper.reset(); refresh(); } };
  $("posbody").querySelectorAll("button[data-sellm]").forEach((b) => b.onclick = () => { engine.paperSell(b.dataset.sellm, +b.dataset.sells); b.disabled = true; });
}

function voteBar(c) {
  const hold = c.votes.filter((v) => v.stance === "HOLD").length;
  const w = (n) => (n / 25 * 100).toFixed(1) + "%";
  return `<div class="votebar" title="${c.buy_votes} BUY · ${hold} HOLD · ${c.avoid_votes} AVOID"><i class="vb-buy" style="width:${w(c.buy_votes)}"></i><i class="vb-hold" style="width:${w(hold)}"></i><i class="vb-avoid" style="width:${w(c.avoid_votes)}"></i></div>`;
}

function renderCandidates() {
  const rows = S.candidates;
  if (!rows.find((c) => c.id === selected)) selected = rows[0] ? rows[0].id : null;
  $("cands").innerHTML = rows.length ? rows.map((c) => {
    const status = c.arb ? `<span class="tag arb">arb</span>` : c.vetoed ? `<span class="tag v" title="${esc(c.veto_reason)}">veto</span>` : "";
    const o0 = (c.outcomes && c.outcomes[0]) || "Yes", o1 = (c.outcomes && c.outcomes[1]) || "No";
    const buyBtns = `<button class="tag" data-buym="${esc(c.id)}" data-buys="0" title="demo buy ${esc(o0)} @ ${cents(c.ask0)}">+${esc(o0.slice(0, 7))}</button> <button class="tag" data-buym="${esc(c.id)}" data-buys="1" title="demo buy ${esc(o1)} @ ${cents(c.ask1)}">+${esc(o1.slice(0, 7))}</button>`;
    return `<tr class="clk ${c.id === selected ? "sel" : ""}" data-id="${esc(c.id)}">
      <td>${esc((c.question || "?").slice(0, 40))}<div class="sym">${esc((c.event || "").slice(0, 36))}</div></td>
      <td><span class="pill ${c.side === 0 ? "yes" : "no"}">${esc((c.outcome || "").slice(0, 10))}</span></td>
      <td>${cents(c.ask)}</td><td>${signedC(c.edge)}</td><td>${cents(c.spread)}</td><td>${usdk(c.vol24)}</td><td>${dur(c.hours)}</td>
      <td>${voteBar(c)} ${status}</td>
      <td style="white-space:nowrap">${buyBtns}</td></tr>`;
  }).join("") : `<tr><td colspan="9" class="muted">${S.running ? "Loading order books…" : "Not running."}</td></tr>`;
  $("cands").querySelectorAll("tr[data-id]").forEach((tr) => tr.onclick = (e) => { if (e.target.closest("button")) return; selected = tr.dataset.id; render(); });
  $("cands").querySelectorAll("button[data-buym]").forEach((b) => b.onclick = (e) => { e.stopPropagation(); engine.paperBuy(b.dataset.buym, +b.dataset.buys); b.disabled = true; b.textContent = "…"; });
}

function renderAgents() {
  const c = S.candidates.find((x) => x.id === selected);
  const votes = c ? Object.fromEntries(c.votes.map((v) => [v.agent, v])) : {};
  $("council-title").innerHTML = c ? `The council on <span style="color:var(--text)">${esc((c.question || "").slice(0, 30))}</span> (${esc(c.outcome)}) · ${c.buy_votes}/25 BUY` : "The council";
  $("agents").innerHTML = S.agents.map((a) => {
    const v = votes[a.id], stance = v ? v.stance : "ABSTAIN", tally = a.tally || {};
    return `<div class="agent ${stance}" title="${esc(a.description)}"><div class="n"><span>${esc(a.name)}${a.veto ? '<span class="veto">VETO</span>' : ""}</span><span class="s">${stance}</span></div>
      <div class="r">${esc(v ? v.reason : (S.running ? "waiting…" : "idle"))}</div>
      <div class="g">${esc(a.group)} · ${tally.BUY || 0} buy / ${tally.AVOID || 0} avoid</div></div>`;
  }).join("");
}

function renderHistory() {
  const r = S.track_record;
  $("record").innerHTML = `${r.arbs} risk-free set(s) flagged · ` + (r.scored
    ? `${r.scored} trades resolved: <b class="pos">${r.wins} won</b>, win rate <b>${r.win_rate}%</b>, avg <b>${signedC(r.avg_net_per_share)}</b>/share (includes exploratory buys, which pay the spread)`
    : `${r.signals} trades so far; results appear as each market resolves (hours).`);
  $("history").innerHTML = S.signals.slice(0, 20).map((s) => {
    const o = s.outcome_result;
    const res = s.kind === "arbitrage" ? `<span class="tag arb">risk-free +${(s.arb.edge * 100).toFixed(1)}¢</span>`
      : o ? `<b class="${o.status === "win" ? "pos" : "neg"}">${o.status.toUpperCase()}</b> ${signedC(o.net_per_share)}/sh → ${esc(o.resolved_to)}`
      : `<span class="muted">open…</span>`;
    return `<div class="item"><span><b>${esc((s.question || s.eventTitle || "").slice(0, 30))}</b> <span class="muted">${s.kind === "arbitrage" ? "set" : esc(s.outcome)} · ${new Date(s.created).toLocaleTimeString()}</span></span><span>${res}</span></div>`;
  }).join("") || `<p class="muted">No signals yet.</p>`;
}

function render() {
  if (!S) return;
  renderHeader(); renderVerdict(); renderStats(); renderArbWatch(); renderPaper(); renderCandidates(); renderAgents(); renderHistory();
  if (!rulesInit) { $("minAgree").value = S.rules.min_buy_votes; $("minAgreeV").textContent = S.rules.min_buy_votes; $("minEdge").value = (S.rules.min_edge * 100).toFixed(1); $("maxHours").value = S.rules.max_resolve_hours; rulesInit = true; }
}

function refresh() {
  try { S = engine.state(); notifyNew(S.signals); render(); }
  catch (e) { console.error(e); $("sources").innerHTML = `<span><i class="dot err"></i>dashboard error: ${esc(e.message)}</span>`; }
}
refresh();
setInterval(refresh, 1500);

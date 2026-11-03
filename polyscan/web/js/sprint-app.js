// Minute-markets dashboard: renders the in-page SprintEngine every second.
import { SprintEngine } from "./sprint-engine.js";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const safeUrl = (u) => (typeof u === "string" && /^https:\/\/polymarket\.com\//.test(u)) ? esc(u) : "#";
const cents = (p) => p == null ? "–" : `${(p * 100).toFixed(1)}¢`;
const usd = (x) => x == null ? "–" : (x < 0 ? "-$" : "$") + Math.abs(x).toFixed(2);
const pct = (p) => p == null ? "–" : `${(p * 100).toFixed(1)}%`;
const signedC = (x) => { if (x == null || !Number.isFinite(x)) return "–"; const c = x * 100; return `<span class="${c >= 0 ? "pos" : "neg"}">${c >= 0 ? "+" : ""}${c.toFixed(1)}¢</span>`; };
const signedPct = (x, d = 1) => { if (x == null || !Number.isFinite(x)) return "–"; return `<span class="${x >= 0 ? "pos" : "neg"}">${x > 0 ? "+" : ""}${x.toFixed(d)}%</span>`; };
const bpStr = (x) => x == null ? "–" : `<span class="${x >= 0 ? "pos" : "neg"}">${x >= 0 ? "+" : ""}${x.toFixed(1)}bp</span>`;
const left = (s) => s == null ? "–" : s <= 0 ? "settling" : s >= 3600 ? `${Math.floor(s / 3600)}h${String(Math.floor(s % 3600 / 60)).padStart(2, "0")}m` : s >= 60 ? `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}` : `${Math.floor(s)}s`;
const hhmm = (ms) => new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

const engine = new SprintEngine();
window.polyscan = { engine };
let S = null, selected = null, seen = null, rulesInit = false;

$("toggle").onclick = () => {
  if (!S) return;
  if (!S.running && "Notification" in window && Notification.permission === "default") Notification.requestPermission();
  if (S.running) engine.stop(); else engine.start();
  refresh();
};
$("minVotes").oninput = () => $("minVotesV").textContent = $("minVotes").value;
$("saveRules").onclick = () => {
  engine.setRules({
    min_edge: Math.max(0, (+$("minEdge").value || 5) / 100), min_votes: Math.round(+$("minVotes").value),
    explore_edge: Math.max(0, (+$("exploreEdge").value || 3) / 100), horizon_min: Math.max(5, Math.min(60, +$("horizon").value || 20)),
  });
  rulesInit = false; refresh();
};

function beep() {
  try {
    const a = new (window.AudioContext || window.webkitAudioContext)();
    [0, .15, .3].forEach((t, i) => { const o = a.createOscillator(), g = a.createGain(); o.frequency.value = [700, 940, 1180][i]; o.connect(g); g.connect(a.destination); g.gain.setValueAtTime(.15, a.currentTime + t); g.gain.exponentialRampToValueAtTime(.001, a.currentTime + t + .14); o.start(a.currentTime + t); o.stop(a.currentTime + t + .15); });
  } catch (e) { /* no audio */ }
}
function notifyNew(signals) {
  const ids = new Set(signals.map((s) => s.id));
  if (seen) {
    const fresh = signals.filter((s) => !seen.has(s.id) && s.kind !== "exploratory");
    if (fresh.length) { beep(); if ("Notification" in window && Notification.permission === "granted") new Notification(fresh[0].kind === "arbitrage" ? "LADDER / SET ARBITRAGE" : "FAIR-VALUE SNIPE", { body: fresh[0].headline.slice(0, 140) }); }
  }
  seen = ids;
}

function renderHeader() {
  const t = $("toggle"); t.textContent = S.running ? "STOP" : "START"; t.className = S.running ? "stop" : "start";
  $("sources").innerHTML = Object.entries(S.sources).map(([k, v]) => {
    const cls = /error|crash|blocked/.test(v) ? "err" : /connecting|rate|REST|\//.test(v) ? "wait" : /stopped/.test(v) ? "" : "ok";
    return `<span title="${esc(v)}"><i class="dot ${cls}"></i>${esc(k)}</span>`;
  }).join("");
}

function renderVerdict() {
  const v = $("verdict"), sig = S.active_signal;
  if (!S.running && !sig) {
    v.className = "";
    v.innerHTML = `<div class="v-title muted">Press START to put 25 agents on Polymarket's minute markets.</div>
      <p class="v-sub">They watch every Chainlink-settled crypto <b>Up or Down</b> window (Bitcoin, Ethereum, Solana, XRP, Dogecoin, BNB, Hyperliquid, Zcash — 5-minute, 15-minute and 4-hour),
      price each one from live exchange data, hunt <b>strike-ladder arbitrage</b> between windows that end together, and trade a <b>$10 demo account</b> on the real order books.
      Results settle within minutes. Everything runs in this tab — keep it open.</p>`;
    return;
  }
  if (!sig) {
    const top = S.rows.filter((r) => r.best && r.best.edge != null && r.started).sort((a, b) => b.best.edge - a.best.edge)[0];
    v.className = "";
    v.innerHTML = `<div class="v-title muted ${S.running ? "pulse" : ""}">${S.running ? "Watching" : "Stopped"} · ${S.stats.open} open windows · ${S.stats.ladders} ladders · no vetted trade right now</div>
      <p class="v-sub">${top ? `Closest: <b>${esc(top.name)} ${esc(top.period)} ${top.best.side === 0 ? "Up" : "Down"}</b> — model ${pct(top.best.P)} vs ask ${cents(top.best.ask)}, edge ${signedC(top.best.edge)} (bar ${cents(S.rules.min_edge)}), ${top.best.buy}/25 BUY${top.best.vetoedBy.length ? `, vetoed by ${esc(top.best.vetoedBy.join(", "))}` : ""}.` : "Loading spot prices, candles and order books…"}
      ${S.paper.settings.aggressive ? ` <b>Aggressive demo mode</b> also takes ${cents(S.rules.explore_edge)}+ edges with ${S.rules.explore_votes}+ votes.` : ""}</p>`;
    return;
  }
  if (sig.kind === "arbitrage") {
    v.className = "arb";
    v.innerHTML = `<div class="v-title arb">${esc(sig.question)}</div>
      <div class="v-sub">${sig.legs.map((l) => `BUY <b>${esc(l.outcome)}</b> on ${esc(l.title)} @ ${cents(l.ask)} (strike ${esc(l.strike || "–")})`).join(" + ")}</div>
      <div class="plan">
        <div><span>Cost per set</span><b>${cents(sig.arb.cost)}</b></div>
        <div><span>Guaranteed payout</span><b class="pos">≥ $1.00</b></div>
        <div><span>Locked edge</span><b class="pos">${(sig.arb.edge * 100).toFixed(1)}¢ / set</b></div>
        <div><span>Settles</span><b>${hhmm(sig.end_ms)}</b></div>
      </div>
      <ul class="reasons">${sig.reasons.map((r) => `<li><b>${esc(r.agent)}:</b> ${esc(r.reason)}</li>`).join("")}</ul>
      <p class="links" style="margin-top:12px"><a href="${safeUrl(sig.url)}" target="_blank" rel="noopener">Open on Polymarket</a></p>`;
    return;
  }
  v.className = "signal";
  v.innerHTML = `<div class="v-title buy">BUY ${esc(sig.asset.toUpperCase())} ${esc(sig.outcome)} @ ${cents(sig.ask)} · ${left((sig.end_ms - S.now) / 1000)} left</div>
    <div class="v-sub">${esc(sig.question)} · <b>${sig.agree}/25</b> agents · ${sig.kind === "snipe" ? "fair-value snipe" : esc(sig.kind)}</div>
    <div class="plan">
      <div><span>Model probability</span><b>${pct(sig.p_model)}</b></div>
      <div><span>Ask + fee</span><b>${cents(sig.ask + sig.fee_per_share)}</b></div>
      <div><span>Net edge</span><b class="pos">${signedC(sig.edge)}/share</b></div>
      <div><span>Price to beat</span><b>${esc(sig.strike || "–")}</b></div>
      <div><span>Stake (½-Kelly)</span><b>${usd(sig.stake)}</b></div>
    </div>
    <h2>Why the council says buy</h2>
    <ul class="reasons">${sig.reasons.map((r) => `<li><b>${esc(r.agent)}:</b> ${esc(r.reason)}</li>`).join("")}</ul>
    ${sig.cautions.length ? `<h2 style="margin-top:14px">Dissent</h2><ul class="reasons cautions">${sig.cautions.map((r) => `<li><b>${esc(r.agent)} (${r.stance}):</b> ${esc(r.reason)}</li>`).join("")}</ul>` : ""}
    <p class="links" style="margin-top:12px"><a href="${safeUrl(sig.url)}" target="_blank" rel="noopener">Open on Polymarket</a><span class="muted">· a model view, not a certainty</span></p>`;
}

function renderStats() {
  const st = S.stats, tr = S.track;
  const items = [
    ["Windows watched", st.windows], ["Open now", st.open], ["Modelled", st.modeled], ["Ladders", st.ladders],
    ["Chainlink strikes", st.oracle_strikes], ["Spot age", st.spot_age == null ? "–" : st.spot_age.toFixed(1) + "s"],
    ["Model vs market", tr.n ? `${tr.model.toFixed(3)} / ${tr.market.toFixed(3)}` : "scoring…"], ["Pass", `${st.pass_ms} ms`],
  ];
  $("stats").innerHTML = items.map(([k, v]) => `<div><span>${k}</span><b>${esc(v)}</b></div>`).join("");
}

function voteBar(b) {
  const w = (n) => (n / 25 * 100).toFixed(1) + "%";
  return `<div class="votebar" title="${b.buy} BUY · ${b.hold} HOLD · ${b.avoid} AVOID"><i class="vb-buy" style="width:${w(b.buy)}"></i><i class="vb-hold" style="width:${w(b.hold)}"></i><i class="vb-avoid" style="width:${w(b.avoid)}"></i></div>`;
}

function renderRows() {
  const later = S.rows.filter((r) => !r.started && r.startMs - S.now > 60000);
  const rows = S.rows.filter((r) => r.started || r.startMs - S.now <= 60000);
  if (!rows.find((r) => r.id === selected)) { const top = rows.filter((r) => r.best && r.started).sort((a, b) => (b.best.edge ?? -9) - (a.best.edge ?? -9))[0]; selected = top ? top.id : rows[0] ? rows[0].id : null; }
  $("rows").innerHTML = rows.length ? rows.map((r) => {
    const b = r.best;
    const src = r.ref ? `<div class="src">${esc(r.ref.src)} ±${r.ref.sdBp}bp</div>` : "";
    const status = b && b.vetoedBy.length ? `<span class="tag v" title="${esc(b.vetoedBy.join(", "))}">veto</span>` : "";
    return `<tr class="clk ${r.id === selected ? "sel" : ""}" data-id="${esc(r.id)}">
      <td><b>${esc(r.name)}</b> <span class="pill">${esc(r.period)}</span><div class="sym">${hhmm(r.startMs)}–${hhmm(r.endMs)}${r.started ? "" : " · opens " + left((r.startMs - S.now) / 1000)}</div></td>
      <td class="cd">${r.started ? left(r.remSec) : "–"}</td>
      <td>${r.ref ? esc(r.ref.display) : '<span class="muted">–</span>'}${src}</td>
      <td>${bpStr(r.moveBp)}${r.z != null ? `<div class="src">${r.z.toFixed(2)}σ</div>` : ""}</td>
      <td><b>${pct(r.pUp)}</b></td>
      <td>${cents(r.up.bid)} / ${cents(r.up.ask)}</td>
      <td>${cents(r.down.bid)} / ${cents(r.down.ask)}</td>
      <td>${b && b.edge != null ? `${b.side === 0 ? "Up" : "Down"} ${signedC(b.edge)}` : "–"}</td>
      <td>${b ? voteBar(b) : ""} ${status}</td>
      <td style="white-space:nowrap">${r.started ? `<button class="tag" data-buym="${esc(r.id)}" data-buys="0">+Up</button> <button class="tag" data-buym="${esc(r.id)}" data-buys="1">+Down</button>` : ""}</td></tr>`;
  }).join("") + (later.length ? `<tr><td colspan="10" class="muted">+ ${later.length} more windows open later (next at ${hhmm(Math.min(...later.map((r) => r.startMs)))})</td></tr>` : "")
    : `<tr><td colspan="10" class="muted">${S.running ? "Loading windows…" : "Not running."}</td></tr>`;
  $("rows").querySelectorAll("tr[data-id]").forEach((tr) => tr.onclick = (e) => { if (e.target.closest("button")) return; selected = tr.dataset.id; render(); });
  $("rows").querySelectorAll("button[data-buym]").forEach((b) => b.onclick = (e) => { e.stopPropagation(); engine.paperBuy(b.dataset.buym, +b.dataset.buys); b.disabled = true; b.textContent = "…"; });
}

function renderAgents() {
  const r = S.rows.find((x) => x.id === selected);
  const b = r && r.best;
  const votes = b ? Object.fromEntries(b.votes.map((v) => [v.agent, v])) : {};
  $("council-title").innerHTML = b ? `The council on <span style="color:var(--text)">${esc(r.name)} ${esc(r.period)} ${b.side === 0 ? "Up" : "Down"}</span> · ${b.buy}/25 BUY` : "The council";
  $("agents").innerHTML = S.agents.map((a) => {
    const v = votes[a.id], stance = v ? v.stance : "ABSTAIN";
    return `<div class="agent ${stance}" title="${esc(a.description)}"><div class="n"><span>${esc(a.name)}${a.veto ? '<span class="veto">VETO</span>' : ""}</span><span class="s">${stance}</span></div>
      <div class="r">${esc(v ? v.reason : (S.running ? "waiting…" : "idle"))}</div><div class="g">${esc(a.group)} desk</div></div>`;
  }).join("");
}

function renderLadders() {
  const L = S.ladders;
  $("ladders").innerHTML = L.length ? L.map((l) => {
    const best = l.best;
    const label = best ? (best.edge >= 0 ? `<b class="pos">ARB +${(best.edge * 100).toFixed(1)}¢</b>` : `<span class="muted">best pair ${cents(best.cost)} (${(-best.edge * 100).toFixed(1)}¢ over $1)</span>`) : `<span class="muted">strikes too close to order</span>`;
    return `<div class="lad"><div class="row" style="justify-content:space-between"><span><b>${esc(l.name)}</b> · ends ${hhmm(l.endMs)}</span>${label}</div>
      <div class="rungs">${l.windows.map((w) => `<span>${esc(w.period)} · strike ${esc(w.strike)} <span class="src">${esc(w.src)}</span> · Up ${cents(w.upAsk)} · Down ${cents(w.downAsk)}</span>`).join("")}</div></div>`;
  }).join("") : `<p class="muted">${S.running ? "A ladder forms when two open windows end at the same moment (every quarter hour: a 5-minute inside a 15-minute window)." : "Not running."}</p>`;
}

function renderPaper() {
  const P = S.paper;
  const cards = [
    ["Equity", usd(P.equity), signedPct(P.pnl_pct) + " vs $" + P.start_usd.toFixed(0)],
    ["Cash", usd(P.cash), "free to trade"],
    ["In positions", usd(P.open_value), `${P.positions.length} open${P.settled ? ` · ${P.settled} settled (${P.settled_wins}W)` : ""}`],
    ["Fees paid", usd(P.totals.fees), "taker fees"],
  ];
  const pos = P.positions.length ? P.positions.map((p) => `<tr><td>${p.kind === "arbitrage" ? '<span class="tag arb">arb</span> ' : ""}${esc((p.question || "").replace(" Up or Down - ", " · ").slice(0, 42))}<div class="sym">${esc(p.outcome)}</div></td>
      <td>${p.shares.toFixed(0)}</td><td>${cents(p.avg)}</td><td>${cents(p.mark)}</td><td>${signedPct(p.pnl_pct)}</td>
      <td>${p.resolves_in_h == null ? "–" : left(p.resolves_in_h * 3600)}</td>
      <td>${p.kind === "arbitrage" ? "" : `<button class="tag" data-sellm="${esc(p.marketId)}" data-sells="${p.side}">sell</button>`}</td></tr>`).join("")
    : `<tr><td colspan="7" class="muted">No open positions.</td></tr>`;
  const trades = P.trades.slice(0, 8).map((t) => {
    const cls = t.status === "win" || t.status === "filled" ? "pos" : t.status === "loss" ? "neg" : "muted";
    return `<div class="item"><span>${esc(t.side)} <b>${esc((t.question || "").replace(" Up or Down - ", " · ").slice(0, 34))}</b> ${esc(t.outcome || "")}<span class="sym"> ${esc(t.reason || "")}</span></span><span class="${cls}">${esc(t.status)}${t.pnl != null ? " " + usd(t.pnl) : ""}</span></div>`;
  }).join("");
  $("paper").innerHTML = `
    <div class="stats" style="grid-template-columns:repeat(auto-fit,minmax(110px,1fr))">${cards.map(([k, v, s]) => `<div><span>${k}</span><b>${v}</b><span style="text-transform:none">${s}</span></div>`).join("")}</div>
    <div class="table-wrap" style="margin-top:10px"><table><thead><tr><th>Position</th><th>Sh</th><th>Avg</th><th>Mark</th><th>P&amp;L</th><th>Left</th><th></th></tr></thead><tbody id="posbody">${pos}</tbody></table></div>
    ${trades ? `<div style="margin-top:8px">${trades}</div>` : ""}
    <div class="row" style="margin-top:10px">
      <label class="muted" style="display:flex;gap:6px;align-items:center"><input type="checkbox" id="autoTrade" ${P.settings.autoTrade ? "checked" : ""}> auto-trade</label>
      <label class="muted" style="display:flex;gap:6px;align-items:center" title="Also take smaller model edges (explore). These are experiments: the backtest edge was small."><input type="checkbox" id="aggressive" ${P.settings.aggressive ? "checked" : ""}> aggressive (explore)</label>
      <label class="muted" style="display:flex;gap:6px;align-items:center">manual size <input type="number" id="sizePct" min="5" max="100" step="1" value="${P.settings.sizePct}">%</label>
      <button id="resetPaper">Reset to $10</button>
    </div>`;
  $("autoTrade").onchange = (e) => engine.paper.setSettings({ autoTrade: e.target.checked });
  $("aggressive").onchange = (e) => engine.paper.setSettings({ aggressive: e.target.checked });
  $("sizePct").onchange = (e) => { const v = +e.target.value; if (v >= 5 && v <= 100) engine.paper.setSettings({ sizePct: v }); };
  $("resetPaper").onclick = () => { if (confirm("Reset the minute-markets demo account to $10? Positions and history are deleted.")) { engine.paper.reset(); refresh(); } };
  $("posbody").querySelectorAll("button[data-sellm]").forEach((b) => b.onclick = () => { engine.paperSell(b.dataset.sellm, +b.dataset.sells); b.disabled = true; });
}

function renderHistory() {
  const t = S.track, r = S.record;
  const score = t.n ? `Model Brier <b>${t.model.toFixed(3)}</b> vs market <b>${t.market.toFixed(3)}</b> on ${t.n} resolved windows (lower is better; ${t.model <= t.market ? '<span class="pos">model ahead</span>' : '<span class="neg">market ahead</span>'}).`
    : "Each window is scored at its half-way mark — model vs market — once it resolves.";
  const rec = r.directional ? ` Directional: ${r.directional} resolved, <b class="pos">${r.wins} won</b>, avg ${signedC(r.avg_net)}/share after fees.` : "";
  const arb = r.arbs ? ` Arbitrage: ${r.arbs} settled, ${signedC(r.arb_net)} total per set.` : "";
  $("record").innerHTML = score + rec + arb;
  $("history").innerHTML = S.signals.slice(0, 16).map((s) => {
    const o = s.outcome_result;
    const res = o ? (s.legs ? `<b class="${o.payout >= 1 ? "pos" : "neg"}">paid $${o.payout}</b> ${signedC(o.net_per_set)}/set` : `<b class="${o.status === "win" ? "pos" : "neg"}">${o.status.toUpperCase()}</b> ${signedC(o.net_per_share)}/sh`) : `<span class="muted">open…</span>`;
    const tag = s.kind === "arbitrage" ? '<span class="tag arb">arb</span>' : s.kind === "exploratory" ? '<span class="tag">explore</span>' : '<span class="tag ok">snipe</span>';
    return `<div class="item"><span>${tag} <b>${esc((s.question || "").replace(" Up or Down - ", " · ").slice(0, 40))}</b> <span class="muted">${s.legs ? "" : esc(s.outcome) + " @ " + cents(s.ask)} · ${new Date(s.created).toLocaleTimeString()}</span></span><span>${res}</span></div>`;
  }).join("") || `<p class="muted">No trades yet.</p>`;
}

function render() {
  if (!S) return;
  renderHeader(); renderVerdict(); renderStats(); renderRows(); renderAgents(); renderLadders(); renderPaper(); renderHistory();
  if (!rulesInit) {
    $("minEdge").value = (S.rules.min_edge * 100).toFixed(1); $("minVotes").value = S.rules.min_votes; $("minVotesV").textContent = S.rules.min_votes;
    $("exploreEdge").value = (S.rules.explore_edge * 100).toFixed(1); $("horizon").value = S.rules.horizon_min; rulesInit = true;
  }
}

function refresh() {
  try { S = engine.state(); notifyNew(S.signals); render(); }
  catch (e) { console.error(e); $("sources").innerHTML = `<span><i class="dot err"></i>dashboard error: ${esc(e.message)}</span>`; }
}
refresh();
setInterval(refresh, 1000);

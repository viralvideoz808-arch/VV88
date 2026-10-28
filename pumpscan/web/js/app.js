// Dashboard: renders the in-page engine's state every 1.5 s.
import { Engine } from "./engine.js";
import { Simulator } from "./simulator.js";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const safeImg = (u) => (typeof u === "string" && /^https:\/\//.test(u)) ? esc(u) : "";
const usd = (x) => x == null ? "–" : x >= 1e6 ? "$" + (x/1e6).toFixed(2) + "M" : x >= 1e3 ? "$" + (x/1e3).toFixed(1) + "k" : "$" + x.toFixed(0);
const pct = (x) => x == null ? "–" : `<span class="${x >= 0 ? "pos" : "neg"}">${x >= 0 ? "+" : ""}${Number(x).toFixed(1)}%</span>`;
const mmss = (s) => `${Math.floor(s/60)}:${String(s%60).padStart(2,"0")}`;
const age = (m) => m < 60 ? m.toFixed(0) + "m" : (m/60).toFixed(1) + "h";

let S = null, selected = null, seenSignals = null, rulesInit = false;

// The scan runs in this tab: one engine for live data, one for the simulated market.
const params = new URLSearchParams(location.search);
const engines = {};
// ?sim=1 runs the offline simulated market (used for testing); the demo account always uses live data.
let mode = params.get("sim") === "1" ? "demo" : "live";
function engine() {
  if (!engines[mode]) {
    engines[mode] = mode === "demo"
      ? new Engine({ demo: true, demoSpeed: Number(params.get("speed")) || 10, makeSimulator: (e, speed) => new Simulator(e, speed) })
      : new Engine();
  }
  return engines[mode];
}

// Phones dim the screen and pause background tabs, which pauses the scan; keep the screen on while it runs.
let wakeLock = null;
async function holdScreen(on) {
  try {
    if (on && "wakeLock" in navigator && document.visibilityState === "visible") wakeLock = await navigator.wakeLock.request("screen");
    else if (!on && wakeLock) { await wakeLock.release(); wakeLock = null; }
  } catch (e) { wakeLock = null; }
}
document.addEventListener("visibilitychange", () => { if (S && S.running && document.visibilityState === "visible") holdScreen(true); });

$("toggle").onclick = () => {
  if (!S) return;
  if (!S.running && "Notification" in window && Notification.permission === "default") Notification.requestPermission();
  if (S.running) { engine().stop(); holdScreen(false); } else { engine().start(); holdScreen(true); }
  refresh();
};

$("minAgree").oninput = () => $("minAgreeV").textContent = $("minAgree").value;
$("saveRules").onclick = () => {
  const n = Math.max(1, Math.min(25, Math.round(+$("minAgree").value)));
  const w = Math.max(1, Math.min(120, +$("window").value || 10));
  engine().setRules({ min_buy_votes: n, window_minutes: w });
  rulesInit = false;
  refresh();
};
$("clearHist").onclick = () => {
  if (confirm("Delete the signal history saved in this browser?")) { engine().clearHistory(); refresh(); }
};

function beep() {
  try {
    const a = new (window.AudioContext || window.webkitAudioContext)();
    [0, .18, .36].forEach((t, i) => {
      const o = a.createOscillator(), g = a.createGain();
      o.frequency.value = [660, 880, 1100][i]; o.connect(g); g.connect(a.destination);
      g.gain.setValueAtTime(.15, a.currentTime + t); g.gain.exponentialRampToValueAtTime(.001, a.currentTime + t + .16);
      o.start(a.currentTime + t); o.stop(a.currentTime + t + .17);
    });
  } catch (e) {}
}

function notifyNew(signals) {
  const ids = new Set(signals.map((s) => s.id));
  if (seenSignals) {
    const fresh = signals.filter((s) => !seenSignals.has(s.id) && (s.state || "active") === "active");
    if (fresh.length) {
      beep();
      const s = fresh[0];
      if ("Notification" in window && Notification.permission === "granted")
        new Notification(`BUY NOW: $${s.symbol}`, {body: `${s.agree}/${s.total_agents} agents agree · act within ${s.window_minutes} min`});
    }
  }
  seenSignals = ids;
}

function renderHeader() {
  const t = $("toggle");
  t.textContent = S.running ? "STOP" : "START";
  t.className = S.running ? "stop" : "start";
  const m = $("mode");
  m.textContent = S.mode === "demo" ? "OFFLINE TEST · simulated coins" : "LIVE · pump.fun";
  m.className = "badge " + (S.mode === "demo" ? "demo" : "live");
  $("sources").innerHTML = Object.entries(S.sources).map(([k, v]) => {
    const cls = /error|crash/.test(v) ? "err" : /connecting|reconnecting|rate/.test(v) ? "wait" : /stopped/.test(v) ? "" : "ok";
    return `<span title="${esc(v)}"><i class="dot ${cls}"></i>${esc(k)}</span>`;
  }).join("");
}

function renderVerdict() {
  const v = $("verdict"), sig = S.active_signal;
  if (!S.running && !sig) {
    v.className = "";
    v.innerHTML = `<div class="v-title muted">Press START to unleash the 25 agents.</div>
      <p class="v-sub">They scan every new pump.fun launch plus coins already trending, re-score all of them every 2 seconds,
      and only speak up when they reach consensus. The scan runs right here in this tab: keep it open (on a phone, keep the
      screen on) while you want signals.</p>`;
    return;
  }
  if (!sig) {
    const top = S.candidates.find((c) => !c.vetoed);
    v.className = "";
    v.innerHTML = `<div class="v-title muted ${S.running ? "pulse" : ""}">${S.running ? "Scanning" : "Stopped"} · ${S.stats.watched} coins watched · no consensus yet</div>
      <p class="v-sub">The council needs <b>${S.rules.min_buy_votes}/25</b> BUY votes, zero AVOID votes and no safety veto.
      No signal is better than a bad signal.
      ${top ? `Closest right now: <b>${esc(top.name || top.symbol)}</b> ($${esc(top.symbol)}) with ${top.buy_votes}/25 BUY.` : ""}</p>`;
    return;
  }
  const state = sig.state || "active";
  v.className = state === "active" ? "signal" : state;
  const title = state === "active"
    ? `<div class="v-title buy">BUY NOW · ${esc(sig.name || sig.symbol)} <span class="muted">$${esc(sig.symbol)}</span></div>
       <div class="v-sub">within the next ${sig.window_minutes} minutes · <b>${sig.agree}/${sig.total_agents}</b> agents agree · conviction ${sig.conviction.toFixed(0)}%</div>`
    : `<div class="v-title" style="color:var(--warn)">${state === "void" ? "TOO LATE" : "CANCELLED"} · $${esc(sig.symbol)}</div>
       <div class="v-sub">${esc(sig.state_reason || "")}</div>`;
  const img = safeImg(sig.image);
  v.innerHTML = `
    <div class="v-head">
      ${img ? `<img src="${img}" alt="">` : ""}
      <div>${title}</div>
      <div class="countdown"><span class="muted">window closes in</span><br><b>${mmss(sig.remaining_s)}</b></div>
    </div>
    <div class="plan">
      <div><span>Entry mcap</span><b>${usd(sig.entry_mcap_usd)}</b></div>
      <div><span>Now</span><b>${usd(sig.now_mcap_usd)}</b> ${pct(sig.since_entry_pct)}</div>
      <div><span>Don't buy above</span><b>${usd(sig.max_entry_mcap_usd)}</b></div>
      <div><span>Stop loss</span><b class="neg">${sig.stop_loss_pct}%</b></div>
      <div><span>Take profit 1 (sell ½)</span><b class="pos">+${sig.take_profit_1_pct}%</b></div>
      <div><span>Take profit 2</span><b class="pos">+${sig.take_profit_2_pct}%</b></div>
      ${sig.cost_hurdle_pct != null ? `<div><span>Round-trip costs</span><b>${sig.cost_hurdle_pct}%</b></div>` : ""}
    </div>
    <h2>Why the council says buy</h2>
    <ul class="reasons">${sig.reasons.map((r) => `<li><b>${esc(r.agent)}:</b> ${esc(r.reason)}</li>`).join("")}</ul>
    ${sig.cautions.length ? `<h2 style="margin-top:14px">Dissent</h2><ul class="reasons cautions">${sig.cautions.map((r) => `<li><b>${esc(r.agent)} (${r.stance}):</b> ${esc(r.reason)}</li>`).join("")}</ul>` : ""}
    <p class="links" style="margin-top:14px">
      <a href="${esc(sig.links.pumpfun)}" target="_blank" rel="noopener">Open on pump.fun</a>
      <a href="${esc(sig.links.dexscreener)}" target="_blank" rel="noopener">Chart</a>
      <a href="${esc(sig.links.rugcheck)}" target="_blank" rel="noopener">Rugcheck</a>
      <button id="copyCa">Copy CA</button>
    </p>
    <div class="ca">${esc(sig.mint)}</div>
    ${paperLine(sig)}
    ${S.mode === "demo" ? `<p class="muted">Offline test mode: this coin is simulated and does not exist.</p>` : ""}`;
  $("copyCa").onclick = () => navigator.clipboard && navigator.clipboard.writeText(sig.mint);
  const pb = $("paperBuy");
  if (pb) pb.onclick = () => { engine().paperBuy(sig.mint); pb.disabled = true; pb.textContent = "Sending…"; };
}

function renderStats() {
  const st = S.stats;
  const items = [
    ["Watching now", st.watched], ["Analyzed this run", st.analyzed_total], ["Deep on-chain checks", st.deep_checked],
    ["New launches seen", st.launches_seen], ["On-chain every 2 s", st.chain_watched ?? 0], ["Pass time", st.last_pass_ms + " ms"],
    ["SOL", st.sol_usd ? "$" + st.sol_usd : "–"], ["Hot narratives", st.hot_words.slice(0, 4).join(", ") || "–"],
  ];
  $("stats").innerHTML = items.map(([k, v]) => `<div><span>${k}</span><b>${esc(v)}</b></div>`).join("");
}

function voteBar(c) {
  const hold = c.votes.filter((v) => v.stance === "HOLD").length;
  const w = (n) => (n / 25 * 100).toFixed(1) + "%";
  return `<div class="votebar" title="${c.buy_votes} BUY · ${hold} HOLD · ${c.avoid_votes} AVOID">
    <i class="vb-buy" style="width:${w(c.buy_votes)}"></i><i class="vb-hold" style="width:${w(hold)}"></i><i class="vb-avoid" style="width:${w(c.avoid_votes)}"></i></div>`;
}

function renderCandidates() {
  const rows = S.candidates;
  if (!rows.find((c) => c.mint === selected)) selected = rows[0] ? rows[0].mint : null;
  $("cands").innerHTML = rows.length ? rows.map((c, i) => {
    const img = safeImg(c.image);
    const status = c.vetoed ? `<span class="tag v" title="${esc(c.veto_reason)}">veto</span>`
      : c.buy_votes >= S.rules.min_buy_votes && c.avoid_votes === 0 ? `<span class="tag ok">consensus</span>`
      : `<span class="muted">${c.buy_votes}/25${c.deep_checked ? "" : " · pre-scan"}</span>`;
    const live = c.on_chain ? ` <span class="tag ok" title="judged from the exact bonding-curve state every 2 s">chain</span>` : "";
    return `<tr data-mint="${esc(c.mint)}" class="${c.mint === selected ? "sel" : ""}">
      <td class="muted">${i + 1}</td>
      <td><div class="coin">${img ? `<img src="${img}" alt="" loading="lazy">` : `<img alt="">`}<div>${esc((c.name || "?").slice(0, 22))}<div class="sym">$${esc(c.symbol)}</div></div></div></td>
      <td>${usd(c.mcap)}</td><td>${pct(c.change_m5)}</td>
      <td>${c.buys_m5 ?? "–"} / ${c.sells_m5 ?? "–"}</td><td>${age(c.age_min)}</td>
      <td>${c.migrated ? "graduated" : c.curve_pct == null ? "–" : c.curve_pct + "%"}</td>
      <td>${voteBar(c)}</td><td>${status}${live}</td></tr>`;
  }).join("") : `<tr><td colspan="9" class="muted">${S.running ? "Collecting market data…" : "Not running."}</td></tr>`;
  document.querySelectorAll("#cands tr[data-mint]").forEach((tr) => tr.onclick = () => { selected = tr.dataset.mint; render(); });
}

function renderAgents() {
  const c = S.candidates.find((x) => x.mint === selected);
  const votes = c ? Object.fromEntries(c.votes.map((v) => [v.agent, v])) : {};
  $("council-title").innerHTML = c ? `The council on <span style="color:var(--text)">$${esc(c.symbol)}</span> · ${c.buy_votes}/25 BUY` : "The council";
  $("agents").innerHTML = S.agents.map((a) => {
    const v = votes[a.id];
    const stance = v ? v.stance : "ABSTAIN";
    const tally = a.tally || {};
    return `<div class="agent ${stance}" title="${esc(a.description)}">
      <div class="n"><span>${esc(a.name)}${a.veto ? '<span class="veto">VETO</span>' : ""}</span><span class="s">${stance}</span></div>
      <div class="r">${esc(v ? v.reason : (S.running ? "waiting…" : "idle"))}</div>
      <div class="g">${esc(a.group)} · all coins: ${tally.BUY || 0} buy / ${tally.AVOID || 0} avoid</div></div>`;
  }).join("");
}

function renderHistory() {
  const r = S.track_record;
  const simNote = S.mode === "demo" ? `<b style="color:var(--warn)">SIMULATED coins: these numbers say nothing about real performance.</b> ` : "";
  $("record").innerHTML = simNote + (r.scored
    ? `Measured on ${r.scored} closed signals: <b class="pos">${r.wins} hit take-profit 1</b> · <b class="neg">${r.stopped} stopped out</b> · win rate <b>${r.win_rate}%</b> · avg at window close ${pct(r.avg_end_pct)} · avg peak ${pct(r.avg_peak_pct)}`
    : `${r.signals} signals so far; outcomes appear when each window closes.`);
  $("history").innerHTML = S.signals.slice(0, 25).map((s) => {
    const o = s.outcome;
    const res = o ? (o.status === "no_data" ? `<span class="muted">no data</span>`
      : `<b class="${o.status === "win" || o.status === "up" ? "pos" : "neg"}">${o.status.toUpperCase()}</b> close ${pct(o.end_pct)} · peak ${pct(o.peak_pct)} · low ${pct(o.low_pct)}`)
      : `<span class="muted">${esc(s.state || "active")}…</span>`;
    return `<div class="item"><span><b>$${esc(s.symbol)}</b> <span class="muted">${new Date(s.created * 1000).toLocaleTimeString()} · ${s.agree}/25 · entry ${usd(s.entry_mcap_usd)}</span></span><span>${res}</span></div>`;
  }).join("") || `<p class="muted">No signals yet.</p>`;
}

// ------------------------------------------------------------------ demo account

const sol = (x, d = 4) => x == null ? "–" : `${Number(x).toFixed(d)} SOL`;
const usd2 = (x) => x == null ? "–" : `${x < 0 ? "-" : ""}$${Math.abs(x).toFixed(2)}`;
const qty = (x) => x == null ? "–" : x >= 1e6 ? (x / 1e6).toFixed(2) + "M" : x >= 1e3 ? (x / 1e3).toFixed(1) + "k" : x.toFixed(0);
function signedPct(x, d = 1) {
  if (x == null || !Number.isFinite(x)) return "–";
  if (Math.abs(x) < 0.5 / 10 ** d) x = 0; // no red "-0.00%"
  return `<span class="${x >= 0 ? "pos" : "neg"}">${x > 0 ? "+" : ""}${x.toFixed(d)}%</span>`;
}
const price = (x) => x == null ? "–" : x.toExponential(3);

// What the demo account did with this signal, shown on the signal card.
function paperLine(sig) {
  const P = S.paper;
  if (!P) return "";
  const tr = (P.trades || []).find((t) => t.mint === sig.mint && t.side === "BUY" && t.t >= sig.created - 1);
  if (tr && tr.status === "filled") {
    return `<p class="pos"><b>Demo account bought</b> ${qty(tr.tokens)} $${esc(sig.symbol)} for ${sol(tr.lamports_in)}
      · price impact ${signedPct(tr.price_impact_pct, 2)} · slippage vs quote ${signedPct(tr.slippage_pct, 2)}</p>`;
  }
  if (tr) return `<p style="color:var(--warn)"><b>Demo buy ${esc(tr.status)}:</b> ${esc(tr.reason)}</p>`;
  if (sig.state !== "active") return "";
  if (P.settings && P.settings.autoTrade) return `<p class="muted">Demo account is sending the buy…</p>`;
  return `<p><button id="paperBuy">Demo buy (${P.settings.sizePct}% of free SOL)</button></p>`;
}

let paperFormInit = false;
const PS_FIELDS = ["autoTrade", "sizePct", "slippagePct", "priorityFeeSol", "latencyMs", "maxPositions", "maxHoldMin", "exitOnVeto",
  "breakevenAfterTp1", "trailAfterTp1Pct", "closeAccounts", "rpcUrl"];

function fillPaperForm(st) {
  for (const k of PS_FIELDS) {
    const el = $("ps_" + k);
    if (el.type === "checkbox") el.checked = Boolean(st[k]); else el.value = st[k];
  }
}

$("ps_save").onclick = () => {
  const patch = {};
  for (const k of PS_FIELDS) {
    const el = $("ps_" + k);
    if (el.type === "checkbox") patch[k] = el.checked;
    else if (el.type === "number") { const v = Number(el.value); if (Number.isFinite(v) && v >= Number(el.min) && v <= Number(el.max)) patch[k] = v; }
    else if (/^https:\/\//.test(el.value.trim())) patch[k] = el.value.trim();
  }
  engine().paper.setSettings(patch);
  paperFormInit = false;
  refresh();
};
$("ps_reset").onclick = () => {
  if (confirm("Reset the demo account to $10? Positions and trade history will be deleted.")) { engine().paper.reset(); refresh(); }
};
$("paperPositions").onclick = (ev) => {
  const b = ev.target.closest("button[data-sell]");
  if (!b) return;
  engine().paperSell(b.dataset.mint, Number(b.dataset.sell));
  b.disabled = true;
};

function renderPaper() {
  const P = S.paper, sec = $("paper");
  if (!P) {
    sec.style.display = "none";
    return;
  }
  sec.style.display = "";
  if (!paperFormInit) { fillPaperForm(P.settings); paperFormInit = true; }
  if (!P.funded) {
    sec.className = "";
    $("paperNote").textContent = "";
    $("paperCards").innerHTML = `<div style="grid-column:1/-1"><span>Starting balance</span><b>$10.00</b>
      <span style="text-transform:none">Opens with $10 worth of SOL at the live SOL price once you press START.</span></div>`;
    $("paperPositions").innerHTML = "";
    $("paperTrades").innerHTML = `<tr><td colspan="10" class="muted">No demo trades yet.</td></tr>`;
    return;
  }
  sec.className = P.pnl_usd > 0.005 ? "up" : P.pnl_usd < -0.005 ? "down" : "";
  const t = P.totals;
  $("paperNote").innerHTML = `${P.settings.autoTrade ? "auto-trading signals" : "manual: use Demo buy on a signal"}
    ${!S.running && P.positions.length ? ` · <span style="color:var(--warn)">stopped: exits are not watched until you press START</span>` : ""}
    ${P.rpc_error ? ` · <span style="color:var(--warn)" title="${esc(P.rpc_error)}">RPC trouble</span>` : ""}`;
  const cards = [
    ["Equity", `${usd2(P.equity_usd)}`, `${signedPct(P.pnl_usd != null ? P.pnl_usd / P.start_usd * 100 : null)} vs $${P.start_usd} start`],
    ["SOL", sol(P.equity_sol), `${signedPct(P.pnl_sol_pct)} in SOL terms`],
    ["Cash", sol(P.cash_sol), "free to trade"],
    ["In positions", sol(P.positions_sol), "net of exit fees"],
    ["Realized P&L", sol(t.realized_sol), "closed trades, before network fees"],
    ["pump.fun fees", sol(t.pump_fees_sol), `+ ${sol(t.lp_fees_sol)} LP`],
    ["Network fees", sol(t.network_fees_sol), "base + priority, incl. failed tx"],
    ["Rent", sol(t.rent_paid_sol - t.rent_refunded_sol, 5), `locked in ${P.positions.length} token account(s)`],
  ];
  $("paperCards").innerHTML = cards.map(([k, v, sub]) => `<div><span>${k}</span><b>${v}</b><span style="text-transform:none">${sub}</span></div>`).join("");
  $("paperPositions").innerHTML = P.positions.length ? P.positions.map((p) => `<tr>
      <td><b>$${esc(p.symbol)}</b><div class="sym muted">${esc((p.name || "").slice(0, 20))}</div></td>
      <td>${qty(p.tokens)}</td><td>${sol(p.cost_sol)}</td><td>${sol(p.value_sol)}</td>
      <td>${signedPct(p.pnl_pct)}</td><td>${signedPct(p.change_pct)}</td>
      <td class="muted">SL ${p.plan.sl}% · TP ${p.tp1_done ? "<s>" : ""}+${p.plan.tp1}%${p.tp1_done ? "</s>" : ""} / +${p.plan.tp2}%</td>
      <td>${p.held_min.toFixed(0)}m</td>
      <td title="${esc(p.venue_note)}">${p.venue === "curve" ? "bonding curve" : p.venue === "pool" ? "PumpSwap" : esc(p.venue)}</td>
      <td style="white-space:nowrap">${p.pending ? `<span class="muted">sending…</span>` :
        `<button class="mini" data-sell="0.5" data-mint="${esc(p.mint)}">Sell ½</button> <button class="mini" data-sell="1" data-mint="${esc(p.mint)}">Sell all</button>`}</td></tr>`).join("")
    : `<tr><td colspan="10" class="muted">No open positions.</td></tr>`;
  $("paperTrades").innerHTML = P.trades.length ? P.trades.slice(0, 40).map((tr) => {
    const res = tr.status === "filled" ? `<span class="tag ok">filled</span>` : tr.status === "failed" ? `<span class="tag fail">failed</span>` : `<span class="tag skip">${esc(tr.status)}</span>`;
    const solAmt = tr.side === "BUY" ? (tr.lamports_in != null ? `-${Number(tr.lamports_in).toFixed(4)}` : "–") : (tr.lamports_out != null ? `+${Number(tr.lamports_out).toFixed(4)}` : "–");
    const fees = tr.status === "filled" || tr.status === "failed"
      ? [tr.pump_fees, tr.lp_fee, tr.network_fee, tr.rent ?? (tr.rent_refund ? -tr.rent_refund : 0)].map((x) => x == null ? "0" : Number(x).toFixed(5)).join(" · ")
      : "–";
    const lat = tr.landed_at && tr.quoted_at ? ` · landed ${(tr.landed_at - tr.quoted_at).toFixed(1)}s after quote on ${tr.venue === "pool" ? "PumpSwap" : "curve"}` : "";
    return `<tr><td>${new Date(tr.t * 1000).toLocaleTimeString()}</td><td>${tr.side}</td><td>$${esc(tr.symbol)}</td><td>${res}</td>
      <td>${solAmt}</td><td>${qty(tr.tokens ?? tr.got_tokens)}</td>
      <td>${tr.avg_price ? `${price(tr.avg_price)} vs ${price(tr.spot_at_fill)} (${signedPct(tr.price_impact_pct, 2)})` : "–"}</td>
      <td>${tr.slippage_pct != null ? signedPct(-tr.slippage_pct, 2) : "–"}</td><td class="muted">${fees}</td>
      <td class="muted" style="white-space:normal;min-width:220px">${esc(tr.reason || "")}${esc(lat)}${tr.pnl_lamports != null ? ` · P&L ${Number(tr.pnl_lamports).toFixed(4)} SOL` : ""}</td></tr>`;
  }).join("") : `<tr><td colspan="10" class="muted">No demo trades yet.</td></tr>`;
}

function render() {
  if (!S) return;
  renderHeader(); renderVerdict(); renderPaper(); renderStats(); renderCandidates(); renderAgents(); renderHistory();
  if (!rulesInit) {
    $("minAgree").value = S.rules.min_buy_votes; $("minAgreeV").textContent = S.rules.min_buy_votes;
    $("window").value = S.rules.window_minutes; rulesInit = true;
  }
}

function refresh() {
  try {
    S = engine().state();
    notifyNew(S.signals);
    render();
  } catch (e) {
    console.error(e);
    $("sources").innerHTML = `<span><i class="dot err"></i>dashboard error: ${esc(e.message)}</span>`;
  }
}
window.pumpscan = { engine }; // for debugging from the browser console
refresh();
setInterval(refresh, 1500);

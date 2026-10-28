// Uwanja Fans: fixtures, results, tables and a points-only predictor league.
// Sample fixtures are generated from a fixed seed so every visitor sees the same season.

const LEAGUES = {
  kpl: {
    name: "Kenyan Premier League",
    teams: ["Gor Mahia", "AFC Leopards", "Tusker", "Kenya Police", "Shabana", "Bandari",
            "KCB", "Ulinzi Stars", "Kakamega Homeboyz", "Nairobi City Stars", "Sofapaka", "Posta Rangers"],
  },
  epl: {
    name: "English Premier League",
    teams: ["Arsenal", "Chelsea", "Liverpool", "Manchester City", "Manchester United", "Tottenham",
            "Newcastle", "Aston Villa", "Brighton", "West Ham", "Everton", "Crystal Palace"],
  },
};
const PLAYED_ROUNDS = 4;
const TOTAL_ROUNDS = 7;
const STORE_KEY = "uwanja-fans-v1";

// Fixed sample fans for the leaderboard; the visitor is added alongside them.
const RIVALS = [
  ["Wanjiru K.", 31], ["Otieno_254", 27], ["Kip the Predictor", 24], ["Achieng M.", 22],
  ["Mwangi FC", 19], ["Nduta", 15], ["Baraka O.", 12],
];

function mulberry32(seed) {
  return function () {
    seed |= 0; seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const GOALS = [0, 0, 1, 1, 1, 2, 2, 2, 3, 4];
const goals = (rnd) => GOALS[Math.floor(rnd() * GOALS.length)];

// Circle-method round robin, alternating home and away each round.
function schedule(teams) {
  const list = teams.slice();
  const n = list.length;
  const rounds = [];
  for (let r = 0; r < n - 1; r++) {
    const pairs = [];
    for (let i = 0; i < n / 2; i++) {
      const a = list[i], b = list[n - 1 - i];
      pairs.push(r % 2 ? [b, a] : [a, b]);
    }
    rounds.push(pairs);
    list.splice(1, 0, list.pop());
  }
  return rounds;
}

function nextSaturday(from) {
  const d = new Date(from);
  d.setHours(15, 0, 0, 0);
  d.setDate(d.getDate() + ((6 - d.getDay() + 7) % 7 || 7));
  return d;
}

function buildLeague(key) {
  const league = LEAGUES[key];
  const rnd = mulberry32(key === "kpl" ? 254 : 1888);
  const firstUpcoming = nextSaturday(new Date());
  const rounds = schedule(league.teams).slice(0, TOTAL_ROUNDS);
  return rounds.map((pairs, r) => {
    const date = new Date(firstUpcoming);
    date.setDate(date.getDate() + 7 * (r - PLAYED_ROUNDS));
    return {
      round: r + 1,
      matches: pairs.map(([home, away], i) => {
        const kickoff = new Date(date);
        kickoff.setHours(13 + (i % 3) * 2, i % 2 ? 30 : 0);
        const seeded = { h: goals(rnd), a: goals(rnd) };
        return {
          id: `${key}-${r + 1}-${i}`, home, away, kickoff,
          result: r < PLAYED_ROUNDS ? seeded : null,
        };
      }),
    };
  });
}

// ---- persisted visitor state (predictions, nickname, demo-played rounds) ----

function freshState() {
  return { name: "", picks: {}, simulated: {}, club: "", pens: { played: 0, won: 0, goals: 0, kicks: 0 } };
}
function loadState() {
  try {
    const raw = localStorage.getItem(STORE_KEY);
    if (raw) return Object.assign(freshState(), JSON.parse(raw));
  } catch (e) { /* storage unavailable: fall back to memory */ }
  return freshState();
}
function saveState() {
  try { localStorage.setItem(STORE_KEY, JSON.stringify(state)); } catch (e) { /* ignore */ }
}

const state = loadState();
const seasons = { kpl: buildLeague("kpl"), epl: buildLeague("epl") };
let league = "kpl";

const resultOf = (m) => m.result || state.simulated[m.id] || null;
const allMatches = (key) => seasons[key].flatMap((r) => r.matches.map((m) => ({ ...m, round: r.round })));

function pointsFor(pick, res) {
  if (!pick || !res) return null;
  if (pick.h === res.h && pick.a === res.a) return 3;
  return Math.sign(pick.h - pick.a) === Math.sign(res.h - res.a) ? 1 : 0;
}

function myPoints() {
  let pts = 0, scored = 0;
  for (const key of Object.keys(seasons)) {
    for (const m of allMatches(key)) {
      const p = pointsFor(state.picks[m.id], resultOf(m));
      if (p !== null) { pts += p; scored++; }
    }
  }
  return { pts, scored };
}

// ---- rendering ----

const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtDay = (d) => d.toLocaleDateString("en-KE", { weekday: "short", day: "numeric", month: "short" });
const fmtTime = (d) => d.toLocaleTimeString("en-KE", { hour: "2-digit", minute: "2-digit" });

function matchRow(m, mid) {
  return `<div class="match">
    <div class="team home">${esc(m.home)}</div>
    <div class="mid">${mid}</div>
    <div class="team">${esc(m.away)}</div>
  </div>`;
}

function roundCard(round, matches, midFn) {
  if (!matches.length) return "";
  return `<div class="card">
    <div class="round-head"><span>Matchday ${round}</span><span>${fmtDay(matches[0].kickoff)}</span></div>
    ${matches.map((m) => matchRow(m, midFn(m))).join("")}
  </div>`;
}

function viewFixtures() {
  const rounds = seasons[league]
    .map((r) => ({ round: r.round, matches: r.matches.filter((m) => !resultOf(m)) }))
    .filter((r) => r.matches.length);
  return `<h2>Upcoming fixtures</h2><p class="sub">${LEAGUES[league].name}</p>
    ${rounds.length ? rounds.map((r) => roundCard(r.round, r.matches, (m) =>
      `<div class="time">${fmtTime(m.kickoff)}</div>`)).join("")
      : `<div class="card empty">Season complete. Reset the demo on the Predict tab.</div>`}`;
}

function viewResults() {
  const rounds = seasons[league]
    .map((r) => ({ round: r.round, matches: r.matches.filter((m) => resultOf(m)) }))
    .filter((r) => r.matches.length)
    .reverse();
  return `<h2>Results</h2><p class="sub">${LEAGUES[league].name}</p>
    ${rounds.map((r) => roundCard(r.round, r.matches, (m) => {
      const res = resultOf(m);
      return `<div class="score">${res.h} – ${res.a}</div><div class="time">FT</div>`;
    })).join("")}`;
}

function standings(key) {
  const rows = Object.fromEntries(LEAGUES[key].teams.map((t) =>
    [t, { team: t, p: 0, w: 0, d: 0, l: 0, gf: 0, ga: 0, pts: 0, form: [] }]));
  for (const m of allMatches(key).sort((a, b) => a.kickoff - b.kickoff)) {
    const res = resultOf(m);
    if (!res) continue;
    const h = rows[m.home], a = rows[m.away];
    h.p++; a.p++; h.gf += res.h; h.ga += res.a; a.gf += res.a; a.ga += res.h;
    if (res.h > res.a) { h.w++; a.l++; h.pts += 3; h.form.push("W"); a.form.push("L"); }
    else if (res.h < res.a) { a.w++; h.l++; a.pts += 3; a.form.push("W"); h.form.push("L"); }
    else { h.d++; a.d++; h.pts++; a.pts++; h.form.push("D"); a.form.push("D"); }
  }
  return Object.values(rows).sort((x, y) =>
    y.pts - x.pts || (y.gf - y.ga) - (x.gf - x.ga) || y.gf - x.gf || x.team.localeCompare(y.team));
}

function viewTable() {
  const rows = standings(league);
  return `<h2>League table</h2><p class="sub">${LEAGUES[league].name}</p>
    <div class="card"><table>
      <thead><tr><th>#</th><th class="club">Club</th><th>P</th><th class="hide-sm">W</th><th class="hide-sm">D</th>
        <th class="hide-sm">L</th><th>GD</th><th>Pts</th><th class="hide-sm">Form</th></tr></thead>
      <tbody>${rows.map((r, i) => `<tr>
        <td>${i + 1}</td><td class="club">${esc(r.team)}</td><td>${r.p}</td>
        <td class="hide-sm">${r.w}</td><td class="hide-sm">${r.d}</td><td class="hide-sm">${r.l}</td>
        <td>${r.gf - r.ga > 0 ? "+" : ""}${r.gf - r.ga}</td><td class="pts">${r.pts}</td>
        <td class="hide-sm"><span class="form">${r.form.slice(-5).map((f) => `<i class="${f}">${f}</i>`).join("")}</span></td>
      </tr>`).join("")}</tbody>
    </table></div>`;
}

function viewPredict() {
  const open = seasons[league]
    .map((r) => ({ round: r.round, matches: r.matches.filter((m) => !resultOf(m)) }))
    .filter((r) => r.matches.length);
  const settled = seasons[league]
    .map((r) => ({ round: r.round, matches: r.matches.filter((m) => state.simulated[m.id] && state.picks[m.id]) }))
    .filter((r) => r.matches.length)
    .reverse();
  const { pts, scored } = myPoints();
  const pickVal = (m, side) => (state.picks[m.id] ? state.picks[m.id][side] : "");

  return `<h2>Score predictor</h2>
    <p class="sub">Exact score = 3 points, correct result = 1 point. Free to play, points only.</p>
    <div class="row">
      <form class="field" id="name-form">
        <input id="name" maxlength="24" placeholder="Your fan name" value="${esc(state.name)}" aria-label="Your fan name">
        <button class="btn" type="submit">Save</button>
      </form>
      <span class="note">You: <strong>${pts}</strong> pts from ${scored} settled picks</span>
    </div>
    ${open.length ? open.map((r) => roundCard(r.round, r.matches, (m) => `
      <div class="pick">
        <input inputmode="numeric" maxlength="2" data-id="${m.id}" data-side="h" value="${pickVal(m, "h")}" aria-label="${esc(m.home)} goals">
        <span>–</span>
        <input inputmode="numeric" maxlength="2" data-id="${m.id}" data-side="a" value="${pickVal(m, "a")}" aria-label="${esc(m.away)} goals">
      </div><div class="time">${fmtTime(m.kickoff)}</div>`)).join("")
      : `<div class="card empty">No open fixtures left in this demo season.</div>`}
    <div class="row">
      <button class="btn" id="play-round" ${open.length ? "" : "disabled"}>Play next matchday (demo)</button>
      <button class="btn ghost" id="reset">Reset demo</button>
    </div>
    <p class="note">The demo button fills in results for the next matchday so you can see your picks scored.</p>
    ${settled.length ? `<h2>Your scored picks</h2>` + settled.map((r) => roundCard(r.round, r.matches, (m) => {
      const res = resultOf(m), pick = state.picks[m.id], p = pointsFor(pick, res);
      return `<div class="score">${res.h} – ${res.a}</div>
        <div class="time">you: ${pick.h}–${pick.a}</div><span class="pts-badge pts-${p}">+${p}</span>`;
    })).join("") : ""}`;
}

function viewLeaders() {
  const me = state.name || "You";
  const rows = RIVALS.map(([name, pts]) => ({ name, pts, me: false }))
    .concat([{ name: me, pts: myPoints().pts, me: true }])
    .sort((a, b) => b.pts - a.pts || Number(b.me) - Number(a.me));
  return `<h2>Predictor leaderboard</h2><p class="sub">Both leagues combined</p>
    <div class="card"><table>
      <thead><tr><th>#</th><th class="club">Fan</th><th>Pts</th></tr></thead>
      <tbody>${rows.map((r, i) => `<tr class="${r.me ? "me" : ""}">
        <td>${i + 1}</td><td class="club">${esc(r.name)}${r.me ? " (you)" : ""}</td><td class="pts">${r.pts}</td>
      </tr>`).join("")}</tbody>
    </table></div>`;
}

// ---- penalty shootout: aim at one of six spots, time the power meter, beat the keeper ----

const ZONES = [[0, 0], [0, 1], [0, 2], [1, 0], [1, 1], [1, 2]]; // [row, col]; row 0 is the top half
const ZONE_NAMES = ["top left", "top centre", "top right", "bottom left", "bottom centre", "bottom right"];
const SWEET = [0.7, 0.92]; // power band that beats a keeper who guessed right on a top corner
const SOFT = 0.25;
const RIVAL_SCORES = 0.75;

let pen = null;
let meterRaf = 0;
let meterPower = 0;

function newShootout() {
  const teams = LEAGUES[league].teams;
  const mine = teams.includes(state.club) ? state.club : teams[0];
  const others = teams.filter((t) => t !== mine);
  return {
    league, mine, rival: others[Math.floor(Math.random() * others.length)],
    you: [], them: [], aims: [], aim: null, busy: false, over: false,
    msg: "Tap a spot in the goal to aim.",
  };
}

const count = (kicks) => kicks.filter(Boolean).length;

// Best of five, stopping early once one side can't be caught; level after five goes to sudden death.
function shootoutDecided(p) {
  const y = count(p.you), t = count(p.them), ky = p.you.length, kt = p.them.length;
  if (ky <= 5 && kt <= 5) {
    if (y > t + (5 - kt) || t > y + (5 - ky)) return true;
    return ky === 5 && kt === 5 && y !== t;
  }
  return ky === kt && y !== t;
}

// The keeper favours the corners, and reads you if your last two kicks went the same way.
function keeperDive(p) {
  const [a, b] = p.aims.slice(-2);
  if (a !== undefined && a === b && Math.random() < 0.6) return a;
  const r = Math.random();
  return r < 0.4 ? 0 : r < 0.6 ? 1 : 2;
}

function resolveKick(row, col, power) {
  const keeper = keeperDive(pen);
  if (power > SWEET[1]) {
    const over = row === 0 || col === 1;
    return { goal: false, miss: over ? "over" : "wide", keeper, why: over ? "Over the bar!" : "Wide of the post!" };
  }
  if (power < SOFT) return { goal: false, keeper: col, why: "Too soft. Easy save." };
  if (keeper === col) {
    if (row === 0 && col !== 1 && power >= SWEET[0]) return { goal: true, keeper, why: "Top corner! The keeper had no chance." };
    return { goal: false, keeper, why: "Saved! The keeper guessed right." };
  }
  return { goal: true, keeper, why: "GOAL!" };
}

function tallyHtml() {
  const n = Math.max(5, pen.you.length, pen.them.length);
  const dots = (kicks) => Array.from({ length: n }, (_, i) =>
    `<i class="dot ${kicks[i] === undefined ? "" : kicks[i] ? "in" : "out"}"></i>`).join("");
  return `<span class="who">${esc(pen.mine)}</span><span class="dots">${dots(pen.you)}</span><span class="goals">${count(pen.you)}</span>
    <span class="who">${esc(pen.rival)}</span><span class="dots">${dots(pen.them)}</span><span class="goals">${count(pen.them)}</span>`;
}

function viewPenalties() {
  if (!pen || pen.league !== league) pen = newShootout();
  const s = state.pens;
  const rate = s.kicks ? Math.round((100 * s.goals) / s.kicks) : 0;
  return `<h2>Penalty shootout</h2>
    <p class="sub">Aim, then stop the needle in the green. Best of five, then sudden death.</p>
    <div class="row">
      <label class="field">
        <select id="pen-club" aria-label="Your club">
          ${LEAGUES[league].teams.map((t) => `<option ${t === pen.mine ? "selected" : ""}>${esc(t)}</option>`).join("")}
        </select>
      </label>
      <span class="note">vs <strong>${esc(pen.rival)}</strong></span>
    </div>
    <div class="card">
      <div class="tally" id="tally">${tallyHtml()}</div>
      <div class="pitch">
        <div class="goal" id="goal">
          ${ZONES.map((_, i) => `<button class="zone ${pen.aim === i ? "aim" : ""}" data-zone="${i}"
            aria-label="Aim ${ZONE_NAMES[i]}" ${pen.busy || pen.over ? "disabled" : ""}></button>`).join("")}
          <div class="keeper" id="keeper"></div>
        </div>
        <div class="ball" id="ball" aria-hidden="true">⚽</div>
      </div>
      <div class="meter-wrap">
        <div class="meter" aria-hidden="true"><span class="soft"></span><span class="sweet"></span><span class="over"></span><span class="needle" id="needle"></span></div>
        <div class="meter-legend"><span>Too soft</span><span>Placed</span><span>Power</span><span>Over</span></div>
      </div>
      <p class="pen-msg" id="pen-msg" role="status">${esc(pen.msg)}</p>
      <div class="row pen-actions">
        <button class="btn" id="shoot" ${pen.aim === null || pen.busy || pen.over ? "disabled" : ""}>Shoot</button>
        <button class="btn ghost" id="pen-new">New shootout</button>
      </div>
    </div>
    <h2>Your record</h2>
    <div class="card stats">
      <div><strong>${s.played}</strong><span>Played</span></div>
      <div><strong>${s.won}</strong><span>Won</span></div>
      <div><strong>${s.goals}</strong><span>Goals</span></div>
      <div><strong>${rate}%</strong><span>Scored</span></div>
    </div>`;
}

function runMeter() {
  const needle = document.getElementById("needle");
  if (!needle || pen.aim === null || pen.busy || pen.over) return;
  const start = performance.now();
  const step = (now) => {
    const phase = ((now - start) / 1300) % 2;
    meterPower = phase < 1 ? phase : 2 - phase;
    needle.style.left = `calc(${(meterPower * 100).toFixed(1)}% - 2px)`;
    meterRaf = requestAnimationFrame(step);
  };
  meterRaf = requestAnimationFrame(step);
}

function setPenMsg(text) {
  pen.msg = text;
  const el = document.getElementById("pen-msg");
  if (el) el.textContent = text;
}

function finishIfDecided() {
  if (!shootoutDecided(pen)) return false;
  const y = count(pen.you), t = count(pen.them);
  pen.over = true;
  pen.busy = false;
  state.pens.played++;
  if (y > t) state.pens.won++;
  saveState();
  pen.msg = y > t ? `${pen.mine} win the shootout ${y}–${t}!` : `${pen.rival} win the shootout ${t}–${y}. Try again.`;
  if (currentTab() === "penalties") render();
  return true;
}

function animateKick(row, col, kick) {
  const ball = document.getElementById("ball");
  const goal = document.getElementById("goal");
  const keeper = document.getElementById("keeper");
  if (!ball || !goal) return;
  const b = ball.getBoundingClientRect();
  const z = goal.querySelector(`[data-zone="${ZONES.findIndex(([r, c]) => r === row && c === col)}"]`).getBoundingClientRect();
  const g = goal.getBoundingClientRect();
  let tx = z.left + z.width / 2 - (b.left + b.width / 2);
  let ty = z.top + z.height / 2 - (b.top + b.height / 2);
  if (kick.miss === "over") ty = g.top - b.height - (b.top + b.height / 2);
  if (kick.miss === "wide") tx += (col === 0 ? -1 : 1) * z.width * 0.75;
  ball.style.transform = `translate(${tx}px, ${ty}px) scale(.6)`;
  keeper.classList.add(`dive-${kick.keeper}`);
}

function shoot() {
  if (pen.aim === null || pen.busy || pen.over) return;
  cancelAnimationFrame(meterRaf);
  pen.busy = true;
  document.getElementById("shoot").disabled = true;
  document.querySelectorAll(".zone").forEach((z) => { z.disabled = true; });

  const [row, col] = ZONES[pen.aim];
  const kick = resolveKick(row, col, meterPower);
  pen.aims.push(col);
  animateKick(row, col, kick);

  setTimeout(() => {
    pen.you.push(kick.goal);
    state.pens.kicks++;
    if (kick.goal) state.pens.goals++;
    saveState();
    setPenMsg(kick.why);
    const tally = document.getElementById("tally");
    if (tally) tally.innerHTML = tallyHtml();
    if (finishIfDecided()) return;

    setTimeout(() => {
      const scored = Math.random() < RIVAL_SCORES;
      pen.them.push(scored);
      if (finishIfDecided()) return;
      pen.busy = false;
      pen.aim = null;
      pen.msg = `${pen.rival} ${scored ? "score" : "miss"}. Your kick: tap a spot to aim.`;
      if (currentTab() === "penalties") render();
    }, 1100);
  }, 650);
}

const VIEWS = {
  fixtures: viewFixtures, results: viewResults, table: viewTable,
  predict: viewPredict, penalties: viewPenalties, leaders: viewLeaders,
};

function currentTab() {
  const t = location.hash.slice(1);
  return VIEWS[t] ? t : "fixtures";
}

function render() {
  cancelAnimationFrame(meterRaf);
  const tab = currentTab();
  document.getElementById("view").innerHTML = VIEWS[tab]();
  document.querySelectorAll(".tabs a").forEach((a) => a.classList.toggle("active", a.dataset.tab === tab));
  const nav = document.querySelector(".tabs");
  const active = nav.querySelector(".active");
  const left = active.offsetLeft - nav.offsetLeft;
  if (left < nav.scrollLeft || left + active.offsetWidth > nav.scrollLeft + nav.clientWidth) nav.scrollLeft = left - 16;
  document.querySelectorAll(".league-switch button").forEach((b) => b.classList.toggle("active", b.dataset.league === league));
  if (tab === "penalties") runMeter();
}

// ---- events ----

document.querySelector(".league-switch").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-league]");
  if (!btn) return;
  league = btn.dataset.league;
  render();
});

document.getElementById("view").addEventListener("input", (e) => {
  const input = e.target.closest("input[data-id]");
  if (!input) return;
  input.value = input.value.replace(/\D/g, "").slice(0, 2);
  const row = input.closest(".pick");
  const [h, a] = [...row.querySelectorAll("input")].map((i) => i.value);
  if (h === "" || a === "") delete state.picks[input.dataset.id];
  else state.picks[input.dataset.id] = { h: Number(h), a: Number(a) };
  saveState();
});

document.getElementById("view").addEventListener("submit", (e) => {
  if (e.target.id !== "name-form") return;
  e.preventDefault();
  state.name = document.getElementById("name").value.trim().slice(0, 24);
  saveState();
  render();
});

document.getElementById("view").addEventListener("click", (e) => {
  if (e.target.id === "play-round") {
    const next = seasons[league].find((r) => r.matches.some((m) => !resultOf(m)));
    if (!next) return;
    for (const m of next.matches) {
      if (!resultOf(m)) state.simulated[m.id] = { h: goals(Math.random), a: goals(Math.random) };
    }
    saveState();
    render();
  } else if (e.target.id === "reset") {
    state.picks = {};
    state.simulated = {};
    saveState();
    render();
  } else if (e.target.dataset.zone !== undefined) {
    if (pen.busy || pen.over) return;
    pen.aim = Number(e.target.dataset.zone);
    pen.msg = `Aiming ${ZONE_NAMES[pen.aim]}. Tap Shoot when the needle is in the green.`;
    render();
  } else if (e.target.id === "shoot") {
    shoot();
  } else if (e.target.id === "pen-new") {
    if (pen.busy) return;
    pen = newShootout();
    render();
  }
});

document.getElementById("view").addEventListener("change", (e) => {
  if (e.target.id !== "pen-club" || pen.busy) return;
  state.club = e.target.value;
  saveState();
  pen = newShootout();
  render();
});

window.addEventListener("hashchange", render);
render();

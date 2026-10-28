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

function loadState() {
  try {
    const raw = localStorage.getItem(STORE_KEY);
    if (raw) return Object.assign({ name: "", picks: {}, simulated: {} }, JSON.parse(raw));
  } catch (e) { /* storage unavailable: fall back to memory */ }
  return { name: "", picks: {}, simulated: {} };
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

const VIEWS = { fixtures: viewFixtures, results: viewResults, table: viewTable, predict: viewPredict, leaders: viewLeaders };

function currentTab() {
  const t = location.hash.slice(1);
  return VIEWS[t] ? t : "fixtures";
}

function render() {
  const tab = currentTab();
  document.getElementById("view").innerHTML = VIEWS[tab]();
  document.querySelectorAll(".tabs a").forEach((a) => a.classList.toggle("active", a.dataset.tab === tab));
  document.querySelectorAll(".league-switch button").forEach((b) => b.classList.toggle("active", b.dataset.league === league));
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
  }
});

window.addEventListener("hashchange", render);
render();

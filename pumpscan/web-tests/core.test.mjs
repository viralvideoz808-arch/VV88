import assert from "node:assert/strict";
import { test } from "node:test";

import {
  AGENTS, AGENTS_BY_ID, AVOID, BUY, buildSignal, curveProgressFromMcapSol, defaultRules, makeSnapshot,
  runCouncil, scoreOutcome, vote,
} from "../web/js/core.js";
import { NOW, context, runner } from "./helpers.mjs";

const rules = (patch = {}) => ({ ...defaultRules(), ...patch });

test("twenty-five agents, eight vetoes", () => {
  assert.equal(AGENTS.length, 25);
  assert.equal(new Set(AGENTS.map((a) => a.id)).size, 25);
  assert.equal(AGENTS.filter((a) => a.veto).length, 8);
});

test("runner reaches consensus", () => {
  const tok = runner();
  const v = runCouncil(tok, context([tok]), rules());
  assert.deepEqual(v.vetoed_by, []);
  assert.ok(v.buy_votes >= 20, JSON.stringify(v.votes.filter((x) => x.stance !== BUY)));
  assert.equal(v.avoid_votes, 0);
  assert.ok(v.is_signal);
});

test("single safety veto blocks the signal", () => {
  const tok = runner();
  tok.rug.mint_authority = true;
  const v = runCouncil(tok, context([tok]), rules());
  assert.ok(v.vetoed_by.includes("mint_auth"));
  assert.ok(!v.is_signal);
});

test("whale holder vetoes", () => {
  const tok = runner();
  tok.rug.max_holder_pct = 25;
  const v = runCouncil(tok, context([tok]), rules());
  assert.ok(v.vetoed_by.includes("holders") && !v.is_signal);
});

test("serial launcher vetoes", () => {
  const tok = runner();
  const ctx = context([tok]);
  ctx.creator_counts.set("dev1", 4);
  assert.deepEqual(runCouncil(tok, ctx, rules()).vetoed_by, ["serial_dev"]);
});

test("crash vetoes", () => {
  const tok = runner();
  const s = tok.snap;
  tok.addSnapshot(makeSnapshot({ t: NOW + 30, price_usd: s.price_usd * 0.4, mcap_usd: s.mcap_usd * 0.4,
    buys_m5: 50, sells_m5: 200, vol_m5: 20000, vol_h1: 60000, change_m5: -45 }));
  const v = runCouncil(tok, context([tok], { now: NOW + 30 }), rules());
  assert.ok(v.vetoed_by.includes("rug_pattern") && v.vetoed_by.includes("exit_liq"));
});

test("unanimous mode needs every agent", () => {
  const tok = runner();
  const v = runCouncil(tok, context([tok]), rules({ min_buy_votes: 25 }));
  assert.equal(v.is_signal, v.buy_votes === 25);
});

test("missing data abstains instead of guessing", () => {
  const tok = runner();
  tok.rug = null;
  tok.holder_history = [];
  const v = runCouncil(tok, context([tok]), rules());
  const abstained = new Set(v.votes.filter((x) => x.stance === "ABSTAIN").map((x) => x.agent));
  for (const id of ["mint_auth", "holders", "rugcheck", "insiders"]) assert.ok(abstained.has(id), id);
  assert.ok(!v.is_signal);
});

test("'no presale' is not spam", () => {
  const tok = runner();
  tok.description = "Community first, no presale, no airdrop.";
  assert.equal(vote(AGENTS_BY_ID.metadata, tok, context([tok])).stance, BUY);
  tok.description = "Presale live now! 1000x guaranteed";
  assert.equal(vote(AGENTS_BY_ID.metadata, tok, context([tok])).stance, AVOID);
});

test("copycat of a bigger coin", () => {
  const big = runner("BIGpump"), small = runner("SMALLpump");
  for (const s of small.snapshots) s.mcap_usd /= 10;
  const ctx = context([big, small]);
  assert.equal(vote(AGENTS_BY_ID.copycat, small, ctx).stance, AVOID);
  assert.equal(vote(AGENTS_BY_ID.copycat, big, ctx).stance, BUY);
});

test("broken input never throws", () => {
  const tok = runner();
  tok.snapshots[tok.snapshots.length - 1].mcap_usd = NaN;
  for (const a of AGENTS) {
    const v = vote(a, tok, context([tok]));
    assert.ok(Number.isFinite(v.strength));
  }
});

test("curve progress", () => {
  assert.ok(curveProgressFromMcapSol(28) < 0.02);
  const full = curveProgressFromMcapSol(411);
  assert.ok(full >= 0.99 && full <= 1);
  const mid = curveProgressFromMcapSol(120);
  assert.ok(mid > 0.3 && mid < 0.5);
});

test("signal plan and outcome", () => {
  const tok = runner();
  const r = rules();
  const v = runCouncil(tok, context([tok]), r);
  const sig = buildSignal(tok, v, r, NOW);
  assert.ok(sig.headline.startsWith("BUY NOW: Kimchi ($KIMCHI) within the next 10 minutes"));
  assert.ok(sig.stop_loss_pct < 0 && 0 < sig.take_profit_1_pct && sig.take_profit_1_pct < sig.take_profit_2_pct);
  assert.equal(sig.agree, v.buy_votes);
  assert.equal(scoreOutcome(sig, tok, NOW + 60), null); // window still open
  const entry = sig.entry_mcap_usd;
  tok.addSnapshot(makeSnapshot({ t: NOW + 300, price_usd: 1, mcap_usd: entry * 1.6 }));
  tok.addSnapshot(makeSnapshot({ t: NOW + 590, price_usd: 1, mcap_usd: entry * 1.3 }));
  const out = scoreOutcome(sig, tok, NOW + 700);
  assert.equal(out.status, "win");
  assert.equal(out.peak_pct, 60);
  assert.equal(out.end_pct, 30);
});

test("outcome counts the stop first when both were hit", () => {
  const tok = runner();
  const r = rules();
  const sig = buildSignal(tok, runCouncil(tok, context([tok]), r), r, NOW);
  const entry = sig.entry_mcap_usd;
  tok.addSnapshot(makeSnapshot({ t: NOW + 100, price_usd: 1, mcap_usd: entry * 0.5 }));
  tok.addSnapshot(makeSnapshot({ t: NOW + 200, price_usd: 1, mcap_usd: entry * 2 }));
  assert.equal(scoreOutcome(sig, tok, NOW + 700).status, "stopped");
});

test("unreadable metadata abstains instead of voting 'no socials'", () => {
  const tok = runner();
  Object.assign(tok, { twitter: "", telegram: "", website: "", description: "", metadata_unreachable: true });
  const ctx = context([tok]);
  assert.equal(vote(AGENTS_BY_ID.socials, tok, ctx).stance, "ABSTAIN");
  assert.equal(vote(AGENTS_BY_ID.metadata, tok, ctx).stance, "ABSTAIN");
  tok.metadata_unreachable = false;
  assert.equal(vote(AGENTS_BY_ID.socials, tok, ctx).stance, AVOID);
});

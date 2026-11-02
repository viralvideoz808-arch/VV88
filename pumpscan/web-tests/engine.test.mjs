import assert from "node:assert/strict";
import { test } from "node:test";

import { makeSnapshot } from "../web/js/core.js";
import { Engine } from "../web/js/engine.js";
import { Simulator } from "../web/js/simulator.js";
import { NOW, runner } from "./helpers.mjs";

test("council pass emits one signal, then respects the cooldown", () => {
  const e = new Engine();
  e.clock = () => NOW;
  const tok = runner();
  e.addToken(tok);
  e.ctx.sol_usd = 150;
  const fresh = e.councilPass();
  assert.equal(fresh.length, 1);
  assert.equal(fresh[0].symbol, "KIMCHI");
  assert.deepEqual(e.councilPass(), []);
  const st = e.state();
  assert.equal(st.active_signal.mint, tok.mint);
  assert.equal(st.active_signal.remaining_s, 600);
});

test("signal is voided when price runs past the max entry", () => {
  const e = new Engine();
  let now = NOW;
  e.clock = () => now;
  const tok = runner();
  e.addToken(tok);
  e.ctx.sol_usd = 150;
  e.councilPass();
  tok.addSnapshot(makeSnapshot({ t: NOW + 20, price_usd: 1, mcap_usd: tok.snap.mcap_usd * 1.5, buys_m5: 200,
    sells_m5: 50, vol_m5: 20000, vol_h1: 50000, change_m5: 60 }));
  now = NOW + 20;
  e.councilPass();
  assert.equal(e.signals.at(-1).state, "void");
});

test("demo engine starts and stops", async () => {
  const e = new Engine({ demo: true, demoSpeed: 30, makeSimulator: (eng, speed) => new Simulator(eng, speed, 7) });
  e.start();
  await new Promise((r) => setTimeout(r, 2500));
  const st = e.state();
  e.stop();
  assert.ok(st.running);
  assert.equal(st.mode, "demo");
  assert.ok(st.stats.watched >= 60);
  assert.ok(st.stats.passes >= 1);
  assert.ok(st.candidates.length > 0);
  assert.equal(st.agents.length, 25);
  assert.ok(!e.running);
});

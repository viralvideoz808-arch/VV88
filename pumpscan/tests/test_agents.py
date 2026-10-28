from pumpscan.agents import AGENTS, AGENTS_BY_ID, AVOID, BUY
from pumpscan.council import Rules, build_signal, run_council, score_outcome
from pumpscan.models import Snapshot, curve_progress_from_mcap_sol

from .helpers import NOW, context, runner


def test_twenty_five_agents_eight_vetoes():
    assert len(AGENTS) == 25
    assert len({a.id for a in AGENTS}) == 25
    assert sum(a.veto for a in AGENTS) == 8


def test_runner_reaches_consensus():
    tok = runner()
    v = run_council(tok, context(tok), Rules())
    assert not v.vetoed_by, [x for x in v.votes if x.stance != BUY]
    assert v.buy_votes >= 20, [(x.agent, x.stance, x.reason) for x in v.votes if x.stance != BUY]
    assert v.avoid_votes == 0
    assert v.is_signal


def test_single_safety_veto_blocks_signal():
    tok = runner()
    tok.rug.mint_authority = True
    v = run_council(tok, context(tok), Rules())
    assert "mint_auth" in v.vetoed_by
    assert not v.is_signal


def test_whale_holder_vetoes():
    tok = runner()
    tok.rug.max_holder_pct = 25
    v = run_council(tok, context(tok), Rules())
    assert "holders" in v.vetoed_by and not v.is_signal


def test_serial_launcher_vetoes():
    tok = runner()
    ctx = context(tok)
    ctx.creator_counts["dev1"] = 4
    assert run_council(tok, ctx, Rules()).vetoed_by == ["serial_dev"]


def test_crash_vetoes():
    tok = runner()
    s = tok.snap
    tok.add_snapshot(Snapshot(t=NOW + 30, price_usd=s.price_usd * 0.4, mcap_usd=s.mcap_usd * 0.4,
                              buys_m5=50, sells_m5=200, vol_m5=20000, vol_h1=60000, change_m5=-45))
    v = run_council(tok, context(tok, now=NOW + 30), Rules())
    assert {"rug_pattern", "exit_liq"} <= set(v.vetoed_by)


def test_unanimous_mode_needs_every_agent():
    tok = runner()
    v = run_council(tok, context(tok), Rules(min_buy_votes=25))
    assert v.is_signal == (v.buy_votes == 25)


def test_missing_data_abstains_instead_of_guessing():
    tok = runner()
    tok.rug = None
    tok.holder_history.clear()
    v = run_council(tok, context(tok), Rules())
    abstained = {x.agent for x in v.votes if x.stance == "ABSTAIN"}
    assert {"mint_auth", "holders", "rugcheck", "insiders"} <= abstained
    assert not v.is_signal


def test_no_presale_is_not_spam():
    tok = runner()
    tok.description = "Community first, no presale, no airdrop."
    assert AGENTS_BY_ID["metadata"].vote(tok, context(tok)).stance == BUY
    tok.description = "Presale live now! 1000x guaranteed"
    assert AGENTS_BY_ID["metadata"].vote(tok, context(tok)).stance == AVOID


def test_copycat_of_bigger_coin():
    big = runner("BIGpump")
    small = runner("SMALLpump")
    for s in small.snapshots:
        s.mcap_usd /= 10
    ctx = context(big, small)
    assert AGENTS_BY_ID["copycat"].vote(small, ctx).stance == AVOID
    assert AGENTS_BY_ID["copycat"].vote(big, ctx).stance == BUY


def test_agent_exception_becomes_abstain():
    tok = runner()
    tok.snapshots[-1].mcap_usd = float("nan")
    for a in AGENTS:
        a.vote(tok, context(tok))  # must not raise


def test_curve_progress():
    assert curve_progress_from_mcap_sol(28) < 0.02
    assert 0.99 <= curve_progress_from_mcap_sol(411) <= 1.0
    assert 0.3 < curve_progress_from_mcap_sol(120) < 0.5


def test_signal_plan_and_outcome():
    tok = runner()
    rules = Rules()
    v = run_council(tok, context(tok), rules)
    sig = build_signal(tok, v, rules, NOW)
    assert sig["headline"].startswith("BUY NOW: Kimchi ($KIMCHI) within the next 10 minutes")
    assert sig["stop_loss_pct"] < 0 < sig["take_profit_1_pct"] < sig["take_profit_2_pct"]
    assert sig["agree"] == v.buy_votes and sig["reasons"]
    assert score_outcome(sig, tok, NOW + 60) is None  # window still open
    entry = sig["entry_mcap_usd"]
    tok.add_snapshot(Snapshot(t=NOW + 300, price_usd=1, mcap_usd=entry * 1.6))
    tok.add_snapshot(Snapshot(t=NOW + 590, price_usd=1, mcap_usd=entry * 1.3))
    out = score_outcome(sig, tok, NOW + 700)
    assert out["status"] == "win" and out["peak_pct"] == 60.0 and out["end_pct"] == 30.0


def test_outcome_counts_stop_first_when_both_hit():
    tok = runner()
    rules = Rules()
    sig = build_signal(tok, run_council(tok, context(tok), rules), rules, NOW)
    entry = sig["entry_mcap_usd"]
    tok.add_snapshot(Snapshot(t=NOW + 100, price_usd=1, mcap_usd=entry * 0.5))
    tok.add_snapshot(Snapshot(t=NOW + 200, price_usd=1, mcap_usd=entry * 2))
    assert score_outcome(sig, tok, NOW + 700)["status"] == "stopped"

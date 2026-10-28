from pumpscan.models import Token
from pumpscan.sources import (
    apply_rug_report,
    parse_create_event,
    parse_dex_pair,
    parse_iso,
    parse_listing,
    parse_rug_report,
    parse_trade_event,
    pick_pair,
)

CREATE = {"signature": "sig", "mint": "5AyMkYkChfYquBwdiqRXr9hgQvvvsYGNWaPdiyKnpump", "traderPublicKey": "3YkM",
          "txType": "create", "initialBuy": 39820358.98, "solAmount": 1.156, "bondingCurveKey": "2c5F",
          "vTokensInBondingCurve": 1033179641.0, "vSolInBondingCurve": 31.15, "marketCapSol": 30.15,
          "name": "blephorse", "symbol": "ROMAN", "uri": "https://ipfs.io/ipfs/x", "pool": "pump"}

PAIR = {"chainId": "solana", "dexId": "pumpfun", "pairAddress": "BH1V",
        "baseToken": {"address": "2mKPpump", "name": "Pingu", "symbol": "PINGU"},
        "quoteToken": {"address": "So111", "symbol": "SOL"}, "priceNative": "0.0000002986", "priceUsd": "0.00003007",
        "txns": {"m5": {"buys": 137, "sells": 98}, "h1": {"buys": 1423, "sells": 1141}},
        "volume": {"h1": 112372.22, "m5": 12097.3}, "priceChange": {"m5": 26.61, "h1": 567},
        "marketCap": 30078.05, "pairCreatedAt": 1793196541000,
        "info": {"imageUrl": "https://img", "websites": [{"url": "https://w"}],
                 "socials": [{"url": "https://x.com/a", "type": "twitter"}]}}


def test_create_event():
    tok = parse_create_event(CREATE, now=100.0)
    assert tok.symbol == "ROMAN" and tok.creator == "3YkM" and tok.dev_buy_sol == 1.156
    assert tok.pair_address == "2c5F" and tok.created_at == 100.0
    assert parse_create_event({**CREATE, "pool": "bonk"}) is None


def test_trade_event():
    mint, tr = parse_trade_event({"mint": "m", "txType": "sell", "traderPublicKey": "w", "solAmount": 2}, now=5)
    assert mint == "m" and not tr.is_buy and tr.sol == 2
    assert parse_trade_event({"txType": "create", "mint": "m"}) is None


def test_dex_pair():
    tok = Token(mint="2mKPpump")
    sol = parse_dex_pair(tok, PAIR, now=10)
    s = tok.snap
    assert s.buys_m5 == 137 and s.mcap_usd == 30078.05 and s.change_m5 == 26.61
    assert round(sol) == 101
    assert tok.symbol == "PINGU" and tok.twitter == "https://x.com/a" and tok.website == "https://w"
    assert tok.created_at == 1793196541 and not tok.migrated and tok.has_profile
    parse_dex_pair(tok, {**PAIR, "dexId": "pumpswap", "liquidity": {"usd": 30000}}, now=20)
    assert tok.migrated and tok.snap.liquidity_usd == 30000


def test_pick_pair_prefers_pump_venues():
    pairs = [{"dexId": "raydium", "liquidity": {"usd": 9e9}}, {"dexId": "pumpswap"}]
    assert pick_pair(pairs)["dexId"] == "pumpswap"
    assert pick_pair([]) is None


def test_rug_report_excludes_pool_and_backfills():
    d = {"token": {"supply": 1_000_000_000_000_000, "decimals": 6},
         "knownAccounts": {"POOL": {"type": "AMM"}, "DEV": {"type": "CREATOR"}},
         "markets": [{"pubkey": "MKT"}],
         "topHolders": [{"owner": "POOL", "address": "a", "pct": 70}, {"owner": "w1", "address": "b", "pct": 4, "insider": True},
                        {"owner": "w2", "address": "c", "pct": 3}],
         "creatorBalance": 20_000_000_000_000, "totalHolders": 120, "risks": [
             {"name": "Low liquidity", "level": "danger"}, {"name": "Few LPs", "level": "warn"}],
         "creator": "DEV", "detectedAt": "2026-10-28T14:09:01.588887933Z",
         "tokenMeta": {"name": "Pingu", "symbol": "PINGU", "uri": "https://meta/x"},
         "fileMeta": {"description": "d", "image": "https://i"}}
    tok = Token(mint="m")
    r = parse_rug_report(d, tok, now=1.0)
    assert r.top10_pct == 7 and r.max_holder_pct == 4 and r.insider_pct == 4
    assert r.creator_pct == 2.0 and r.danger_risks == ["Low liquidity"] and r.warn_risks == ["Few LPs"]
    assert apply_rug_report(tok, r) is True  # metadata JSON should be fetched
    assert tok.creator == "DEV" and tok.symbol == "PINGU" and tok.uri == "https://meta/x"
    assert tok.created_at == parse_iso("2026-10-28T14:09:01.588887Z")
    assert tok.holder_history[-1] == (1.0, 120)


def test_listing_filters_to_pump_on_solana():
    items = [{"chainId": "solana", "tokenAddress": "Abcpump", "totalAmount": 30},
             {"chainId": "solana", "tokenAddress": "NotAPumpCoin"}, {"chainId": "bsc", "tokenAddress": "0xpump"}]
    out = parse_listing(items)
    assert [o["mint"] for o in out] == ["Abcpump"] and out[0]["boosts"] == 30


def test_metadata_falls_back_across_ipfs_gateways():
    import asyncio

    import httpx

    from pumpscan.sources import Http

    seen = []

    def handler(req):
        seen.append(str(req.url))
        if req.url.host == "pump.mypinata.cloud":
            return httpx.Response(429)
        return httpx.Response(200, json={"description": "hi", "twitter": "https://x.com/a"})

    async def go():
        h = Http()
        h.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            return await h.metadata("https://ipfs.io/ipfs/bafkCID")
        finally:
            await h.close()

    assert asyncio.run(go())["description"] == "hi"
    assert seen == ["https://pump.mypinata.cloud/ipfs/bafkCID", "https://ipfs.io/ipfs/bafkCID"]

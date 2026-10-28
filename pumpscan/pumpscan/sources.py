"""Live data: PumpPortal (launches, migrations, optional trades), DexScreener, Rugcheck, IPFS metadata.

Parsing is kept in plain functions so it can be tested without the network.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime
from typing import Any, Awaitable, Callable

import httpx

from .models import RugReport, Snapshot, Token, Trade

log = logging.getLogger("pumpscan.sources")

PUMPPORTAL_WS = "wss://pumpportal.fun/api/data"
DEX_TOKENS = "https://api.dexscreener.com/tokens/v1/solana/{}"
DEX_PROFILES = "https://api.dexscreener.com/token-profiles/latest/v1"
DEX_BOOSTS_LATEST = "https://api.dexscreener.com/token-boosts/latest/v1"
DEX_BOOSTS_TOP = "https://api.dexscreener.com/token-boosts/top/v1"
RUGCHECK_REPORT = "https://api.rugcheck.xyz/v1/tokens/{}/report"
PUMPFUN_COINS = "https://frontend-api-v3.pump.fun/coins"
# Public gateways rate-limit hard (ipfs.io answers 429 under load); pump.fun's own pin gateway goes first.
IPFS_GATEWAYS = (
    "https://pump.mypinata.cloud/ipfs/",
    "https://ipfs.io/ipfs/",
    "https://gateway.pinata.cloud/ipfs/",
    "https://dweb.link/ipfs/",
)
USER_AGENT = "pumpscan/0.1 (+https://github.com/viralvideoz808-arch/VV88)"


class RateLimiter:
    """At most `rate` calls per `per` seconds, spread evenly."""

    def __init__(self, rate: int, per: float):
        self.interval = per / rate
        self._next = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            delay = self._next - now
            self._next = max(now, self._next) + self.interval
        if delay > 0:
            await asyncio.sleep(delay)


# ---------------------------------------------------------------- parsing


def is_pump_mint(mint: str) -> bool:
    return mint.endswith("pump")


def parse_create_event(msg: dict, now: float | None = None) -> Token | None:
    """A PumpPortal `create` event -> Token. Only pump.fun coins are kept."""
    if msg.get("txType") != "create" or msg.get("pool", "pump") != "pump":
        return None
    mint = msg.get("mint")
    if not mint:
        return None
    t = now or time.time()
    return Token(
        mint=mint,
        name=str(msg.get("name") or "")[:64],
        symbol=str(msg.get("symbol") or "")[:24],
        created_at=t,
        first_seen=t,
        creator=msg.get("traderPublicKey") or "",
        pool="pump",
        dev_buy_sol=float(msg.get("solAmount") or 0),
        dev_tokens=float(msg.get("initialBuy") or 0),
        uri=msg.get("uri") or "",
        pair_address=msg.get("bondingCurveKey") or "",
    )


def parse_trade_event(msg: dict, now: float | None = None) -> tuple[str, Trade] | None:
    if msg.get("txType") not in ("buy", "sell") or not msg.get("mint"):
        return None
    return msg["mint"], Trade(
        t=now or time.time(),
        trader=msg.get("traderPublicKey") or "",
        is_buy=msg["txType"] == "buy",
        sol=float(msg.get("solAmount") or 0),
        mcap_sol=float(msg.get("marketCapSol") or 0),
    )


def parse_metadata(tok: Token, meta: dict) -> None:
    tok.description = str(meta.get("description") or "")[:500]
    tok.twitter = tok.twitter or str(meta.get("twitter") or "")
    tok.telegram = tok.telegram or str(meta.get("telegram") or "")
    tok.website = tok.website or str(meta.get("website") or "")
    tok.image = tok.image or str(meta.get("image") or "")
    tok.metadata_loaded = True


def pick_pair(pairs: list[dict]) -> dict | None:
    """Prefer the pump.fun curve or PumpSwap pool; otherwise the deepest pool."""
    if not pairs:
        return None
    for dex in ("pumpswap", "pumpfun"):
        for p in pairs:
            if p.get("dexId") == dex:
                return p
    return max(pairs, key=lambda p: (p.get("liquidity") or {}).get("usd") or 0)


def parse_dex_pair(tok: Token, pair: dict, now: float) -> float | None:
    """Apply a DexScreener pair to the token. Returns the implied SOL/USD price, if any."""
    txns = pair.get("txns") or {}
    vol = pair.get("volume") or {}
    chg = pair.get("priceChange") or {}
    m5, h1 = txns.get("m5") or {}, txns.get("h1") or {}
    price = float(pair.get("priceUsd") or 0)
    mcap = float(pair.get("marketCap") or pair.get("fdv") or 0)
    if price <= 0 or mcap <= 0:
        return None
    liq = (pair.get("liquidity") or {}).get("usd")
    tok.add_snapshot(Snapshot(
        t=now, price_usd=price, mcap_usd=mcap,
        buys_m5=int(m5.get("buys") or 0), sells_m5=int(m5.get("sells") or 0),
        buys_h1=int(h1.get("buys") or 0), sells_h1=int(h1.get("sells") or 0),
        vol_m5=float(vol.get("m5") or 0), vol_h1=float(vol.get("h1") or 0),
        change_m5=float(chg.get("m5") or 0), change_h1=float(chg.get("h1") or 0),
        liquidity_usd=float(liq) if liq is not None else None,
    ))
    tok.dex_id = pair.get("dexId") or tok.dex_id
    tok.migrated = tok.migrated or tok.dex_id not in ("pumpfun", "")
    if tok.dex_id == "pumpfun" and pair.get("pairAddress"):
        tok.pair_address = pair["pairAddress"]
    base = pair.get("baseToken") or {}
    tok.name = tok.name or str(base.get("name") or "")
    tok.symbol = tok.symbol or str(base.get("symbol") or "")
    if pair.get("pairCreatedAt"):
        tok.pair_created_at = pair["pairCreatedAt"] / 1000
        if tok.created_at is None and tok.dex_id == "pumpfun":
            tok.created_at = tok.pair_created_at
    info = pair.get("info") or {}
    if info:
        tok.has_profile = True
        tok.image = tok.image or str(info.get("imageUrl") or "")
        for soc in info.get("socials") or []:
            if soc.get("type") == "twitter":
                tok.twitter = tok.twitter or soc.get("url", "")
            elif soc.get("type") == "telegram":
                tok.telegram = tok.telegram or soc.get("url", "")
        for site in info.get("websites") or []:
            tok.website = tok.website or site.get("url", "")
    tok.boosts = max(tok.boosts, int((pair.get("boosts") or {}).get("active") or 0))
    quote = pair.get("quoteToken") or {}
    native = float(pair.get("priceNative") or 0)
    if quote.get("symbol") == "SOL" and native > 0:
        return price / native
    return None


def parse_iso(ts: str | None) -> float | None:
    """'2026-10-28T14:09:01.588887933Z' -> unix seconds (Python's parser takes at most 6 fraction digits)."""
    if not ts:
        return None
    ts = re.sub(r"(\.\d{6})\d+", r"\1", ts).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(ts).timestamp()
    except ValueError:
        return None


def parse_rug_report(d: dict, tok: Token, now: float) -> RugReport:
    token_info = d.get("token") or {}
    supply = float(token_info.get("supply") or 0)
    known = d.get("knownAccounts") or {}
    excluded = {addr for addr, k in known.items() if (k or {}).get("type") in ("AMM", "LOCKER")}
    excluded |= {m.get("pubkey") for m in d.get("markets") or [] if m.get("pubkey")}
    if tok.pair_address:
        excluded.add(tok.pair_address)
    holders = [h for h in d.get("topHolders") or []
               if h.get("owner") not in excluded and h.get("address") not in excluded]
    pcts = [float(h.get("pct") or 0) for h in holders]
    creator_bal = d.get("creatorBalance")
    creator_tokens = d.get("creatorTokens")
    risks = d.get("risks") or []
    return RugReport(
        fetched_at=now,
        score_normalised=d.get("score_normalised"),
        mint_authority=bool(d.get("mintAuthority") or token_info.get("mintAuthority")),
        freeze_authority=bool(d.get("freezeAuthority") or token_info.get("freezeAuthority")),
        rugged=bool(d.get("rugged")),
        total_holders=d.get("totalHolders"),
        top10_pct=sum(pcts[:10]) if d.get("topHolders") is not None else None,
        max_holder_pct=max(pcts) if pcts else 0.0,
        insider_pct=sum(float(h.get("pct") or 0) for h in holders if h.get("insider")),
        insiders_detected=int(d.get("graphInsidersDetected") or 0),
        creator_pct=(float(creator_bal) / supply * 100) if creator_bal is not None and supply > 0 else None,
        creator_token_count=len(creator_tokens) if isinstance(creator_tokens, list) else None,
        danger_risks=[r.get("name", "") for r in risks if r.get("level") == "danger"],
        warn_risks=[r.get("name", "") for r in risks if r.get("level") == "warn"],
        creator=str(d.get("creator") or ""),
        detected_at=parse_iso(d.get("detectedAt")),
        name=str((d.get("tokenMeta") or {}).get("name") or ""),
        symbol=str((d.get("tokenMeta") or {}).get("symbol") or ""),
        uri=str((d.get("tokenMeta") or {}).get("uri") or ""),
        description=str((d.get("fileMeta") or {}).get("description") or ""),
        image=str((d.get("fileMeta") or {}).get("image") or ""),
    )


def apply_rug_report(tok: Token, r: RugReport) -> bool:
    """Store the report and fill facts the scan lacked. True when the metadata JSON should be fetched."""
    tok.rug = r
    if r.total_holders is not None:
        tok.holder_history.append((r.fetched_at, r.total_holders))
    tok.creator = tok.creator or r.creator
    tok.name = tok.name or r.name[:64]
    tok.symbol = tok.symbol or r.symbol[:24]
    if tok.created_at is None and r.detected_at:
        tok.created_at = r.detected_at
    if tok.metadata_loaded:
        return False
    if r.uri and not tok.uri:
        tok.uri = r.uri
        return True
    parse_metadata(tok, {"description": r.description, "image": r.image})
    return False


def parse_listing(items: list[dict]) -> list[dict]:
    """DexScreener profiles/boosts -> pump.fun mints with their marketing flags."""
    out = []
    for it in items or []:
        mint = it.get("tokenAddress") or ""
        if it.get("chainId") != "solana" or not is_pump_mint(mint):
            continue
        out.append({
            "mint": mint,
            "boosts": int(it.get("totalAmount") or it.get("amount") or 0),
            "cto": bool(it.get("cto")),
            "description": str(it.get("description") or ""),
            "links": it.get("links") or [],
        })
    return out


def parse_pumpfun_coin(c: dict) -> Token | None:
    """A coin from pump.fun's frontend API -> Token."""
    mint = c.get("mint") or ""
    if not mint:
        return None
    created = c.get("created_timestamp")
    tok = Token(
        mint=mint,
        name=str(c.get("name") or "")[:64],
        symbol=str(c.get("symbol") or "")[:24],
        created_at=created / 1000 if created else None,
        creator=c.get("creator") or "",
        pair_address=c.get("bonding_curve") or "",
        uri=c.get("metadata_uri") or "",
    )
    parse_metadata(tok, {
        "description": c.get("description"), "twitter": c.get("twitter"), "telegram": c.get("telegram"),
        "website": c.get("website"), "image": c.get("image_uri"),
    })
    tok.migrated = bool(c.get("complete"))
    return tok


# ---------------------------------------------------------------- network clients


class Http:
    def __init__(self) -> None:
        self.client = httpx.AsyncClient(timeout=12, headers={"User-Agent": USER_AGENT}, follow_redirects=True)
        self.dex_limit = RateLimiter(250, 60)  # DexScreener allows 300/min on these endpoints
        self.rug_limit = RateLimiter(50, 60)
        self.errors: dict[str, str] = {}

    async def close(self) -> None:
        await self.client.aclose()

    async def get_json(self, url: str, source: str, params: dict | None = None) -> Any:
        try:
            r = await self.client.get(url, params=params)
            if r.status_code == 429:
                self.errors[source] = "rate limited (429)"
                await asyncio.sleep(5)
                return None
            r.raise_for_status()
            self.errors.pop(source, None)
            return r.json()
        except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
            self.errors[source] = f"{type(exc).__name__}: {str(exc)[:120]}"
            return None

    async def dex_tokens(self, mints: list[str]) -> list[dict]:
        await self.dex_limit.wait()
        data = await self.get_json(DEX_TOKENS.format(",".join(mints[:30])), "dexscreener")
        return data if isinstance(data, list) else []

    async def dex_listings(self) -> list[dict]:
        out = []
        for url in (DEX_PROFILES, DEX_BOOSTS_LATEST, DEX_BOOSTS_TOP):
            await self.dex_limit.wait()
            data = await self.get_json(url, "dexscreener")
            out.extend(parse_listing(data if isinstance(data, list) else []))
        return out

    async def rug_report(self, mint: str) -> dict | None:
        await self.rug_limit.wait()
        data = await self.get_json(RUGCHECK_REPORT.format(mint), "rugcheck")
        return data if isinstance(data, dict) else None

    async def metadata(self, uri: str) -> dict | None:
        """Fetch the coin's metadata JSON; IPFS URIs are tried across several gateways."""
        if not uri.startswith("http"):
            return None
        urls = [uri]
        if "/ipfs/" in uri:
            path = uri.split("/ipfs/", 1)[1]
            urls = [g + path for g in IPFS_GATEWAYS]
        for url in urls:
            try:
                r = await self.client.get(url, timeout=8)
                r.raise_for_status()
                d = r.json()
                if isinstance(d, dict):
                    return d
            except (httpx.HTTPError, json.JSONDecodeError, ValueError):
                continue
        return None

    async def pumpfun_active(self, limit: int = 50) -> list[dict]:
        """Recently traded coins from pump.fun's own API. Often behind Cloudflare; failure is fine."""
        params = {"offset": 0, "limit": limit, "sort": "last_trade_timestamp", "order": "DESC", "includeNsfw": "false"}
        data = await self.get_json(PUMPFUN_COINS, "pump.fun api", params)
        return data if isinstance(data, list) else []


async def pumpportal_stream(
    on_message: Callable[[dict], Awaitable[None] | None],
    subscriptions: asyncio.Queue,
    api_key: str = "",
    status: dict | None = None,
) -> None:
    """Keep one PumpPortal websocket open (their rules: one connection), reconnecting with backoff.

    Anything put on `subscriptions` (a dict payload) is sent on the open socket.
    """
    import websockets

    url = PUMPPORTAL_WS + (f"?api-key={api_key}" if api_key else "")
    backoff = 2.0
    status = status if status is not None else {}
    trade_keys: set[str] = set()
    while True:
        try:
            async with websockets.connect(url, open_timeout=15, ping_interval=20, max_size=2**20) as ws:
                status["pumpportal"] = "connected"
                backoff = 2.0
                await ws.send(json.dumps({"method": "subscribeNewToken"}))
                await ws.send(json.dumps({"method": "subscribeMigration"}))
                if trade_keys:
                    await ws.send(json.dumps({"method": "subscribeTokenTrade", "keys": sorted(trade_keys)}))

                async def pump_subs():
                    while True:
                        payload = await subscriptions.get()
                        keys = payload.get("keys") or []
                        if payload.get("method") == "subscribeTokenTrade":
                            trade_keys.update(keys)
                        elif payload.get("method") == "unsubscribeTokenTrade":
                            trade_keys.difference_update(keys)
                        await ws.send(json.dumps(payload))

                sender = asyncio.create_task(pump_subs())
                try:
                    async for raw in ws:
                        try:
                            msg = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        res = on_message(msg)
                        if asyncio.iscoroutine(res):
                            await res
                finally:
                    sender.cancel()
        except asyncio.CancelledError:
            status["pumpportal"] = "stopped"
            raise
        except Exception as exc:  # network errors of every flavour: reconnect
            status["pumpportal"] = f"reconnecting ({type(exc).__name__})"
            log.warning("pumpportal: %s; reconnecting in %.0fs", exc, backoff)
            await asyncio.sleep(backoff)
            backoff = min(60.0, backoff * 2)

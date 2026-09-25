"""Compatibility slice for active R2.4 World/Polymarket BTC5M runtime.

Historical source blob: 5a0365cf113709ec39892a0464f1df52fc83ce13.
Only identities, RPC helpers, fee validation and current-market discovery used by
R2.4 are carried forward. Public read-only; NO_TRADE.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.request import Request, urlopen

CLOB_TIME_URL = "https://clob.polymarket.com/time"
GAMMA_EVENTS_URL = "https://gamma-api.polymarket.com/events"
DEFAULT_SOLANA_RPC = "https://api.mainnet-beta.solana.com"
WORLD_METADATA_ROOT = "https://m.world.xyz"
PREDICT_PROGRAM = "prediCtPZCttYMvm2W3PtxmMxLmT1dtN7riU6Cxh6tM"
CASH_MINT = "CASHx9KJUStyftLFWGvEVf59SGeG9sh5FfcnZMVPCASH"
USER_AGENT = "prediction-market-relative-value-world-pm-r24/1.0 (+public-market-data)"
MARKET_SECONDS = 300
CONFIRMED = "confirmed"
EXPECTED_PM_CRYPTO_CONFIG = {
    "id": "btc-5m-twap-60",
    "asset": "btc",
    "duration": "5m",
    "twapEnabled": True,
    "twapLookbackSeconds": 60,
}

@dataclass(frozen=True)
class WorldMarket:
    start_ts: int
    end_ts: int
    market: str
    yes_mint: str
    no_mint: str
    description: str

@dataclass(frozen=True)
class PolymarketMarket:
    start_ts: int
    condition_id: str
    up_token: str
    down_token: str
    fee_schedule: dict[str, Any]
    crypto_config: dict[str, Any]
    description: str


def _json_request(url: str, *, method: str = "GET", payload: Any = None, timeout: float = 10.0) -> Any:
    data = None if payload is None else json.dumps(payload, separators=(",", ":")).encode("utf-8")
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = Request(url, data=data, headers=headers, method=method)
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _rpc(url: str, method: str, params: list[Any]) -> Any:
    payload = _json_request(url, method="POST", payload={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, timeout=12.0)
    if not isinstance(payload, Mapping):
        raise RuntimeError("SOLANA_RPC_RESPONSE_INVALID")
    if payload.get("error") is not None:
        raise RuntimeError(f"SOLANA_RPC_ERROR:{payload['error']}")
    return payload.get("result")


def _json_list(value: object, field: str) -> list[object]:
    if isinstance(value, list):
        return list(value)
    if not isinstance(value, str):
        raise ValueError(f"{field}_INVALID")
    parsed = json.loads(value)
    if not isinstance(parsed, list):
        raise ValueError(f"{field}_INVALID")
    return parsed


def world_description(asset: str, start_ts: int, end_ts: int) -> str:
    start = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(start_ts))
    end = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(end_ts))
    return f"{asset.upper()}/USD closes at or above its open between {start} and {end}."


def validate_fee_schedule(fee_schedule: Mapping[str, Any]) -> None:
    if "rate" not in fee_schedule or "exponent" not in fee_schedule:
        raise ValueError("PM_FEE_SCHEDULE_MISSING")
    try:
        rate = float(fee_schedule["rate"])
        exponent = float(fee_schedule["exponent"])
    except (TypeError, ValueError) as exc:
        raise ValueError("PM_FEE_SCHEDULE_INVALID") from exc
    if rate < 0.0 or exponent <= 0.0:
        raise ValueError("PM_FEE_SCHEDULE_INVALID")


def validate_pm_crypto_config(config: Mapping[str, Any]) -> None:
    for key, expected in EXPECTED_PM_CRYPTO_CONFIG.items():
        if config.get(key) != expected:
            raise RuntimeError(f"PM_CRYPTO_CONFIG_MISMATCH:{key}:{config.get(key)!r}:{expected!r}")


def _server_time() -> int:
    return int(_json_request(CLOB_TIME_URL))


def fetch_polymarket_market(start_ts: int) -> PolymarketMarket:
    slug = f"btc-updown-5m-{start_ts}"
    rows = _json_request(f"{GAMMA_EVENTS_URL}?slug={slug}")
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], Mapping):
        raise RuntimeError(f"PM_EVENT_NOT_UNIQUE:{slug}")
    event = rows[0]
    markets = event.get("markets")
    if not isinstance(markets, list) or len(markets) != 1 or not isinstance(markets[0], Mapping):
        raise RuntimeError(f"PM_MARKET_NOT_UNIQUE:{slug}")
    market = markets[0]
    tokens = [str(value) for value in _json_list(market.get("clobTokenIds"), "PM_TOKEN_IDS")]
    outcomes = [str(value) for value in _json_list(market.get("outcomes"), "PM_OUTCOMES")]
    if len(tokens) != 2 or outcomes != ["Up", "Down"]:
        raise RuntimeError("PM_BINARY_IDENTITY_INVALID")
    if market.get("active") is not True or market.get("closed") is True or market.get("acceptingOrders") is not True:
        raise RuntimeError("PM_MARKET_NOT_TRADABLE")
    fee_schedule = dict(market.get("feeSchedule") or {})
    crypto_config = dict(market.get("cryptoMarketConfig") or {})
    validate_fee_schedule(fee_schedule)
    validate_pm_crypto_config(crypto_config)
    condition_id = str(market.get("conditionId") or "")
    if not condition_id:
        raise RuntimeError("PM_CONDITION_ID_MISSING")
    return PolymarketMarket(
        start_ts=start_ts,
        condition_id=condition_id,
        up_token=tokens[0],
        down_token=tokens[1],
        fee_schedule=fee_schedule,
        crypto_config=crypto_config,
        description=str(event.get("description") or market.get("description") or ""),
    )


def _all_instructions(tx: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    instructions = list(tx["transaction"]["message"].get("instructions") or [])
    for group in tx.get("meta", {}).get("innerInstructions") or []:
        instructions.extend(group.get("instructions") or [])
    return [row for row in instructions if isinstance(row, Mapping)]


def discover_world_market(start_ts: int, *, rpc_url: str, signature_limit: int = 80) -> WorldMarket:
    end_ts = start_ts + MARKET_SECONDS
    target = world_description("BTC", start_ts, end_ts)
    cache_path = Path("world_btc5m_identity_cache.json")
    if cache_path.exists():
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
        for item in cache.get("entries") or []:
            if int(item.get("startTs") or 0) == start_ts and int(item.get("endTs") or 0) == end_ts:
                market = str(item.get("marketLedger") or "")
                yes_mint = str(item.get("yesMint") or "")
                no_mint = str(item.get("noMint") or "")
                if market and yes_mint and no_mint:
                    return WorldMarket(start_ts=start_ts, end_ts=end_ts, market=market, yes_mint=yes_mint, no_mint=no_mint, description=target)

    rows = _rpc(rpc_url, "getSignaturesForAddress", [PREDICT_PROGRAM, {"limit": signature_limit, "commitment": CONFIRMED}]) or []
    for row in rows:
        if not isinstance(row, Mapping) or int(row.get("blockTime") or 0) < start_ts:
            continue
        tx = _rpc(rpc_url, "getTransaction", [str(row["signature"]), {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0, "commitment": CONFIRMED}])
        if not isinstance(tx, Mapping) or tx.get("meta", {}).get("err") is not None:
            continue
        logs = tx.get("meta", {}).get("logMessages") or []
        if not any("Instruction: Split" in str(log) for log in logs):
            continue
        for instruction in _all_instructions(tx):
            accounts = instruction.get("accounts") or []
            if instruction.get("programId") != PREDICT_PROGRAM or len(accounts) < 11:
                continue
            yes_mint = str(accounts[3])
            try:
                metadata = _json_request(f"{WORLD_METADATA_ROOT}/{yes_mint}", timeout=5.0)
            except Exception:
                continue
            if not isinstance(metadata, Mapping) or str(metadata.get("description") or "") != target:
                continue
            return WorldMarket(start_ts=start_ts, end_ts=end_ts, market=str(accounts[1]), yes_mint=yes_mint, no_mint=str(accounts[4]), description=target)
    raise RuntimeError("WORLD_CURRENT_BTC5M_MARKET_NOT_DISCOVERED")

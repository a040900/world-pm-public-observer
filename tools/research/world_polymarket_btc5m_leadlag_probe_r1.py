"""Compatibility slice for active R2.4 World/Polymarket BTC5M runtime.

Historical source blob: 5a0365cf113709ec39892a0464f1df52fc83ce13.
Only identities, RPC helpers, fee validation and current-market discovery used by
R2.4 are carried forward. Public read-only; NO_TRADE.
"""
from __future__ import annotations

import json
import os
import hashlib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
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


def _json_request_transient_retry(
    url: str,
    *,
    method: str = "GET",
    payload: Any = None,
    timeout: float = 10.0,
    attempts: int = 4,
) -> Any:
    """Bounded retry for transport failures only; response semantics remain unchanged."""
    last: Exception | None = None
    for index in range(max(1, attempts)):
        try:
            return _json_request(url, method=method, payload=payload, timeout=timeout)
        except HTTPError as exc:
            if exc.code != 429 and not 500 <= exc.code <= 599:
                raise
            last = exc
        except (URLError, TimeoutError, ConnectionResetError, OSError) as exc:
            last = exc
        if index + 1 < attempts:
            time.sleep(0.5 * (2**index))
    if last is not None:
        raise last
    raise RuntimeError("TRANSIENT_HTTP_RETRY_EXHAUSTED")


PUBLIC_RPC_FALLBACK = "https://solana-rpc.publicnode.com"
DISCOVERY_TRACES: dict[int, dict[str, Any]] = {}


def _rpc(url: str, method: str, params: list[Any], *, deadline: float | None = None,
         trace: dict[str, Any] | None = None) -> Any:
    """Read-only bounded transport retry. Never retry semantic/identity validation."""
    deadline = deadline if deadline is not None else time.time() + 30.0
    endpoints = list(dict.fromkeys([url, PUBLIC_RPC_FALLBACK]))
    if trace and trace.get("workingEndpoint") in endpoints:
        endpoints.remove(trace["workingEndpoint"])
        endpoints.insert(0, trace["workingEndpoint"])
    last: Exception = TimeoutError("SOLANA_RPC_DEADLINE")
    for attempt in range(4):
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        endpoint = endpoints[attempt % len(endpoints)]
        evidence = {"endpoint": endpoint, "method": method, "attempt": attempt + 1, "at": time.time()}
        if trace is not None:
            trace.setdefault("requests", []).append(evidence)
        try:
            payload = _json_request(endpoint, method="POST", payload={"jsonrpc": "2.0", "id": 1,
                                    "method": method, "params": params}, timeout=min(8.0, remaining))
            if not isinstance(payload, Mapping):
                raise ValueError("SOLANA_RPC_RESPONSE_INVALID")
            error = payload.get("error")
            if error is not None:
                if isinstance(error, Mapping) and error.get("code") in (429, -32005, -32603):
                    raise OSError(f"SOLANA_RPC_TRANSIENT:{error}")
                raise ValueError(f"SOLANA_RPC_ERROR:{error}")
            evidence["status"] = "SUCCESS"
            if trace is not None:
                trace["workingEndpoint"] = endpoint
            return payload.get("result")
        except HTTPError as exc:
            if exc.code != 429 and not 500 <= exc.code <= 599:
                raise
            last = exc
        except (URLError, TimeoutError, ConnectionResetError, OSError) as exc:
            last = exc
        evidence["error"] = f"{type(last).__name__}:{last}"
        if attempt < 3:
            time.sleep(min(2 ** attempt, max(0.0, deadline - time.time())))
    raise RuntimeError(f"SOLANA_RPC_RETRY_EXHAUSTED:{method}:{last}") from last


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
    rows = _json_request_transient_retry(f"{GAMMA_EVENTS_URL}?slug={slug}", timeout=10.0, attempts=4)
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


def _is_split_instruction(instruction: Mapping[str, Any]) -> bool:
    accounts = instruction.get("accounts") or []
    if (instruction.get("programId") != PREDICT_PROGRAM or len(accounts) != 11
            or accounts[2] != CASH_MINT):
        return False
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    encoded = str(instruction.get("data") or "")
    if not encoded or any(c not in alphabet for c in encoded):
        return False
    value = 0
    for c in encoded:
        value = value * 58 + alphabet.index(c)
    decoded = b"\x00" * (len(encoded) - len(encoded.lstrip("1"))) + value.to_bytes((value.bit_length() + 7) // 8, "big")
    return len(decoded) == 16 and decoded[:8] == hashlib.sha256(b"global:split").digest()[:8]


def _identity_cache_path() -> Path:
    return Path(os.environ.get("WORLD_IDENTITY_CACHE", "world_btc5m_identity_cache.json"))


def _cached_world_market(start_ts: int) -> WorldMarket | None:
    path = _identity_cache_path()
    if not path.exists():
        return None
    cache = json.loads(path.read_text(encoding="utf-8"))
    matches = [x for x in cache.get("entries", []) if x.get("startTs") == start_ts]
    if not matches:
        return None
    if len(matches) != 1:
        raise ValueError("WORLD_IDENTITY_CACHE_AMBIGUOUS")
    item = matches[0]
    target = world_description("BTC", start_ts, start_ts + MARKET_SECONDS)
    if (item.get("endTs") != start_ts + MARKET_SECONDS
            or item.get("description", target) != target
            or not all(item.get(k) for k in ("marketLedger", "yesMint", "noMint"))
            or item["yesMint"] == item["noMint"]):
        raise ValueError("WORLD_IDENTITY_CACHE_INVALID")
    return WorldMarket(start_ts, start_ts + MARKET_SECONDS, item["marketLedger"],
                       item["yesMint"], item["noMint"], target)


def _save_world_identity(market: WorldMarket, trace: dict[str, Any]) -> None:
    path = _identity_cache_path()
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"entries": []}
    data["schemaVersion"] = "WORLD_BTC5M_IDENTITY_CACHE_R2"
    data["entries"].append({"startTs": market.start_ts, "endTs": market.end_ts,
        "marketLedger": market.market, "yesMint": market.yes_mint, "noMint": market.no_mint,
        "description": market.description, "discoveredAt": time.time(),
        "source": "SOLANA_SPLIT_AND_EXACT_WORLD_METADATA", "evidence": trace})
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def discover_world_market(start_ts: int, *, rpc_url: str, signature_limit: int = 80,
                          deadline: float | None = None) -> WorldMarket:
    deadline = deadline if deadline is not None else time.time() + 45.0
    target = world_description("BTC", start_ts, start_ts + MARKET_SECONDS)
    # A verified Split may predate its market's start. This is a bounded search
    # allowance, not evidence that future identities are always publicly available.
    earliest_signature = start_ts - 600 if time.time() < start_ts else start_ts
    trace: dict[str, Any] = {"startTs": start_ts, "requests": [], "cacheHit": False,
                             "earliestSignatureBlockTime": earliest_signature}
    DISCOVERY_TRACES[start_ts] = trace
    cached = _cached_world_market(start_ts)
    if cached:
        trace["cacheHit"] = True
        return cached
    path = _identity_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_suffix(".lock")
    # Shared by the two isolated observer processes. A crashed writer fails closed.
    while True:
        if time.time() >= deadline:
            raise TimeoutError("WORLD_DISCOVERY_CACHE_LOCK_DEADLINE")
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
            break
        except FileExistsError:
            time.sleep(min(.2, max(0.0, deadline - time.time())))
    try:
        cached = _cached_world_market(start_ts)
        if cached:
            trace["cacheHit"] = True
            return cached
        seen: set[str] = set()
        metadata_cache: dict[str, Any] = {}
        while time.time() < deadline:
            rows = _rpc(rpc_url, "getSignaturesForAddress", [PREDICT_PROGRAM,
                {"limit": signature_limit, "commitment": CONFIRMED}], deadline=deadline, trace=trace) or []
            for row in rows:
                if (not isinstance(row, Mapping) or row.get("err") is not None
                        or int(row.get("blockTime") or 0) < earliest_signature):
                    continue
                signature = str(row["signature"])
                if signature in seen:
                    continue
                if time.time() >= deadline:
                    raise TimeoutError("WORLD_DISCOVERY_DEADLINE")
                tx = _rpc(rpc_url, "getTransaction", [signature, {"encoding": "jsonParsed",
                    "maxSupportedTransactionVersion": 0, "commitment": CONFIRMED}], deadline=deadline, trace=trace)
                if tx is None:  # RPC lag: not a durable negative result.
                    continue
                seen.add(signature)
                time.sleep(min(.35, max(0.0, deadline - time.time())))
                if not isinstance(tx, Mapping) or tx.get("meta", {}).get("err") is not None:
                    continue
                for instruction in _all_instructions(tx):
                    accounts = instruction.get("accounts") or []
                    if not _is_split_instruction(instruction):
                        continue
                    yes_mint = str(accounts[3])
                    if yes_mint not in metadata_cache:
                        remaining = deadline - time.time()
                        if remaining <= 0:
                            raise TimeoutError("WORLD_METADATA_DEADLINE")
                        url = f"{WORLD_METADATA_ROOT}/{yes_mint}"
                        trace["requests"].append({"endpoint": url, "method": "GET", "at": time.time()})
                        metadata_cache[yes_mint] = _json_request(url, timeout=min(5.0, remaining))
                    metadata = metadata_cache[yes_mint]
                    if not isinstance(metadata, Mapping) or str(metadata.get("description") or "") != target:
                        continue
                    market = WorldMarket(start_ts, start_ts + MARKET_SECONDS, str(accounts[1]),
                                         yes_mint, str(accounts[4]), target)
                    trace["signature"] = signature
                    trace["signatureBlockTime"] = row.get("blockTime")
                    trace["identityObservedAt"] = time.time()
                    trace["metadataSha256"] = hashlib.sha256(json.dumps(metadata, sort_keys=True).encode()).hexdigest()
                    _save_world_identity(market, trace)
                    return market
            time.sleep(min(3.0, max(0.0, deadline - time.time())))
        raise TimeoutError("WORLD_CURRENT_BTC5M_MARKET_NOT_DISCOVERED")
    finally:
        lock.unlink()

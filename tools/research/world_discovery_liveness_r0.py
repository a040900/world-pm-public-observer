"""Bounded World BTC5m discovery/liveness diagnostic.

Zero research score. Public read-only. No credentials, signing, orders, or capital.
It measures whether the current discovery algorithm's 80-signature horizon can
cover recent BTC5m Split identities and, when a live identity is found, compares
DFlow indicative quotes with the anonymous exact-quote endpoint.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from typing import Any, Mapping

import requests

from tools.research import world_limitless_polymarket_btc5m_relative_value_smoke_r0 as radar
from tools.research import world_polymarket_btc5m_executable_sync_r23 as r23
from tools.research import world_polymarket_btc5m_leadlag_probe_r1 as wp


def _slot(ts: int) -> int:
    return (ts // 300) * 300


def _safe_error(exc: BaseException) -> str:
    return f"{type(exc).__name__}:{exc}"


def _metadata(yes_mint: str, timeout: float = 4.0) -> Mapping[str, Any] | None:
    try:
        value = wp._json_request(f"{wp.WORLD_METADATA_ROOT}/{yes_mint}", timeout=timeout)
        return value if isinstance(value, Mapping) else None
    except Exception:
        return None


def broad_discovery(now: int, *, signature_limit: int, scan_cap: int, deadline_seconds: float) -> dict[str, Any]:
    slots = [_slot(now) - 300, _slot(now), _slot(now) + 300]
    targets = {start: wp.world_description("BTC", start, start + 300) for start in slots}
    result: dict[str, Any] = {
        "slots": slots,
        "targets": targets,
        "signatureLimit": signature_limit,
        "scanCap": scan_cap,
        "matches": {},
        "errors": [],
    }
    deadline = time.time() + deadline_seconds
    trace: dict[str, Any] = {"requests": []}
    try:
        rows = wp._rpc(
            radar.DEFAULT_RPC,
            "getSignaturesForAddress",
            [wp.PREDICT_PROGRAM, {"limit": signature_limit, "commitment": wp.CONFIRMED}],
            deadline=deadline,
            trace=trace,
        ) or []
    except Exception as exc:
        result["errors"].append(_safe_error(exc))
        result["rpcTrace"] = trace
        return result

    valid_rows = [r for r in rows if isinstance(r, Mapping) and r.get("err") is None]
    result["signatureCount"] = len(valid_rows)
    result["newestBlockTime"] = int(valid_rows[0].get("blockTime") or 0) if valid_rows else None
    result["oldestBlockTime"] = int(valid_rows[-1].get("blockTime") or 0) if valid_rows else None
    result["row80BlockTime"] = (
        int(valid_rows[79].get("blockTime") or 0) if len(valid_rows) >= 80 else None
    )
    min_needed = min(slots) - 600
    recent = [r for r in valid_rows if int(r.get("blockTime") or 0) >= min_needed]
    result["signaturesSinceEarliestNeeded"] = len(recent)
    result["default80CoversEarliestNeeded"] = bool(
        len(valid_rows) < 80 or int(valid_rows[79].get("blockTime") or 0) <= min_needed
    )

    seen_mints: set[str] = set()
    split_count = 0
    fetched = 0
    for row in recent[:scan_cap]:
        if time.time() >= deadline:
            result["errors"].append("SCAN_DEADLINE")
            break
        signature = str(row.get("signature") or "")
        if not signature:
            continue
        try:
            tx = wp._rpc(
                radar.DEFAULT_RPC,
                "getTransaction",
                [signature, {
                    "encoding": "jsonParsed",
                    "maxSupportedTransactionVersion": 0,
                    "commitment": wp.CONFIRMED,
                }],
                deadline=deadline,
                trace=trace,
            )
        except Exception as exc:
            result["errors"].append(f"{signature}:{_safe_error(exc)}")
            continue
        fetched += 1
        if not isinstance(tx, Mapping) or tx.get("meta", {}).get("err") is not None:
            continue
        for instruction in wp._all_instructions(tx):
            if not wp._is_split_instruction(instruction):
                continue
            split_count += 1
            accounts = instruction.get("accounts") or []
            if len(accounts) < 5:
                continue
            yes_mint = str(accounts[3])
            if yes_mint in seen_mints:
                continue
            seen_mints.add(yes_mint)
            metadata = _metadata(yes_mint)
            if not metadata:
                continue
            description = str(metadata.get("description") or "")
            for start, target in targets.items():
                if description != target or str(start) in result["matches"]:
                    continue
                result["matches"][str(start)] = {
                    "market": str(accounts[1]),
                    "yesMint": yes_mint,
                    "noMint": str(accounts[4]),
                    "description": description,
                    "signature": signature,
                    "signatureBlockTime": row.get("blockTime"),
                    "foundAt": time.time(),
                    "scanOrdinal": fetched,
                }
        if len(result["matches"]) == len(slots):
            break

    result["transactionsFetched"] = fetched
    result["splitInstructionCount"] = split_count
    result["uniqueSplitYesMintCount"] = len(seen_mints)
    result["rpcTrace"] = trace
    return result


async def collect_dflow(market: wp.WorldMarket, seconds: float = 6.0) -> dict[str, Any]:
    feed = radar.DflowQuotes(market.yes_mint, market.no_mint)
    stop = asyncio.Event()
    task = asyncio.create_task(feed.run(stop))
    deadline = time.time() + seconds
    try:
        while time.time() < deadline:
            snap = feed.snapshot(time.time())
            if snap.get("yes", {}).get("ask") is not None and snap.get("no", {}).get("ask") is not None:
                return snap
            await asyncio.sleep(0.1)
        return feed.snapshot(time.time())
    finally:
        stop.set()
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except Exception:
            task.cancel()


def exact_quote(session: requests.Session, *, output_mint: str, cash_decimals: int,
                outcome_decimals: int, request_cash: float) -> dict[str, Any]:
    amount = max(1, int(round(request_cash * (10 ** cash_decimals))))
    params = {
        "inputMint": wp.CASH_MINT,
        "outputMint": output_mint,
        "amount": str(amount),
        "slippageBps": "200",
        "predictionMarketSlippageBps": "200",
        "allowSyncExec": "true",
        "allowAsyncExec": "true",
    }
    headers = {
        "Accept": "application/json",
        "Origin": "https://world.xyz",
        "Referer": "https://world.xyz/",
        "User-Agent": "world-pm-economic-first-discovery-liveness-r0/1.0",
    }
    started = time.time()
    try:
        response = session.get(r23.WORLD_PROXY_ORDER_URL, params=params, headers=headers, timeout=10.0)
    except Exception as exc:
        return {"success": False, "transportError": _safe_error(exc), "requestParams": params}
    received = time.time()
    raw = response.text[:8000]
    try:
        payload = response.json()
    except ValueError:
        payload = None
    row: dict[str, Any] = {
        "requestStartedAt": started,
        "receivedAt": received,
        "elapsedMs": (received - started) * 1000.0,
        "httpStatus": response.status_code,
        "contentType": response.headers.get("content-type"),
        "requestParams": params,
        "responseBody": raw,
        "success": False,
    }
    if response.status_code == 200 and isinstance(payload, Mapping):
        out_amount = int(payload.get("outAmount") or 0)
        min_out = int(payload.get("minOutAmount") or 0)
        row.update({
            "success": out_amount > 0 and min_out > 0,
            "executionMode": payload.get("executionMode"),
            "inAmount": payload.get("inAmount"),
            "outAmount": payload.get("outAmount"),
            "minOutAmount": payload.get("minOutAmount"),
            "outputUiShares": out_amount / (10 ** outcome_decimals),
            "minOutputUiShares": min_out / (10 ** outcome_decimals),
            "priceImpactPct": payload.get("priceImpactPct"),
            "platformFee": payload.get("platformFee"),
        })
    return row


async def main(args: argparse.Namespace) -> None:
    now = int(time.time())
    result: dict[str, Any] = {
        "schemaVersion": "WORLD_DISCOVERY_LIVENESS_R0",
        "researchScore": 0,
        "mode": "PUBLIC_READ_ONLY_NO_TRADE",
        "startedAt": time.time(),
    }
    discovery = await asyncio.to_thread(
        broad_discovery,
        now,
        signature_limit=args.signature_limit,
        scan_cap=args.scan_cap,
        deadline_seconds=args.deadline_seconds,
    )
    result["discovery"] = discovery

    current_start = _slot(now)
    match = discovery.get("matches", {}).get(str(current_start))
    if isinstance(match, Mapping) and time.time() < current_start + 300:
        market = wp.WorldMarket(
            current_start,
            current_start + 300,
            str(match["market"]),
            str(match["yesMint"]),
            str(match["noMint"]),
            str(match["description"]),
        )
        result["liveCurrentMarket"] = dict(match)
        result["dflowRadar"] = await collect_dflow(market)
        try:
            cash_decimals = await asyncio.to_thread(r23._token_decimals, wp.CASH_MINT, wp.DEFAULT_SOLANA_RPC)
            yes_decimals = await asyncio.to_thread(r23._token_decimals, market.yes_mint, wp.DEFAULT_SOLANA_RPC)
            no_decimals = await asyncio.to_thread(r23._token_decimals, market.no_mint, wp.DEFAULT_SOLANA_RPC)
            session = requests.Session()
            result["exactQuote"] = {
                "yes": await asyncio.to_thread(exact_quote, session, output_mint=market.yes_mint,
                                                cash_decimals=cash_decimals, outcome_decimals=yes_decimals,
                                                request_cash=args.request_cash),
                "no": await asyncio.to_thread(exact_quote, session, output_mint=market.no_mint,
                                               cash_decimals=cash_decimals, outcome_decimals=no_decimals,
                                               request_cash=args.request_cash),
            }
        except Exception as exc:
            result["exactQuoteSetupError"] = _safe_error(exc)

    result["finishedAt"] = time.time()
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "signatureCount": discovery.get("signatureCount"),
        "signaturesSinceEarliestNeeded": discovery.get("signaturesSinceEarliestNeeded"),
        "default80CoversEarliestNeeded": discovery.get("default80CoversEarliestNeeded"),
        "matchedSlots": sorted(discovery.get("matches", {}).keys()),
        "currentMatched": bool(match),
        "yesHttpStatus": (result.get("exactQuote") or {}).get("yes", {}).get("httpStatus"),
        "noHttpStatus": (result.get("exactQuote") or {}).get("no", {}).get("httpStatus"),
    }, sort_keys=True))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--output", default="artifacts/world-discovery-liveness-r0.json")
    p.add_argument("--signature-limit", type=int, default=500)
    p.add_argument("--scan-cap", type=int, default=220)
    p.add_argument("--deadline-seconds", type=float, default=180.0)
    p.add_argument("--request-cash", type=float, default=10.0)
    asyncio.run(main(p.parse_args()))

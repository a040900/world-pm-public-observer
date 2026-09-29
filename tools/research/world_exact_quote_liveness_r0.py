"""One-shot World exact-quote liveness diagnostic.

Zero research score. Public read-only. No credentials, signing, orders, or capital.
The purpose is to distinguish endpoint/route semantics from a generic HTTP 404
that previously collapsed to WORLD_EXACT_QUOTE_INVALID without response detail.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from typing import Any

import requests

from tools.research import world_limitless_polymarket_btc5m_relative_value_smoke_r0 as radar
from tools.research import world_polymarket_btc5m_executable_sync_r23 as r23
from tools.research import world_polymarket_btc5m_leadlag_probe_r1 as wp


def exact_quote(session: requests.Session, *, output_mint: str, cash_decimals: int,
                outcome_decimals: int, request_cash: float, timeout_seconds: float) -> dict[str, Any]:
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
        "User-Agent": "world-pm-economic-first-exact-quote-liveness-r0/1.0",
    }
    started = time.time()
    response = session.get(r23.WORLD_PROXY_ORDER_URL, params=params, headers=headers, timeout=timeout_seconds)
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
        "requestSizeCash": request_cash,
        "amountRaw": amount,
        "outputMint": output_mint,
        "responseBody": raw,
        "success": False,
        "credentialUsed": False,
        "userPublicKeySupplied": False,
        "signingPerformed": False,
        "transactionSubmitted": False,
    }
    if response.status_code == 200 and isinstance(payload, dict):
        out_amount = int(payload.get("outAmount") or 0)
        min_out = int(payload.get("minOutAmount") or 0)
        row.update({
            "success": out_amount > 0 and min_out > 0,
            "contextSlot": payload.get("contextSlot"),
            "executionMode": payload.get("executionMode"),
            "inAmount": payload.get("inAmount"),
            "outAmount": payload.get("outAmount"),
            "minOutAmount": payload.get("minOutAmount"),
            "priceImpactPct": payload.get("priceImpactPct"),
            "outputUiShares": out_amount / (10 ** outcome_decimals),
            "minOutputUiShares": min_out / (10 ** outcome_decimals),
            "platformFee": payload.get("platformFee"),
        })
    return row


async def collect_radar(market: wp.WorldMarket, seconds: float = 8.0) -> dict[str, Any]:
    feed = radar.DflowQuotes(market.yes_mint, market.no_mint)
    stop = asyncio.Event()
    task = asyncio.create_task(feed.run(stop))
    deadline = time.time() + seconds
    try:
        while time.time() < deadline:
            snap = feed.snapshot(time.time())
            if snap.get("yes", {}).get("ask") and snap.get("no", {}).get("ask"):
                return snap
            await asyncio.sleep(0.1)
        return feed.snapshot(time.time())
    finally:
        stop.set()
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except Exception:
            task.cancel()


def discover() -> tuple[int, wp.WorldMarket]:
    now = int(time.time())
    starts = [(now // 300) * 300, ((now // 300) + 1) * 300]
    errors: list[str] = []
    for start in starts:
        try:
            return start, radar._discover_world_market(start, radar.DEFAULT_RPC, time.time() + 45.0)
        except Exception as exc:
            errors.append(f"{start}:{type(exc).__name__}:{exc}")
    raise RuntimeError("WORLD_DISCOVERY_FAILED:" + "|".join(errors))


async def main(output: str, request_cash: float) -> None:
    selected_start, market = await asyncio.to_thread(discover)
    cash_decimals = await asyncio.to_thread(r23._token_decimals, wp.CASH_MINT, wp.DEFAULT_SOLANA_RPC)
    yes_decimals = await asyncio.to_thread(r23._token_decimals, market.yes_mint, wp.DEFAULT_SOLANA_RPC)
    no_decimals = await asyncio.to_thread(r23._token_decimals, market.no_mint, wp.DEFAULT_SOLANA_RPC)
    dflow = await collect_radar(market)
    session = requests.Session()
    yes = await asyncio.to_thread(exact_quote, session, output_mint=market.yes_mint,
                                  cash_decimals=cash_decimals, outcome_decimals=yes_decimals,
                                  request_cash=request_cash, timeout_seconds=10.0)
    no = await asyncio.to_thread(exact_quote, session, output_mint=market.no_mint,
                                 cash_decimals=cash_decimals, outcome_decimals=no_decimals,
                                 request_cash=request_cash, timeout_seconds=10.0)
    result = {
        "schemaVersion": "WORLD_EXACT_QUOTE_LIVENESS_R0",
        "researchScore": 0,
        "mode": "PUBLIC_READ_ONLY_NO_TRADE",
        "observedAt": time.time(),
        "selectedStartTs": selected_start,
        "market": {
            "startTs": market.start_ts,
            "endTs": market.end_ts,
            "market": market.market,
            "yesMint": market.yes_mint,
            "noMint": market.no_mint,
            "description": market.description,
        },
        "dflowRadar": dflow,
        "exactQuote": {"yes": yes, "no": no},
    }
    with open(output, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2, sort_keys=True)
    print(json.dumps({
        "selectedStartTs": selected_start,
        "yesHttpStatus": yes.get("httpStatus"),
        "yesSuccess": yes.get("success"),
        "noHttpStatus": no.get("httpStatus"),
        "noSuccess": no.get("success"),
        "yesRadarAsk": dflow.get("yes", {}).get("ask"),
        "noRadarAsk": dflow.get("no", {}).get("ask"),
    }, sort_keys=True))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="artifacts/world-exact-quote-liveness-r0.json")
    parser.add_argument("--request-cash", type=float, default=10.0)
    args = parser.parse_args()
    asyncio.run(main(args.output, args.request_cash))

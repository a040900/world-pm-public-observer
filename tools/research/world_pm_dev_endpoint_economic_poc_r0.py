"""Bounded World dev-endpoint + Polymarket BTC5m economic POC R0.

Public read-only. No credentials, signing, transaction submission, orders, or capital.
Uses one live World BTC5m market, DFlow dev quote-stream, DFlow dev GET /order,
and Polymarket public REST books. This is an economic/execution-access diagnostic only.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from typing import Any, Mapping

import requests

from tools.research import world_discovery_liveness_r0 as discovery
from tools.research import world_limitless_polymarket_btc5m_relative_value_smoke_r0 as radar
from tools.research import world_polymarket_btc5m_executable_sync_r23 as r23
from tools.research import world_polymarket_btc5m_leadlag_probe_r1 as wp

DEV_ORDER = "https://dev-quote-api.dflow.net/order"


def _safe(exc: BaseException) -> str:
    return f"{type(exc).__name__}:{exc}"


def _dev_order(
    session: requests.Session,
    *,
    output_mint: str,
    cash_decimals: int,
    outcome_decimals: int,
    request_cash: float,
) -> dict[str, Any]:
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
    started = time.time()
    try:
        response = session.get(
            DEV_ORDER,
            params=params,
            headers={"Accept": "application/json", "User-Agent": "world-pm-dev-economic-poc-r0/1.0"},
            timeout=10.0,
        )
    except Exception as exc:
        return {
            "requestStartedAt": started,
            "success": False,
            "transportError": _safe(exc),
            "requestParams": params,
        }
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
        "responseBody": raw,
        "requestParams": params,
        "success": False,
    }
    if response.status_code == 200 and isinstance(payload, Mapping):
        out_amount = int(payload.get("outAmount") or 0)
        min_out = int(payload.get("minOutAmount") or 0)
        row.update({
            "success": out_amount > 0 and min_out > 0,
            "outAmount": out_amount,
            "minOutAmount": min_out,
            "outputUiShares": out_amount / (10 ** outcome_decimals),
            "minOutputUiShares": min_out / (10 ** outcome_decimals),
            "priceImpactPct": payload.get("priceImpactPct"),
            "executionMode": payload.get("executionMode"),
            "routePlan": payload.get("routePlan"),
            "platformFee": payload.get("platformFee"),
        })
    return row


async def _collect_dflow(market: wp.WorldMarket, seconds: float = 8.0) -> dict[str, Any]:
    feed = radar.DflowQuotes(market.yes_mint, market.no_mint)
    stop = asyncio.Event()
    task = asyncio.create_task(feed.run(stop))
    deadline = time.time() + seconds
    try:
        while time.time() < deadline:
            snap = feed.snapshot(time.time())
            if (
                snap.get("yes", {}).get("ask") is not None
                and snap.get("no", {}).get("ask") is not None
            ):
                return snap
            await asyncio.sleep(0.05)
        return feed.snapshot(time.time())
    finally:
        stop.set()
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except Exception:
            task.cancel()


def _package_from_stream(
    *,
    world_ask: float | None,
    pm_book: dict[str, Any],
    pm_fee_schedule: dict[str, Any],
    request_cash: float,
) -> dict[str, Any]:
    if world_ask is None or world_ask <= 0:
        return {"status": "WORLD_STREAM_ASK_MISSING"}
    target_shares = request_cash / world_ask
    walked = r23._walk_pm_book_for_net_shares(pm_book.get("asks") or [], target_shares, pm_fee_schedule)
    if not walked.get("filled"):
        return {
            "status": "PM_DEPTH_INSUFFICIENT",
            "worldApproxCash": request_cash,
            "worldApproxShares": target_shares,
            "pmWalk": walked,
        }
    total_cost = request_cash + float(walked["cost"])
    unit_cost = total_cost / target_shares
    return {
        "status": "ECONOMIC_POC",
        "worldApproxCash": request_cash,
        "worldApproxShares": target_shares,
        "pmCost": walked["cost"],
        "pmWalk": walked,
        "packageUnitCost": unit_cost,
        "edgePerShare": 1.0 - unit_cost,
        "edgeBps": (1.0 - unit_cost) * 10000.0,
    }


def _package_from_order(
    *,
    order: dict[str, Any],
    pm_book: dict[str, Any],
    pm_fee_schedule: dict[str, Any],
    request_cash: float,
) -> dict[str, Any]:
    if order.get("success") is not True:
        return {"status": "WORLD_DEV_ORDER_UNAVAILABLE"}
    target_shares = float(order.get("minOutputUiShares") or 0.0)
    if target_shares <= 0:
        return {"status": "WORLD_DEV_ORDER_INVALID_SIZE"}
    walked = r23._walk_pm_book_for_net_shares(pm_book.get("asks") or [], target_shares, pm_fee_schedule)
    if not walked.get("filled"):
        return {"status": "PM_DEPTH_INSUFFICIENT", "targetShares": target_shares, "pmWalk": walked}
    total_cost = request_cash + float(walked["cost"])
    unit_cost = total_cost / target_shares
    return {
        "status": "EXECUTABLE_QUOTE_POC",
        "worldCash": request_cash,
        "worldMinShares": target_shares,
        "pmCost": walked["cost"],
        "pmWalk": walked,
        "packageUnitCost": unit_cost,
        "edgePerShare": 1.0 - unit_cost,
        "edgeBps": (1.0 - unit_cost) * 10000.0,
    }


async def main(args: argparse.Namespace) -> None:
    out: dict[str, Any] = {
        "schemaVersion": "WORLD_PM_DEV_ENDPOINT_ECONOMIC_POC_R0",
        "mode": "PUBLIC_READ_ONLY_NO_TRADE",
        "researchScore": 0,
        "startedAt": time.time(),
        "requestCash": args.request_cash,
        "attempts": [],
    }
    now = int(time.time())
    start = (now // 300) * 300
    broad = await asyncio.to_thread(
        discovery.broad_discovery,
        now,
        signature_limit=500,
        scan_cap=220,
        deadline_seconds=180.0,
    )
    out["discovery"] = broad
    match = broad.get("matches", {}).get(str(start))
    if not isinstance(match, Mapping) or time.time() >= start + 300:
        out["status"] = "CURRENT_WORLD_MARKET_NOT_AVAILABLE"
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")
        print(json.dumps({"status": out["status"], "matched": sorted((broad.get("matches") or {}).keys())}))
        return

    market = wp.WorldMarket(
        start,
        start + 300,
        str(match["market"]),
        str(match["yesMint"]),
        str(match["noMint"]),
        str(match["description"]),
    )
    out["worldMarket"] = dict(match)

    try:
        pm_market = await asyncio.to_thread(wp.fetch_polymarket_market, start)
    except Exception as exc:
        out["status"] = "PM_MARKET_DISCOVERY_FAILED"
        out["error"] = _safe(exc)
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")
        print(json.dumps({"status": out["status"], "error": out["error"]}))
        return

    out["polymarket"] = {
        "conditionId": pm_market.condition_id,
        "upToken": pm_market.up_token,
        "downToken": pm_market.down_token,
        "feeSchedule": pm_market.fee_schedule,
        "cryptoConfig": pm_market.crypto_config,
    }

    cash_decimals = await asyncio.to_thread(r23._token_decimals, wp.CASH_MINT, wp.DEFAULT_SOLANA_RPC)
    yes_decimals = await asyncio.to_thread(r23._token_decimals, market.yes_mint, wp.DEFAULT_SOLANA_RPC)
    no_decimals = await asyncio.to_thread(r23._token_decimals, market.no_mint, wp.DEFAULT_SOLANA_RPC)

    world_session = requests.Session()
    pm_session = requests.Session()

    for idx in range(args.attempts):
        if time.time() >= start + 295:
            break
        observed_at = time.time()
        dflow = await _collect_dflow(market, seconds=6.0)
        try:
            pm_down = await asyncio.to_thread(r23._fetch_pm_rest_book, pm_session, pm_market.down_token, 8.0)
            pm_up = await asyncio.to_thread(r23._fetch_pm_rest_book, pm_session, pm_market.up_token, 8.0)
        except Exception as exc:
            out["attempts"].append({"index": idx + 1, "observedAt": observed_at, "pmBookError": _safe(exc)})
            if idx + 1 < args.attempts:
                await asyncio.sleep(args.interval_seconds)
            continue

        yes_order = await asyncio.to_thread(
            _dev_order, world_session,
            output_mint=market.yes_mint,
            cash_decimals=cash_decimals,
            outcome_decimals=yes_decimals,
            request_cash=args.request_cash,
        )
        no_order = await asyncio.to_thread(
            _dev_order, world_session,
            output_mint=market.no_mint,
            cash_decimals=cash_decimals,
            outcome_decimals=no_decimals,
            request_cash=args.request_cash,
        )

        yes_ask = dflow.get("yes", {}).get("ask")
        no_ask = dflow.get("no", {}).get("ask")
        attempt = {
            "index": idx + 1,
            "observedAt": observed_at,
            "secondsIntoWindow": observed_at - start,
            "dflow": dflow,
            "devOrder": {"yes": yes_order, "no": no_order},
            "pmBooks": {"down": pm_down, "up": pm_up},
            "packages": {
                "WORLD_YES+PM_DOWN": {
                    "streamBased": _package_from_stream(
                        world_ask=float(yes_ask) if yes_ask is not None else None,
                        pm_book=pm_down,
                        pm_fee_schedule=pm_market.fee_schedule,
                        request_cash=args.request_cash,
                    ),
                    "devOrderBased": _package_from_order(
                        order=yes_order,
                        pm_book=pm_down,
                        pm_fee_schedule=pm_market.fee_schedule,
                        request_cash=args.request_cash,
                    ),
                },
                "WORLD_NO+PM_UP": {
                    "streamBased": _package_from_stream(
                        world_ask=float(no_ask) if no_ask is not None else None,
                        pm_book=pm_up,
                        pm_fee_schedule=pm_market.fee_schedule,
                        request_cash=args.request_cash,
                    ),
                    "devOrderBased": _package_from_order(
                        order=no_order,
                        pm_book=pm_up,
                        pm_fee_schedule=pm_market.fee_schedule,
                        request_cash=args.request_cash,
                    ),
                },
            },
        }
        out["attempts"].append(attempt)
        if idx + 1 < args.attempts:
            await asyncio.sleep(args.interval_seconds)

    out["finishedAt"] = time.time()
    out["status"] = "COMPLETED"
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    summary = []
    for a in out["attempts"]:
        if "packages" not in a:
            continue
        summary.append({
            "index": a["index"],
            "yesDevStatus": a["devOrder"]["yes"].get("httpStatus"),
            "yesDevBody": a["devOrder"]["yes"].get("responseBody"),
            "noDevStatus": a["devOrder"]["no"].get("httpStatus"),
            "noDevBody": a["devOrder"]["no"].get("responseBody"),
            "yesStreamEdgeBps": a["packages"]["WORLD_YES+PM_DOWN"]["streamBased"].get("edgeBps"),
            "noStreamEdgeBps": a["packages"]["WORLD_NO+PM_UP"]["streamBased"].get("edgeBps"),
            "yesOrderEdgeBps": a["packages"]["WORLD_YES+PM_DOWN"]["devOrderBased"].get("edgeBps"),
            "noOrderEdgeBps": a["packages"]["WORLD_NO+PM_UP"]["devOrderBased"].get("edgeBps"),
        })
    print(json.dumps({"status": out["status"], "attempts": summary}, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--output", default="artifacts/world-pm-dev-endpoint-economic-poc-r0.json")
    p.add_argument("--request-cash", type=float, default=10.0)
    p.add_argument("--attempts", type=int, default=3)
    p.add_argument("--interval-seconds", type=float, default=5.0)
    asyncio.run(main(p.parse_args()))

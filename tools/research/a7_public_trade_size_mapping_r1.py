#!/usr/bin/env python3
import argparse
import asyncio
import json
import math
import time
from pathlib import Path
from typing import Any

import requests
import websockets
from Crypto.Hash import keccak

from tools.research import world_polymarket_btc5m_executable_sync_r23 as r23

SCHEMA_VERSION = "A7_PUBLIC_TRADE_SIZE_MAPPING_R1"
PM_WS_URL = r23.PM_WS_URL
RPC_ENDPOINTS = [
    "https://polygon-bor-rpc.publicnode.com",
    "https://polygon-rpc.com",
    "https://1rpc.io/matic",
]
EXCHANGES = {
    "0xe111180000d2663c0091e4f400237545b87b996b",
    "0xe2222d279d744050d28e00520010520000310f59",
    "0xe3333700ca9d93003f00f0f71f8515005f6c00aa",
}
ORDER_FILLED_SIGNATURE = (
    "OrderFilled(bytes32,address,address,uint8,uint256,uint256,uint256,uint256,bytes32,bytes32)"
)
_k = keccak.new(digest_bits=256)
_k.update(ORDER_FILLED_SIGNATURE.encode())
ORDER_FILLED_TOPIC = "0x" + _k.hexdigest()
UNIT = 1_000_000.0


def _rpc(method: str, params: list[Any], timeout: float = 15.0) -> Any:
    last = None
    for url in RPC_ENDPOINTS:
        try:
            resp = requests.post(
                url,
                json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                timeout=timeout,
            )
            resp.raise_for_status()
            payload = resp.json()
            if payload.get("error") is None:
                return payload.get("result")
            last = payload.get("error")
        except Exception as exc:
            last = f"{type(exc).__name__}:{exc}"
    raise RuntimeError(f"RPC_FAILED:{last}")


def _receipt(tx_hash: str) -> dict[str, Any] | None:
    for delay in (0.0, 1.0, 2.0, 4.0):
        if delay:
            time.sleep(delay)
        result = _rpc("eth_getTransactionReceipt", [tx_hash])
        if isinstance(result, dict):
            return result
    return None


def _word(data: str, index: int) -> int:
    raw = data[2:] if data.startswith("0x") else data
    start = index * 64
    return int(raw[start:start + 64], 16)


def _addr_from_topic(topic: str) -> str:
    raw = topic.lower().replace("0x", "")
    return "0x" + raw[-40:]


def _decode_order_filled(log: dict[str, Any]) -> dict[str, Any] | None:
    address = str(log.get("address") or "").lower()
    topics = [str(x).lower() for x in (log.get("topics") or [])]
    data = str(log.get("data") or "")
    if address not in EXCHANGES or len(topics) < 4:
        return None
    if topics[0] != ORDER_FILLED_TOPIC.lower():
        return None
    raw = data[2:] if data.startswith("0x") else data
    if len(raw) < 7 * 64:
        return None
    side = _word(data, 0)
    token_id = _word(data, 1)
    maker_amount = _word(data, 2)
    taker_amount = _word(data, 3)
    fee = _word(data, 4)
    if side == 0:
        cash = maker_amount / UNIT
        shares = taker_amount / UNIT
    elif side == 1:
        shares = maker_amount / UNIT
        cash = taker_amount / UNIT
    else:
        return None
    price = None if shares <= 0 else cash / shares
    return {
        "exchange": address,
        "orderHash": topics[1],
        "maker": _addr_from_topic(topics[2]),
        "taker": _addr_from_topic(topics[3]),
        "side": "BUY" if side == 0 else "SELL",
        "tokenId": str(token_id),
        "makerAmountFilledRaw": str(maker_amount),
        "takerAmountFilledRaw": str(taker_amount),
        "feeRaw": str(fee),
        "shares": shares,
        "cash": cash,
        "price": price,
        "logIndex": int(str(log.get("logIndex") or "0x0"), 16),
    }


def _close(a: float | None, b: float | None, tol: float = 1e-6) -> bool:
    return a is not None and b is not None and abs(a - b) <= max(tol, tol * max(abs(a), abs(b), 1.0))


def _analyze_event(event: dict[str, Any]) -> dict[str, Any]:
    tx_hash = str(event.get("transaction_hash") or "")
    token_id = str(event.get("asset_id") or "")
    ws_price = float(event["price"])
    ws_size = float(event["size"])
    ws_side = str(event.get("side") or "").upper()
    out: dict[str, Any] = {
        "ws": {
            "transactionHash": tx_hash,
            "assetId": token_id,
            "price": ws_price,
            "size": ws_size,
            "side": ws_side,
            "timestamp": event.get("timestamp"),
        },
        "status": "UNKNOWN",
    }
    if not tx_hash:
        out["reason"] = "NO_TRANSACTION_HASH"
        return out
    receipt = _receipt(tx_hash)
    if not receipt:
        out["reason"] = "RECEIPT_UNAVAILABLE"
        return out
    fills = []
    for log in receipt.get("logs") or []:
        decoded = _decode_order_filled(log)
        if decoded is not None:
            fills.append(decoded)
    token_fills = [x for x in fills if x["tokenId"] == token_id]
    out["orderFilled"] = token_fills
    if not token_fills:
        out["reason"] = "NO_MATCHING_ORDERFILLED"
        return out

    # V2/V3 taker-level OrderFilled is emitted with taker == exchange contract.
    taker_logs = [
        x for x in token_fills
        if x["taker"] == x["exchange"] and x["side"] == ws_side
    ]
    maker_logs = [
        x for x in token_fills
        if not (x["taker"] == x["exchange"]) and x["side"] != ws_side
    ]
    out["takerLogs"] = taker_logs
    out["makerLogs"] = maker_logs
    if len(taker_logs) != 1:
        out["status"] = "AMBIGUOUS"
        out["reason"] = f"TAKER_LOG_COUNT_{len(taker_logs)}"
        return out

    taker = taker_logs[0]
    same_price = [x for x in maker_logs if _close(x["price"], ws_price, tol=5e-6)]
    same_price_sum = sum(x["shares"] for x in same_price)
    individual_match = any(_close(x["shares"], ws_size) and _close(x["price"], ws_price, tol=5e-6) for x in maker_logs)
    price_level_match = _close(same_price_sum, ws_size)
    aggregate_match = _close(taker["shares"], ws_size)
    maker_prices = sorted({round(float(x["price"]), 8) for x in maker_logs if x["price"] is not None})
    out["metrics"] = {
        "takerShares": taker["shares"],
        "samePriceMakerShares": same_price_sum,
        "makerFillCount": len(maker_logs),
        "makerPriceCount": len(maker_prices),
        "makerPrices": maker_prices,
        "wsEqualsIndividualMakerFill": individual_match,
        "wsEqualsPriceLevelMakerSum": price_level_match,
        "wsEqualsAggregateTakerShares": aggregate_match,
    }
    if len(maker_logs) < 2:
        out["status"] = "QUALIFIED_SINGLE_MAKER_CONTROL"
        out["reason"] = "ONE_MAKER_FILL"
    elif len(maker_prices) < 2:
        out["status"] = "QUALIFIED_MULTI_MAKER_SAME_PRICE"
        out["reason"] = "MULTI_MAKER_BUT_ONE_PRICE"
    else:
        out["status"] = "QUALIFIED_MULTI_PRICE"
        if price_level_match and not aggregate_match:
            out["reason"] = "SUPPORT_PRICE_LEVEL_OR_INDIVIDUAL_SIZE"
        elif aggregate_match and not price_level_match:
            out["reason"] = "SUPPORT_AGGREGATE_TAKER_SIZE"
        elif individual_match and not aggregate_match:
            out["reason"] = "SUPPORT_INDIVIDUAL_MAKER_FILL_SIZE"
        else:
            out["reason"] = "MIXED_OR_NONUNIQUE_MAPPING"
    return out


async def _collect_window(start_ts: int, end_ts: int) -> list[dict[str, Any]]:
    market = await asyncio.to_thread(r23.wp.fetch_polymarket_market, start_ts)
    tokens = [market.up_token, market.down_token]
    events: list[dict[str, Any]] = []
    async with websockets.connect(PM_WS_URL, ping_interval=None, close_timeout=5, max_queue=4096) as ws:
        await ws.send(json.dumps({"assets_ids": tokens, "type": "market", "custom_feature_enabled": True}))
        while time.time() < end_ts:
            timeout = max(0.2, min(2.0, end_ts - time.time()))
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
            except asyncio.TimeoutError:
                continue
            if raw == "PONG":
                continue
            try:
                payload = json.loads(raw)
            except Exception:
                continue
            rows = payload if isinstance(payload, list) else [payload]
            for row in rows:
                if not isinstance(row, dict) or row.get("event_type") != "last_trade_price":
                    continue
                if row.get("asset_id") not in tokens:
                    continue
                if row.get("transaction_hash") and row.get("size") not in (None, ""):
                    events.append(dict(row))
    return events


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--windows", type=int, default=6)
    ap.add_argument("--output", default="artifacts/a7-size-mapping-r1.json")
    args = ap.parse_args()

    now = int(time.time())
    first = ((now // 300) + 1) * 300
    report: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "mode": "PUBLIC_READ_ONLY_NO_TRADE",
        "noTrade": True,
        "orderFilledTopic": ORDER_FILLED_TOPIC,
        "windows": [],
        "events": [],
    }
    seen = set()
    for i in range(args.windows):
        start = first + i * 300
        end = start + 300
        if time.time() < start:
            await asyncio.sleep(start - time.time())
        rows = await _collect_window(start, end)
        report["windows"].append({"startTs": start, "endTs": end, "publicTradeEvents": len(rows)})
        for row in rows:
            key = (
                str(row.get("transaction_hash") or "").lower(),
                str(row.get("asset_id") or ""),
                str(row.get("price") or ""),
                str(row.get("size") or ""),
                str(row.get("side") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            try:
                analyzed = await asyncio.to_thread(_analyze_event, row)
            except Exception as exc:
                analyzed = {"ws": row, "status": "ERROR", "reason": f"{type(exc).__name__}:{exc}"}
            report["events"].append(analyzed)

    qualified = [x for x in report["events"] if str(x.get("status", "")).startswith("QUALIFIED")]
    multi_price = [x for x in qualified if x.get("status") == "QUALIFIED_MULTI_PRICE"]
    aggregate_only = [
        x for x in multi_price
        if x.get("metrics", {}).get("wsEqualsAggregateTakerShares")
        and not x.get("metrics", {}).get("wsEqualsPriceLevelMakerSum")
    ]
    level_or_individual = [
        x for x in multi_price
        if x.get("metrics", {}).get("wsEqualsPriceLevelMakerSum")
        and not x.get("metrics", {}).get("wsEqualsAggregateTakerShares")
    ]
    if aggregate_only:
        verdict = "A7_FAIL_AGGREGATE_TAKER_SIZE_COUNTEREXAMPLE"
    elif len(multi_price) >= 5 and len(level_or_individual) == len(multi_price):
        verdict = "A7_OPERATIONALLY_VALIDATED_PRICE_LEVEL_SIZE"
    else:
        verdict = "A7_INSUFFICIENT_EVIDENCE"
    report["summary"] = {
        "eventCount": len(report["events"]),
        "qualifiedCount": len(qualified),
        "multiMakerSamePriceCount": sum(x.get("status") == "QUALIFIED_MULTI_MAKER_SAME_PRICE" for x in qualified),
        "multiPriceCount": len(multi_price),
        "multiPriceAggregateOnlyCount": len(aggregate_only),
        "multiPricePriceLevelOnlyCount": len(level_or_individual),
        "verdict": verdict,
    }
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

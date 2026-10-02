"""Prospective PM-side capture for the fixed 12-window World↔Polymarket BTC5m indicative signal pilot.

This script does NOT collect World data. World read-only 1-second history is joined
after the pre-frozen windows complete via the official World MCP.

NO_TRADE:
- no signing
- no orders
- no simulation
- no paper fills
- no execution/PnL adjudication
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Mapping

from tools.research import world_polymarket_btc5m_leadlag_probe_r1 as wp
from tools.research import world_pm_jupiter_economic_poc_r0 as jp
from tools.research import world_polymarket_btc5m_executable_sync_r23 as r23


def book_summary(token: str) -> dict[str, Any]:
    started = time.time()
    url = r23.PM_BOOK_URL + "?token_id=" + token
    try:
        body = jp.http_json(url, timeout=6.0)
    except Exception as exc:
        return {
            "success": False,
            "startedAt": started,
            "receivedAt": time.time(),
            "error": f"{type(exc).__name__}:{exc}",
        }
    received = time.time()
    asks = r23._book_rows(r23._levels(body.get("asks") if isinstance(body, Mapping) else None), asks=True)
    bids = r23._book_rows(r23._levels(body.get("bids") if isinstance(body, Mapping) else None), asks=False)
    return {
        "success": True,
        "startedAt": started,
        "receivedAt": received,
        "elapsedMs": (received - started) * 1000.0,
        "timestamp": body.get("timestamp") if isinstance(body, Mapping) else None,
        "hash": body.get("hash") if isinstance(body, Mapping) else None,
        "bestAsk": asks[0]["price"] if asks else None,
        "bestBid": bids[0]["price"] if bids else None,
        "askLevels": len(asks),
        "bidLevels": len(bids),
        "topAsks": asks[:3],
        "topBids": bids[:3],
    }


def atomic_write(path: Path, obj: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def wait_until(ts: float) -> None:
    while True:
        remaining = ts - time.time()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 2.0))


def capture_window(start: int, args: argparse.Namespace, result: dict[str, Any], output: Path) -> dict[str, Any]:
    end = start + 300
    wait_until(start + args.start_offset_seconds)

    record: dict[str, Any] = {
        "startTs": start,
        "endTs": end,
        "expectedSlug": f"btc-updown-5m-{start}",
        "scheduledAnchors": [],
        "snapshots": [],
        "identity": None,
        "identityError": None,
        "clobClock": None,
    }

    try:
        before = time.time()
        server = wp._server_time()
        after = time.time()
        record["clobClock"] = {
            "serverTime": server,
            "localMid": (before + after) / 2.0,
            "skewSeconds": (before + after) / 2.0 - server,
        }
        market = wp.fetch_polymarket_market(start)
        record["identity"] = {
            "conditionId": market.condition_id,
            "upToken": market.up_token,
            "downToken": market.down_token,
            "feeSchedule": market.fee_schedule,
            "cryptoConfig": market.crypto_config,
            "description": market.description,
        }
    except Exception as exc:
        record["identityError"] = f"{type(exc).__name__}:{exc}"
        record["completedAt"] = time.time()
        return record

    first = start + args.start_offset_seconds
    last = end - args.end_offset_seconds
    anchor = float(first)
    index = 0
    while anchor <= last + 1e-9:
        record["scheduledAnchors"].append(anchor)
        now = time.time()
        if now > anchor + args.max_anchor_lateness_seconds:
            record["snapshots"].append({
                "index": index,
                "scheduledAt": anchor,
                "captured": False,
                "reason": "ANCHOR_LATE",
                "observedAt": now,
                "lateBySeconds": now - anchor,
            })
            anchor += args.cadence_seconds
            index += 1
            continue

        wait_until(anchor)
        up = book_summary(record["identity"]["upToken"])
        down = book_summary(record["identity"]["downToken"])
        observed = time.time()
        record["snapshots"].append({
            "index": index,
            "scheduledAt": anchor,
            "captured": True,
            "observedAt": observed,
            "lateBySeconds": observed - anchor,
            "up": up,
            "down": down,
        })
        if index % 15 == 0:
            atomic_write(output, result)
        anchor += args.cadence_seconds
        index += 1

    record["completedAt"] = time.time()
    captured = [x for x in record["snapshots"] if x.get("captured")]
    paired = [x for x in captured if (x.get("up") or {}).get("success") and (x.get("down") or {}).get("success")]
    record["summary"] = {
        "scheduled": len(record["scheduledAnchors"]),
        "captured": len(captured),
        "pairedSuccess": len(paired),
        "upBestAskPresent": sum((x.get("up") or {}).get("bestAsk") is not None for x in paired),
        "downBestAskPresent": sum((x.get("down") or {}).get("bestAsk") is not None for x in paired),
        "maxPairReceiveSkewSeconds": max(
            [abs(float(x["up"]["receivedAt"]) - float(x["down"]["receivedAt"])) for x in paired] or [0.0]
        ),
    }
    return record


def main(args: argparse.Namespace) -> None:
    if args.windows != 12:
        raise SystemExit("FROZEN_WINDOW_COUNT_MUST_BE_12")
    if args.cadence_seconds != 2.0:
        raise SystemExit("FROZEN_CADENCE_MUST_BE_2_SECONDS")

    starts = [args.first_start_ts + i * 300 for i in range(args.windows)]
    output = Path(args.output)
    result: dict[str, Any] = {
        "schemaVersion": "WORLD_PM_BTC5M_INDICATIVE_SIGNAL_PILOT_PM_CAPTURE_R0",
        "mode": "PROSPECTIVE_PM_LIVE_CAPTURE_WORLD_MCP_JOIN_PENDING",
        "NO_TRADE": True,
        "researchScore": 0,
        "firstStartTs": args.first_start_ts,
        "windowStarts": starts,
        "windowCountFrozen": 12,
        "cadenceSeconds": 2.0,
        "startOffsetSeconds": args.start_offset_seconds,
        "endOffsetSeconds": args.end_offset_seconds,
        "maxAnchorLatenessSeconds": args.max_anchor_lateness_seconds,
        "startedAt": time.time(),
        "windows": [],
        "adjudicationSeparation": {
            "signal": "PENDING_WORLD_MCP_JOIN",
            "executionAfterCost": "NOT_TESTED_BY_DESIGN",
            "settlementAdmission": "SEPARATE_PENDING",
        },
    }
    atomic_write(output, result)

    for start in starts:
        rec = capture_window(start, args, result, output)
        result["windows"].append(rec)
        atomic_write(output, result)

    result["finishedAt"] = time.time()
    result["captureSummary"] = {
        "windowsAttempted": len(result["windows"]),
        "windowsWithIdentity": sum(x.get("identity") is not None for x in result["windows"]),
        "pairedSnapshots": sum((x.get("summary") or {}).get("pairedSuccess", 0) for x in result["windows"]),
        "scheduledSnapshots": sum((x.get("summary") or {}).get("scheduled", 0) for x in result["windows"]),
    }
    result["limitations"] = [
        "PM capture alone cannot adjudicate the World↔PM signal; World MCP history must be joined after these pre-frozen windows.",
        "No displayed quote is treated as a fill.",
        "No World quantity is interpreted as executable depth.",
        "Execution/after-cost and settlement admission are explicitly outside the signal verdict.",
    ]
    atomic_write(output, result)
    print(json.dumps(result["captureSummary"], sort_keys=True))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--first-start-ts", type=int, required=True)
    p.add_argument("--windows", type=int, default=12)
    p.add_argument("--cadence-seconds", type=float, default=2.0)
    p.add_argument("--start-offset-seconds", type=int, default=5)
    p.add_argument("--end-offset-seconds", type=int, default=5)
    p.add_argument("--max-anchor-lateness-seconds", type=float, default=1.25)
    p.add_argument("--output", default="artifacts/world-pm-btc5m-indicative-signal-pilot-pm-capture-r0.json")
    main(p.parse_args())

"""World <-> Polymarket BTC5m Jupiter Economic POC R0.

Purpose: bypass the currently unavailable World/DFlow public /order proxy by
using Jupiter's keyless lite quote API against the same World outcome mints.

Read-only. No wallet, signing, transaction building, order placement, or capital.
Research score = 0: bounded economic/access diagnostic only.
"""
from __future__ import annotations

import argparse
import base64
import json
import struct
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Mapping

from tools.research import world_polymarket_btc5m_executable_sync_r23 as r23
from tools.research import world_polymarket_btc5m_leadlag_probe_r1 as wp

PREDICT_PROGRAM = "prediCtPZCttYMvm2W3PtxmMxLmT1dtN7riU6Cxh6tM"
CASH_MINT = "CASHx9KJUStyftLFWGvEVf59SGeG9sh5FfcnZMVPCASH"
SOLANA_USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
MARKET_SIZE = 320
MARKET_DISC = bytes.fromhex("dbbed53700e3c69a")
JUPITER_QUOTE = "https://lite-api.jup.ag/swap/v1/quote"
B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58encode(raw: bytes) -> str:
    n = int.from_bytes(raw, "big")
    out = ""
    while n:
        n, rem = divmod(n, 58)
        out = B58[rem] + out
    zeros = len(raw) - len(raw.lstrip(b"\0"))
    return "1" * zeros + (out or ("" if zeros else "1"))


def rpc(method: str, params: list[Any], deadline: float) -> Any:
    return wp._rpc(wp.DEFAULT_SOLANA_RPC, method, params, deadline=deadline)


def current_start() -> int:
    return (int(time.time()) // 300) * 300


def program_markets_for_start(start_ts: int, deadline: float) -> list[dict[str, Any]]:
    start_ms = start_ts * 1000
    filters = [
        {"dataSize": MARKET_SIZE},
        {"memcmp": {"offset": 258, "bytes": b58encode(struct.pack("<Q", start_ms))}},
    ]
    rows = rpc(
        "getProgramAccounts",
        [PREDICT_PROGRAM, {"encoding": "base64", "filters": filters, "commitment": "confirmed"}],
        deadline,
    ) or []
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        value = row.get("account")
        if not isinstance(value, Mapping):
            continue
        data_field = value.get("data")
        if not isinstance(data_field, list) or not data_field:
            continue
        try:
            data = base64.b64decode(str(data_field[0]))
        except Exception:
            continue
        if len(data) != MARKET_SIZE or data[:8] != MARKET_DISC:
            continue
        start_ms2, end_ms = struct.unpack_from("<QQ", data, 258)
        if start_ms2 != start_ms:
            continue
        pk = lambda at: b58encode(data[at : at + 32])
        out.append(
            {
                "market": str(row.get("pubkey")),
                "cashMint": pk(40),
                "yesMint": pk(72),
                "noMint": pk(104),
                "vault": pk(136),
                "startTs": start_ms2 // 1000,
                "endTs": end_ms // 1000,
            }
        )
    return out


def token_metadata(mints: list[str], deadline: float) -> dict[str, dict[str, Any]]:
    if not mints:
        return {}
    rows = rpc("getMultipleAccounts", [mints, {"encoding": "jsonParsed", "commitment": "confirmed"}], deadline) or {}
    values = rows.get("value") if isinstance(rows, Mapping) else None
    if not isinstance(values, list):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for mint, acc in zip(mints, values):
        if not isinstance(acc, Mapping):
            continue
        info = (((acc.get("data") or {}).get("parsed") or {}).get("info") or {}) if isinstance(acc.get("data"), Mapping) else {}
        extensions = info.get("extensions") or []
        state = None
        for ext in extensions:
            if isinstance(ext, Mapping) and ext.get("extension") == "tokenMetadata":
                state = ext.get("state")
                break
        if isinstance(state, Mapping):
            out[mint] = {
                "name": state.get("name"),
                "symbol": state.get("symbol"),
                "uri": state.get("uri"),
                "decimals": info.get("decimals"),
            }
    return out


def http_json(url: str, timeout: float = 10.0) -> Any:
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "world-pm-jupiter-economic-poc-r0/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def describe_candidates(markets: list[dict[str, Any]], deadline: float) -> list[dict[str, Any]]:
    md = token_metadata([m["yesMint"] for m in markets], deadline)
    out = []
    for m in markets:
        row = dict(m)
        meta = md.get(m["yesMint"], {})
        row["yesMetadata"] = meta
        uri = meta.get("uri")
        desc = None
        if isinstance(uri, str) and uri.startswith(("http://", "https://")):
            try:
                body = http_json(uri, timeout=max(1.0, min(8.0, deadline - time.time())))
                if isinstance(body, Mapping):
                    desc = body.get("description") or body.get("name")
            except Exception as exc:
                row["metadataError"] = f"{type(exc).__name__}:{exc}"
        row["description"] = desc
        out.append(row)
    return out


def select_btc(markets: list[dict[str, Any]]) -> dict[str, Any]:
    matches = []
    for m in markets:
        hay = " ".join(
            str(x or "") for x in [m.get("description"), (m.get("yesMetadata") or {}).get("name"), (m.get("yesMetadata") or {}).get("symbol")]
        ).lower()
        if "btc" in hay or "bitcoin" in hay:
            matches.append(m)
    if len(matches) != 1:
        raise RuntimeError(f"WORLD_BTC5M_NOT_UNIQUE:{len(matches)}")
    m = matches[0]
    if int(m["endTs"]) - int(m["startTs"]) != 300:
        raise RuntimeError("WORLD_BTC_NOT_5M")
    return m


def jupiter_quote(input_mint: str, output_mint: str, amount: int, slippage_bps: int = 100) -> dict[str, Any]:
    q = urllib.parse.urlencode(
        {
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": str(amount),
            "slippageBps": str(slippage_bps),
        }
    )
    started = time.time()
    try:
        body = http_json(f"{JUPITER_QUOTE}?{q}", timeout=10.0)
    except Exception as exc:
        return {"success": False, "startedAt": started, "receivedAt": time.time(), "error": f"{type(exc).__name__}:{exc}"}
    received = time.time()
    ok = isinstance(body, Mapping) and body.get("outAmount") is not None
    return {
        "success": bool(ok),
        "startedAt": started,
        "receivedAt": received,
        "elapsedMs": (received - started) * 1000.0,
        "request": {"inputMint": input_mint, "outputMint": output_mint, "amount": amount, "slippageBps": slippage_bps},
        "response": body,
    }


def pm_book(token: str) -> dict[str, Any]:
    started = time.time()
    url = r23.PM_BOOK_URL + "?" + urllib.parse.urlencode({"token_id": token})
    try:
        body = http_json(url, timeout=8.0)
    except Exception as exc:
        return {"success": False, "startedAt": started, "receivedAt": time.time(), "error": f"{type(exc).__name__}:{exc}"}
    received = time.time()
    asks = r23._book_rows(r23._levels(body.get("asks") if isinstance(body, Mapping) else None), asks=True)
    return {
        "success": True,
        "startedAt": started,
        "receivedAt": received,
        "elapsedMs": (received - started) * 1000.0,
        "asks": asks,
        "bestAsk": asks[0]["price"] if asks else None,
        "timestamp": body.get("timestamp") if isinstance(body, Mapping) else None,
        "hash": body.get("hash") if isinstance(body, Mapping) else None,
    }


def route_legs(q: Mapping[str, Any]) -> list[dict[str, Any]]:
    body = q.get("response")
    if not isinstance(body, Mapping):
        return []
    out = []
    for row in body.get("routePlan") or []:
        if not isinstance(row, Mapping):
            continue
        info = row.get("swapInfo")
        if isinstance(info, Mapping):
            out.append(
                {
                    "label": info.get("label"),
                    "ammKey": info.get("ammKey"),
                    "inputMint": info.get("inputMint"),
                    "outputMint": info.get("outputMint"),
                    "inAmount": info.get("inAmount"),
                    "outAmount": info.get("outAmount"),
                }
            )
    return out


def pair_economics(world_quote: Mapping[str, Any], book: Mapping[str, Any], fee_schedule: Mapping[str, Any], cash_units: float) -> dict[str, Any]:
    if not world_quote.get("success") or not book.get("success"):
        return {"qualified": False}
    body = world_quote.get("response")
    if not isinstance(body, Mapping):
        return {"qualified": False}
    try:
        expected = int(str(body["outAmount"])) / 1_000_000.0
        min_out = int(str(body.get("otherAmountThreshold") or body["outAmount"])) / 1_000_000.0
    except Exception:
        return {"qualified": False}
    if min_out <= 0:
        return {"qualified": False}
    walk = r23._walk_pm_book_for_net_shares(book.get("asks") or [], min_out, fee_schedule)
    if not walk.get("filled"):
        return {
            "qualified": False,
            "worldExpectedShares": expected,
            "worldMinShares": min_out,
            "pmWalk": walk,
        }
    package_cost = cash_units + float(walk["cost"])
    edge_usd_nominal = min_out - package_cost
    return {
        "qualified": True,
        "worldExpectedShares": expected,
        "worldMinShares": min_out,
        "pmWalk": walk,
        "packageCostNominal": package_cost,
        "payoutIfDirectionPayoffsEquivalentNominal": min_out,
        "edgeNominal": edge_usd_nominal,
        "edgePerShareNominal": edge_usd_nominal / min_out,
        "edgeBpsNominal": edge_usd_nominal / min_out * 10000.0,
        "unitCostNominal": package_cost / min_out,
    }


def one_snapshot(cash_units: float) -> dict[str, Any]:
    observed = time.time()
    start = (int(observed) // 300) * 300
    deadline = time.time() + 45.0
    candidates = describe_candidates(program_markets_for_start(start, deadline), deadline)
    market = select_btc(candidates)
    pm = wp.fetch_polymarket_market(start)
    amount = int(round(cash_units * 1_000_000))
    yes_q = jupiter_quote(CASH_MINT, market["yesMint"], amount)
    no_q = jupiter_quote(CASH_MINT, market["noMint"], amount)
    down_book = pm_book(pm.down_token)
    up_book = pm_book(pm.up_token)
    control = jupiter_quote(CASH_MINT, SOLANA_USDC, amount)
    return {
        "observedAt": observed,
        "startTs": start,
        "worldMarket": market,
        "worldCandidateCountForSlot": len(candidates),
        "worldCandidates": candidates,
        "polymarket": {
            "conditionId": pm.condition_id,
            "upToken": pm.up_token,
            "downToken": pm.down_token,
            "feeSchedule": pm.fee_schedule,
            "cryptoConfig": pm.crypto_config,
        },
        "jupiter": {
            "yes": {**yes_q, "routeLegs": route_legs(yes_q)},
            "no": {**no_q, "routeLegs": route_legs(no_q)},
            "cashToSolanaUsdcControl": {**control, "routeLegs": route_legs(control)},
        },
        "pmBooks": {"up": up_book, "down": down_book},
        "pairs": {
            "WORLD_YES+PM_DOWN": pair_economics(yes_q, down_book, pm.fee_schedule, cash_units),
            "WORLD_NO+PM_UP": pair_economics(no_q, up_book, pm.fee_schedule, cash_units),
        },
    }


def main(args: argparse.Namespace) -> None:
    result: dict[str, Any] = {
        "schemaVersion": "WORLD_PM_JUPITER_ECONOMIC_POC_R0",
        "mode": "PUBLIC_READ_ONLY_NO_TRADE",
        "researchScore": 0,
        "requestCashUnits": args.request_cash,
        "snapshotsRequested": args.snapshots,
        "startedAt": time.time(),
        "snapshots": [],
    }
    for idx in range(args.snapshots):
        try:
            snap = one_snapshot(args.request_cash)
        except Exception as exc:
            snap = {"observedAt": time.time(), "error": f"{type(exc).__name__}:{exc}"}
        result["snapshots"].append(snap)
        if idx + 1 < args.snapshots:
            time.sleep(args.interval_seconds)
    attempts = []
    for snap in result["snapshots"]:
        for pair, econ in (snap.get("pairs") or {}).items():
            qname = "yes" if pair.startswith("WORLD_YES") else "no"
            q = ((snap.get("jupiter") or {}).get(qname) or {})
            attempts.append(
                {
                    "startTs": snap.get("startTs"),
                    "pair": pair,
                    "jupiterRouteSuccess": q.get("success"),
                    "routeLabels": [x.get("label") for x in q.get("routeLegs") or []],
                    "qualified": econ.get("qualified"),
                    "edgeBpsNominal": econ.get("edgeBpsNominal"),
                }
            )
    result["summary"] = {
        "snapshotsCompleted": sum("error" not in x for x in result["snapshots"]),
        "pairAttempts": len(attempts),
        "jupiterRouteSuccesses": sum(bool(x["jupiterRouteSuccess"]) for x in attempts),
        "qualifiedEconomics": sum(bool(x["qualified"]) for x in attempts),
        "positiveNominalEconomics": sum(bool(x["qualified"]) and float(x["edgeBpsNominal"] or 0) > 0 for x in attempts),
        "attempts": attempts,
    }
    result["limitations"] = [
        "Jupiter quote is pre-trade and no transaction is signed or simulated here.",
        "otherAmountThreshold is used as conservative World minimum output under the requested slippage.",
        "World CASH and Polymarket collateral are not assumed risk-free equivalent; stablecoin/cross-chain basis remains unresolved.",
        "World and Polymarket settlement-source equivalence remains a separate basis-risk question.",
        "This is a bounded diagnostic and does not promote EXECUTABLE_EDGE.",
    ]
    result["finishedAt"] = time.time()
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result["summary"], sort_keys=True))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--request-cash", type=float, default=10.0)
    p.add_argument("--snapshots", type=int, default=3)
    p.add_argument("--interval-seconds", type=float, default=3.0)
    p.add_argument("--output", default="artifacts/world-pm-jupiter-economic-poc-r0.json")
    main(p.parse_args())

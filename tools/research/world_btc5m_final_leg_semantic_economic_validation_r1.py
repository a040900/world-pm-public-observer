"""WORLD BTC5M bounded final-leg semantic/economic validation R1.

Continuation of run 36678004671 under the same aggregate budget:
- prior run: 1 fresh window, 2 unsigned simulations
- this run: at most 1 fresh window, 4 unsigned simulations
Aggregate maximum remains 2 windows / 6 simulations.

No private keys, signing, broadcast, orders, or capital.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import time
import urllib.request
from pathlib import Path
from typing import Any, Mapping

from websockets.sync.client import connect

from tools.research import world_btc5m_final_leg_bounded_validation_r0 as base
from tools.research import world_pm_jupiter_economic_poc_r0 as jp
from tools.research import world_polymarket_btc5m_executable_sync_r23 as r23
from tools.research import world_polymarket_btc5m_leadlag_probe_r1 as wp

DFLOW_WS = "wss://dev-quote-api.dflow.net/quote-stream"
PREDICT = base.PREDICT
DFLOW = base.DFLOW
COMPUTE_BUDGET = base.COMPUTE_BUDGET
CASH = base.CASH

ANCHOR_NAMES = [
    "split",
    "merge",
    "initialize_market",
    "determine_outcome",
    "redeem_outcome_for_user",
    "close_market",
    "update_metadata",
    "create_user_settings",
    "update_user_settings",
    "close_user_settings",
]
PREDICT_DISCS = {
    hashlib.sha256(f"global:{name}".encode()).digest()[:8].hex(): name
    for name in ANCHOR_NAMES
}


def dflow_snapshot(yes_mint: str, no_mint: str, timeout: float = 4.0) -> dict[str, Any]:
    pairs = {
        f"{yes_mint}/{CASH}": "yes",
        f"{no_mint}/{CASH}": "no",
    }
    latest: dict[str, Any] = {}
    started = time.time()
    error = None
    try:
        with connect(
            DFLOW_WS,
            open_timeout=min(3.0, timeout),
            close_timeout=1.0,
            ping_interval=20.0,
            ping_timeout=10.0,
        ) as ws:
            for base_mint, side in ((yes_mint, "yes"), (no_mint, "no")):
                ws.send(json.dumps({"op": "subscribe", "base_mint": base_mint, "quote_mint": CASH}, separators=(",", ":")))
            deadline = started + timeout
            while time.time() < deadline and len(latest) < 2:
                try:
                    raw = ws.recv(timeout=max(0.05, deadline - time.time()))
                except TimeoutError:
                    break
                frame = json.loads(raw)
                for update in frame.get("updates", []) or []:
                    key = f"{update.get('sb')}/{update.get('sq')}"
                    side = pairs.get(key)
                    if not side:
                        continue
                    latest[side] = {
                        "receivedAt": time.time(),
                        "sourceTimestamp": update.get("ts", frame.get("ts")),
                        "slot": frame.get("u"),
                        "error": update.get("e"),
                        "bid": update.get("b"),
                        "ask": update.get("a"),
                        "bidSizeQuote": update.get("B"),
                        "askSizeQuote": update.get("A"),
                    }
    except Exception as exc:
        error = f"{type(exc).__name__}:{exc}"
    observed = time.time()
    for row in latest.values():
        row["quoteAgeSeconds"] = observed - float(row["receivedAt"])
    return {"observedAt": observed, "startedAt": started, "elapsedMs": (observed-started)*1000, "error": error, "yes": latest.get("yes"), "no": latest.get("no")}


def predict_top_level(parsed: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = (((parsed.get("transaction") or {}).get("message") or {}).get("instructions") or [])
    out = []
    for pos, ix in enumerate(rows):
        if not isinstance(ix, Mapping) or str(ix.get("programId") or "") != PREDICT:
            continue
        data58 = str(ix.get("data") or "")
        try:
            raw = base.dma.b58decode(data58)
            disc = raw[:8].hex()
        except Exception:
            raw = b""
            disc = ""
        out.append({
            "position": pos,
            "discriminatorHex": disc,
            "decodedName": PREDICT_DISCS.get(disc),
            "dataHex": raw.hex(),
            "dataLength": len(raw),
            "accountCount": len(ix.get("accounts") or []),
            "accounts": [str(x) for x in (ix.get("accounts") or [])],
        })
    return out


def build_variant(raw: bytes, parsed: Mapping[str, Any], predict_keep: list[int]) -> tuple[bytes | None, dict[str, Any]]:
    try:
        from solders.message import MessageV0
        from solders.signature import Signature
        from solders.transaction import VersionedTransaction
        tx = VersionedTransaction.from_bytes(raw)
        msg = tx.message
    except Exception as exc:
        return None, {"supported": False, "reason": f"DECODE:{type(exc).__name__}:{exc}"}

    if not isinstance(msg, MessageV0):
        return None, {"supported": False, "reason": f"MESSAGE_NOT_V0:{type(msg).__name__}"}

    programs = base.top_programs(parsed)
    if len(programs) != len(msg.instructions):
        return None, {"supported": False, "reason": "TOP_IX_COUNT_MISMATCH"}

    keep = [
        i for i, pid in enumerate(programs)
        if pid in {COMPUTE_BUDGET, DFLOW} or (pid == PREDICT and i in predict_keep)
    ]
    try:
        new_msg = MessageV0(
            header=msg.header,
            account_keys=list(msg.account_keys),
            recent_blockhash=msg.recent_blockhash,
            instructions=[msg.instructions[i] for i in keep],
            address_table_lookups=list(msg.address_table_lookups),
        )
        sigs = [Signature.default() for _ in range(msg.header.num_required_signatures)]
        built = bytes(VersionedTransaction.populate(new_msg, sigs))
    except Exception as exc:
        return None, {"supported": False, "reason": f"BUILD:{type(exc).__name__}:{exc}"}
    return built, {"supported": True, "keepPositions": keep, "predictKeep": predict_keep, "topPrograms": programs}


def http_json(url: str, timeout: float = 8.0) -> Any:
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "world-btc5m-final-leg-r1/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def pm_book(token: str) -> dict[str, Any]:
    return jp.pm_book(token)


def simulation_user_effects(result: Mapping[str, Any], candidate: Mapping[str, Any], market: Mapping[str, Any]) -> dict[str, Any]:
    effects = ((result.get("accountEffects") or {}).get("accounts") or [])
    user = str(candidate["user"])
    cash_delta = sum(int(x.get("deltaAtoms") or 0) for x in effects if x.get("owner") == user and x.get("mint") == CASH)
    yes_delta = sum(int(x.get("deltaAtoms") or 0) for x in effects if x.get("owner") == user and x.get("mint") == str(market["yesMint"]))
    no_delta = sum(int(x.get("deltaAtoms") or 0) for x in effects if x.get("owner") == user and x.get("mint") == str(market["noMint"]))
    sol_delta = next((int(x.get("deltaLamports") or 0) for x in effects if x.get("address") == user and x.get("mint") == "SOL"), None)
    return {"cashDeltaAtoms": cash_delta, "yesDeltaAtoms": yes_delta, "noDeltaAtoms": no_delta, "solDeltaLamports": sol_delta}


def economic_eval(result: Mapping[str, Any], candidate: Mapping[str, Any], market: Mapping[str, Any], pm: Any, context: Mapping[str, Any]) -> dict[str, Any]:
    if result.get("success") is not True:
        return {"qualified": False, "reason": "SIMULATION_FAILED"}
    eff = simulation_user_effects(result, candidate, market)
    yes = eff["yesDeltaAtoms"] / 1_000_000.0
    no = eff["noDeltaAtoms"] / 1_000_000.0
    cash_spent = max(0.0, -eff["cashDeltaAtoms"] / 1_000_000.0)
    if yes > 0 and yes >= no:
        world_side, shares, pm_token, pm_side = "yes", yes, pm.down_token, "down"
    elif no > 0:
        world_side, shares, pm_token, pm_side = "no", no, pm.up_token, "up"
    else:
        return {"qualified": False, "reason": "NO_USER_OUTCOME_RECEIVED", "userEffects": eff}

    book = (context.get("pmBooks") or {}).get(pm_side) or {}
    if book.get("success") is not True:
        return {"qualified": False, "reason": "PM_BOOK_UNAVAILABLE", "userEffects": eff}
    walk = r23._walk_pm_book_for_net_shares(book.get("asks") or [], shares, pm.fee_schedule)
    execution_price = cash_spent / shares if shares > 0 else None
    stream_leg = ((context.get("dflow") or {}).get(world_side) or {})
    try:
        stream_ask = float(stream_leg.get("ask")) if stream_leg.get("ask") is not None else None
    except Exception:
        stream_ask = None
    stream_gap_bps = None if not stream_ask or execution_price is None else (execution_price / stream_ask - 1.0) * 10000.0
    out = {
        "qualified": bool(walk.get("filled")),
        "worldSide": world_side,
        "pmSide": pm_side,
        "worldCashSpent": cash_spent,
        "worldOutcomeSharesReceived": shares,
        "worldEffectiveCashPerShare": execution_price,
        "streamAsk": stream_ask,
        "executionVsStreamAskBps": stream_gap_bps,
        "userEffects": eff,
        "pmWalk": walk,
    }
    if walk.get("filled"):
        package = cash_spent + float(walk["cost"])
        edge = shares - package
        out.update({
            "conditionalPackageCost": package,
            "conditionalPayoutShares": shares,
            "conditionalEdge": edge,
            "conditionalEdgeBps": edge / shares * 10000.0 if shares else None,
        })
    return out


def capture_context(market: Mapping[str, Any], pm: Any) -> dict[str, Any]:
    dflow = dflow_snapshot(str(market["yesMint"]), str(market["noMint"]))
    down = pm_book(pm.down_token)
    up = pm_book(pm.up_token)
    return {"capturedAt": time.time(), "dflow": dflow, "pmBooks": {"up": up, "down": down}}


def choose_direct_candidate(market: Mapping[str, Any], start: int, end: int, seen: set[str], deadline: float) -> tuple[dict[str, Any], bytes, Mapping[str, Any]] | None:
    rows = base.fresh_candidate_rows(str(market["market"]), start, end, min(deadline, time.time()+20))
    for sigrow in rows:
        sig = str(sigrow.get("signature") or "")
        if not sig or sig in seen:
            continue
        seen.add(sig)
        parsed = base.parsed_tx(sig, min(deadline, time.time()+20))
        if not parsed:
            continue
        summary = base.candidate_summary(sigrow, parsed, market)
        if not summary or summary.get("directCashToOutcome") is not True:
            continue
        raw = base.raw_tx(sig, min(deadline, time.time()+20))
        if raw is not None:
            return summary, raw, parsed
    return None


def main(args: argparse.Namespace) -> None:
    if args.max_simulations < 1 or args.max_simulations > 4:
        raise SystemExit("R1 continuation max_simulations must be 1..4")
    start = base.next_eligible_start(int(time.time()), args.min_remaining)
    if time.time() < start:
        time.sleep(start-time.time())
    if time.time() < start + args.min_window_age:
        time.sleep(start + args.min_window_age - time.time())
    end = start + 300
    deadline = end - 3

    result: dict[str, Any] = {
        "schemaVersion": "WORLD_BTC5M_FINAL_LEG_SEMANTIC_ECONOMIC_VALIDATION_R1",
        "mode": "PUBLIC_READ_ONLY_UNSIGNED_SIMULATION_ONLY",
        "researchScore": 0,
        "priorRun": 36678004671,
        "aggregateBudget": {"maxWindows": 2, "maxSimulations": 6},
        "thisRunBudget": {"maxWindows": 1, "maxSimulations": args.max_simulations},
        "startTs": start,
        "endTs": end,
        "startedAt": time.time(),
        "simulations": [],
    }

    market, markets = base.discover_btc(start, min(deadline, time.time()+45))
    pm = wp.fetch_polymarket_market(start)
    result["worldMarket"] = market
    result["slotMarketCount"] = len(markets)
    result["polymarket"] = {"conditionId": pm.condition_id, "upToken": pm.up_token, "downToken": pm.down_token, "feeSchedule": pm.fee_schedule, "cryptoConfig": pm.crypto_config}

    seen: set[str] = set()
    selected = None
    while time.time() < deadline and selected is None:
        try:
            selected = choose_direct_candidate(market, start, end, seen, deadline)
        except Exception as exc:
            result.setdefault("selectionErrors", []).append(f"{type(exc).__name__}:{exc}")
        if selected is None:
            time.sleep(min(args.poll_seconds, max(0.0, deadline-time.time())))

    if selected is None:
        result["summary"] = {"verdict": "NO_RESULT_NO_DIRECT_CASH_CONTROL", "simulationsUsed": 0}
    else:
        candidate, raw, parsed = selected
        result["candidate"] = candidate
        predict_ix = predict_top_level(parsed)
        result["predictTopLevel"] = predict_ix
        context = capture_context(market, pm)
        result["context"] = context
        watch = base.watch_accounts(candidate, market)

        exact = base.simulate(raw, watch, time.time()+35)
        result["simulations"].append({"kind": "EXACT_CONTROL", "result": exact, "economic": economic_eval(exact, candidate, market, pm, context)})
        used = 1

        if exact.get("success") is True and used < args.max_simulations:
            positions = [int(x["position"]) for x in predict_ix]
            variants: list[tuple[str, list[int]]] = [("DFLOW_ONLY", [])]
            if positions:
                variants.append(("DFLOW_PLUS_PREDICT_1", [positions[0]]))
            if len(positions) > 1:
                variants.append(("DFLOW_PLUS_PREDICT_2", [positions[1]]))
            for name, keep_pred in variants:
                if used >= args.max_simulations:
                    break
                built, builder = build_variant(raw, parsed, keep_pred)
                if built is None:
                    result["simulations"].append({"kind": name, "builder": builder, "result": {"success": False, "notSimulated": True}})
                    continue
                sim = base.simulate(built, watch, time.time()+35)
                used += 1
                result["simulations"].append({"kind": name, "builder": builder, "result": sim, "economic": economic_eval(sim, candidate, market, pm, context)})

        successes = [x for x in result["simulations"] if (x.get("result") or {}).get("success") is True]
        result["summary"] = {
            "verdict": "SEMANTIC_VARIANTS_MEASURED" if successes else "NO_RESULT_CONTROL_FAILED",
            "simulationsUsed": sum(1 for x in result["simulations"] if not (x.get("result") or {}).get("notSimulated")),
            "successfulSimulationKinds": [x["kind"] for x in successes],
            "conditionalEdgeBpsByKind": {x["kind"]: (x.get("economic") or {}).get("conditionalEdgeBps") for x in successes},
            "executionVsStreamAskBpsByKind": {x["kind"]: (x.get("economic") or {}).get("executionVsStreamAskBps") for x in successes},
        }

    result["finishedAt"] = time.time()
    result["limitations"] = [
        "All economics remain conditional on World/Polymarket payoff equivalence.",
        "The reused successful transaction carries route parameters from a recent real trade; success does not prove an independent quote builder.",
        "Unsigned simulations use current on-chain state and do not commit changes.",
        "No private key, signature, broadcast, order, or capital is used.",
    ]
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)+"\n")
    print(json.dumps(result["summary"], sort_keys=True))


if __name__ == "__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--max-simulations", type=int, default=4)
    p.add_argument("--min-window-age", type=int, default=20)
    p.add_argument("--min-remaining", type=int, default=90)
    p.add_argument("--poll-seconds", type=float, default=6.0)
    p.add_argument("--output", default="artifacts/world-btc5m-final-leg-semantic-economic-validation-r1.json")
    main(p.parse_args())

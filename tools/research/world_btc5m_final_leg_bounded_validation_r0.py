"""WORLD BTC5M bounded final-leg execution validation R0.

Read-only / simulation-only diagnostic authorized by the R2 research decision.
Hard limits:
- at most two fresh BTC 5m windows
- at most six simulateTransaction calls
- no private keys, signing, sendTransaction, orders, or capital

The diagnostic first requires a same-market successful mainnet transaction as a
control. It replays that transaction with sigVerify=false and a replacement
blockhash. Only after a control simulation succeeds does it try a trimmed v0
message containing ComputeBudget + DFlow top-level instructions.

A trimmed success is execution reachability evidence only. It is not an
economic-success verdict and is not an authorization to trade.
"""
from __future__ import annotations

import argparse
import base64
import json
import time
from pathlib import Path
from typing import Any, Mapping

from tools.research import direct_maker_access_poc_r0 as dma
from tools.research import world_pm_jupiter_economic_poc_r0 as jp
from tools.research import world_polymarket_btc5m_leadlag_probe_r1 as wp

DFLOW = dma.DFLOW
BISON = dma.BISON
PREDICT = dma.PREDICT
CASH = jp.CASH_MINT
USDC = jp.SOLANA_USDC
COMPUTE_BUDGET = "ComputeBudget111111111111111111111111111111"

SIM_LIMIT_HARD = 6
WINDOW_LIMIT_HARD = 2


def rpc(method: str, params: list[Any], deadline: float) -> Any:
    return wp._rpc(wp.DEFAULT_SOLANA_RPC, method, params, deadline=deadline)


def parsed_tx(sig: str, deadline: float) -> Mapping[str, Any] | None:
    value = rpc(
        "getTransaction",
        [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 1, "commitment": "confirmed"}],
        deadline,
    )
    return value if isinstance(value, Mapping) else None


def raw_tx(sig: str, deadline: float) -> bytes | None:
    value = rpc(
        "getTransaction",
        [sig, {"encoding": "base64", "maxSupportedTransactionVersion": 1, "commitment": "confirmed"}],
        deadline,
    )
    if not isinstance(value, Mapping):
        return None
    tx = value.get("transaction")
    if not isinstance(tx, list) or not tx:
        return None
    try:
        return base64.b64decode(str(tx[0]))
    except Exception:
        return None


def account_keys(tx: Mapping[str, Any]) -> list[dict[str, Any]]:
    message = ((tx.get("transaction") or {}).get("message") or {})
    rows = message.get("accountKeys") or []
    out = []
    for row in rows:
        if isinstance(row, Mapping):
            out.append({
                "pubkey": str(row.get("pubkey")),
                "signer": bool(row.get("signer")),
                "writable": bool(row.get("writable")),
                "source": row.get("source"),
            })
        else:
            out.append({"pubkey": str(row), "signer": False, "writable": False, "source": None})
    return out


def all_programs(tx: Mapping[str, Any]) -> set[str]:
    out: set[str] = set()
    for _, ix in dma.all_ix(tx):
        pid = ix.get("programId") if isinstance(ix, Mapping) else None
        if pid:
            out.add(str(pid))
    return out


def top_programs(tx: Mapping[str, Any]) -> list[str]:
    rows = (((tx.get("transaction") or {}).get("message") or {}).get("instructions") or [])
    return [str(ix.get("programId") or "") for ix in rows if isinstance(ix, Mapping)]


def signer_pubkeys(tx: Mapping[str, Any]) -> list[str]:
    return [x["pubkey"] for x in account_keys(tx) if x["signer"]]


def token_balance_map(tx: Mapping[str, Any], field: str) -> dict[tuple[int, str, str], int]:
    meta = tx.get("meta") or {}
    rows = meta.get(field) or []
    out: dict[tuple[int, str, str], int] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        try:
            idx = int(row.get("accountIndex"))
            mint = str(row.get("mint") or "")
            owner = str(row.get("owner") or "")
            amount = int((((row.get("uiTokenAmount") or {}).get("amount"))))
        except Exception:
            continue
        out[(idx, mint, owner)] = amount
    return out


def original_token_deltas(tx: Mapping[str, Any]) -> list[dict[str, Any]]:
    keys = account_keys(tx)
    pre = token_balance_map(tx, "preTokenBalances")
    post = token_balance_map(tx, "postTokenBalances")
    tuples = set(pre) | set(post)
    rows = []
    for idx, mint, owner in sorted(tuples):
        before = pre.get((idx, mint, owner), 0)
        after = post.get((idx, mint, owner), 0)
        rows.append({
            "accountIndex": idx,
            "account": keys[idx]["pubkey"] if 0 <= idx < len(keys) else None,
            "mint": mint,
            "owner": owner,
            "preAtoms": before,
            "postAtoms": after,
            "deltaAtoms": after - before,
        })
    return rows


def candidate_summary(sigrow: Mapping[str, Any], tx: Mapping[str, Any], market: Mapping[str, Any]) -> dict[str, Any] | None:
    if (tx.get("meta") or {}).get("err") is not None:
        return None
    programs = all_programs(tx)
    if not {DFLOW, BISON, PREDICT}.issubset(programs):
        return None
    signers = signer_pubkeys(tx)
    if not signers:
        return None
    user = signers[0]
    deltas = original_token_deltas(tx)
    user_cash = sum(r["deltaAtoms"] for r in deltas if r["owner"] == user and r["mint"] == CASH)
    yes = str(market["yesMint"])
    no = str(market["noMint"])
    user_yes = sum(r["deltaAtoms"] for r in deltas if r["owner"] == user and r["mint"] == yes)
    user_no = sum(r["deltaAtoms"] for r in deltas if r["owner"] == user and r["mint"] == no)
    user_usdc = sum(r["deltaAtoms"] for r in deltas if r["owner"] == user and r["mint"] == USDC)
    outcome = max(user_yes, user_no)
    direct_cash = user_cash < 0 and outcome > 0 and user_usdc == 0

    meta = tx.get("meta") or {}
    keys = account_keys(tx)
    pre_lamports = meta.get("preBalances") or []
    post_lamports = meta.get("postBalances") or []
    signer_idx = next((i for i, x in enumerate(keys) if x["pubkey"] == user), None)
    sol_delta = None
    if signer_idx is not None and signer_idx < len(pre_lamports) and signer_idx < len(post_lamports):
        sol_delta = int(post_lamports[signer_idx]) - int(pre_lamports[signer_idx])

    return {
        "signature": str(sigrow.get("signature")),
        "blockTime": sigrow.get("blockTime"),
        "slot": tx.get("slot"),
        "signers": signers,
        "user": user,
        "programs": sorted(programs),
        "topPrograms": top_programs(tx),
        "originalTokenDeltas": deltas,
        "originalUserCashDeltaAtoms": user_cash,
        "originalUserYesDeltaAtoms": user_yes,
        "originalUserNoDeltaAtoms": user_no,
        "originalUserUsdcDeltaAtoms": user_usdc,
        "originalUserSolDeltaLamports": sol_delta,
        "directCashToOutcome": direct_cash,
    }


def watch_accounts(candidate: Mapping[str, Any], market: Mapping[str, Any]) -> list[dict[str, str]]:
    user = str(candidate["user"])
    relevant = {CASH, str(market["yesMint"]), str(market["noMint"])}
    rows = []
    seen = set()
    for row in candidate.get("originalTokenDeltas") or []:
        if row.get("mint") not in relevant:
            continue
        account = row.get("account")
        if not account or account in seen:
            continue
        # Preserve all CASH accounts to expose fee destinations; outcome accounts
        # are kept when they belonged to the transaction.
        seen.add(account)
        rows.append({"address": account, "mint": str(row.get("mint") or ""), "owner": str(row.get("owner") or "")})
    if user not in seen:
        rows.insert(0, {"address": user, "mint": "SOL", "owner": user})
    return rows[:20]


def account_snapshot(addresses: list[str], deadline: float) -> list[Mapping[str, Any] | None]:
    if not addresses:
        return []
    value = rpc("getMultipleAccounts", [addresses, {"encoding": "base64", "commitment": "confirmed"}], deadline)
    rows = value.get("value") if isinstance(value, Mapping) else None
    return list(rows) if isinstance(rows, list) else [None] * len(addresses)


def token_amount(acc: Mapping[str, Any] | None) -> int | None:
    if not isinstance(acc, Mapping):
        return None
    data = acc.get("data")
    if not isinstance(data, list) or not data:
        return None
    try:
        raw = base64.b64decode(str(data[0]))
    except Exception:
        return None
    if len(raw) < 72:
        return None
    return int.from_bytes(raw[64:72], "little")


def summarize_sim_accounts(watch: list[dict[str, str]], before: list[Mapping[str, Any] | None], after: list[Any] | None) -> dict[str, Any]:
    out = {"accounts": [], "cashDeltaAtoms": 0, "yesNoDeltaAtoms": {}, "solDeltaLamports": None}
    after = after or []
    for i, spec in enumerate(watch):
        b = before[i] if i < len(before) else None
        a = after[i] if i < len(after) and isinstance(after[i], Mapping) else None
        row: dict[str, Any] = {"address": spec["address"], "mint": spec["mint"], "owner": spec["owner"]}
        if spec["mint"] == "SOL":
            bl = int(b.get("lamports", 0)) if isinstance(b, Mapping) else 0
            al = int(a.get("lamports", 0)) if isinstance(a, Mapping) else 0
            row.update({"preLamports": bl, "postLamports": al, "deltaLamports": al - bl})
            out["solDeltaLamports"] = al - bl
        else:
            ba = token_amount(b) or 0
            aa = token_amount(a) or 0
            delta = aa - ba
            row.update({"preAtoms": ba, "postAtoms": aa, "deltaAtoms": delta})
            if spec["mint"] == CASH:
                out["cashDeltaAtoms"] += delta
            else:
                out["yesNoDeltaAtoms"][spec["mint"]] = out["yesNoDeltaAtoms"].get(spec["mint"], 0) + delta
        out["accounts"].append(row)
    return out


def simulate(raw: bytes, watch: list[dict[str, str]], deadline: float) -> dict[str, Any]:
    addresses = [x["address"] for x in watch]
    before = account_snapshot(addresses, deadline)
    params = [
        base64.b64encode(raw).decode(),
        {
            "encoding": "base64",
            "sigVerify": False,
            "replaceRecentBlockhash": True,
            "commitment": "confirmed",
            "innerInstructions": True,
            "accounts": {"encoding": "base64", "addresses": addresses} if addresses else None,
        },
    ]
    if params[1]["accounts"] is None:
        del params[1]["accounts"]
    started = time.time()
    value = rpc("simulateTransaction", params, deadline)
    elapsed = (time.time() - started) * 1000.0
    result = value.get("value") if isinstance(value, Mapping) else None
    if not isinstance(result, Mapping):
        return {"success": False, "elapsedMs": elapsed, "transportResult": value}
    return {
        "success": result.get("err") is None,
        "elapsedMs": elapsed,
        "err": result.get("err"),
        "unitsConsumed": result.get("unitsConsumed"),
        "logs": result.get("logs") or [],
        "returnData": result.get("returnData"),
        "accountEffects": summarize_sim_accounts(watch, before, result.get("accounts")),
    }


def trimmed_v0(raw: bytes, parsed: Mapping[str, Any]) -> tuple[bytes | None, dict[str, Any]]:
    try:
        from solders.message import MessageV0
        from solders.signature import Signature
        from solders.transaction import VersionedTransaction
    except Exception as exc:
        return None, {"supported": False, "reason": f"SOLDERS_IMPORT:{type(exc).__name__}:{exc}"}

    try:
        tx = VersionedTransaction.from_bytes(raw)
        msg = tx.message
    except Exception as exc:
        return None, {"supported": False, "reason": f"TX_DECODE:{type(exc).__name__}:{exc}"}

    if not isinstance(msg, MessageV0):
        return None, {"supported": False, "reason": f"MESSAGE_NOT_V0:{type(msg).__name__}"}

    programs = top_programs(parsed)
    if len(programs) != len(msg.instructions):
        return None, {"supported": False, "reason": f"TOP_IX_COUNT_MISMATCH:{len(programs)}:{len(msg.instructions)}"}

    keep_positions = [i for i, pid in enumerate(programs) if pid in {COMPUTE_BUDGET, DFLOW}]
    dflow_positions = [i for i, pid in enumerate(programs) if pid == DFLOW]
    if not dflow_positions:
        return None, {"supported": False, "reason": "NO_TOP_LEVEL_DFLOW"}

    try:
        message = MessageV0(
            header=msg.header,
            account_keys=list(msg.account_keys),
            recent_blockhash=msg.recent_blockhash,
            instructions=[msg.instructions[i] for i in keep_positions],
            address_table_lookups=list(msg.address_table_lookups),
        )
        signatures = [Signature.default() for _ in range(msg.header.num_required_signatures)]
        tx2 = VersionedTransaction.populate(message, signatures)
        encoded = bytes(tx2)
    except Exception as exc:
        return None, {"supported": False, "reason": f"TRIM_BUILD:{type(exc).__name__}:{exc}"}

    return encoded, {
        "supported": True,
        "keepPositions": keep_positions,
        "dflowPositions": dflow_positions,
        "originalTopPrograms": programs,
        "trimmedInstructionCount": len(keep_positions),
    }


def discover_btc(start: int, deadline: float) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    candidates = jp.describe_candidates(jp.program_markets_for_start(start, deadline), deadline)
    return jp.select_btc(candidates), candidates


def fresh_candidate_rows(market_key: str, start: int, end: int, deadline: float) -> list[Mapping[str, Any]]:
    rows = rpc("getSignaturesForAddress", [market_key, {"limit": 60, "commitment": "confirmed"}], deadline) or []
    out = []
    for row in rows:
        if not isinstance(row, Mapping) or row.get("err") is not None:
            continue
        bt = row.get("blockTime")
        if isinstance(bt, (int, float)) and start <= int(bt) < end:
            out.append(row)
    return out


def run_window(start: int, args: argparse.Namespace, state: dict[str, Any]) -> dict[str, Any]:
    end = start + 300
    deadline = end - 3
    now = time.time()
    if now < start + args.min_window_age:
        time.sleep(max(0.0, start + args.min_window_age - now))

    record: dict[str, Any] = {"startTs": start, "endTs": end, "startedAt": time.time(), "candidates": [], "simulations": []}
    try:
        market, all_markets = discover_btc(start, min(deadline, time.time() + 45))
    except Exception as exc:
        record["error"] = f"DISCOVERY:{type(exc).__name__}:{exc}"
        return record
    record["worldMarket"] = market
    record["slotMarketCount"] = len(all_markets)
    record["worldMarketKey"] = market["market"]

    seen: set[str] = set()
    fallback: list[tuple[dict[str, Any], bytes, Mapping[str, Any]]] = []

    while time.time() < deadline and state["simulationsUsed"] < args.max_simulations:
        try:
            rows = fresh_candidate_rows(str(market["market"]), start, end, min(deadline, time.time() + 20))
        except Exception as exc:
            record.setdefault("pollErrors", []).append(f"{type(exc).__name__}:{exc}")
            rows = []

        for sigrow in rows:
            sig = str(sigrow.get("signature") or "")
            if not sig or sig in seen:
                continue
            seen.add(sig)
            try:
                parsed = parsed_tx(sig, min(deadline, time.time() + 20))
                if not parsed:
                    continue
                summary = candidate_summary(sigrow, parsed, market)
                if not summary:
                    continue
                raw = raw_tx(sig, min(deadline, time.time() + 20))
                if raw is None:
                    continue
            except Exception as exc:
                record.setdefault("candidateErrors", []).append(f"{sig}:{type(exc).__name__}:{exc}")
                continue

            record["candidates"].append(summary)
            item = (summary, raw, parsed)
            if not summary["directCashToOutcome"]:
                fallback.append(item)
                continue

            control = attempt_candidate(item, market, args, state)
            record["simulations"].extend(control)
            if any(x.get("kind") == "TRIMMED_DFLOW" and x.get("result", {}).get("success") for x in control):
                record["completedAt"] = time.time()
                record["verdict"] = "CONTROL_AND_TRIMMED_SUCCESS"
                state["stop"] = True
                return record

            if state["simulationsUsed"] >= args.max_simulations:
                break

        if state["simulationsUsed"] >= args.max_simulations:
            break

        # Near the window end, spend remaining budget on the freshest qualifying
        # route even if it had a pay-in ramp. This can establish a control but
        # cannot establish a direct-CASH final leg.
        if time.time() >= end - args.fallback_seconds and fallback:
            while fallback and state["simulationsUsed"] < args.max_simulations:
                item = fallback.pop(0)
                control = attempt_candidate(item, market, args, state)
                record["simulations"].extend(control)
                if any(x.get("kind") == "TRIMMED_DFLOW" and x.get("result", {}).get("success") for x in control):
                    record["completedAt"] = time.time()
                    record["verdict"] = "CONTROL_AND_TRIMMED_SUCCESS_NON_DIRECT_INPUT"
                    state["stop"] = True
                    return record
                if any(x.get("kind") == "EXACT_CONTROL" and x.get("result", {}).get("success") for x in control):
                    # An exact non-direct control is useful, but do not burn all
                    # remaining budget replaying ramped variants in this window.
                    break

        time.sleep(min(args.poll_seconds, max(0.0, deadline - time.time())))

    record["completedAt"] = time.time()
    return record


def attempt_candidate(item: tuple[dict[str, Any], bytes, Mapping[str, Any]], market: Mapping[str, Any], args: argparse.Namespace, state: dict[str, Any]) -> list[dict[str, Any]]:
    summary, raw, parsed = item
    watch = watch_accounts(summary, market)
    out = []

    if state["simulationsUsed"] >= args.max_simulations:
        return out
    exact = simulate(raw, watch, time.time() + 35)
    state["simulationsUsed"] += 1
    row = {"kind": "EXACT_CONTROL", "signature": summary["signature"], "directCashToOutcome": summary["directCashToOutcome"], "watch": watch, "result": exact}
    out.append(row)

    if not exact.get("success") or state["simulationsUsed"] >= args.max_simulations:
        return out

    state["controlsSucceeded"] += 1
    trimmed, build = trimmed_v0(raw, parsed)
    trimrow: dict[str, Any] = {"kind": "TRIMMED_DFLOW", "signature": summary["signature"], "directCashToOutcome": summary["directCashToOutcome"], "builder": build}
    if trimmed is None:
        trimrow["result"] = {"success": False, "notSimulated": True, "reason": build.get("reason")}
        out.append(trimrow)
        return out

    result = simulate(trimmed, watch, time.time() + 35)
    state["simulationsUsed"] += 1
    trimrow["result"] = result
    out.append(trimrow)
    if result.get("success"):
        state["trimmedSucceeded"] += 1
    return out


def next_eligible_start(now: int, min_remaining: int) -> int:
    start = (now // 300) * 300
    if start + 300 - now < min_remaining:
        start += 300
    return start


def main(args: argparse.Namespace) -> None:
    if args.windows < 1 or args.windows > WINDOW_LIMIT_HARD:
        raise SystemExit(f"windows must be 1..{WINDOW_LIMIT_HARD}")
    if args.max_simulations < 1 or args.max_simulations > SIM_LIMIT_HARD:
        raise SystemExit(f"max_simulations must be 1..{SIM_LIMIT_HARD}")

    started = time.time()
    state = {"simulationsUsed": 0, "controlsSucceeded": 0, "trimmedSucceeded": 0, "stop": False}
    result: dict[str, Any] = {
        "schemaVersion": "WORLD_BTC5M_FINAL_LEG_BOUNDED_VALIDATION_R0",
        "mode": "PUBLIC_READ_ONLY_UNSIGNED_SIMULATION_ONLY",
        "researchScore": 0,
        "startedAt": started,
        "hardLimits": {"windows": args.windows, "maxSimulations": args.max_simulations},
        "windows": [],
    }

    start = next_eligible_start(int(time.time()), args.min_remaining)
    for i in range(args.windows):
        target = start + i * 300
        if time.time() < target:
            time.sleep(target - time.time())
        record = run_window(target, args, state)
        result["windows"].append(record)
        if state["stop"] or state["simulationsUsed"] >= args.max_simulations:
            break

    if state["trimmedSucceeded"] > 0:
        verdict = "BOUNDED_EXECUTION_REACHABILITY_POSITIVE"
    elif state["simulationsUsed"] >= args.max_simulations and state["controlsSucceeded"] == 0:
        verdict = "PARK_OPERATIONAL_ACCESS_BLOCKED"
    elif state["simulationsUsed"] >= args.max_simulations:
        verdict = "PARK_OPERATIONAL_ACCESS_BLOCKED_AFTER_CONTROL"
    elif state["controlsSucceeded"] > 0:
        verdict = "NO_RESULT_CONTROL_ONLY"
    else:
        verdict = "NO_RESULT_INSUFFICIENT_FRESH_CONTROL_TRANSACTIONS"

    result["summary"] = {
        **state,
        "verdict": verdict,
        "windowsObserved": len(result["windows"]),
        "freshCandidateCount": sum(len(x.get("candidates") or []) for x in result["windows"]),
        "directCashCandidateCount": sum(sum(bool(y.get("directCashToOutcome")) for y in x.get("candidates") or []) for x in result["windows"]),
    }
    result["limitations"] = [
        "Historical-success exact replay can fail because state changed or replay-protection material is stale; without a successful control such a failure is NO_RESULT.",
        "A trimmed DFlow success establishes reachability only and does not prove economic profitability.",
        "No signed transaction is created, broadcast, or submitted.",
        "The diagnostic does not change settlement-basis classification.",
    ]
    result["finishedAt"] = time.time()
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result["summary"], sort_keys=True))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--windows", type=int, default=2)
    p.add_argument("--max-simulations", type=int, default=6)
    p.add_argument("--min-window-age", type=int, default=20)
    p.add_argument("--min-remaining", type=int, default=80)
    p.add_argument("--poll-seconds", type=float, default=8.0)
    p.add_argument("--fallback-seconds", type=int, default=45)
    p.add_argument("--output", default="artifacts/world-btc5m-final-leg-bounded-validation-r0.json")
    main(p.parse_args())

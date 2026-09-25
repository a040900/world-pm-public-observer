"""Public read-only World/Polymarket BTC5M two-leg paper bot R1.

The bot observes public radar data, waits the frozen decision latency, obtains a
new exact World quote plus verified Polymarket depth, and, only under an explicit
World fee assumption, records quote/depth-based simulated fills through the
shared event-sourced execution core.

It cannot sign, submit transactions, place real orders, or use capital.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import platform
import sys
import time
from typing import Any, Mapping

import requests

from tools.research import world_pm_edge_lifetime_r2_integrity_r1 as integrity
from tools.research import world_pm_edge_lifetime_low_interference_r3 as r3
from tools.research import world_pm_paper_execution_core_r1 as core
from tools.research import world_pm_paper_orchestration_r2 as orchestration
from tools.research import world_pm_world_session_randomized_r2 as r2s
from tools.research import world_polymarket_btc5m_executable_sync_r23 as r23

SCHEMA_VERSION = "WORLD_PM_TWO_LEG_PAPER_BOT_R1"
WORLD_QUOTE_FEE_CORRECTION_AUTHORITY = "docs/decisions/2026-09-22-world-pm-dflow-quote-fee-semantics-r1.md"
WINDOW_COUNT_MAX = 6
GLOBAL_RESEARCH_WINDOW_BUDGET = 60
GLOBAL_BUDGET_CONSUMED_BEFORE_SMOKE = 42
MAX_CANDIDATES_PER_PAIR_PER_WINDOW = 2
CANDIDATE_COOLDOWN_SECONDS = 2.0
RADAR_POLL_SECONDS = 0.05
PM_SOURCE_FUTURE_TOLERANCE_MS = 250.0
SETTLEMENT_WAIT_MAX_SECONDS = 90.0
SETTLEMENT_POLL_SECONDS = 15.0
WORLD_SETTLEMENT_SIGNATURE_LIMIT = 12
WORLD_SETTLEMENT_RPC_FALLBACK = "https://solana-rpc.publicnode.com"
WORLD_REDEEM_OUTCOME_DISCRIMINATOR = bytes.fromhex("0011a762e91c6b34")
WORLD_BURN_WORTHLESS_DISCRIMINATOR = bytes.fromhex("b080ce016e205a2d")
_BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _finite(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out and abs(out) != float("inf") else None


def _pm_source_age_ms(
    pm_snapshot: Mapping[str, Any],
    *,
    observed_at: float,
) -> float | None:
    source_timestamp_ms = _finite(pm_snapshot.get("sourceTimestampMs"))
    if source_timestamp_ms is None:
        return None
    # /time has whole-second resolution; its inferred offset cannot calibrate
    # the millisecond timestamps carried by PM books.
    return observed_at * 1000.0 - source_timestamp_ms


def _pm_source_fresh(
    source_age_ms: float | None,
    *,
    max_age_ms: float,
) -> bool:
    return bool(
        source_age_ms is not None
        and source_age_ms >= -PM_SOURCE_FUTURE_TOLERANCE_MS
        and source_age_ms <= max_age_ms
    )


def _base58_decode(value: str) -> bytes:
    number = 0
    for char in value:
        index = _BASE58_ALPHABET.find(char)
        if index < 0:
            raise ValueError("INVALID_BASE58")
        number = number * 58 + index
    body = b"" if number == 0 else number.to_bytes((number.bit_length() + 7) // 8, "big")
    leading_zeroes = len(value) - len(value.lstrip("1"))
    return b"\x00" * leading_zeroes + body


def _decode_world_settlement_instruction(
    tx: Mapping[str, Any],
    *,
    market: str,
    yes_mint: str,
    no_mint: str,
) -> dict[str, Any] | None:
    for instruction in r23.wp._all_instructions(tx):
        if instruction.get("programId") != r23.wp.PREDICT_PROGRAM:
            continue
        accounts = [str(value) for value in (instruction.get("accounts") or [])]
        if market not in accounts:
            continue
        raw_data = instruction.get("data")
        if not isinstance(raw_data, str):
            continue
        try:
            decoded = _base58_decode(raw_data)
        except ValueError:
            continue
        discriminator = decoded[:8]
        touched_yes = yes_mint in accounts
        touched_no = no_mint in accounts
        if touched_yes == touched_no:
            continue
        touched_outcome = "Up" if touched_yes else "Down"
        if discriminator == WORLD_REDEEM_OUTCOME_DISCRIMINATOR:
            return {
                "status": "RESOLVED",
                "outcome": touched_outcome,
                "settlementAction": "REDEEM_WINNING_OUTCOME",
                "outcomeMint": yes_mint if touched_yes else no_mint,
            }
        if discriminator == WORLD_BURN_WORTHLESS_DISCRIMINATOR:
            return {
                "status": "RESOLVED",
                "outcome": "Down" if touched_yes else "Up",
                "settlementAction": "BURN_WORTHLESS_OUTCOME",
                "worthlessOutcome": touched_outcome,
                "outcomeMint": yes_mint if touched_yes else no_mint,
            }
    return None


def _solana_rpc_http(rpc_url: str, method: str, params: list[Any], timeout: float = 15.0) -> Any:
    response = requests.post(
        rpc_url,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, Mapping) or payload.get("error") is not None:
        raise RuntimeError(f"SOLANA_RPC_ERROR:{payload.get('error') if isinstance(payload, Mapping) else 'INVALID'}")
    return payload.get("result")


def _solana_get_transactions_batch(rpc_url: str, signatures: list[str], timeout: float = 25.0) -> dict[str, Any]:
    if not signatures:
        return {}
    requests_payload = [
        {
            "jsonrpc": "2.0",
            "id": index + 1,
            "method": "getTransaction",
            "params": [
                signature,
                {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0, "commitment": "confirmed"},
            ],
        }
        for index, signature in enumerate(signatures)
    ]
    response = requests.post(
        rpc_url,
        json=requests_payload,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise RuntimeError("SOLANA_BATCH_RESPONSE_INVALID")
    by_id = {int(row.get("id")): row.get("result") for row in payload if isinstance(row, Mapping) and row.get("error") is None}
    return {signature: by_id.get(index + 1) for index, signature in enumerate(signatures)}


def _fetch_world_settlement(
    market: str,
    yes_mint: str,
    no_mint: str,
    rpc_url: str,
    end_ts: int,
) -> dict[str, Any]:
    endpoints = [rpc_url]
    if WORLD_SETTLEMENT_RPC_FALLBACK not in endpoints:
        endpoints.append(WORLD_SETTLEMENT_RPC_FALLBACK)
    last_error: str | None = None
    for endpoint in endpoints:
        try:
            eligible_by_signature: dict[str, dict[str, Any]] = {}
            for mint in (yes_mint, no_mint):
                rows = _solana_rpc_http(
                    endpoint,
                    "getSignaturesForAddress",
                    [mint, {"limit": WORLD_SETTLEMENT_SIGNATURE_LIMIT, "commitment": "confirmed"}],
                ) or []
                if not isinstance(rows, list):
                    raise RuntimeError("WORLD_SETTLEMENT_SIGNATURES_INVALID")
                for row in rows:
                    if (
                        isinstance(row, Mapping)
                        and int(row.get("blockTime") or 0) >= end_ts
                        and row.get("err") is None
                        and row.get("signature")
                    ):
                        eligible_by_signature[str(row["signature"])] = dict(row)
            eligible = sorted(
                eligible_by_signature.values(),
                key=lambda row: int(row.get("blockTime") or 0),
            )
            signatures = [str(row["signature"]) for row in eligible]
            try:
                transactions = _solana_get_transactions_batch(endpoint, signatures)
            except Exception:
                transactions = {}
                for signature in signatures:
                    transactions[signature] = _solana_rpc_http(
                        endpoint,
                        "getTransaction",
                        [signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0, "commitment": "confirmed"}],
                        timeout=20.0,
                    )
            checked = 0
            for row in eligible:
                signature = str(row["signature"])
                tx = transactions.get(signature)
                if not isinstance(tx, Mapping) or tx.get("meta", {}).get("err") is not None:
                    continue
                checked += 1
                decoded = _decode_world_settlement_instruction(
                    tx,
                    market=market,
                    yes_mint=yes_mint,
                    no_mint=no_mint,
                )
                if decoded is not None:
                    return {
                        **decoded,
                        "signature": signature,
                        "blockTime": int(row.get("blockTime") or 0),
                        "transactionsChecked": checked,
                        "rpcEndpoint": endpoint,
                        "evidenceClass": "PUBLIC_SOLANA_REDEEM_OR_BURN_SETTLEMENT_ACTION",
                    }
            return {
                "status": "UNKNOWN",
                "reason": "WORLD_SETTLEMENT_ACTION_NOT_FOUND",
                "transactionsChecked": checked,
                "rpcEndpoint": endpoint,
            }
        except Exception as exc:
            last_error = f"{type(exc).__name__}:{exc}"
    return {
        "status": "UNKNOWN",
        "reason": "WORLD_SETTLEMENT_RPC_UNAVAILABLE",
        "error": last_error,
        "transactionsChecked": 0,
    }


def _fetch_pm_settlement(start_ts: int) -> dict[str, Any]:
    slug = f"btc-updown-5m-{start_ts}"
    rows = r23.wp._json_request(f"{r23.wp.GAMMA_EVENTS_URL}?slug={slug}")
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], Mapping):
        return {"status": "UNKNOWN", "reason": "PM_SETTLEMENT_EVENT_NOT_UNIQUE"}
    event = rows[0]
    markets = event.get("markets")
    if not isinstance(markets, list) or len(markets) != 1 or not isinstance(markets[0], Mapping):
        return {"status": "UNKNOWN", "reason": "PM_SETTLEMENT_MARKET_NOT_UNIQUE"}
    market = markets[0]
    try:
        prices = [float(value) for value in r23.wp._json_list(market.get("outcomePrices"), "PM_OUTCOME_PRICES")]
    except Exception as exc:
        return {"status": "UNKNOWN", "reason": f"PM_SETTLEMENT_PRICES_INVALID:{type(exc).__name__}"}
    if len(prices) != 2 or market.get("closed") is not True:
        return {"status": "PENDING", "reason": "PM_SETTLEMENT_NOT_FINAL", "outcomePrices": prices}
    if prices[0] == 1.0 and prices[1] == 0.0:
        outcome = "Up"
    elif prices[0] == 0.0 and prices[1] == 1.0:
        outcome = "Down"
    else:
        return {"status": "PENDING", "reason": "PM_SETTLEMENT_PRICES_NOT_BINARY_FINAL", "outcomePrices": prices}
    metadata = event.get("eventMetadata") if isinstance(event.get("eventMetadata"), Mapping) else {}
    return {
        "status": "RESOLVED",
        "outcome": outcome,
        "outcomePrices": prices,
        "finalPrice": metadata.get("finalPrice"),
        "priceToBeat": metadata.get("priceToBeat"),
        "evidenceClass": "PUBLIC_POLYMARKET_GAMMA_FINAL_OUTCOME",
    }


def _paired_settlement(pair: str, world_outcome: str, pm_outcome: str) -> dict[str, Any]:
    if pair == "WORLD_YES+PM_DOWN":
        world_payout = 1 if world_outcome == "Up" else 0
        pm_payout = 1 if pm_outcome == "Down" else 0
    elif pair == "WORLD_NO+PM_UP":
        world_payout = 1 if world_outcome == "Down" else 0
        pm_payout = 1 if pm_outcome == "Up" else 0
    else:
        return {"status": "UNKNOWN", "reason": "PAIR_UNKNOWN"}
    combined = world_payout + pm_payout
    return {
        "status": "RESOLVED",
        "worldPayoutPerShare": world_payout,
        "pmPayoutPerShare": pm_payout,
        "combinedAlignedPayoutPerShare": combined,
        "sameDirection": world_outcome == pm_outcome,
        "harmfulMismatch": combined == 0,
        "beneficialMismatch": combined == 2,
        "settlementEquivalenceAssumed": False,
    }


async def _collect_settlement_observations(report: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    deadline = time.time() + SETTLEMENT_WAIT_MAX_SECONDS
    observations: dict[int, dict[str, Any]] = {}
    while True:
        unresolved = False
        for window in report.get("windows", []):
            start_ts = int(window["startTs"])
            current = observations.get(start_ts) or {}
            world_market = window.get("worldMarket") if isinstance(window.get("worldMarket"), Mapping) else None
            if world_market is None:
                observations[start_ts] = {"status": "UNKNOWN", "reason": "WORLD_MARKET_NOT_DISCOVERED"}
                continue
            world = current.get("world") if isinstance(current.get("world"), Mapping) else None
            pm = current.get("polymarket") if isinstance(current.get("polymarket"), Mapping) else None
            if world is None or world.get("status") != "RESOLVED":
                try:
                    world = await asyncio.to_thread(
                        _fetch_world_settlement,
                        str(world_market["market"]),
                        str(world_market["yesMint"]),
                        str(world_market["noMint"]),
                        args.rpc_url,
                        int(window["endTs"]),
                    )
                except Exception as exc:
                    world = {"status": "UNKNOWN", "reason": f"WORLD_SETTLEMENT_READ_FAILED:{type(exc).__name__}:{exc}"}
            if pm is None or pm.get("status") != "RESOLVED":
                try:
                    pm = await asyncio.to_thread(_fetch_pm_settlement, start_ts)
                except Exception as exc:
                    pm = {"status": "UNKNOWN", "reason": f"PM_SETTLEMENT_READ_FAILED:{type(exc).__name__}:{exc}"}
            row = {"world": world, "polymarket": pm, "settlementEquivalenceAssumed": False}
            if world.get("status") == "RESOLVED" and pm.get("status") == "RESOLVED":
                row["status"] = "RESOLVED"
                row["sameDirection"] = world.get("outcome") == pm.get("outcome")
                for candidate in window.get("candidates", []):
                    if isinstance(candidate, dict):
                        candidate["settlementObservation"] = _paired_settlement(
                            str(candidate.get("pair")),
                            str(world.get("outcome")),
                            str(pm.get("outcome")),
                        )
            else:
                row["status"] = "PENDING_OR_UNKNOWN"
                unresolved = True
            observations[start_ts] = row
            window["settlementObservation"] = row
        if not unresolved or time.time() >= deadline:
            break
        await asyncio.sleep(min(SETTLEMENT_POLL_SECONDS, max(0.0, deadline - time.time())))
    resolved = [row for row in observations.values() if row.get("status") == "RESOLVED"]
    return {
        "status": "COMPLETE" if len(resolved) == len(report.get("windows", [])) else "PARTIAL",
        "windowCount": len(report.get("windows", [])),
        "resolvedWindowCount": len(resolved),
        "sameDirectionCount": sum(row.get("sameDirection") is True for row in resolved),
        "mismatchCount": sum(row.get("sameDirection") is False for row in resolved),
        "settlementEquivalenceAssumed": False,
        "method": "PUBLIC_SOLANA_REDEEM_OR_BURN_ACTION_PLUS_POLYMARKET_GAMMA_FINAL_OUTCOME",
    }


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    temp.replace(path)


def _runtime() -> dict[str, Any]:
    return {
        "pythonVersion": sys.version,
        "platform": platform.platform(),
        "githubRunId": os.environ.get("GITHUB_RUN_ID"),
        "githubSha": os.environ.get("GITHUB_SHA"),
        "mode": "PUBLIC_READ_ONLY_NO_TRADE",
    }


def _source_hashes() -> dict[str, str]:
    paths = (Path(__file__), Path(core.__file__), Path(r2s.__file__), Path(r3.__file__), Path(r23.__file__))
    return {p.as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def _route_schema_ok(world_http: Mapping[str, Any]) -> bool:
    facts = world_http.get("routeFacts")
    if not isinstance(facts, Mapping):
        return False
    missing = facts.get("missing")
    if not isinstance(missing, Mapping) or any(bool(missing.get(k)) for k in ("legs", "marketKey", "venue")):
        return False
    legs = facts.get("legs")
    if not isinstance(legs, list) or len(legs) != 1:
        return False
    mints = legs[0].get("mints") if isinstance(legs[0], Mapping) else None
    if not isinstance(mints, Mapping):
        return False
    return mints.get("inputMintDecimals") is not None and mints.get("outputMintDecimals") is not None


def admission_decision(
    *,
    spec: core.ExecutionSpec,
    quote: Mapping[str, Any],
    world_http: Mapping[str, Any],
    pm_state: Mapping[str, Any] | None,
    verification: Mapping[str, Any],
    costs: Mapping[str, Any],
    evaluated_at: float,
    window_end_ts: int,
) -> dict[str, Any]:
    if quote.get("success") is not True:
        return {"status": "UNKNOWN", "reason": "WORLD_EXACT_QUOTE_INVALID", "paperTrade": False}
    if not _route_schema_ok(world_http):
        return {"status": "UNKNOWN", "reason": "WORLD_ROUTE_OR_DECIMALS_UNKNOWN", "paperTrade": False}
    if verification.get("verified") is not True:
        return {"status": "UNKNOWN", "reason": "PM_FULL_DEPTH_INTEGRITY_UNVERIFIED", "paperTrade": False}
    quote_at = _finite(quote.get("receivedAt"))
    if quote_at is None or (evaluated_at - quote_at) * 1000.0 > spec.quote_ttl_ms:
        return {"status": "UNKNOWN", "reason": "WORLD_EXACT_QUOTE_STALE", "paperTrade": False}
    if quote_at >= window_end_ts or evaluated_at >= window_end_ts:
        return {"status": "UNKNOWN", "reason": "DECISION_AFTER_MARKET_END", "paperTrade": False}
    if not isinstance(pm_state, Mapping):
        return {"status": "UNKNOWN", "reason": "PM_RESPONSE_DEPTH_MISSING", "paperTrade": False}
    pm_effective_at = _finite(pm_state.get("effectiveAt"))
    if pm_effective_at is None or (evaluated_at - pm_effective_at) * 1000.0 > spec.pm_evidence_ttl_ms:
        return {"status": "UNKNOWN", "reason": "PM_RESPONSE_DEPTH_STALE", "paperTrade": False}
    source_age_ms = _pm_source_age_ms(pm_state, observed_at=evaluated_at)
    if not _pm_source_fresh(source_age_ms, max_age_ms=spec.pm_evidence_ttl_ms):
        return {"status": "UNKNOWN", "reason": "PM_RESPONSE_SOURCE_NOT_FRESH", "paperTrade": False}
    protected = costs.get("minOut") or {}
    if protected.get("status") != "COMPLETE":
        return {"status": "UNKNOWN", "reason": protected.get("reason") or "PM_DEPTH_INSUFFICIENT", "paperTrade": False}
    unit_cost = _finite(protected.get("unitCost"))
    total_cost = _finite(protected.get("totalCostUsdEquivalent"))
    target = _finite(protected.get("targetNetShares"))
    world_cost = _finite(protected.get("worldCostUsdEquivalent"))
    if None in (unit_cost, total_cost, target, world_cost):
        return {"status": "UNKNOWN", "reason": "COST_ARITHMETIC_UNKNOWN", "paperTrade": False}
    nominal_edge = target - total_cost
    if world_cost > spec.max_unhedged_world_cost_usd:
        return {"status": "NO_TRADE", "reason": "WORLD_UNHEDGED_COST_LIMIT", "paperTrade": False}
    if total_cost > spec.max_pair_cost_usd:
        return {"status": "NO_TRADE", "reason": "PAIR_COST_LIMIT", "paperTrade": False}
    if unit_cost > spec.max_unit_cost:
        return {"status": "NO_TRADE", "reason": "PROTECTED_UNIT_COST_GATE_FAIL", "paperTrade": False}
    if nominal_edge < spec.min_nominal_edge_usd:
        return {"status": "NO_TRADE", "reason": "NOMINAL_EDGE_GATE_FAIL", "paperTrade": False}
    return {
        "status": "PAPER_TRADE",
        "reason": "PROTECTED_COST_GATE_PASS_ASSUMPTION_DEPENDENT",
        "paperTrade": True,
        "unitCost": unit_cost,
        "nominalConditionalEdgeUsdEquivalent": nominal_edge,
        "settlementEquivalenceAssumed": False,
    }


async def _fresh_pm_second_leg_book(
    *,
    token: str,
    http_timeout_seconds: float,
    target_at: float,
    ttl_ms: float,
    window_end_ts: int,
) -> dict[str, Any]:
    if target_at >= window_end_ts:
        return {"status": "UNKNOWN", "reason": "PM_SECOND_LEG_MARKET_ENDED"}
    wait = target_at - time.time()
    if wait > 0:
        await asyncio.sleep(wait)
    started = time.time()
    if started >= window_end_ts:
        return {"status": "UNKNOWN", "reason": "PM_SECOND_LEG_MARKET_ENDED", "startedAt": started}
    session = requests.Session()
    try:
        try:
            book = await asyncio.to_thread(r23._fetch_pm_rest_book, session, token, http_timeout_seconds)
        except Exception as exc:
            return {
                "status": "UNKNOWN",
                "reason": "PM_SECOND_LEG_REST_REQUEST_FAILED",
                "startedAt": started,
                "error": f"{type(exc).__name__}:{exc}",
            }
    finally:
        session.close()

    received_at = _finite(book.get("receivedAt"))
    if received_at is None or received_at >= window_end_ts:
        return {
            "status": "UNKNOWN", "reason": "PM_SECOND_LEG_MARKET_ENDED_OR_RECEIPT_MISSING",
            "startedAt": started, "book": book,
        }
    source_timestamp_ms = _finite(book.get("timestamp"))
    elapsed_ms = _finite(book.get("elapsedMs"))
    source_age_ms = None
    if received_at is not None and source_timestamp_ms is not None:
        source_age_ms = received_at * 1000.0 - source_timestamp_ms
    source_fresh = _pm_source_fresh(source_age_ms, max_age_ms=ttl_ms)
    request_timely = bool(elapsed_ms is not None and elapsed_ms <= ttl_ms)
    if received_at is None or not source_fresh or not request_timely:
        return {
            "status": "UNKNOWN",
            "reason": "PM_SECOND_LEG_REST_BOOK_NOT_FRESH",
            "startedAt": started,
            "book": book,
            "sourceTimestampMs": source_timestamp_ms,
            "sourceAgeMsAtReceipt": source_age_ms,
            "requestElapsedMs": elapsed_ms,
            "sourceFresh": source_fresh,
            "requestTimely": request_timely,
        }
    return {
        "status": "VERIFIED",
        "reason": "FRESH_REST_SECOND_LEG_OBSERVATION",
        "evidenceClass": "PUBLIC_FRESH_REST_BOOK_NOT_VENUE_FILL",
        "startedAt": started,
        "book": book,
        "sourceTimestampMs": source_timestamp_ms,
        "sourceAgeMsAtReceipt": source_age_ms,
        "requestElapsedMs": elapsed_ms,
        "sourceFresh": True,
        "requestTimely": True,
    }


def _pm_fee_usd_equivalent(walk: Mapping[str, Any]) -> float:
    total = 0.0
    inner = walk.get("walk")
    if not isinstance(inner, Mapping):
        return total
    for level in inner.get("levels") or []:
        if isinstance(level, Mapping):
            total += float(level.get("feeShares") or 0.0) * float(level.get("price") or 0.0)
    return total


async def _evaluate_candidate(
    *,
    candidate: dict[str, Any],
    spec: core.ExecutionSpec,
    ledger: core.EventLedger,
    pm_feed: Any,
    pm_fee_schedule: dict[str, Any],
    cash_decimals: int,
    world_decimals: int,
    http_timeout_seconds: float,
    window_end_ts: int,
    pm_clock_offset_seconds: float,
) -> dict[str, Any]:
    trade_id = candidate["candidateId"]
    if ledger.state["stopNewTrades"]:
        decision = {
            "status": "NO_TRADE",
            "reason": "OPEN_RISK_BLOCKS_NEW_ENTRY",
            "paperTrade": False,
            "stopReasons": list(ledger.state["stopReasons"]),
        }
        ledger.append(core.event(f"{trade_id}:decision", "TRADE_DECISION", tradeId=trade_id, decision=decision))
        return {
            **candidate,
            "decision": decision,
            "paperFillClass": core.PAPER_FILL_CLASS,
            "liveFillabilityClaimed": False,
            "paperExecutionStatus": "NOT_STARTED_OPEN_RISK_BLOCK",
            "finalState": core.invariant_report(ledger.state),
        }

    session = r2s.EvidenceSession(session_id=f"{trade_id}:decision", mode="paper")
    pm_verify_session = requests.Session()
    try:
        anchor = await r3._capture_anchor(
            event=candidate,
            target_at=float(candidate["triggeredAt"]) + spec.decision_latency_ms / 1000.0,
            label="PAPER_DECISION",
            world_session=session,
            pm_feed=pm_feed,
            world_decimals=world_decimals,
            pm_fee_schedule=pm_fee_schedule,
            cash_decimals=cash_decimals,
            http_timeout_seconds=http_timeout_seconds,
            clock_offset_seconds=pm_clock_offset_seconds,
            window_end_ts=window_end_ts,
        )
        verification = await r3._verify_anchor(
            capture=anchor,
            event=candidate,
            pm_session=pm_verify_session,
            pm_feed=pm_feed,
            pm_fee_schedule=pm_fee_schedule,
            http_timeout_seconds=http_timeout_seconds,
            clock_offset_seconds=pm_clock_offset_seconds,
        )
    except Exception as exc:
        decision = {
            "status": "UNKNOWN",
            "reason": "DECISION_CAPTURE_OR_VERIFICATION_EXCEPTION",
            "paperTrade": False,
            "error": f"{type(exc).__name__}:{exc}",
        }
        ledger.append(core.event(
            f"{trade_id}:decision",
            "TRADE_DECISION",
            tradeId=trade_id,
            decision=decision,
        ))
        return {
            **candidate,
            "decision": decision,
            "worldHttp": dict(session.last_http),
            "liveFillabilityClaimed": False,
            "finalState": core.invariant_report(ledger.state),
        }
    finally:
        pm_verify_session.close()
        session.close()

    quote = anchor.get("worldQuote") or {}
    pm_state = anchor.get("responseState") if isinstance(anchor.get("responseState"), Mapping) else None
    world_http = dict(session.last_http)
    expected_shares = _finite(quote.get("outputUiShares"))
    minout_shares = _finite(quote.get("minOutputUiShares"))
    raw_in = _finite(quote.get("inAmount"))
    input_cash = None if raw_in is None else raw_in / (10 ** cash_decimals)
    if expected_shares is None or minout_shares is None or input_cash is None:
        costs = {
            "feeStatus": "INCLUDED_IN_DFLOW_QUOTE_OUTPUT_AFTER_ALL_FEES",
            "worldFeeTreatment": "NO_SEPARATE_CASH_ADDON_OUTPUT_SHARES_ALREADY_AFTER_ALL_FEES",
            "worldFeeBpsAssumption": spec.world_fee_bps_assumption,
            "worldFeeBpsAssumptionApplied": False,
            "expected": {"status": "UNKNOWN", "reason": "WORLD_QUOTE_AMOUNT_INVALID"},
            "minOut": {"status": "UNKNOWN", "reason": "WORLD_QUOTE_AMOUNT_INVALID"},
        }
    else:
        costs = core.candidate_costs(
            world_expected_shares=expected_shares,
            world_minout_shares=minout_shares,
            world_cash=input_cash,
            pm_asks=[] if pm_state is None else list(pm_state.get("asks") or []),
            pm_fee_schedule=pm_fee_schedule,
            world_fee_bps=spec.world_fee_bps_assumption,
        )

    evaluated_at = time.time()
    decision = admission_decision(
        spec=spec,
        quote=quote,
        world_http=world_http,
        pm_state=pm_state,
        verification=verification,
        costs=costs,
        evaluated_at=evaluated_at,
        window_end_ts=window_end_ts,
    )
    ledger.append(core.event(
        f"{trade_id}:decision",
        "TRADE_DECISION",
        tradeId=trade_id,
        decision={
            **decision,
            "worldFeeStatus": costs.get("feeStatus"),
            "cashBasisUsdAssumption": spec.cash_usd_assumption,
            "quoteEvidenceClass": "PUBLIC_EXACT_QUOTE_NOT_VENUE_FILL",
        },
    ))
    result: dict[str, Any] = {
        **candidate,
        "decisionAnchor": anchor,
        "worldHttp": world_http,
        "pmDecisionIntegrity": verification,
        "costs": costs,
        "decision": decision,
        "paperFillClass": core.PAPER_FILL_CLASS,
        "liveFillabilityClaimed": False,
    }
    if decision.get("paperTrade") is not True:
        result["finalState"] = core.invariant_report(ledger.state)
        return result

    protected = costs["minOut"]
    target = float(protected["targetNetShares"])
    world_cost = float(protected["worldCostUsdEquivalent"])
    # DFlow quote outputs are after all fees. Keep the World cash basis equal
    # to inAmount and do not add a separate fee cashflow.
    world_fee = 0.0
    world_order = f"{trade_id}:world"
    pm_order = f"{trade_id}:pm"
    world_evidence = {
        "observedAt": quote.get("receivedAt"),
        "quote": quote,
        "worldHttp": world_http,
        "rule": "USE_QUOTE_MIN_OUT_AFTER_FROZEN_DECISION_LATENCY",
        "legacyWorldFeeBpsAssumption": spec.world_fee_bps_assumption,
        "legacyWorldFeeBpsAssumptionApplied": False,
        "worldFeeTreatment": "NO_SEPARATE_CASH_ADDON_OUTPUT_SHARES_ALREADY_AFTER_ALL_FEES",
        "worldFeeSemanticsAuthority": WORLD_QUOTE_FEE_CORRECTION_AUTHORITY,
        "cashBasisUsdAssumption": spec.cash_usd_assumption,
    }
    world_plan = [{
        "type": "FILL", "eventId": f"{world_order}:fill", "tradeId": trade_id,
        "orderId": world_order, "fillId": f"{world_order}:fill", "venue": "WORLD",
        "grossShares": target, "netShares": target, "cashOutUsd": world_cost,
        "feeUsdEquivalent": world_fee, "evidenceObject": world_evidence,
        "simulationRule": "QUOTE_MINOUT_AFTER_DECISION_LATENCY_NOT_LIVE_FILL",
    }]

    async def pm_plan_factory(hedge_target: float) -> list[dict[str, Any]]:
        # This factory runs after the simulated World fill is recorded.
        second_target_at = time.time() + spec.second_leg_latency_ms / 1000.0
        pm_execution = await _fresh_pm_second_leg_book(
            token=str(candidate["pmToken"]),
            http_timeout_seconds=http_timeout_seconds,
            target_at=second_target_at,
            ttl_ms=spec.pm_evidence_ttl_ms,
            window_end_ts=window_end_ts,
        )
        result["pmSecondLegEvidence"] = pm_execution
        if pm_execution.get("status") != "VERIFIED":
            return [core.event(f"{trade_id}:pm-evidence-unknown", "NOTE", tradeId=trade_id, reason=pm_execution.get("reason"))]
        pm_book = pm_execution["book"]
        pm_walk = r2s._walk_cost(pm_book.get("asks"), hedge_target, pm_fee_schedule)
        result["pmWalk"] = pm_walk
        if pm_walk.get("status") != "COMPLETE":
            result["secondLegSurvived"] = None
            return [core.event(f"{trade_id}:pm-depth-unknown", "NOTE", tradeId=trade_id, reason=pm_walk.get("reason"))]
        pm_cost = float(pm_walk["pmCost"])
        second_total = world_cost + pm_cost
        second_unit_cost = second_total / hedge_target
        second_nominal_edge = hedge_target - second_total
        second_survived = bool(
            second_unit_cost <= spec.max_unit_cost
            and second_nominal_edge >= spec.min_nominal_edge_usd
        )
        result["secondLegEconomics"] = {
            "targetNetShares": hedge_target,
            "worldCostUsdEquivalent": world_cost,
            "pmCostUsdEquivalent": pm_cost,
            "totalCostUsdEquivalent": second_total,
            "unitCost": second_unit_cost,
            "nominalConditionalEdgeUsdEquivalent": second_nominal_edge,
            "conditionalEdgeFraction": 1.0 - second_unit_cost,
            "survivedFrozenSecondLegLatency": second_survived,
            "settlementEquivalenceAssumed": False,
        }
        result["secondLegSurvived"] = second_survived
        inner = pm_walk["walk"]
        pm_fee_equiv = _pm_fee_usd_equivalent(pm_walk)
        pm_evidence = {
            "observedAt": pm_book.get("receivedAt"), "restBook": pm_book,
            "verification": pm_execution, "targetNetShares": hedge_target,
            "feeSchedule": pm_fee_schedule, "walk": pm_walk,
            "rule": "FRESH_REST_FULL_ASK_DEPTH_WALK_AFTER_FROZEN_SECOND_LEG_LATENCY",
        }
        result["pmPaperFill"] = {
            "targetNetShares": hedge_target, "grossShares": inner["grossSharesBought"],
            "netShares": inner["netSharesReceived"], "cashOutUsdEquivalent": pm_walk["pmCost"],
            "feeUsdEquivalentInformational": pm_fee_equiv,
            "sourceObservedAt": pm_book.get("receivedAt"), "simulationOnly": True,
        }
        return [{
            "type": "FILL", "eventId": f"{pm_order}:fill", "tradeId": trade_id,
            "orderId": pm_order, "fillId": f"{pm_order}:fill", "venue": "POLYMARKET",
            "grossShares": float(inner["grossSharesBought"]), "netShares": float(inner["netSharesReceived"]),
            "cashOutUsd": float(pm_walk["pmCost"]), "feeUsdEquivalent": pm_fee_equiv,
            "evidenceObject": pm_evidence,
            "simulationRule": "FRESH_REST_FULL_DEPTH_WALK_AFTER_SECOND_LEG_LATENCY_NOT_LIVE_FILL",
        }]

    flow = await orchestration.execute_two_leg_flow(
        ledger=ledger, spec=spec, trade_id=trade_id,
        world_order_id=world_order, world_target_net_shares=target,
        world_plan=world_plan, pm_order_id=pm_order, pm_plan_factory=pm_plan_factory,
    )
    result["orchestration"] = flow
    result["worldPaperFill"] = {
        "netShares": target, "cashOutUsdEquivalent": world_cost,
        "feeUsdEquivalent": world_fee,
        "feeAccounting": "EMBEDDED_IN_QUOTED_NET_OUTPUT_NOT_ZERO_FEE_CLAIM",
        "sourceObservedAt": quote.get("receivedAt"),
        "simulationOnly": True,
    }
    result["paperExecutionStatus"] = (
        "TWO_LEG_PAPER_COMPLETE"
        if flow["status"] == "TWO_LEG_RECONCILED"
        else "SECOND_LEG_NOT_LOCKED_RISK_REMAINS"
    )
    result["maxUnhedgedHoldExceeded"] = bool(flow.get("deadlineBreached"))
    result["exitRequiredAtHoldLimit"] = bool(flow.get("deadlineBreached") and flow["status"] != "TWO_LEG_RECONCILED")
    result["finalState"] = core.invariant_report(ledger.state)
    return result


async def _run_window(
    *,
    window_index: int,
    start_ts: int,
    args: argparse.Namespace,
    spec: core.ExecutionSpec,
    ledger: core.EventLedger,
    cash_decimals: int,
    pm_clock_offset_seconds: float,
) -> dict[str, Any]:
    end_ts = start_ts + r23.WINDOW_SECONDS
    if time.time() < start_ts:
        await asyncio.sleep(start_ts - time.time())
    pm_market = await asyncio.to_thread(r23.wp.fetch_polymarket_market, start_ts)
    stop = asyncio.Event()
    pm_feed = r2s.r2.RollingTimelineBook([pm_market.up_token, pm_market.down_token])
    pm_task = asyncio.create_task(pm_feed.run(stop))
    try:
        world_market = await asyncio.to_thread(
            r23.radar._discover_world_market,
            start_ts,
            args.rpc_url,
            min(float(end_ts), time.time() + args.world_discovery_timeout_seconds),
        )
    except Exception as exc:
        world_market = None
        world_error = f"{type(exc).__name__}:{exc}"
    else:
        world_error = None

    row: dict[str, Any] = {
        "windowIndex": window_index,
        "startTs": start_ts,
        "endTs": end_ts,
        "worldDiscoveryError": world_error,
        "candidates": [],
        "radarPollCount": 0,
        "radarEpisodeCount": {"WORLD_YES+PM_DOWN": 0, "WORLD_NO+PM_UP": 0},
        "radarFreshnessRejectPollCount": {"WORLD_YES+PM_DOWN": 0, "WORLD_NO+PM_UP": 0},
    }
    if world_market is None:
        while time.time() < end_ts:
            await asyncio.sleep(min(1.0, max(0.0, end_ts - time.time())))
        stop.set()
        pm_task.cancel()
        await asyncio.gather(pm_task, return_exceptions=True)
        return row

    yes_decimals, no_decimals = await asyncio.gather(
        asyncio.to_thread(r2s.r2._retry_token_decimals, world_market.yes_mint, args.rpc_url),
        asyncio.to_thread(r2s.r2._retry_token_decimals, world_market.no_mint, args.rpc_url),
    )
    row["worldMarket"] = {
        "market": world_market.market,
        "yesMint": world_market.yes_mint,
        "noMint": world_market.no_mint,
        "yesDecimals": yes_decimals,
        "noDecimals": no_decimals,
    }
    row["pmMarket"] = {
        "conditionId": pm_market.condition_id,
        "upToken": pm_market.up_token,
        "downToken": pm_market.down_token,
        "feeSchedule": pm_market.fee_schedule,
    }
    dflow = r2s.GapRadar(world_market.yes_mint, world_market.no_mint)
    dflow_task = asyncio.create_task(dflow.run(stop))
    active = {"WORLD_YES+PM_DOWN": False, "WORLD_NO+PM_UP": False}
    last = {"WORLD_YES+PM_DOWN": -float("inf"), "WORLD_NO+PM_UP": -float("inf")}
    sampled = {"WORLD_YES+PM_DOWN": 0, "WORLD_NO+PM_UP": 0}
    sequence = 0
    pairs = (
        ("WORLD_YES+PM_DOWN", "yes", world_market.yes_mint, yes_decimals, pm_market.down_token),
        ("WORLD_NO+PM_UP", "no", world_market.no_mint, no_decimals, pm_market.up_token),
    )
    try:
        while time.time() < end_ts:
            loop_at = time.time()
            row["radarPollCount"] += 1
            world_snapshot = dflow.snapshot(loop_at)
            for pair, side, mint, decimals, token in pairs:
                pm_snapshot = pm_feed.snapshot(token)
                leg = world_snapshot.get(side) or {}
                world_ask = _finite(leg.get("ask"))
                world_age = _finite(leg.get("quoteAgeSeconds"))
                pm_ask = _finite(pm_snapshot.get("bestAsk"))
                pm_source_age_ms = _pm_source_age_ms(pm_snapshot, observed_at=loop_at)
                transport_healthy = bool(
                    pm_snapshot.get("healthy") is True
                    and world_snapshot.get("healthy") is True
                    and world_ask is not None
                    and world_age is not None
                    and world_age <= r23.MAX_WORLD_RADAR_AGE_SECONDS
                    and pm_ask is not None
                )
                source_fresh = _pm_source_fresh(
                    pm_source_age_ms,
                    max_age_ms=spec.pm_evidence_ttl_ms,
                )
                effective = r23._pm_effective_top_cost(pm_ask, pm_market.fee_schedule) if transport_healthy else None
                indicative_below = bool(effective is not None and world_ask is not None and world_ask + effective < 1.0)
                if indicative_below and not source_fresh:
                    row["radarFreshnessRejectPollCount"][pair] += 1
                below = bool(indicative_below and source_fresh)
                if below and not active[pair]:
                    active[pair] = True
                    row["radarEpisodeCount"][pair] += 1
                    if sampled[pair] >= MAX_CANDIDATES_PER_PAIR_PER_WINDOW or loop_at - last[pair] < CANDIDATE_COOLDOWN_SECONDS:
                        continue
                    sampled[pair] += 1
                    last[pair] = loop_at
                    sequence += 1
                    candidate = {
                        "candidateId": f"live:{start_ts}:{pair}:{sequence}",
                        "eventId": f"live:{start_ts}:{pair}:{sequence}",
                        "windowIndex": window_index,
                        "pair": pair,
                        "triggeredAt": loop_at,
                        "worldMint": mint,
                        "pmToken": token,
                        "indicativeSum": world_ask + effective,
                        "worldIndicativeAsk": world_ask,
                        "pmLocalBestAsk": pm_ask,
                        "pmSourceTimestampMs": pm_snapshot.get("sourceTimestampMs"),
                        "pmSourceAgeMs": pm_source_age_ms,
                        "pmReceiptAgeMs": pm_snapshot.get("receiptAgeMs"),
                        "pmSourceFreshnessMaxAgeMs": spec.pm_evidence_ttl_ms,
                        "radarTrigger": {**dict(leg), "healthy": True, "observedAt": loop_at},
                    }
                    evaluated = await _evaluate_candidate(
                        candidate=candidate,
                        spec=spec,
                        ledger=ledger,
                        pm_feed=pm_feed,
                        pm_fee_schedule=pm_market.fee_schedule,
                        cash_decimals=cash_decimals,
                        world_decimals=decimals,
                        http_timeout_seconds=args.http_timeout_seconds,
                        window_end_ts=end_ts,
                        pm_clock_offset_seconds=pm_clock_offset_seconds,
                    )
                    row["candidates"].append(evaluated)
                elif not below:
                    active[pair] = False
            await asyncio.sleep(max(0.0, RADAR_POLL_SECONDS - (time.time() - loop_at)))
    finally:
        stop.set()
        dflow_task.cancel()
        pm_task.cancel()
        await asyncio.gather(dflow_task, pm_task, return_exceptions=True)
        row["pmFeedDiagnostics"] = {
            "frameCount": pm_feed.frame_count,
            "reconnectCount": pm_feed.reconnect_count,
            "errorHistory": pm_feed.error_history,
        }
    return row


def _summary(report: Mapping[str, Any]) -> dict[str, Any]:
    candidates = [c for w in report.get("windows", []) for c in w.get("candidates", [])]
    return {
        "windowsCompleted": len(report.get("windows", [])),
        "candidateCount": len(candidates),
        "paperTradeDecisionCount": sum(c.get("decision", {}).get("paperTrade") is True for c in candidates),
        "twoLegSimulationCompleteCount": sum(c.get("paperExecutionStatus") == "TWO_LEG_PAPER_COMPLETE" for c in candidates),
        "secondLegPositiveCompleteCount": sum(
            c.get("paperExecutionStatus") == "TWO_LEG_PAPER_COMPLETE"
            and c.get("secondLegSurvived") is True
            for c in candidates
        ),
        "unknownDecisionCount": sum(c.get("decision", {}).get("status") == "UNKNOWN" for c in candidates),
        "noTradeDecisionCount": sum(c.get("decision", {}).get("status") == "NO_TRADE" for c in candidates),
        "pmSourceFreshnessRejectedPollCount": sum(
            sum(int(v) for v in (w.get("radarFreshnessRejectPollCount") or {}).values())
            for w in report.get("windows", [])
        ),
        "openRiskAtEnd": bool(report.get("ledgerState", {}).get("stopNewTrades")),
        "liveCandidatePathObserved": bool(candidates),
        "liveTwoLegPathObserved": any(c.get("paperExecutionStatus") == "TWO_LEG_PAPER_COMPLETE" for c in candidates),
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.windows < 1 or args.windows > WINDOW_COUNT_MAX:
        raise ValueError("WINDOW_COUNT_OUT_OF_RANGE")
    spec = core.ExecutionSpec(
        decision_latency_ms=250.0,
        second_leg_latency_ms=250.0,
        quote_ttl_ms=1000.0,
        pm_evidence_ttl_ms=1000.0,
        max_unhedged_hold_ms=2000.0,
        max_unhedged_world_cost_usd=12.0,
        max_pair_cost_usd=50.0,
        max_unit_cost=1.0,
        min_nominal_edge_usd=0.0,
        world_fee_bps_assumption=args.world_fee_bps_assumption,
        cash_usd_assumption=1.0,
        world_cash_size=10.0,
    )
    ledger = core.EventLedger(args.ledger, spec, args.checkpoint)
    report: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "mode": "PUBLIC_READ_ONLY_PAPER_NO_TRADE",
        "noTradeBoundary": {
            "credentials": False,
            "privateKeys": False,
            "signing": False,
            "transactionSubmission": False,
            "orderPlacement": False,
            "capital": False,
        },
        "spec": asdict(spec),
        "cashBasisStatus": "ASSUMPTION_DEPENDENT_1_CASH_EQ_1_USD",
        "worldFeeStatus": "INCLUDED_IN_DFLOW_QUOTE_OUTPUT_AFTER_ALL_FEES",
        "worldFeeTreatment": "NO_SEPARATE_CASH_ADDON_OUTPUT_SHARES_ALREADY_AFTER_ALL_FEES",
        "worldFeeSemanticsAuthority": WORLD_QUOTE_FEE_CORRECTION_AUTHORITY,
        "legacyWorldFeeBpsAssumption": spec.world_fee_bps_assumption,
        "legacyWorldFeeBpsAssumptionApplied": False,
        "runtime": _runtime(),
        "sourceSha256": _source_hashes(),
        "requestedWindows": args.windows,
        "researchBudget": {
            "authority": "EXISTING_WORLD_PM_GLOBAL_60_WINDOW_BUDGET",
            "limit": GLOBAL_RESEARCH_WINDOW_BUDGET,
            "consumedBeforeSmoke": GLOBAL_BUDGET_CONSUMED_BEFORE_SMOKE,
            "plannedAfterSmoke": GLOBAL_BUDGET_CONSUMED_BEFORE_SMOKE + args.windows,
            "fullBudgetCompletionRequiredForThisAcceptance": False,
        },
        "windows": [],
        "runState": "RUNNING",
    }
    _write_json(args.out, report)
    try:
        clock = await asyncio.to_thread(r23._calibrate_pm_clock)
        server_now = _finite(clock.get("serverNowEstimate")) or time.time()
        if args.first_window_start_ts is None:
            first = r23._next_full_window(server_now)
        else:
            first = int(args.first_window_start_ts)
            if first % r23.WINDOW_SECONDS != 0:
                raise ValueError("FIRST_WINDOW_START_NOT_ALIGNED")
            if first < server_now + 15:
                raise ValueError("FIRST_WINDOW_START_NOT_FUTURE_ENOUGH")
        report["clockCalibration"] = clock
        pm_clock_offset_seconds = _finite(clock.get("medianServerMinusLocalSeconds"))
        if pm_clock_offset_seconds is None:
            raise RuntimeError("PM_CLOCK_CALIBRATION_OFFSET_INVALID")
        report["pmSourceFreshnessGate"] = {
            "maxAgeMs": spec.pm_evidence_ttl_ms,
            "futureToleranceMs": PM_SOURCE_FUTURE_TOLERANCE_MS,
            "clockBasis": "LOCAL_RECEIPT_VS_PM_MILLISECOND_SOURCE",
            "calibrationOffsetApplied": False,
            "sourceTimestampField": "Polymarket WSS sourceTimestampMs",
        }
        report["firstWindowStartTs"] = first
        cash_decimals = await asyncio.to_thread(r2s.r2._retry_token_decimals, r23.wp.CASH_MINT, args.rpc_url)
        report["cashDecimals"] = cash_decimals
        for index in range(args.windows):
            row = await _run_window(
                window_index=index + 1,
                start_ts=first + index * r23.WINDOW_SECONDS,
                args=args,
                spec=spec,
                ledger=ledger,
                cash_decimals=cash_decimals,
                pm_clock_offset_seconds=pm_clock_offset_seconds,
            )
            report["windows"].append(row)
            report["ledgerState"] = ledger.state
            report["invariants"] = core.invariant_report(ledger.state)
            report["summary"] = _summary(report)
            checkpoint = args.out.with_name(f"{args.out.stem}.window-{index + 1:02d}{args.out.suffix}")
            _write_json(checkpoint, {"schemaVersion": SCHEMA_VERSION, "window": row, "ledgerState": ledger.state})
            _write_json(args.out, report)
            print(json.dumps({"completedWindow": index + 1, "summary": report["summary"]}, sort_keys=True), flush=True)
        try:
            report["settlementObservationSummary"] = await _collect_settlement_observations(report, args)
        except Exception as exc:
            report["settlementObservationSummary"] = {
                "status": "FAILED_READ_ONLY_OBSERVATION",
                "error": f"{type(exc).__name__}:{exc}",
                "settlementEquivalenceAssumed": False,
            }
        report["summary"] = _summary(report)
        report["runState"] = "COMPLETED"
    except BaseException as exc:
        report["runState"] = "FAILED_OR_INTERRUPTED"
        report["error"] = f"{type(exc).__name__}:{exc}"
        raise
    finally:
        ledger.close()
        report["ledgerState"] = ledger.state
        report["invariants"] = core.invariant_report(ledger.state)
        report["summary"] = _summary(report)
        report["researchBudget"]["consumedAfterRun"] = GLOBAL_BUDGET_CONSUMED_BEFORE_SMOKE + len(report["windows"])
        report["completedAt"] = time.time()
        _write_json(args.out, report)
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--windows", type=int, default=6)
    parser.add_argument("--first-window-start-ts", type=int)
    parser.add_argument(
        "--world-fee-bps-assumption",
        type=float,
        default=None,
        help="Legacy compatibility value; recorded but not applied because DFlow quote outputs are after all fees.",
    )
    parser.add_argument("--rpc-url", default=r23.wp.DEFAULT_SOLANA_RPC)
    parser.add_argument("--http-timeout-seconds", type=float, default=8.0)
    parser.add_argument("--world-discovery-timeout-seconds", type=float, default=45.0)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    asyncio.run(run(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

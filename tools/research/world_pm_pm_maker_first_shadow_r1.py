"""Bounded public-read-only PM-maker-first -> World-taker shadow probe.

This is a counterfactual paper measurement. It NEVER places a Polymarket order.
A hypothetical maker BUY joins the currently displayed best-bid queue. A fill is
eligible only when public last_trade_price evidence shows:
  * a SELL trade strictly below our maker bid, which would have had to cross our
    still-resting higher bid first; or
  * cumulative SELL trade size at our exact bid exceeds the queue that was
    already ahead of us.

After the first eligible partial/full shadow fill, the probe immediately requests
a fresh anonymous World exact quote and measures protected unit cost from
minOutAmount. It does not count maker rebates or liquidity rewards.

NO_TRADE remains binding: no credentials, private keys, signing, transaction
submission, order placement, or capital.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import time
from typing import Any, Mapping

import requests

from tools.research import world_polymarket_btc5m_executable_sync_r23 as r23
from tools.research import world_polymarket_btc5m_executable_sync_r24 as r24
from tools.research import world_pm_world_session_randomized_r2 as r2s
from tools.research import a7_public_trade_size_mapping_r1 as a7
from tools.research import world_pm_condition_execution_tape_r1 as condition_tape

SCHEMA_VERSION = "WORLD_PM_PM_MAKER_FIRST_SHADOW_R1"
WINDOW_SECONDS = 300
POLL_SECONDS = 0.05
MAKER_SIZE_SHARES = 5.0
MAX_SHADOWS_PER_PAIR_PER_WINDOW = 2
WORLD_QUOTE_CASH = r23.PRIMARY_SIZE_CASH
PM_SOURCE_TTL_MS = 1000.0
PM_SOURCE_FUTURE_TOLERANCE_MS = 250.0
WORLD_QUOTE_TTL_MS = 1000.0
NEAR_FILL_QUEUE_FRACTION = 0.25


def _finite(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out and abs(out) != float("inf") else None


def _snapshot_source_age_ms(snapshot: Mapping[str, Any], observed_at: float) -> float | None:
    source_ms = _finite(snapshot.get("sourceTimestampMs"))
    if source_ms is None:
        return None
    return observed_at * 1000.0 - source_ms


def _snapshot_fresh(snapshot: Mapping[str, Any], observed_at: float) -> bool:
    age = _snapshot_source_age_ms(snapshot, observed_at)
    return bool(
        snapshot.get("healthy") is True
        and age is not None
        and -PM_SOURCE_FUTURE_TOLERANCE_MS <= age <= PM_SOURCE_TTL_MS
    )


def _bid_size(snapshot: Mapping[str, Any], price: float) -> float:
    total = 0.0
    for level in snapshot.get("bids") or []:
        p = _finite(level.get("price")) if isinstance(level, Mapping) else None
        size = _finite(level.get("size")) if isinstance(level, Mapping) else None
        if p is not None and size is not None and abs(p - price) <= 1e-12:
            total += max(0.0, size)
    return total


def protected_world_unit_cost(quote: Mapping[str, Any], *, cash: float | None = None) -> float | None:
    if quote.get("success") is not True:
        return None
    shares = _finite(quote.get("minOutputUiShares"))
    request_cash = (
        (_finite(quote.get("requestSizeCash")) or WORLD_QUOTE_CASH)
        if cash is None
        else cash
    )
    if shares is None or shares <= 0 or request_cash <= 0:
        return None
    return request_cash / shares


def combined_protected_unit_cost(maker_price: float, quote: Mapping[str, Any]) -> float | None:
    world = protected_world_unit_cost(quote)
    return None if world is None else maker_price + world


def size_matched_protected_unit_cost(
    maker_price: float,
    maker_fill_shares: float,
    quote: Mapping[str, Any],
) -> float | None:
    request_cash = _finite(quote.get("requestSizeCash"))
    min_out = _finite(quote.get("minOutputUiShares"))
    if (
        quote.get("success") is not True
        or request_cash is None
        or min_out is None
        or maker_fill_shares <= 0
        or min_out + 1e-12 < maker_fill_shares
    ):
        return None
    return maker_price + request_cash / maker_fill_shares


def _world_quote_fresh(quote: Mapping[str, Any], observed_at: float) -> bool:
    received_at = _finite(quote.get("receivedAt"))
    return bool(
        received_at is not None
        and 0.0 <= (observed_at - received_at) * 1000.0 <= WORLD_QUOTE_TTL_MS
    )


def protected_edge_verdict(
    maker_price: float,
    quote: Mapping[str, Any],
    *,
    maker_fill_shares: float | None = None,
    observed_at: float | None = None,
) -> dict[str, Any]:
    if observed_at is not None and not _world_quote_fresh(quote, observed_at):
        return {"status": "UNKNOWN", "unitCost": None, "survived": None, "reason": "WORLD_QUOTE_STALE"}
    unit = (
        combined_protected_unit_cost(maker_price, quote)
        if maker_fill_shares is None
        else size_matched_protected_unit_cost(maker_price, maker_fill_shares, quote)
    )
    if unit is None:
        return {"status": "UNKNOWN", "unitCost": None, "survived": None}
    survived = unit < 1.0
    return {
        "status": "SURVIVED" if survived else "FAILED",
        "unitCost": unit,
        "survived": survived,
    }


def _world_anonymous_quote_for_cash(
    session: requests.Session,
    *,
    output_mint: str,
    cash_decimals: int,
    outcome_decimals: int,
    request_cash: float,
    timeout_seconds: float,
) -> dict[str, Any]:
    amount = max(1, int(round(request_cash * (10**cash_decimals))))
    params = {
        "inputMint": r23.wp.CASH_MINT,
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
        "User-Agent": "prediction-market-relative-value-maker-size-r1/1.0",
    }
    started = time.time()
    response = session.get(r23.WORLD_PROXY_ORDER_URL, params=params, headers=headers, timeout=timeout_seconds)
    received = time.time()
    try:
        payload = response.json()
    except ValueError:
        payload = {"rawText": response.text[:2000]}
    row = {
        "requestStartedAt": started,
        "receivedAt": received,
        "elapsedMs": (received - started) * 1000,
        "httpStatus": response.status_code,
        "requestSizeCash": amount / (10**cash_decimals),
        "userPublicKeySupplied": False,
        "credentialUsed": False,
        "signingPerformed": False,
        "transactionSubmitted": False,
        "success": False,
    }
    if not isinstance(payload, Mapping) or response.status_code != 200:
        return row
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
        "outputUiShares": out_amount / (10**outcome_decimals),
        "minOutputUiShares": min_out / (10**outcome_decimals),
        "platformFee": payload.get("platformFee"),
    })
    return row


def _world_quote_for_target_minout(
    session: requests.Session,
    *,
    output_mint: str,
    cash_decimals: int,
    outcome_decimals: int,
    target_shares: float,
    timeout_seconds: float,
    max_iterations: int = 7,
) -> dict[str, Any]:
    if target_shares <= 0:
        return {"success": False, "error": "INVALID_TARGET_SHARES", "targetShares": target_shares}

    upper = WORLD_QUOTE_CASH
    upper_quote = _world_anonymous_quote_for_cash(
        session,
        output_mint=output_mint,
        cash_decimals=cash_decimals,
        outcome_decimals=outcome_decimals,
        request_cash=upper,
        timeout_seconds=timeout_seconds,
    )
    upper_min = _finite(upper_quote.get("minOutputUiShares"))
    if upper_quote.get("success") is not True or upper_min is None or upper_min < target_shares:
        upper_quote["targetShares"] = target_shares
        upper_quote["targetCovered"] = False
        upper_quote["sizingMethod"] = "BOUNDED_BINARY_SEARCH"
        return upper_quote

    lower = 0.0
    best = upper_quote
    for _ in range(max_iterations):
        mid = (lower + upper) / 2.0
        quote = _world_anonymous_quote_for_cash(
            session,
            output_mint=output_mint,
            cash_decimals=cash_decimals,
            outcome_decimals=outcome_decimals,
            request_cash=mid,
            timeout_seconds=timeout_seconds,
        )
        min_out = _finite(quote.get("minOutputUiShares"))
        if quote.get("success") is True and min_out is not None and min_out >= target_shares:
            upper = _finite(quote.get("requestSizeCash")) or mid
            best = quote
        else:
            lower = mid

    best["targetShares"] = target_shares
    best["targetCovered"] = bool(
        best.get("success") is True
        and (_finite(best.get("minOutputUiShares")) or 0.0) + 1e-12 >= target_shares
    )
    best["sizingMethod"] = "BOUNDED_BINARY_SEARCH"
    best["searchIterations"] = max_iterations
    best["searchLowerCash"] = lower
    best["searchUpperCash"] = upper
    return best


def queue_shadow_classification(
    *,
    maker_bid: float,
    queue_ahead: float,
    maker_size: float,
    trades: list[Mapping[str, Any]],
    queue_timeline: list[Mapping[str, Any]],
    placed_at: float | None = None,
    target_token: str | None = None,
    complement_token: str | None = None,
    queue_ahead_known: bool = False,
) -> dict[str, Any]:
    """Classify only receipt-backed condition-level evidence.

    A7 proved public WS size is aggregate taker size. Therefore plausible/near
    classifications may use displayed-queue diagnostics, but never WS size.
    """
    valid_trades: list[Mapping[str, Any]] = []
    unknown_trade_time_count = 0
    pre_placement_trade_count = 0
    for trade in trades:
        source_ms = _finite(trade.get("sourceTimestampMs") or trade.get("timestamp"))
        if source_ms is None:
            unknown_trade_time_count += 1
            continue
        if placed_at is not None and source_ms < placed_at * 1000.0:
            pre_placement_trade_count += 1
            continue
        valid_trades.append(trade)

    definite = queue_shadow_fill(
        maker_bid=maker_bid,
        queue_ahead=queue_ahead,
        maker_size=maker_size,
        trades=valid_trades,
        target_token=target_token,
        complement_token=complement_token,
        queue_ahead_known=queue_ahead_known,
    )
    observed_sizes: list[float] = []
    for point in sorted(queue_timeline, key=lambda row: float(row.get("observedAt") or 0.0)):
        size = _finite(point.get("bidSize"))
        if size is not None and size >= 0:
            observed_sizes.append(size)

    initial_queue = max(0.0, queue_ahead)
    min_displayed_queue = min(observed_sizes, default=initial_queue)
    plausible_advancement = max(0.0, initial_queue - min_displayed_queue)
    near_threshold = max(maker_size, initial_queue * NEAR_FILL_QUEUE_FRACTION)

    if definite["status"] != "NO_SHADOW_FILL":
        classification = "DEFINITE_FILL"
    elif min_displayed_queue <= 1e-12 and initial_queue > 0:
        classification = "PLAUSIBLE_FILL"
    elif min_displayed_queue < initial_queue and min_displayed_queue <= near_threshold:
        classification = "NEAR_FILL"
    elif unknown_trade_time_count > 0:
        classification = "UNKNOWN_TRADE_TIME"
    else:
        classification = "NO_FILL_EVIDENCE"

    return {
        "classification": classification,
        "definite": definite,
        "samePriceSellVolume": None,
        "belowBidSellObserved": definite.get("strictlyWorseShares", 0.0) > 0,
        "minimumDisplayedQueueShares": min_displayed_queue,
        "remainingFromConfirmedSamePriceSellFlowShares": None,
        "plausibleQueueAdvancementShares": plausible_advancement,
        "plausibleQueueRemainingShares": min_displayed_queue,
        "nearFillThresholdShares": near_threshold,
        "queueTimelinePointCount": len(observed_sizes),
        "unknownRelevantTradeSourceTimeCount": unknown_trade_time_count,
        "prePlacementRelevantTradeCount": pre_placement_trade_count,
        "plausibleInterpretation": (
            "DISPLAYED_QUEUE_DIAGNOSTIC_ONLY;PUBLIC_WS_SIZE_NEVER_COUNTS_AS_PRICE_LEVEL_VOLUME;"
            "CANCELLATION_ALONE_NEVER_COUNTS_AS_FILL"
        ),
    }


def validate_maker_entry_snapshot(
    snapshot: Mapping[str, Any],
    *,
    observed_at: float,
    expected_generation: int,
    world_ask: float,
    maker_size_shares: float,
    world_quote: Mapping[str, Any],
    window_end_ts: float,
) -> dict[str, Any]:
    if observed_at >= window_end_ts:
        return {"eligible": False, "reason": "MARKET_ENDED"}
    if int(snapshot.get("connectionGeneration") or -1) != expected_generation:
        return {"eligible": False, "reason": "PM_GENERATION_CHANGED"}
    if not _snapshot_fresh(snapshot, observed_at):
        return {"eligible": False, "reason": "PM_SOURCE_NOT_FRESH"}
    if not _world_quote_fresh(world_quote, observed_at):
        return {"eligible": False, "reason": "WORLD_SIZE_MATCHED_QUOTE_STALE"}
    maker_bid = _finite(snapshot.get("bestBid"))
    best_ask = _finite(snapshot.get("bestAsk"))
    if maker_bid is None or best_ask is None or maker_bid <= 0 or maker_bid >= best_ask:
        return {"eligible": False, "reason": "PM_BOOK_NOT_MAKER_ELIGIBLE"}
    if maker_bid + world_ask >= 1.0:
        return {"eligible": False, "reason": "INDICATIVE_EDGE_GONE"}
    pre_unit = size_matched_protected_unit_cost(maker_bid, maker_size_shares, world_quote)
    if pre_unit is None:
        return {"eligible": False, "reason": "WORLD_SIZE_MATCHED_QUOTE_INVALID"}
    if pre_unit >= 1.0:
        return {"eligible": False, "reason": "PROTECTED_EDGE_GONE", "preUnitCost": pre_unit}
    return {
        "eligible": True,
        "reason": "FRESH_POST_QUOTE_PM_ENTRY",
        "makerBid": maker_bid,
        "bestAsk": best_ask,
        "preUnitCost": pre_unit,
        "queueAheadShares": _bid_size(snapshot, maker_bid),
        "sourceTimestampMs": snapshot.get("sourceTimestampMs"),
        "connectionGeneration": int(snapshot.get("connectionGeneration") or -1),
    }


def _hydrate_tx_execution_evidence(trade: dict[str, Any]) -> None:
    """Attach one bounded condition-level receipt decode to a public trade."""
    existing = trade.get("executionEvidence")
    if isinstance(existing, Mapping) and existing.get("status") not in {"PENDING"}:
        return
    attempts = int(trade.get("receiptLookupAttempts") or 0)
    if attempts >= 3:
        trade["executionEvidence"] = {
            "status": "UNAVAILABLE",
            "reason": "RECEIPT_RETRY_EXHAUSTED",
            "receiptLookupAttempts": attempts,
        }
        return
    trade["receiptLookupAttempts"] = attempts + 1
    tx_hash = str(trade.get("transactionHash") or trade.get("transaction_hash") or "")
    if not tx_hash:
        trade["executionEvidence"] = {"status": "UNAVAILABLE", "reason": "MISSING_TX_HASH"}
        return
    lookup_started_at = time.time()
    receipt = condition_tape.fetch_receipt_once(tx_hash)
    lookup_completed_at = time.time()
    if receipt is None:
        trade["executionEvidence"] = {
            "status": "PENDING",
            "reason": "RECEIPT_NOT_YET_AVAILABLE",
            "receiptLookupStartedAt": lookup_started_at,
            "receiptLookupCompletedAt": lookup_completed_at,
            "receiptLookupAttempts": attempts + 1,
        }
        return
    evidence = condition_tape.decode_match_groups(receipt)
    evidence["receiptLookupStartedAt"] = lookup_started_at
    evidence["receiptLookupCompletedAt"] = lookup_completed_at
    evidence["receiptLookupAttempts"] = attempts + 1
    trade["executionEvidence"] = evidence


def queue_shadow_fill(
    *,
    maker_bid: float,
    queue_ahead: float,
    maker_size: float,
    trades: list[Mapping[str, Any]],
    target_token: str | None = None,
    complement_token: str | None = None,
    queue_ahead_known: bool = True,
) -> dict[str, Any]:
    """Cumulative lower-bound fill using condition-level execution evidence.

    Public WS price/size is never used as execution quantity or as a pre-filter.
    Partial fills accumulate across transactions. Exact-bid direct volume is
    only usable when queue_ahead is a defensible upper bound.
    """
    target_token = str(target_token or "")
    complement_token = str(complement_token or "")
    strict_worse = 0.0
    same_direct = 0.0
    same_synthetic = 0.0
    seen: set[str] = set()
    unavailable = 0
    ambiguous = 0
    triggers: list[dict[str, Any]] = []
    for trade in trades:
        tx_hash = str(trade.get("transactionHash") or trade.get("transaction_hash") or "").lower()
        if not tx_hash:
            unavailable += 1
            continue
        if tx_hash in seen:
            continue
        seen.add(tx_hash)
        evidence = trade.get("executionEvidence")
        if not isinstance(evidence, Mapping):
            unavailable += 1
            continue
        if evidence.get("status") == "PENDING" or evidence.get("status") == "UNAVAILABLE":
            unavailable += 1
            continue
        inferred = condition_tape.infer_hypothetical_buy_lower_bound(
            evidence,
            target_token=target_token,
            complement_token=complement_token,
            maker_bid=maker_bid,
        )
        if inferred.get("status") != "QUALIFIED":
            ambiguous += 1
            continue
        tx_strict = float(inferred.get("strictlyWorseShares") or 0.0)
        tx_same = float(inferred.get("sameDirectTargetBidShares") or 0.0)
        tx_synth = float(inferred.get("sameSyntheticShares") or 0.0)
        strict_worse += tx_strict
        same_direct += tx_same
        same_synthetic += tx_synth
        if tx_strict > 0 or tx_same > 0 or tx_synth > 0:
            triggers.append({
                "transactionHash": trade.get("transactionHash") or trade.get("transaction_hash"),
                "sourceTimestampMs": trade.get("sourceTimestampMs"),
                "receivedAt": trade.get("receivedAt"),
                "inference": inferred,
            })

    exact_executable = max(0.0, same_direct - max(0.0, queue_ahead)) if queue_ahead_known else 0.0
    lower_bound = min(maker_size, strict_worse + exact_executable)
    if lower_bound > 0:
        status = "FULL_SHADOW_FILL" if lower_bound >= maker_size - 1e-12 else "PARTIAL_SHADOW_FILL"
        rule = "CONDITION_LEVEL_STRICTLY_WORSE_PLUS_EXACT_DIRECT_QUEUE"
    else:
        status = "NO_SHADOW_FILL"
        rule = "NO_QUALIFIED_CONDITION_LEVEL_FILL_LOWER_BOUND"
    return {
        "status": status,
        "fillShares": lower_bound,
        "strictlyWorseShares": strict_worse,
        "sameDirectTargetBidShares": same_direct,
        "sameSyntheticShares": same_synthetic,
        "exactBidExecutableShares": exact_executable,
        "queueAheadKnown": queue_ahead_known,
        "trigger": triggers[-1] if triggers else None,
        "triggers": triggers,
        "rule": rule,
        "executionEvidenceUnavailableCount": unavailable,
        "executionEvidenceAmbiguousCount": ambiguous,
    }



def _execution_evidence_incomplete_at_expiry(final_shadow: Mapping[str, Any]) -> bool:
    definite = final_shadow.get("definite") or {}
    unavailable = int(definite.get("executionEvidenceUnavailableCount") or 0)
    ambiguous = int(definite.get("executionEvidenceAmbiguousCount") or 0)
    unknown_time = int(final_shadow.get("unknownRelevantTradeSourceTimeCount") or 0)
    return unavailable > 0 or ambiguous > 0 or unknown_time > 0


def _refresh_candidate_economics(current: dict[str, Any]) -> None:
    """Aggregate all confirmed fill/hedge legs; never promote a marginal quote to whole-candidate PnL."""
    maker_size = float(current["makerSizeShares"])
    lower = float(current.get("confirmedFillLowerBoundShares") or current.get("cumulativeFillShares") or 0.0)
    covered = float(current.get("hypotheticalHedgeCoveredShares") or 0.0)
    world_cash = 0.0
    all_covered_cost_known = True
    for event in current.get("fillEvents") or []:
        econ = event.get("protectedEconomics") or {}
        quote = event.get("worldQuote") or {}
        if econ.get("survived") is None:
            all_covered_cost_known = False
            continue
        cash = _finite(quote.get("requestSizeCash"))
        if cash is None:
            all_covered_cost_known = False
            continue
        world_cash += cash
    exact_inventory = lower >= maker_size - 1e-12
    fully_hedged = exact_inventory and covered >= maker_size - 1e-12 and all_covered_cost_known
    current["fullyHedged"] = fully_hedged
    current["cumulativeWorldHedgeCash"] = world_cash if covered > 0 else 0.0
    if fully_hedged:
        unit = (float(current["makerBid"]) * maker_size + world_cash) / maker_size
        current["cumulativeProtectedUnitCost"] = unit
        current["protectedEdgeSurvived"] = unit < 1.0
        current["candidateEconomicsStatus"] = "SURVIVED" if unit < 1.0 else "FAILED"
    else:
        current["cumulativeProtectedUnitCost"] = None
        current["protectedEdgeSurvived"] = None
        current["candidateEconomicsStatus"] = "INCOMPLETE_HEDGE_OR_INVENTORY_UNKNOWN"


def apply_shadow_fill_lifecycle(
    current: dict[str, Any],
    *,
    cumulative_fill: float,
    incremental_hedge_covered: float = 0.0,
) -> bool:
    """Persist monotone confirmed fill lower bound plus conservative inventory/exposure bounds."""
    maker_size = float(current["makerSizeShares"])
    previous_fill = float(current.get("confirmedFillLowerBoundShares") or current.get("cumulativeFillShares") or 0.0)
    if cumulative_fill < previous_fill - 1e-12:
        current["inventoryState"] = "INVENTORY_UNKNOWN"
        current["inventoryUnknownReason"] = "CONFIRMED_FILL_LOWER_BOUND_REGRESSED"
        return False
    lower = min(maker_size, cumulative_fill)
    upper = maker_size
    current["confirmedFillLowerBoundShares"] = lower
    current["confirmedFillUpperBoundShares"] = upper
    current["cumulativeFillShares"] = lower  # compatibility alias: lower bound, not exact inventory
    current["hypotheticalHedgeCoveredShares"] = min(
        lower,
        float(current.get("hypotheticalHedgeCoveredShares") or 0.0)
        + max(0.0, incremental_hedge_covered),
    )
    covered = float(current["hypotheticalHedgeCoveredShares"])
    current["residualInventoryLowerBoundShares"] = max(0.0, lower - covered)
    current["residualInventoryUpperBoundShares"] = max(0.0, upper - covered)
    current["residualInventoryShares"] = current["residualInventoryLowerBoundShares"]
    current["remainingOrderLowerBoundShares"] = 0.0 if lower < maker_size - 1e-12 else 0.0
    current["remainingOrderUpperBoundShares"] = max(0.0, maker_size - lower)
    current["remainingOrderShares"] = current["remainingOrderUpperBoundShares"]
    full = lower >= maker_size - 1e-12
    current["inventoryState"] = "FULLY_FILLED_CONFIRMED" if full else (
        "PARTIAL_FILL_LOWER_BOUND_INVENTORY_UNKNOWN" if lower > 0 else "RESTING"
    )
    _refresh_candidate_economics(current)
    return full


class TradeAwareBook(r24.PolymarketTimelineBook):
    def __init__(self, token_ids: list[str]) -> None:
        super().__init__(token_ids)
        self.trades: dict[str, list[dict[str, Any]]] = {token: [] for token in token_ids}

    def trade_count(self, token: str) -> int:
        return len(self.trades.get(token) or [])

    def trades_since(self, token: str, index: int) -> list[dict[str, Any]]:
        return list((self.trades.get(token) or [])[index:])

    def condition_trade_counts(self) -> dict[str, int]:
        return {token: len(rows) for token, rows in self.trades.items()}

    def condition_trades_since(self, indices: Mapping[str, int]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for token, trades in self.trades.items():
            rows.extend(trades[int(indices.get(token, 0)):])
        rows.sort(key=lambda row: (
            int(row.get("sourceTimestampMs") or 0),
            float(row.get("receivedAt") or 0.0),
        ))
        return rows

    def _apply_last_trade(self, message: Mapping[str, Any], received_at: float) -> None:
        token = str(message.get("asset_id") or message.get("tokenId") or "")
        if token not in self.trades:
            return
        price = _finite(message.get("price"))
        size = _finite(message.get("size"))
        side = str(message.get("side") or "").upper()
        if price is None or size is None or size <= 0 or side not in {"BUY", "SELL"}:
            return
        self.trades[token].append(
            {
                "receivedAt": received_at,
                "sourceTimestampMs": r23._to_int(message.get("timestamp")),
                "price": price,
                "size": size,
                "side": side,
                "feeRateBps": message.get("fee_rate_bps") or message.get("feeRateBps"),
                "transactionHash": message.get("transaction_hash") or message.get("transactionHash"),
                "assetId": token,
                "connectionGeneration": self.connection_generation,
                "executionEvidence": None,
            }
        )

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            self.connection_generation += 1
            generation = self.connection_generation
            self._reset_books()
            self.connected = False
            self.current_error = None
            try:
                async with r23.websockets.connect(
                    r23.PM_WS_URL,
                    open_timeout=10,
                    close_timeout=5,
                    ping_interval=None,
                    max_size=r23.MAX_WS_MESSAGE_BYTES,
                    max_queue=r24.WS_INTERNAL_MAX_QUEUE,
                ) as ws:
                    self.connected = True
                    self.last_pong_at = None
                    self.last_ping_sent_at = None
                    self.ping_outstanding = False
                    await ws.send(json.dumps({"assets_ids": sorted(self.token_ids), "type": "market"}, separators=(",", ":")))
                    ping_sent_monotonic = None
                    while not stop.is_set():
                        heartbeat_now = time.monotonic()
                        if (
                            self.ping_outstanding
                            and ping_sent_monotonic is not None
                            and heartbeat_now - ping_sent_monotonic >= r23.APP_PONG_TIMEOUT_SECONDS
                        ):
                            raise RuntimeError("PM_PONG_TIMEOUT")
                        if ping_sent_monotonic is None or heartbeat_now - ping_sent_monotonic >= r23.APP_HEARTBEAT_SECONDS:
                            await ws.send("PING")
                            ping_sent_monotonic = time.monotonic()
                            self.last_ping_sent_at = time.time()
                            self.ping_outstanding = True
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=1)
                        except asyncio.TimeoutError:
                            continue
                        received = time.time()
                        self.last_frame_at = received
                        self.frame_count += 1
                        if raw in {"PONG", "pong"}:
                            self.last_pong_at = received
                            self.ping_outstanding = False
                            continue
                        payload = json.loads(raw)
                        for message in payload if isinstance(payload, list) else [payload]:
                            if not isinstance(message, Mapping):
                                continue
                            event_type = message.get("event_type")
                            if event_type == "book":
                                self._apply_book(message, received)
                            elif event_type == "price_change":
                                self._apply_price_change(message, received)
                            elif event_type == "last_trade_price":
                                self._apply_last_trade(message, received)
            except Exception as exc:
                error = f"GEN{generation}:{type(exc).__name__}:{exc}"
                self.current_error = error
                self.last_disconnect_error = error
                self.error_history.append(error)
                self.connected = False
                self._reset_books()
                if not stop.is_set():
                    self.reconnect_count += 1
                    await asyncio.sleep(r23.RECONNECT_DELAY_SECONDS)
        self.connected = False


async def _run_window(*, start_ts: int, args: argparse.Namespace, cash_decimals: int) -> dict[str, Any]:
    end_ts = start_ts + WINDOW_SECONDS
    if time.time() < start_ts:
        await asyncio.sleep(start_ts - time.time())
    pm_market = await asyncio.to_thread(r23.wp.fetch_polymarket_market, start_ts)
    stop = asyncio.Event()
    feed = TradeAwareBook([pm_market.up_token, pm_market.down_token])
    feed_task = asyncio.create_task(feed.run(stop))
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
        "startTs": start_ts,
        "endTs": end_ts,
        "worldDiscoveryError": world_error,
        "shadowCandidates": [],
        "summary": {},
    }
    if world_market is None:
        stop.set()
        feed_task.cancel()
        await asyncio.gather(feed_task, return_exceptions=True)
        row["summary"] = {"candidateCount": 0, "shadowFillCount": 0, "survivingProtectedEdgeCount": 0}
        return row

    yes_decimals, no_decimals = await asyncio.gather(
        asyncio.to_thread(r2s.r2._retry_token_decimals, world_market.yes_mint, args.rpc_url),
        asyncio.to_thread(r2s.r2._retry_token_decimals, world_market.no_mint, args.rpc_url),
    )
    dflow = r2s.GapRadar(world_market.yes_mint, world_market.no_mint)
    dflow_task = asyncio.create_task(dflow.run(stop))
    world_session = requests.Session()
    receipt_tasks: dict[str, asyncio.Task[Any]] = {}
    tx_evidence_cache: dict[str, dict[str, Any]] = {}
    tx_attempts: dict[str, int] = {}

    async def hydrate_tx_nonblocking(tx_hash: str, representative: dict[str, Any]) -> None:
        cached = tx_evidence_cache.get(tx_hash)
        if isinstance(cached, Mapping) and cached.get("status") == "QUALIFIED":
            evidence = dict(cached)
        else:
            representative["receiptLookupAttempts"] = tx_attempts.get(tx_hash, 0)
            await asyncio.to_thread(_hydrate_tx_execution_evidence, representative)
            evidence = representative.get("executionEvidence")
            tx_attempts[tx_hash] = int(representative.get("receiptLookupAttempts") or tx_attempts.get(tx_hash, 0))
            if isinstance(evidence, Mapping):
                old = tx_evidence_cache.get(tx_hash)
                # Evidence is monotone: transient failure/replay can never downgrade QUALIFIED.
                if not (isinstance(old, Mapping) and old.get("status") == "QUALIFIED" and evidence.get("status") != "QUALIFIED"):
                    tx_evidence_cache[tx_hash] = dict(evidence)
                evidence = tx_evidence_cache.get(tx_hash, dict(evidence))
        for rows in feed.trades.values():
            for row_trade in rows:
                if str(row_trade.get("transactionHash") or "").lower() == tx_hash:
                    row_trade["executionEvidence"] = evidence
                    row_trade["receiptLookupAttempts"] = tx_attempts.get(tx_hash, 0)

    active: dict[str, dict[str, Any] | None] = {"WORLD_YES+PM_DOWN": None, "WORLD_NO+PM_UP": None}
    sampled = {"WORLD_YES+PM_DOWN": 0, "WORLD_NO+PM_UP": 0}
    pairs = (
        ("WORLD_YES+PM_DOWN", "yes", world_market.yes_mint, yes_decimals, pm_market.down_token, pm_market.up_token),
        ("WORLD_NO+PM_UP", "no", world_market.no_mint, no_decimals, pm_market.up_token, pm_market.down_token),
    )
    try:
        while time.time() < end_ts:
            now = time.time()
            world_snapshot = dflow.snapshot(now)
            for pair, side, world_mint, world_decimals, pm_token, complement_token in pairs:
                current = active[pair]
                if current is not None:
                    snapshot = feed.snapshot(pm_token)
                    if int(snapshot.get("connectionGeneration") or -1) != int(current["connectionGeneration"]):
                        current["status"] = "UNKNOWN_FEED_RECONNECT"
                        current["completedAt"] = now
                        row["shadowCandidates"].append(current)
                        active[pair] = None
                        continue
                    if not _snapshot_fresh(snapshot, now):
                        current["queueObservationUnknownCount"] += 1
                        continue

                    displayed = _bid_size(snapshot, float(current["makerBid"]))
                    timeline = current["queueTimeline"]
                    if not timeline or abs(float(timeline[-1]["bidSize"]) - displayed) > 1e-12:
                        timeline.append(
                            {
                                "observedAt": now,
                                "sourceTimestampMs": snapshot.get("sourceTimestampMs"),
                                "bidSize": displayed,
                            }
                        )
                    trades = feed.condition_trades_since(current["conditionTradeIndices"])
                    pending = [
                        trade for trade in trades
                        if trade.get("executionEvidence") is None
                        or (
                            isinstance(trade.get("executionEvidence"), Mapping)
                            and trade["executionEvidence"].get("status") == "PENDING"
                        )
                    ]
                    if pending:
                        launched = 0
                        for trade in pending:
                            tx_hash = str(trade.get("transactionHash") or "").lower()
                            if not tx_hash:
                                continue
                            cached = tx_evidence_cache.get(tx_hash)
                            if isinstance(cached, Mapping) and cached.get("status") == "QUALIFIED":
                                trade["executionEvidence"] = cached
                                continue
                            existing_task = receipt_tasks.get(tx_hash)
                            if existing_task is not None and not existing_task.done():
                                continue
                            if existing_task is not None and existing_task.done():
                                receipt_tasks.pop(tx_hash, None)
                            receipt_tasks[tx_hash] = asyncio.create_task(
                                hydrate_tx_nonblocking(tx_hash, trade)
                            )
                            launched += 1
                            if launched >= 8:
                                break
                    classification = queue_shadow_classification(
                        maker_bid=float(current["makerBid"]),
                        queue_ahead=float(current["queueAheadShares"]),
                        maker_size=float(current["makerSizeShares"]),
                        trades=trades,
                        target_token=str(current["pmToken"]),
                        complement_token=str(current["complementPmToken"]),
                        queue_ahead_known=bool(current.get("queueAheadKnown")),
                        queue_timeline=timeline,
                        placed_at=float(current["placedAt"]),
                    )
                    current["queueShadow"] = classification
                    rank = {"NO_FILL_EVIDENCE": 0, "UNKNOWN_TRADE_TIME": 0, "NEAR_FILL": 1, "PLAUSIBLE_FILL": 2, "DEFINITE_FILL": 3}
                    if rank[classification["classification"]] > rank[current["highestQueueClassification"]]:
                        current["highestQueueClassification"] = classification["classification"]

                    stage = classification["classification"]
                    quote_field = None
                    economics_field = None
                    if stage == "NEAR_FILL" and "nearFillWorldQuote" not in current:
                        quote_field, economics_field = "nearFillWorldQuote", "nearFillProtectedEconomics"
                    elif stage == "PLAUSIBLE_FILL" and "plausibleFillWorldQuote" not in current:
                        quote_field, economics_field = "plausibleFillWorldQuote", "plausibleFillProtectedEconomics"
                    if quote_field is not None:
                        quote = await asyncio.to_thread(
                            _world_quote_for_target_minout,
                            world_session,
                            output_mint=world_mint,
                            cash_decimals=cash_decimals,
                            outcome_decimals=world_decimals,
                            target_shares=float(current["makerSizeShares"]),
                            timeout_seconds=args.http_timeout_seconds,
                        )
                        current[quote_field] = quote
                        verdict_at = time.time()
                        current[economics_field] = protected_edge_verdict(
                            float(current["makerBid"]),
                            quote,
                            maker_fill_shares=float(current["makerSizeShares"]),
                            observed_at=verdict_at,
                        )
                        current[f"{economics_field}ObservedAt"] = verdict_at

                    if stage == "DEFINITE_FILL":
                        cumulative_fill = float(classification["definite"]["fillShares"])
                        previous_fill = float(current.get("cumulativeFillShares") or 0.0)
                        incremental_fill = max(0.0, cumulative_fill - previous_fill)
                        if incremental_fill > 1e-12:
                            fill_observed_at = time.time()
                            quote = await asyncio.to_thread(
                                _world_quote_for_target_minout,
                                world_session,
                                output_mint=world_mint,
                                cash_decimals=cash_decimals,
                                outcome_decimals=world_decimals,
                                target_shares=incremental_fill,
                                timeout_seconds=args.http_timeout_seconds,
                            )
                            verdict_at = time.time()
                            verdict = protected_edge_verdict(
                                float(current["makerBid"]),
                                quote,
                                maker_fill_shares=incremental_fill,
                                observed_at=verdict_at,
                            )
                            current["fillEvents"].append({
                                "observedAt": fill_observed_at,
                                "cumulativeFillShares": cumulative_fill,
                                "incrementalFillShares": incremental_fill,
                                "worldQuote": quote,
                                "protectedEconomics": verdict,
                                "quoteCompletedAt": verdict_at,
                                "trigger": classification["definite"].get("trigger"),
                            })
                            hedge_covered = incremental_fill if verdict.get("survived") is not None else 0.0
                            full_fill = apply_shadow_fill_lifecycle(
                                current,
                                cumulative_fill=cumulative_fill,
                                incremental_hedge_covered=hedge_covered,
                            )
                            current["postFillWorldQuote"] = quote
                            current["postFillProtectedEconomics"] = verdict
                            current["postFillProtectedUnitCost"] = verdict["unitCost"]
                            _refresh_candidate_economics(current)
                        if cumulative_fill >= float(current["makerSizeShares"]) - 1e-12:
                            current["status"] = "FULL_SHADOW_FILL"
                            current["completedAt"] = time.time()
                            row["shadowCandidates"].append(current)
                            active[pair] = None
                        else:
                            current["status"] = "PARTIALLY_FILLED_RESTING_SHADOW"
                    continue

                if sampled[pair] >= MAX_SHADOWS_PER_PAIR_PER_WINDOW:
                    continue
                pm_snapshot = feed.snapshot(pm_token)
                leg = world_snapshot.get(side) or {}
                world_ask = _finite(leg.get("ask"))
                world_age = _finite(leg.get("quoteAgeSeconds"))
                maker_bid = _finite(pm_snapshot.get("bestBid"))
                best_ask = _finite(pm_snapshot.get("bestAsk"))
                if (
                    not _snapshot_fresh(pm_snapshot, now)
                    or world_snapshot.get("healthy") is not True
                    or world_ask is None
                    or world_age is None
                    or world_age > r23.MAX_WORLD_RADAR_AGE_SECONDS
                    or maker_bid is None
                    or best_ask is None
                    or maker_bid <= 0
                    or maker_bid >= best_ask
                    or maker_bid + world_ask >= 1.0
                ):
                    continue
                quote = await asyncio.to_thread(
                    _world_quote_for_target_minout,
                    world_session,
                    output_mint=world_mint,
                    cash_decimals=cash_decimals,
                    outcome_decimals=world_decimals,
                    target_shares=MAKER_SIZE_SHARES,
                    timeout_seconds=args.http_timeout_seconds,
                )
                refreshed_at = time.time()
                refreshed_pm = feed.snapshot(pm_token)
                entry = validate_maker_entry_snapshot(
                    refreshed_pm,
                    observed_at=refreshed_at,
                    expected_generation=int(pm_snapshot.get("connectionGeneration") or -1),
                    world_ask=world_ask,
                    maker_size_shares=MAKER_SIZE_SHARES,
                    world_quote=quote,
                    window_end_ts=end_ts,
                )
                if entry["eligible"] is not True:
                    continue
                maker_bid = float(entry["makerBid"])
                best_ask = float(entry["bestAsk"])
                pre_unit = float(entry["preUnitCost"])
                queue_ahead = float(entry["queueAheadShares"])
                sampled[pair] += 1
                active[pair] = {
                    "candidateId": f"{start_ts}:{pair}:{sampled[pair]}",
                    "pair": pair,
                    "pmToken": pm_token,
                    "worldMint": world_mint,
                    "placedAt": refreshed_at,
                    "makerBid": maker_bid,
                    "bestAskAtPlacement": best_ask,
                    "entrySnapshotReason": entry["reason"],
                    "makerSizeShares": MAKER_SIZE_SHARES,
                    "queueAheadShares": queue_ahead,
                    "queueTimeline": [{
                        "observedAt": refreshed_at,
                        "sourceTimestampMs": entry["sourceTimestampMs"],
                        "bidSize": queue_ahead,
                    }],
                    "queueObservationUnknownCount": 0,
                    "highestQueueClassification": "NO_FILL_EVIDENCE",
                    "conditionTradeIndices": feed.condition_trade_counts(),
                    "complementPmToken": complement_token,
                    "connectionGeneration": int(entry["connectionGeneration"]),
                    "queueAheadKnown": False,
                    "queueAheadUncertaintyReason": "SNAPSHOT_PRECEDES_HYPOTHETICAL_ADMISSION_NO_POST_ADMISSION_UPPER_BOUND",
                    "confirmedFillLowerBoundShares": 0.0,
                    "confirmedFillUpperBoundShares": MAKER_SIZE_SHARES,
                    "cumulativeFillShares": 0.0,
                    "hypotheticalHedgeCoveredShares": 0.0,
                    "residualInventoryLowerBoundShares": 0.0,
                    "residualInventoryUpperBoundShares": MAKER_SIZE_SHARES,
                    "residualInventoryShares": 0.0,
                    "inventoryState": "RESTING",
                    "remainingOrderLowerBoundShares": 0.0,
                    "remainingOrderUpperBoundShares": MAKER_SIZE_SHARES,
                    "remainingOrderShares": MAKER_SIZE_SHARES,
                    "candidateEconomicsStatus": "INCOMPLETE_HEDGE_OR_INVENTORY_UNKNOWN",
                    "fullyHedged": False,
                    "fillEvents": [],
                    "preFillWorldQuote": quote,
                    "preFillProtectedUnitCost": pre_unit,
                    "preFillConditionalEdgeFraction": 1.0 - pre_unit,
                    "makerFeeUsdModeled": 0.0,
                    "makerRebateModeled": False,
                    "liquidityRewardsModeled": False,
                    "paperOnly": True,
                    "orderActuallyPlaced": False,
                    "status": "RESTING_SHADOW",
                }
            await asyncio.sleep(POLL_SECONDS)
    finally:
        for pair, current in active.items():
            if current is not None:
                final_shadow = queue_shadow_classification(
                    maker_bid=float(current["makerBid"]),
                    queue_ahead=float(current["queueAheadShares"]),
                    maker_size=float(current["makerSizeShares"]),
                    trades=feed.condition_trades_since(current["conditionTradeIndices"]),
                    target_token=str(current["pmToken"]),
                    complement_token=str(current["complementPmToken"]),
                    queue_ahead_known=bool(current.get("queueAheadKnown")),
                    queue_timeline=list(current["queueTimeline"]),
                    placed_at=float(current["placedAt"]),
                )
                current["queueShadow"] = final_shadow
                final_lower = float(final_shadow["definite"].get("fillShares") or 0.0)
                prior_lower = float(current.get("confirmedFillLowerBoundShares") or current.get("cumulativeFillShares") or 0.0)
                if final_lower > prior_lower + 1e-12:
                    # Reconcile inventory only. Never invent a historical World hedge at expiry.
                    apply_shadow_fill_lifecycle(current, cumulative_fill=final_lower, incremental_hedge_covered=0.0)
                    current["expiryReconciledAdditionalFillLowerBoundShares"] = final_lower - prior_lower
                lower = float(current.get("confirmedFillLowerBoundShares") or 0.0)
                if _execution_evidence_incomplete_at_expiry(final_shadow):
                    current["executionEvidenceComplete"] = False
                    current["status"] = "UNKNOWN_EXECUTION_EVIDENCE_AT_EXPIRY"
                elif lower >= float(current["makerSizeShares"]) - 1e-12:
                    current["executionEvidenceComplete"] = True
                    current["status"] = "FULL_SHADOW_FILL"
                elif lower > 0:
                    current["executionEvidenceComplete"] = True
                    current["status"] = "PARTIAL_FILL_EXPIRED_WITH_RESIDUAL_ORDER"
                else:
                    current["executionEvidenceComplete"] = True
                    final_class = current.get("highestQueueClassification") or final_shadow["classification"]
                    if final_class == "PLAUSIBLE_FILL":
                        current["status"] = "PLAUSIBLE_ONLY_EXPIRED"
                    elif final_class == "NEAR_FILL":
                        current["status"] = "NEAR_FILL_ONLY_EXPIRED"
                    else:
                        current["status"] = "EXPIRED_UNFILLED_AT_WINDOW_END"
                _refresh_candidate_economics(current)
                current["completedAt"] = time.time()
                row["shadowCandidates"].append(current)
        stop.set()
        dflow_task.cancel()
        feed_task.cancel()
        for task in receipt_tasks.values():
            if not task.done():
                task.cancel()
        await asyncio.gather(dflow_task, feed_task, *receipt_tasks.values(), return_exceptions=True)
        world_session.close()

    candidates = row["shadowCandidates"]
    definite = [c for c in candidates if float(c.get("confirmedFillLowerBoundShares") or c.get("cumulativeFillShares") or 0.0) > 0]
    plausible = [c for c in candidates if c.get("highestQueueClassification") in {"PLAUSIBLE_FILL", "DEFINITE_FILL"}]
    near = [c for c in candidates if c.get("highestQueueClassification") in {"NEAR_FILL", "PLAUSIBLE_FILL", "DEFINITE_FILL"}]
    definite_survived = [c for c in definite if c.get("fullyHedged") is True and c.get("protectedEdgeSurvived") is True]
    definite_unknown = [c for c in definite if c.get("fullyHedged") is not True or c.get("protectedEdgeSurvived") is None]
    plausible_survived = [
        c for c in candidates
        if isinstance(c.get("plausibleFillProtectedEconomics"), Mapping)
        and c["plausibleFillProtectedEconomics"].get("survived") is True
    ]
    near_survived = [
        c for c in candidates
        if isinstance(c.get("nearFillProtectedEconomics"), Mapping)
        and c["nearFillProtectedEconomics"].get("survived") is True
    ]
    row["summary"] = {
        "candidateCount": len(candidates),
        "definiteFillCount": len(definite),
        "plausibleOrDefiniteFillCount": len(plausible),
        "nearOrBetterCount": len(near),
        "definiteSurvivingProtectedEdgeCount": len(definite_survived),
        "definiteProtectedEdgeUnknownCount": len(definite_unknown),
        "plausibleStageSurvivingProtectedEdgeCount": len(plausible_survived),
        "nearStageSurvivingProtectedEdgeCount": len(near_survived),
        "shadowFillCount": len(definite),
        "survivingProtectedEdgeCount": len(definite_survived),
        "noTrade": True,
    }
    return row


async def run(args: argparse.Namespace) -> dict[str, Any]:
    cash_decimals = await asyncio.to_thread(r23._token_decimals, r23.wp.CASH_MINT, args.rpc_url)
    server_now = time.time()
    first_start_ts = args.start_ts if args.start_ts is not None else r23._next_full_window(server_now)
    windows = []
    for index in range(args.windows):
        start_ts = first_start_ts + index * WINDOW_SECONDS
        window = await _run_window(start_ts=start_ts, args=args, cash_decimals=cash_decimals)
        window["windowIndex"] = index + 1
        windows.append(window)
        print(json.dumps({"completedWindow": index + 1, "summary": window["summary"]}, sort_keys=True), flush=True)
    candidates = [c for w in windows for c in w.get("shadowCandidates", [])]
    fills = [c for c in candidates if float(c.get("confirmedFillLowerBoundShares") or c.get("cumulativeFillShares") or 0.0) > 0]
    plausible = [c for c in candidates if c.get("highestQueueClassification") in {"PLAUSIBLE_FILL", "DEFINITE_FILL"}]
    near = [c for c in candidates if c.get("highestQueueClassification") in {"NEAR_FILL", "PLAUSIBLE_FILL", "DEFINITE_FILL"}]
    survived = [c for c in fills if c.get("fullyHedged") is True and c.get("protectedEdgeSurvived") is True]
    definite_unknown = [c for c in fills if c.get("fullyHedged") is not True or c.get("protectedEdgeSurvived") is None]
    plausible_survived = [
        c for c in candidates
        if isinstance(c.get("plausibleFillProtectedEconomics"), Mapping)
        and c["plausibleFillProtectedEconomics"].get("survived") is True
    ]
    near_survived = [
        c for c in candidates
        if isinstance(c.get("nearFillProtectedEconomics"), Mapping)
        and c["nearFillProtectedEconomics"].get("survived") is True
    ]
    result = {
        "schemaVersion": SCHEMA_VERSION,
        "noTradeBoundary": {
            "credentials": False,
            "privateKeys": False,
            "signing": False,
            "transactionSubmission": False,
            "orderPlacement": False,
            "capital": False,
        },
        "makerModel": {
            "side": "POLYMARKET_BUY_MAKER_FIRST",
            "placement": "JOIN_EXISTING_BEST_BID",
            "makerSizeShares": MAKER_SIZE_SHARES,
            "queueRule": "CONDITION_LEVEL_ORDERFILLED_LOWER_BOUND_DIRECT_MINT_MERGE_FAIL_CLOSED",
            "plausibleRule": "DISPLAYED_QUEUE_DECREASE_CAN_ADVANCE_POSITION_BUT_CANCELLATION_ALONE_NEVER_COUNTS_AS_FILL",
            "nearFillQueueFraction": NEAR_FILL_QUEUE_FRACTION,
            "makerRebateModeled": False,
            "liquidityRewardsModeled": False,
        },
        "windows": windows,
        "summary": {
            "windowsCompleted": len(windows),
            "candidateCount": len(candidates),
            "definiteFillCount": len(fills),
            "plausibleOrDefiniteFillCount": len(plausible),
            "nearOrBetterCount": len(near),
            "definiteSurvivingProtectedEdgeCount": len(survived),
            "definiteProtectedEdgeUnknownCount": len(definite_unknown),
            "plausibleStageSurvivingProtectedEdgeCount": len(plausible_survived),
            "nearStageSurvivingProtectedEdgeCount": len(near_survived),
            "shadowFillCount": len(fills),
            "survivingProtectedEdgeCount": len(survived),
            "noTrade": True,
        },
    }
    return result


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser()
    p.add_argument("--rpc-url", default=r23.radar.DEFAULT_RPC)
    p.add_argument("--world-discovery-timeout-seconds", type=float, default=60.0)
    p.add_argument("--http-timeout-seconds", type=float, default=8.0)
    p.add_argument("--start-ts", type=int)
    p.add_argument("--windows", type=int, default=1, choices=range(1, 7))
    p.add_argument("--out", type=Path)
    return p


def main() -> int:
    args = parser().parse_args()
    if args.out is None:
        raise SystemExit("--out required")
    result = asyncio.run(run(args))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result["summary"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

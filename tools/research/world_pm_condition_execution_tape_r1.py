"""Condition-level Polymarket execution tape for maker-shadow research.

Public-read-only. Decodes Polygon OrderFilled logs into match groups and derives
conservative lower-bound fill evidence for a hypothetical resting BUY on one
binary outcome. It normalizes:
- direct SELL target -> BUY target matches,
- BUY complement -> BUY target MINT opportunities,
- SELL target -> SELL complement MERGE opportunities as synthetic bids.

The contract does not enforce off-chain price priority. Therefore the derived
fill is explicitly conditional on the CLOB honoring better-price priority.
Ambiguous batching fails closed.
"""
from __future__ import annotations

from typing import Any, Mapping

import requests

from tools.research import a7_public_trade_size_mapping_r1 as a7

PRICE_TOL = 5e-6


def _finite(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out and abs(out) != float("inf") else None


def _close(a: float | None, b: float | None, tol: float = PRICE_TOL) -> bool:
    return a is not None and b is not None and abs(a - b) <= max(tol, tol * max(abs(a), abs(b), 1.0))


def fetch_receipt_once(tx_hash: str, *, timeout_seconds: float = 2.5) -> dict[str, Any] | None:
    """Bounded public RPC lookup. One pass across configured endpoints."""
    for url in a7.RPC_ENDPOINTS:
        try:
            response = requests.post(
                url,
                json={"jsonrpc": "2.0", "id": 1, "method": "eth_getTransactionReceipt", "params": [tx_hash]},
                timeout=timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("error") is None and isinstance(payload.get("result"), dict):
                return payload["result"]
        except Exception:
            continue
    return None


def decode_match_groups(receipt: Mapping[str, Any]) -> dict[str, Any]:
    """Partition OrderFilled logs into maker logs followed by their taker log.

    In CTFExchangeV2 the maker OrderFilled events are emitted while settling the
    maker array and the taker OrderFilled/OrdersMatched event is emitted at the
    end of that match call. We keep independent pending lists per exchange.
    """
    decoded: list[dict[str, Any]] = []
    for log in receipt.get("logs") or []:
        if not isinstance(log, dict):
            continue
        row = a7._decode_order_filled(log)
        if row is not None:
            decoded.append(row)
    decoded.sort(key=lambda row: int(row.get("logIndex") or 0))

    pending: dict[str, list[dict[str, Any]]] = {}
    groups: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for row in decoded:
        exchange = str(row.get("exchange") or "").lower()
        pending.setdefault(exchange, [])
        if str(row.get("taker") or "").lower() == exchange:
            makers = pending[exchange]
            pending[exchange] = []
            taker_maker = str(row.get("maker") or "").lower()
            if not makers:
                errors.append({"reason": "TAKER_WITHOUT_PENDING_MAKERS", "takerLogIndex": row.get("logIndex")})
                continue
            if any(str(m.get("taker") or "").lower() != taker_maker for m in makers):
                errors.append({
                    "reason": "MAKER_TAKER_ADDRESS_MISMATCH",
                    "takerLogIndex": row.get("logIndex"),
                    "makerLogIndices": [m.get("logIndex") for m in makers],
                })
                continue
            groups.append({
                "exchange": exchange,
                "makers": makers,
                "taker": row,
                "firstLogIndex": min(int(m.get("logIndex") or 0) for m in makers),
                "lastLogIndex": int(row.get("logIndex") or 0),
            })
        else:
            pending[exchange].append(row)

    for exchange, makers in pending.items():
        if makers:
            errors.append({
                "reason": "DANGLING_MAKER_LOGS_WITHOUT_TAKER",
                "exchange": exchange,
                "makerLogIndices": [m.get("logIndex") for m in makers],
            })

    return {
        "status": "AMBIGUOUS" if errors else "QUALIFIED",
        "groups": groups,
        "errors": errors,
        "orderFilledCount": len(decoded),
    }


def _maker_effective_level(
    *,
    taker: Mapping[str, Any],
    maker: Mapping[str, Any],
    target_token: str,
    complement_token: str,
) -> tuple[str, float] | None:
    """Map a maker fill into the taker's effective price axis.

    For incoming SELL target, higher effective bids are better:
      BUY target @ p -> p
      SELL complement @ q -> 1-q (MERGE synthetic bid)

    For incoming BUY complement, lower effective asks are better:
      SELL complement @ q -> q
      BUY target @ p -> 1-p (MINT synthetic ask)
    """
    taker_side = str(taker.get("side") or "").upper()
    taker_token = str(taker.get("tokenId") or "")
    maker_side = str(maker.get("side") or "").upper()
    maker_token = str(maker.get("tokenId") or "")
    price = _finite(maker.get("price"))
    if price is None:
        return None

    if taker_side == "SELL" and taker_token == target_token:
        if maker_side == "BUY" and maker_token == target_token:
            return "DIRECT_TARGET_BID", price
        if maker_side == "SELL" and maker_token == complement_token:
            return "MERGE_SYNTHETIC_TARGET_BID", 1.0 - price
        return None

    if taker_side == "BUY" and taker_token == complement_token:
        if maker_side == "SELL" and maker_token == complement_token:
            return "DIRECT_COMPLEMENT_ASK", price
        if maker_side == "BUY" and maker_token == target_token:
            return "MINT_SYNTHETIC_COMPLEMENT_ASK", 1.0 - price
        return None

    return None


def infer_hypothetical_buy_lower_bound(
    evidence: Mapping[str, Any],
    *,
    target_token: str,
    complement_token: str,
    maker_bid: float,
) -> dict[str, Any]:
    """Infer conservative flow that demonstrably reached a worse effective level.

    strictlyWorseShares is independent of same-price queue position.
    sameDirectTargetBidShares is kept separate and may only be used after a
    post-placement queue-ahead upper bound has been established from the target
    token book. Same-price synthetic liquidity is never used to clear that queue.
    """
    if evidence.get("status") != "QUALIFIED":
        return {
            "status": "AMBIGUOUS",
            "reason": "MATCH_GROUPS_NOT_QUALIFIED",
            "strictlyWorseShares": 0.0,
            "sameDirectTargetBidShares": 0.0,
            "sameSyntheticShares": 0.0,
            "relevantGroupCount": 0,
            "groups": [],
        }

    relevant: list[dict[str, Any]] = []
    strict_worse = 0.0
    same_direct = 0.0
    same_synthetic = 0.0
    target_token = str(target_token)
    complement_token = str(complement_token)
    synthetic_ask = 1.0 - maker_bid

    for group in evidence.get("groups") or []:
        if not isinstance(group, Mapping):
            continue
        taker = group.get("taker") or {}
        taker_side = str(taker.get("side") or "").upper()
        taker_token = str(taker.get("tokenId") or "")
        route = None
        if taker_side == "SELL" and taker_token == target_token:
            route = "TAKER_SELL_TARGET"
        elif taker_side == "BUY" and taker_token == complement_token:
            route = "TAKER_BUY_COMPLEMENT"
        if route is None:
            continue

        group_strict = 0.0
        group_same_direct = 0.0
        group_same_synth = 0.0
        maker_rows: list[dict[str, Any]] = []
        group_ambiguous = False
        for maker in group.get("makers") or []:
            if not isinstance(maker, Mapping):
                group_ambiguous = True
                continue
            mapped = _maker_effective_level(
                taker=taker,
                maker=maker,
                target_token=target_token,
                complement_token=complement_token,
            )
            shares = _finite(maker.get("shares"))
            if mapped is None or shares is None or shares <= 0:
                group_ambiguous = True
                continue
            maker_route, effective = mapped
            relation = "BETTER"
            if route == "TAKER_SELL_TARGET":
                if effective < maker_bid - PRICE_TOL:
                    relation = "STRICTLY_WORSE"
                    group_strict += shares
                elif _close(effective, maker_bid):
                    relation = "SAME"
                    if maker_route == "DIRECT_TARGET_BID":
                        group_same_direct += shares
                    else:
                        group_same_synth += shares
            else:
                if effective > synthetic_ask + PRICE_TOL:
                    relation = "STRICTLY_WORSE"
                    group_strict += shares
                elif _close(effective, synthetic_ask):
                    relation = "SAME"
                    group_same_synth += shares
            maker_rows.append({
                "route": maker_route,
                "effectivePrice": effective,
                "relationToHypothetical": relation,
                "shares": shares,
                "logIndex": maker.get("logIndex"),
                "orderHash": maker.get("orderHash"),
            })

        if group_ambiguous:
            return {
                "status": "AMBIGUOUS",
                "reason": "RELEVANT_GROUP_CONTAINS_UNMAPPABLE_MAKER",
                "strictlyWorseShares": 0.0,
                "sameDirectTargetBidShares": 0.0,
                "sameSyntheticShares": 0.0,
                "relevantGroupCount": len(relevant) + 1,
                "groups": relevant,
            }

        strict_worse += group_strict
        same_direct += group_same_direct
        same_synthetic += group_same_synth
        relevant.append({
            "route": route,
            "takerOrderHash": taker.get("orderHash"),
            "takerSide": taker_side,
            "takerTokenId": taker_token,
            "takerShares": taker.get("shares"),
            "takerPrice": taker.get("price"),
            "strictlyWorseShares": group_strict,
            "sameDirectTargetBidShares": group_same_direct,
            "sameSyntheticShares": group_same_synth,
            "makerLevels": maker_rows,
            "firstLogIndex": group.get("firstLogIndex"),
            "lastLogIndex": group.get("lastLogIndex"),
        })

    return {
        "status": "QUALIFIED",
        "reason": "CONDITION_LEVEL_PRICE_PRIORITY_LOWER_BOUND",
        "strictlyWorseShares": strict_worse,
        "sameDirectTargetBidShares": same_direct,
        "sameSyntheticShares": same_synthetic,
        "relevantGroupCount": len(relevant),
        "groups": relevant,
        "assumption": "OFFCHAIN_CLOB_BETTER_PRICE_PRIORITY",
    }

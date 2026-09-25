"""Deterministic event-sourced execution core for World/Polymarket paper trading.

This module has no network transport and cannot place an order. It is deliberately
shared by deterministic fault tests, historical replay decisions, and the public
read-only live paper adapter.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import threading
import tempfile
import time
from typing import Any, Callable, Iterable, Mapping

from tools.research import world_pm_world_session_randomized_r2 as r2

SCHEMA_VERSION = "WORLD_PM_TWO_LEG_PAPER_EXECUTION_CORE_R1"
PAPER_FILL_CLASS = "QUOTE_DEPTH_SIMULATED_FILL_NOT_VENUE_EXECUTION"


def _finite(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _digest(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class ExecutionSpec:
    decision_latency_ms: float = 250.0
    second_leg_latency_ms: float = 250.0
    quote_ttl_ms: float = 1000.0
    pm_evidence_ttl_ms: float = 1000.0
    max_unhedged_hold_ms: float = 2000.0
    max_unhedged_world_cost_usd: float = 12.0
    max_pair_cost_usd: float = 50.0
    max_unit_cost: float = 1.0
    min_nominal_edge_usd: float = 0.0
    world_fee_bps_assumption: float | None = None
    cash_usd_assumption: float = 1.0
    world_cash_size: float = 10.0

    def __post_init__(self) -> None:
        for key, value in asdict(self).items():
            if key == "world_fee_bps_assumption" and value is None:
                continue
            x = _finite(value)
            if x is None or x < 0:
                raise ValueError(f"INVALID_SPEC_{key}")
        if self.quote_ttl_ms <= 0 or self.pm_evidence_ttl_ms <= 0 or self.max_unhedged_hold_ms <= 0:
            raise ValueError("INVALID_NONPOSITIVE_TTL")
        if self.max_unit_cost <= 0:
            raise ValueError("INVALID_MAX_UNIT_COST")


def initial_state(spec: ExecutionSpec) -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "spec": asdict(spec),
        "orders": {},
        "trades": {},
        "seenEventIds": [],
        "seenFillIds": [],
        "cashOutUsd": 0.0,
        "settledPnlUsd": 0.0,
        "stopNewTrades": False,
        "stopReasons": [],
    }


def _trade(state: dict[str, Any], trade_id: str) -> dict[str, Any]:
    trades = state["trades"]
    if trade_id not in trades:
        trades[trade_id] = {
            "tradeId": trade_id,
            "worldNetShares": 0.0,
            "pmNetShares": 0.0,
            "costUsd": 0.0,
            "worldCostUsd": 0.0,
            "pmCostUsd": 0.0,
            "worldFeeUsd": 0.0,
            "pmFeeUsdEquivalent": 0.0,
            "openOrders": [],
            "decision": None,
            "settlement": None,
            "risk": {
                "episode": 0,
                "unhedgedOpenedAt": None,
                "deadlineAt": None,
                "deadlineBreached": False,
                "everDeadlineBreached": False,
                "breachAt": None,
                "resolutionRequired": False,
                "resolution": None,
                "history": [],
            },
            "events": [],
        }
    return trades[trade_id]


def exposure_shares(trade: Mapping[str, Any]) -> float:
    return abs(float(trade.get("worldNetShares") or 0.0) - float(trade.get("pmNetShares") or 0.0))


def _order_unresolved(order: Mapping[str, Any]) -> bool:
    return bool(
        order.get("reconciliationRequired") is True
        or order.get("status") in {
            "UNKNOWN_PENDING_STATUS", "OPEN", "PARTIAL", "CANCEL_FAILED",
            "FILLED_PENDING_RECONCILIATION", "PARTIAL_PENDING_RECONCILIATION",
        }
    )


def _fill_status_rank(status: Any) -> int:
    return {
        "STATUS_CONFIRMED_PARTIAL": 1,
        "STATUS_CONFIRMED_FILLED": 2,
    }.get(str(status), 0)


def _fill_status_evidence_is_forward(
    existing: Mapping[str, Any] | None,
    candidate: Mapping[str, Any],
) -> bool:
    if not isinstance(existing, Mapping):
        return True

    old_status = str(existing.get("status"))
    new_status = str(candidate.get("status"))
    old_rank = _fill_status_rank(old_status)
    new_rank = _fill_status_rank(new_status)

    old_at = _finite(existing.get("observedAt"))
    new_at = _finite(candidate.get("observedAt"))
    if old_at is not None and new_at is None:
        return False
    if old_at is not None and new_at is not None:
        if new_at < old_at - 1e-9:
            return False
        if new_at > old_at + 1e-9 and new_rank < old_rank:
            return False

    if new_rank < old_rank:
        return False

    old_tuple = (
        _finite(existing.get("expectedCumulativeNetShares")),
        _finite(existing.get("expectedCumulativeCashOutUsd")),
        _finite(existing.get("expectedCumulativeFeeUsdEquivalent")),
    )
    new_tuple = (
        _finite(candidate.get("expectedCumulativeNetShares")),
        _finite(candidate.get("expectedCumulativeCashOutUsd")),
        _finite(candidate.get("expectedCumulativeFeeUsdEquivalent")),
    )
    for old_value, new_value in zip(old_tuple, new_tuple):
        if old_value is not None and new_value is None:
            return False
        if old_value is not None and new_value is not None and new_value < old_value - 1e-9:
            return False
    return True


def _deadline_breach_event_if_due(
    state: Mapping[str, Any],
    trade_id: str,
    now: float,
    *,
    source: str,
) -> dict[str, Any] | None:
    trade = (state.get("trades") or {}).get(trade_id)
    if not isinstance(trade, Mapping):
        return None
    risk = trade.get("risk")
    if not isinstance(risk, Mapping):
        return None
    deadline_at = _finite(risk.get("deadlineAt"))
    if (
        deadline_at is None
        or risk.get("resolutionRequired") is not True
        or risk.get("deadlineBreached") is True
        or now < deadline_at - 1e-9
    ):
        return None
    episode = int(risk.get("episode") or 0)
    return event(
        f"{trade_id}:deadline-breached:{episode}",
        "UNHEDGED_DEADLINE_BREACHED",
        tradeId=trade_id,
        riskEpisode=episode,
        breachAt=deadline_at,
        detectedAt=now,
        source=source,
    )


def _retain_conflicting_fill_knowledge(order: dict[str, Any], candidate: Mapping[str, Any]) -> None:
    existing = order["fillStatusEvidence"]
    fields = (
        "expectedCumulativeNetShares", "expectedCumulativeCashOutUsd",
        "expectedCumulativeFeeUsdEquivalent",
    )
    stronger = _fill_status_rank(candidate["status"]) > _fill_status_rank(existing["status"])
    for key in fields:
        old, new = _finite(existing.get(key)), _finite(candidate.get(key))
        stronger |= new is not None and (old is None or new > old + 1e-9)
    old_at, new_at = _finite(existing.get("observedAt")), _finite(candidate.get("observedAt"))
    if old_at is not None and new_at is not None and new_at < old_at - 1e-9 and not stronger:
        return  # A strictly older, weaker report adds no knowledge.

    # These maxima are knowledge bounds, not an invented authoritative status.
    # A subsequent complete forward status must confirm them before release.
    joined = dict(existing)
    for key in (*fields, "observedAt"):
        known = [value for row in (existing, candidate) if (value := _finite(row.get(key))) is not None]
        joined[key] = max(known) if known else None
    if _fill_status_rank(candidate["status"]) > _fill_status_rank(existing["status"]):
        joined["status"] = candidate["status"]
    order["fillStatusEvidence"] = joined
    order["fillStatusEvidenceConflict"] = True


def _refresh_order_reconciliation(order: dict[str, Any]) -> None:
    target = float(order["targetNetShares"])
    evidence = order.get("fillStatusEvidence")
    if isinstance(evidence, Mapping):
        expected_net = _finite(evidence.get("expectedCumulativeNetShares"))
        expected_cash = _finite(evidence.get("expectedCumulativeCashOutUsd"))
        expected_fee = _finite(evidence.get("expectedCumulativeFeeUsdEquivalent"))
        fields_complete = None not in (expected_net, expected_cash, expected_fee)
        amounts_match = bool(
            fields_complete
            and abs(order["filledNetShares"] - expected_net) <= 1e-9
            and abs(order["filledCashOutUsd"] - expected_cash) <= 1e-9
            and abs(order["filledFeeUsdEquivalent"] - expected_fee) <= 1e-9
        )
        status = evidence.get("status")
        remainder_resolved = bool(order.get("remainingQuantityResolved"))
        order["reconciliationFieldsComplete"] = fields_complete
        order["reconciliationAmountsMatch"] = amounts_match
        order["reconciliationRequired"] = not (
            fields_complete and amounts_match and remainder_resolved
            and not order.get("fillStatusEvidenceConflict")
        )
        if order["reconciliationRequired"]:
            order["status"] = (
                "FILLED_PENDING_RECONCILIATION"
                if status == "STATUS_CONFIRMED_FILLED"
                else "PARTIAL_PENDING_RECONCILIATION"
            )
        else:
            order["status"] = (
                "FILLED_RECONCILED"
                if order["filledNetShares"] >= target - 1e-9
                else "PARTIAL_RECONCILED"
            )
        return

    order["reconciliationFieldsComplete"] = None
    order["reconciliationAmountsMatch"] = None
    order["reconciliationRequired"] = False
    if order["filledNetShares"] >= target - 1e-9:
        order["status"] = "FILLED_RECONCILED"
    elif order["filledNetShares"] > 1e-9:
        order["status"] = "PARTIAL_RECONCILED" if order.get("remainingQuantityResolved") else "PARTIAL"
    else:
        venue_status = order.get("venueStatus")
        mapping = {
            "OPEN": "OPEN",
            "UNKNOWN_PENDING_STATUS": "UNKNOWN_PENDING_STATUS",
            "STATUS_CONFIRMED_UNFILLED": "CONFIRMED_UNFILLED",
            "STATUS_CONFIRMED_REJECTED": "REJECTED",
            "STATUS_CONFIRMED_CANCELLED": "CANCELLED",
            "CANCEL_FAILED": "CANCEL_FAILED",
        }
        order["status"] = mapping.get(venue_status, "OPEN")


def _open_risk_episode(
    risk: dict[str, Any],
    *,
    opened_at: float,
    deadline_at: float,
    source_event_id: str,
) -> None:
    if risk.get("unhedgedOpenedAt") is not None:
        risk.setdefault("history", []).append({
            "episode": int(risk.get("episode") or 0),
            "unhedgedOpenedAt": risk.get("unhedgedOpenedAt"),
            "deadlineAt": risk.get("deadlineAt"),
            "deadlineBreached": bool(risk.get("deadlineBreached")),
            "breachAt": risk.get("breachAt"),
            "resolution": copy.deepcopy(risk.get("resolution")),
        })
    risk["episode"] = int(risk.get("episode") or 0) + 1
    risk["unhedgedOpenedAt"] = opened_at
    risk["deadlineAt"] = deadline_at
    risk["deadlineBreached"] = False
    risk["breachAt"] = None
    risk["resolutionRequired"] = True
    risk["resolution"] = None
    risk["openedByEventId"] = source_event_id


def _recompute_stop(state: dict[str, Any]) -> None:
    reasons: list[str] = []
    for order in state["orders"].values():
        if _order_unresolved(order):
            reasons.append(f"ORDER_{order['status']}:{order['orderId']}")
        if order.get("reconciliationRequired") is True:
            reasons.append(f"ORDER_RECONCILIATION_REQUIRED:{order['orderId']}")
    for trade in state["trades"].values():
        if exposure_shares(trade) > 1e-9:
            reasons.append(f"UNHEDGED_EXPOSURE:{trade['tradeId']}")
        risk = trade.get("risk") or {}
        if risk.get("resolutionRequired") is True:
            reasons.append(f"RISK_RESOLUTION_REQUIRED:{trade['tradeId']}")
    state["stopReasons"] = sorted(set(reasons))
    state["stopNewTrades"] = bool(reasons)


def _apply_event_mutating(state: dict[str, Any], event: Mapping[str, Any]) -> str:
    event_id = str(event.get("eventId") or "")
    if not event_id:
        raise ValueError("EVENT_ID_REQUIRED")
    if event_id in state["seenEventIds"]:
        return "DUPLICATE_EVENT_IGNORED"

    state["seenEventIds"].append(event_id)

    kind = event.get("type")
    trade_id = str(event.get("tradeId") or "")
    if kind == "TRADE_DECISION":
        if not trade_id:
            raise ValueError("TRADE_ID_REQUIRED")
        trade = _trade(state, trade_id)
        trade["decision"] = event.get("decision")
        trade["events"].append(event_id)

    elif kind == "ORDER_INTENT":
        order_id = str(event.get("orderId") or "")
        if not trade_id or not order_id:
            raise ValueError("ORDER_ID_AND_TRADE_ID_REQUIRED")
        if order_id in state["orders"]:
            raise ValueError("ORDER_ID_REUSED")
        purpose = event.get("purpose", "ENTRY")
        if state["stopNewTrades"] and purpose == "ENTRY":
            raise ValueError("NEW_TRADE_BLOCKED_BY_OPEN_RISK")
        if purpose in {"HEDGE", "EXIT"}:
            for existing in state["orders"].values():
                if (
                    existing.get("tradeId") == trade_id
                    and existing.get("purpose") in {"HEDGE", "EXIT"}
                    and _order_unresolved(existing)
                ):
                    raise ValueError("RISK_REDUCING_ORDER_BLOCKED_BY_UNRESOLVED_PRIOR_ORDER")
        order = {
            "orderId": order_id,
            "tradeId": trade_id,
            "venue": event.get("venue"),
            "purpose": purpose,
            "targetNetShares": float(event.get("targetNetShares") or 0.0),
            "status": "OPEN",
            "venueStatus": "OPEN",
            "resendAllowed": False,
            "filledNetShares": 0.0,
            "filledCashOutUsd": 0.0,
            "filledFeeUsdEquivalent": 0.0,
            "reconciliationRequired": False,
            "reconciliationFieldsComplete": None,
            "reconciliationAmountsMatch": None,
            "remainingQuantityResolved": False,
            "fillStatusEvidence": None,
            "fillStatusEvidenceConflict": False,
        }
        state["orders"][order_id] = order
        trade = _trade(state, trade_id)
        trade["openOrders"].append(order_id)
        trade["events"].append(event_id)

    elif kind == "ORDER_TIMEOUT":
        order = state["orders"][str(event["orderId"])]
        order["venueStatus"] = "UNKNOWN_PENDING_STATUS"
        order["resendAllowed"] = False
        _refresh_order_reconciliation(order)

    elif kind == "ORDER_STATUS":
        order = state["orders"][str(event["orderId"])]
        status = str(event.get("status"))
        if status not in {
            "STATUS_CONFIRMED_FILLED", "STATUS_CONFIRMED_PARTIAL",
            "STATUS_CONFIRMED_UNFILLED", "STATUS_CONFIRMED_REJECTED",
            "STATUS_CONFIRMED_CANCELLED",
        }:
            raise ValueError("UNKNOWN_ORDER_STATUS")

        if status in {"STATUS_CONFIRMED_FILLED", "STATUS_CONFIRMED_PARTIAL"}:
            candidate_evidence = {
                "status": status,
                "observedAt": event.get("observedAt"),
                "expectedCumulativeNetShares": event.get("expectedCumulativeNetShares"),
                "expectedCumulativeCashOutUsd": event.get("expectedCumulativeCashOutUsd"),
                "expectedCumulativeFeeUsdEquivalent": event.get("expectedCumulativeFeeUsdEquivalent"),
            }
            if _fill_status_evidence_is_forward(order.get("fillStatusEvidence"), candidate_evidence):
                order["fillStatusEvidence"] = candidate_evidence
                order["fillStatusEvidenceConflict"] = False
                order["venueStatus"] = status
                if status == "STATUS_CONFIRMED_FILLED":
                    order["remainingQuantityResolved"] = True
            else:
                _retain_conflicting_fill_knowledge(order, candidate_evidence)
                order.setdefault("ignoredStatusEvidence", []).append({
                    "eventId": event_id,
                    **candidate_evidence,
                    "reason": (
                        "CONFLICTING_CUMULATIVE_KNOWLEDGE_REQUIRES_CONFIRMATION"
                        if order.get("fillStatusEvidenceConflict")
                        else "STALE_WEAKER_FILL_STATUS"
                    ),
                })
            if order["fillStatusEvidence"]["status"] == "STATUS_CONFIRMED_FILLED":
                order["remainingQuantityResolved"] = True
        else:
            order["venueStatus"] = status
            if status in {"STATUS_CONFIRMED_CANCELLED", "STATUS_CONFIRMED_UNFILLED"}:
                order["remainingQuantityResolved"] = True

        _refresh_order_reconciliation(order)
        order["resendAllowed"] = bool(
            status == "STATUS_CONFIRMED_UNFILLED"
            and order["filledNetShares"] <= 1e-9
            and order.get("reconciliationRequired") is not True
        )

    elif kind == "CANCEL_FAILED":
        order = state["orders"][str(event["orderId"])]
        order["venueStatus"] = "CANCEL_FAILED"
        order["resendAllowed"] = False
        _refresh_order_reconciliation(order)
        order["status"] = "CANCEL_FAILED"

    elif kind == "FILL":
        fill_id = str(event.get("fillId") or "")
        order_id = str(event.get("orderId") or "")
        if not fill_id or not order_id:
            raise ValueError("FILL_ID_AND_ORDER_ID_REQUIRED")
        if fill_id in state["seenFillIds"]:
            _recompute_stop(state)
            return "DUPLICATE_FILL_IGNORED"
        order = state["orders"][order_id]
        if order["tradeId"] != trade_id:
            raise ValueError("FILL_TRADE_ORDER_MISMATCH")
        net = _finite(event.get("netShares"))
        gross = _finite(event.get("grossShares"))
        cash = _finite(event.get("cashOutUsd"))
        fee = _finite(event.get("feeUsdEquivalent"))
        if None in (net, gross, cash, fee) or net < 0 or gross < net or cash < 0 or fee < 0:
            raise ValueError("INVALID_FILL_ARITHMETIC")
        evidence = event.get("evidence")
        if not isinstance(evidence, Mapping) or not evidence.get("sourceSha256"):
            raise ValueError("FILL_EVIDENCE_REQUIRED")
        if evidence.get("evidenceClass") != PAPER_FILL_CLASS:
            raise ValueError("NON_PAPER_FILL_CLASS_REJECTED")
        state["seenFillIds"].append(fill_id)
        order["filledNetShares"] += net
        order["filledCashOutUsd"] += cash
        order["filledFeeUsdEquivalent"] += fee
        if order["filledNetShares"] >= float(order["targetNetShares"]) - 1e-9:
            order["remainingQuantityResolved"] = True
        _refresh_order_reconciliation(order)
        trade = _trade(state, trade_id)
        exposure_before = exposure_shares(trade)
        if order["venue"] == "WORLD":
            trade["worldNetShares"] += net
            trade["worldCostUsd"] += cash
            trade["worldFeeUsd"] += fee
        elif order["venue"] == "POLYMARKET":
            trade["pmNetShares"] += net
            trade["pmCostUsd"] += cash
            trade["pmFeeUsdEquivalent"] += fee
        else:
            raise ValueError("UNKNOWN_VENUE")
        trade["costUsd"] += cash
        state["cashOutUsd"] += cash
        trade["events"].append(event_id)

        exposure_after = exposure_shares(trade)
        risk = trade["risk"]
        if (
            exposure_before <= 1e-9
            and exposure_after > 1e-9
            and risk.get("resolutionRequired") is not True
            and risk.get("resolution") is not None
        ):
            processed_at = _finite(event.get("processedAt"))
            if processed_at is None:
                raise ValueError("FILL_PROCESSED_AT_REQUIRED_FOR_RISK_REOPEN")
            hold_seconds = float(state["spec"]["max_unhedged_hold_ms"]) / 1000.0
            _open_risk_episode(
                risk,
                opened_at=processed_at,
                deadline_at=processed_at + hold_seconds,
                source_event_id=event_id,
            )

    elif kind == "UNHEDGED_RISK_OPENED":
        trade = _trade(state, trade_id)
        opened_at = _finite(event.get("openedAt"))
        deadline_at = _finite(event.get("deadlineAt"))
        if opened_at is None or deadline_at is None or deadline_at <= opened_at:
            raise ValueError("INVALID_UNHEDGED_DEADLINE")
        risk = trade["risk"]
        _open_risk_episode(
            risk,
            opened_at=opened_at,
            deadline_at=deadline_at,
            source_event_id=event_id,
        )
        trade["events"].append(event_id)

    elif kind == "UNHEDGED_DEADLINE_BREACHED":
        trade = _trade(state, trade_id)
        risk = trade["risk"]
        if risk.get("deadlineAt") is None:
            raise ValueError("DEADLINE_BREACH_WITHOUT_OPEN_RISK")
        breach_at = _finite(event.get("breachAt"))
        if breach_at is None:
            raise ValueError("INVALID_BREACH_AT")
        risk["deadlineBreached"] = True
        risk["everDeadlineBreached"] = True
        risk["breachAt"] = breach_at
        risk["resolutionRequired"] = True
        trade["events"].append(event_id)

    elif kind == "RISK_RESOLUTION_CONFIRMED":
        trade = _trade(state, trade_id)
        unresolved_orders = [
            oid for oid in trade.get("openOrders", [])
            if state["orders"].get(oid, {}).get("status") in {
                "UNKNOWN_PENDING_STATUS", "OPEN", "PARTIAL", "CANCEL_FAILED",
                "FILLED_PENDING_RECONCILIATION", "PARTIAL_PENDING_RECONCILIATION",
            }
            or state["orders"].get(oid, {}).get("reconciliationRequired") is True
        ]
        if unresolved_orders:
            raise ValueError("RISK_RESOLUTION_WITH_UNRESOLVED_ORDERS")
        if exposure_shares(trade) > 1e-9:
            raise ValueError("RISK_RESOLUTION_WITH_OPEN_EXPOSURE")
        risk = trade["risk"]
        risk["resolutionRequired"] = False
        risk["resolution"] = {
            "resolvedAt": event.get("resolvedAt"),
            "action": event.get("action"),
            "deadlineBreached": bool(risk.get("deadlineBreached")),
        }
        trade["events"].append(event_id)

    elif kind == "SETTLEMENT":
        trade = _trade(state, trade_id)
        if trade["settlement"] is not None:
            raise ValueError("DUPLICATE_SETTLEMENT")
        w = _finite(event.get("worldPayout"))
        p = _finite(event.get("pmPayout"))
        if w is None or p is None or not (0 <= w <= 1) or not (0 <= p <= 1):
            raise ValueError("INVALID_SETTLEMENT_PAYOUT")
        proceeds = (
            trade["worldNetShares"] * w * float(state["spec"]["cash_usd_assumption"])
            + trade["pmNetShares"] * p
        )
        pnl = proceeds - trade["costUsd"]
        trade["settlement"] = {
            "worldPayout": w,
            "pmPayout": p,
            "combinedAlignedPayout": w + p,
            "proceedsUsdEquivalent": proceeds,
            "pnlUsd": pnl,
        }
        state["settledPnlUsd"] += pnl
        trade["events"].append(event_id)

    elif kind == "NOTE":
        pass
    else:
        raise ValueError(f"UNKNOWN_EVENT_TYPE:{kind}")

    _recompute_stop(state)
    return "APPLIED"


def apply_event(state: dict[str, Any], event: Mapping[str, Any]) -> str:
    event_id = str(event.get("eventId") or "")
    if not event_id:
        raise ValueError("EVENT_ID_REQUIRED")
    if event_id in state["seenEventIds"]:
        return "DUPLICATE_EVENT_IGNORED"
    snapshot = copy.deepcopy(state)
    try:
        return _apply_event_mutating(state, event)
    except Exception:
        state.clear()
        state.update(snapshot)
        raise


class EventLedger:
    def __init__(
        self,
        path: Path,
        spec: ExecutionSpec,
        checkpoint: Path | None = None,
        *,
        now_fn: Callable[[], float] | None = None,
    ) -> None:
        self.path = path
        self.checkpoint = checkpoint
        self.spec = spec
        self._now = now_fn or time.time
        self._lock = threading.RLock()
        self._timer: threading.Timer | None = None
        self._timer_at: float | None = None
        self._timer_generation = 0
        self._closed = False
        self._checkpoint_error: str | None = None
        self.state = initial_state(spec)
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    apply_event(self.state, json.loads(line))
        with self._lock:
            self.recover_overdue_deadlines()
            try:
                self._checkpoint()
                self._checkpoint_error = None
            except Exception as exc:
                # JSONL replay is authoritative during restart as well. A
                # persistently unavailable checkpoint must not prevent state
                # recovery or deadline watcher restoration.
                self._checkpoint_error = f"{type(exc).__name__}:{exc}"
            self._schedule_deadline()

    def _schedule_deadline(self) -> None:
        """Called under the ledger lock, including after replay and early wakeups."""
        if self._closed:
            return
        deadlines = [
            float(risk["deadlineAt"])
            for trade in self.state["trades"].values()
            if (risk := trade["risk"]).get("deadlineAt") is not None
            and risk.get("resolutionRequired") is True
            and risk.get("deadlineBreached") is not True
        ]
        next_at = min(deadlines) if deadlines else None
        if self._timer_at == next_at:
            return
        self._timer_generation += 1
        if self._timer is not None:
            self._timer.cancel()
        self._timer = None
        self._timer_at = next_at
        if next_at is not None:
            self._timer = threading.Timer(
                max(.001, next_at - self._now()), self._watch_deadline,
                args=(self._timer_generation,),
            )
            self._timer.daemon = True
            self._timer.start()

    def _watch_deadline(self, generation: int) -> None:
        with self._lock:
            if self._closed or generation != self._timer_generation:
                return
            self._timer = None
            self._timer_at = None
            self.recover_overdue_deadlines()
            self._schedule_deadline()  # Re-arm if the wall clock says it is not due yet.

    def close(self) -> None:
        """Stop this process's watcher; unresolved deadlines remain in the ledger."""
        with self._lock:
            self._closed = True
            timer = self._timer
            self._timer = None
            self._timer_at = None
            if timer is not None:
                timer.cancel()
        if timer is not None and timer is not threading.current_thread():
            timer.join()

    def recover_overdue_deadlines(self, now: float | None = None) -> list[str]:
        with self._lock:
            observed_now = self._now() if now is None else float(now)
            applied: list[str] = []
            for trade_id in sorted(self.state["trades"]):
                candidate = _deadline_breach_event_if_due(
                    self.state,
                    trade_id,
                    observed_now,
                    source="LEDGER_RECOVERY",
                )
                if candidate is not None:
                    applied.append(self.append(candidate))
            return applied

    def _append_candidate_durable(self, candidate: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = (json.dumps(candidate, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
        mode = "r+b" if self.path.exists() else "w+b"
        with self.path.open(mode) as handle:
            handle.seek(0, os.SEEK_END)
            original_size = handle.tell()
            try:
                written = handle.write(line)
                if written != len(line):
                    raise OSError(f"SHORT_LEDGER_WRITE:{written}/{len(line)}")
                handle.flush()
                os.fsync(handle.fileno())
            except Exception:
                try:
                    handle.seek(original_size)
                    handle.truncate()
                    handle.flush()
                    os.fsync(handle.fileno())
                except Exception as rollback_exc:
                    self._closed = True
                    raise RuntimeError("LEDGER_APPEND_ROLLBACK_FAILED") from rollback_exc
                raise

    def append(self, event: Mapping[str, Any]) -> str:
        with self._lock:
            if self._closed:
                raise ValueError("LEDGER_CLOSED")
            candidate = json.loads(json.dumps(event, allow_nan=False))
            if candidate.get("type") == "FILL" and candidate.get("processedAt") is None:
                candidate["processedAt"] = self._now()
            if candidate.get("type") == "RISK_RESOLUTION_CONFIRMED":
                trade_id = str(candidate["tradeId"])
                resolved_at = _finite(candidate.get("resolvedAt"))
                overdue = _deadline_breach_event_if_due(
                    self.state, trade_id, self._now() if resolved_at is None else resolved_at,
                    source="RISK_RESOLUTION",
                )
                if overdue is not None:
                    self.append(overdue)
                if candidate.get("action") == "HEDGE_RECONCILED" and self.state["trades"][trade_id]["risk"]["deadlineBreached"]:
                    candidate["action"] = "HEDGE_RECONCILED_AFTER_DEADLINE"

            next_state = copy.deepcopy(self.state)
            result = apply_event(next_state, candidate)
            if result == "DUPLICATE_EVENT_IGNORED":
                return result

            self._append_candidate_durable(candidate)
            self.state = next_state
            try:
                self._checkpoint()
                self._checkpoint_error = None
            except Exception as exc:
                # The append-only JSONL is authoritative. A checkpoint failure
                # must not turn a durably committed event into an apparent
                # execution failure or interrupt subsequent risk handling.
                self._checkpoint_error = f"{type(exc).__name__}:{exc}"
            self._schedule_deadline()
            return result

    def _checkpoint(self) -> None:
        if self.checkpoint is None:
            return
        self.checkpoint.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.state, indent=2, sort_keys=True, allow_nan=False) + "\n"
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.checkpoint.parent,
                prefix=self.checkpoint.name + ".",
                suffix=".tmp",
                delete=False,
            ) as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
                temp_path = Path(handle.name)
            temp_path.replace(self.checkpoint)
        finally:
            if temp_path is not None and temp_path.exists():
                temp_path.unlink()


def candidate_costs(
    *,
    world_expected_shares: float,
    world_minout_shares: float,
    world_cash: float,
    pm_asks: list[dict[str, Any]],
    pm_fee_schedule: dict[str, Any],
    world_fee_bps: float | None,
) -> dict[str, Any]:
    expected = r2._walk_cost(pm_asks, world_expected_shares, pm_fee_schedule)
    minout = r2._walk_cost(pm_asks, world_minout_shares, pm_fee_schedule)

    legacy_fee_bps = None
    if world_fee_bps is not None:
        legacy_fee_bps = _finite(world_fee_bps)
        if legacy_fee_bps is None or legacy_fee_bps < 0:
            raise ValueError("INVALID_WORLD_FEE_BPS")

    # DFlow GET /order defines outAmount and otherAmountThreshold/minOutAmount
    # as output amounts after all fees. The quote input amount is therefore the
    # complete World cash basis for this quote-based paper model. Applying an
    # additional fee-bps multiplier here would double count execution fees.
    world_cost = float(world_cash)

    def row(target: float, walk: Mapping[str, Any]) -> dict[str, Any]:
        if walk.get("status") != "COMPLETE":
            return {
                "status": "UNKNOWN",
                "reason": walk.get("reason"),
                "targetNetShares": target,
                "walk": dict(walk),
            }
        pm_cost = float(walk["pmCost"])
        total = world_cost + pm_cost
        return {
            "status": "COMPLETE",
            "targetNetShares": target,
            "worldCostUsdEquivalent": world_cost,
            "pmCostUsdEquivalent": pm_cost,
            "totalCostUsdEquivalent": total,
            "unitCost": total / target,
            "walk": dict(walk),
        }

    return {
        "worldExpectedOutputShares": world_expected_shares,
        "worldMinOutputShares": world_minout_shares,
        "expected": row(world_expected_shares, expected),
        "minOut": row(world_minout_shares, minout),
        "worldFeeBpsAssumption": legacy_fee_bps,
        "worldFeeBpsAssumptionApplied": False,
        "feeStatus": "INCLUDED_IN_DFLOW_QUOTE_OUTPUT_AFTER_ALL_FEES",
        "worldFeeTreatment": "NO_SEPARATE_CASH_ADDON_OUTPUT_SHARES_ALREADY_AFTER_ALL_FEES",
    }


def make_fill_event(
    *,
    event_id: str,
    trade_id: str,
    order_id: str,
    fill_id: str,
    venue: str,
    gross_shares: float,
    net_shares: float,
    cash_out_usd: float,
    fee_usd_equivalent: float,
    evidence_object: Mapping[str, Any],
    simulation_rule: str,
) -> dict[str, Any]:
    return {
        "eventId": event_id,
        "type": "FILL",
        "tradeId": trade_id,
        "orderId": order_id,
        "fillId": fill_id,
        "venue": venue,
        "grossShares": gross_shares,
        "netShares": net_shares,
        "cashOutUsd": cash_out_usd,
        "feeUsdEquivalent": fee_usd_equivalent,
        "evidence": {
            "sourceSha256": _digest(evidence_object),
            "evidenceClass": PAPER_FILL_CLASS,
            "simulationRule": simulation_rule,
            "observation": dict(evidence_object),
        },
    }


def hedge_target_shares(state: Mapping[str, Any], trade_id: str) -> float:
    trade = state["trades"][trade_id]
    return max(0.0, float(trade.get("worldNetShares") or 0.0) - float(trade.get("pmNetShares") or 0.0))


def invariant_report(state: Mapping[str, Any]) -> dict[str, Any]:
    fill_cash = sum(float(t.get("costUsd") or 0.0) for t in state["trades"].values())
    exposures = {tid: exposure_shares(t) for tid, t in state["trades"].items()}
    duplicate_fill_ids = len(state["seenFillIds"]) != len(set(state["seenFillIds"]))
    return {
        "cashLedgerMatchesTrades": abs(float(state["cashOutUsd"]) - fill_cash) <= 1e-9,
        "duplicateFillIds": duplicate_fill_ids,
        "exposureShares": exposures,
        "stopNewTradesConsistent": bool(state["stopNewTrades"]) == bool(state["stopReasons"]),
        "pass": (
            abs(float(state["cashOutUsd"]) - fill_cash) <= 1e-9
            and not duplicate_fill_ids
            and bool(state["stopNewTrades"]) == bool(state["stopReasons"])
        ),
    }


def event(event_id: str, kind: str, **fields: Any) -> dict[str, Any]:
    return {"eventId": event_id, "type": kind, **fields}

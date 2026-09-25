"""Prospective randomized World HTTP-session experiment for BTC5M relative value.

Each eligible R3 radar episode is assigned exactly once to fresh or persistent
World HTTP session strategy before the exact result is known. Public read-only;
NO_TRADE. No credentials, signing, transaction submission, orders, or capital.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import math
import os
import platform
import random
import statistics
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Mapping

import requests

from tools.research import world_pm_edge_lifetime_low_interference_r3 as r3
from tools.research import world_pm_edge_lifetime_r2 as r2
from tools.research import world_polymarket_btc5m_executable_sync_r23 as r23
from tools.research.world_pm_world_quote_gap_r1 import GapRadar

SCHEMA_VERSION = "WORLD_PM_WORLD_SESSION_RANDOMIZED_R2"
PROTOCOL = "docs/decisions/2026-09-20-world-pm-world-session-randomized-r2.md"
AMOUNT_CASH = 10.0
BLOCK_SIZE = 4
BLOCK_TEMPLATE = ("fresh", "fresh", "persistent", "persistent")
MAX_WINDOWS_PER_BATCH = 60
MAX_CAPTURED_ROUTE_DEPTH = 10
SENSITIVE_KEY_TERMS = (
    "transaction", "signature", "private", "secret", "authorization",
    "calldata", "serialized", "userpublickey", "user_public_key",
)


def _finite(value: Any) -> float | None:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _sanitize_public(value: Any, depth: int = 0) -> Any:
    if depth > MAX_CAPTURED_ROUTE_DEPTH:
        return "<MAX_DEPTH>"
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, child in value.items():
            name = str(key)
            lower = name.lower()
            if any(term in lower for term in SENSITIVE_KEY_TERMS):
                continue
            out[name] = _sanitize_public(child, depth + 1)
        return out
    if isinstance(value, list):
        return [_sanitize_public(x, depth + 1) for x in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _collect_public_fee_fields(value: Any, path: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    if isinstance(value, Mapping):
        for key, child in value.items():
            name = str(key)
            lower = name.lower()
            if any(term in lower for term in SENSITIVE_KEY_TERMS):
                continue
            child_path = f"{path}.{name}" if path else name
            if "fee" in lower and (child is None or isinstance(child, (str, int, float, bool))):
                out[child_path] = child
            if isinstance(child, (Mapping, list)):
                out.update(_collect_public_fee_fields(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            out.update(_collect_public_fee_fields(child, f"{path}[{index}]"))
    return out


def _extract_route_facts(route: Any) -> dict[str, Any]:
    venues: list[Any] = []
    market_keys: list[Any] = []
    legs: list[dict[str, Any]] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, Mapping):
            lower = {str(k).lower(): k for k in node}
            for candidate in ("venue", "dex", "source", "exchange", "protocol"):
                if candidate in lower:
                    value = node.get(lower[candidate])
                    if value not in (None, ""):
                        venues.append(value)
            for candidate in ("marketkey", "market_key", "market", "poolkey", "pool_key"):
                if candidate in lower:
                    value = node.get(lower[candidate])
                    if value not in (None, ""):
                        market_keys.append(value)
            mint_fields = {}
            amount_fields = {}
            for key, value in node.items():
                lname = str(key).lower()
                if "mint" in lname and value not in (None, ""):
                    mint_fields[str(key)] = value
                if "amount" in lname and value not in (None, ""):
                    amount_fields[str(key)] = value
            if mint_fields or amount_fields:
                legs.append({"path": path, "mints": mint_fields, "amounts": amount_fields})
            for key, child in node.items():
                walk(child, f"{path}.{key}" if path else str(key))
        elif isinstance(node, list):
            for index, child in enumerate(node):
                walk(child, f"{path}[{index}]")

    walk(route, "routePlan")
    return {
        "venueValues": venues,
        "marketKeyValues": market_keys,
        "legs": legs,
        "routeValuesPresent": route is not None,
        "missing": {
            "venue": not venues,
            "marketKey": not market_keys,
            "legs": not legs,
        },
    }


def _assignment_plan(seed: int, count: int, offset: int = 0) -> list[dict[str, Any]]:
    if count < 0 or offset < 0:
        raise ValueError("assignment count/offset must be nonnegative")
    needed = count + offset
    rng = random.Random(seed)
    sequence: list[str] = []
    block = 0
    while len(sequence) < needed:
        values = list(BLOCK_TEMPLATE)
        rng.shuffle(values)
        sequence.extend(values)
        block += 1
    selected = sequence[offset:offset + count]
    return [
        {
            "assignmentIndex": offset + i,
            "blockIndex": (offset + i) // BLOCK_SIZE,
            "withinBlockIndex": (offset + i) % BLOCK_SIZE,
            "group": group,
        }
        for i, group in enumerate(selected)
    ]


class AssignmentCursor:
    def __init__(self, seed: int, max_assignments: int, offset: int = 0) -> None:
        self.seed = seed
        self.offset = offset
        self.plan = _assignment_plan(seed, max_assignments, offset)
        self.index = 0

    def next(self) -> dict[str, Any]:
        if self.index >= len(self.plan):
            raise RuntimeError("ASSIGNMENT_PLAN_EXHAUSTED")
        row = dict(self.plan[self.index])
        self.index += 1
        return row


class EvidenceSession(requests.Session):
    def __init__(self, *, session_id: str, mode: str) -> None:
        super().__init__()
        self.session_id = session_id
        self.mode = mode
        self.use_count = 0
        self.last_completed_perf_ns: int | None = None
        self.last_http: dict[str, Any] = {}

    def get(self, url: str, *args: Any, **kwargs: Any):
        params = dict(kwargs.get("params") or {})
        use_before = self.use_count
        started_utc = time.time()
        started_perf = time.perf_counter_ns()
        since_last_ms = None
        if self.last_completed_perf_ns is not None:
            since_last_ms = (started_perf - self.last_completed_perf_ns) / 1_000_000.0
        record: dict[str, Any] = {
            "sessionId": self.session_id,
            "sessionMode": self.mode,
            "sessionUseCountBefore": use_before,
            "sessionFirstUse": use_before == 0,
            "msSinceSessionPreviousCompletedRequest": since_last_ms,
            "requestStartedAt": started_utc,
            "requestParameters": _sanitize_public(params),
            "connectionReuseVerified": "unknown",
            "reconnectObservation": "unknown",
        }
        try:
            response = super().get(url, *args, **kwargs)
            response_at = time.time()
            completed_perf = time.perf_counter_ns()
            self.use_count += 1
            self.last_completed_perf_ns = completed_perf
            record.update({
                "httpResponseAt": response_at,
                "transportElapsedMonotonicMs": (completed_perf - started_perf) / 1_000_000.0,
                "httpStatus": response.status_code,
                "sessionUseCountAfter": self.use_count,
            })
            try:
                payload = response.json()
            except Exception:
                payload = None
            if isinstance(payload, Mapping):
                route = payload.get("routePlan")
                record["routePlan"] = _sanitize_public(route)
                record["routeFacts"] = _extract_route_facts(record["routePlan"])
                record["publicFeeFields"] = _collect_public_fee_fields(payload)
                record["responseFields"] = {
                    key: payload.get(key)
                    for key in (
                        "inAmount", "outAmount", "minOutAmount", "contextSlot",
                        "executionMode", "priceImpactPct", "platformFee",
                    )
                }
                user_public_key_supplied = bool(params.get("userPublicKey"))
                transaction_present = bool(payload.get("transaction"))
                record["transactionBuildEvidence"] = {
                    "userPublicKeySupplied": user_public_key_supplied,
                    "transactionPresent": transaction_present,
                    "buildVerified": bool(user_public_key_supplied and transaction_present),
                    "transactionBytesStored": False,
                }
            else:
                record.update({
                    "routePlan": None,
                    "routeFacts": _extract_route_facts(None),
                    "publicFeeFields": {},
                    "responseFields": {},
                    "transactionBuildEvidence": {
                        "userPublicKeySupplied": bool(params.get("userPublicKey")),
                        "transactionPresent": False,
                        "buildVerified": False,
                        "transactionBytesStored": False,
                    },
                })
            self.last_http = record
            return response
        except Exception as exc:
            completed_perf = time.perf_counter_ns()
            self.use_count += 1
            self.last_completed_perf_ns = completed_perf
            record.update({
                "httpResponseAt": time.time(),
                "transportElapsedMonotonicMs": (completed_perf - started_perf) / 1_000_000.0,
                "transportError": f"{type(exc).__name__}:{exc}",
                "sessionUseCountAfter": self.use_count,
                "routePlan": None,
                "routeFacts": _extract_route_facts(None),
                "publicFeeFields": {},
                "responseFields": {},
                "transactionBuildEvidence": {
                    "userPublicKeySupplied": bool(params.get("userPublicKey")),
                    "transactionPresent": False,
                    "buildVerified": False,
                    "transactionBytesStored": False,
                },
            })
            self.last_http = record
            raise


def _walk_cost(
    asks: Any,
    target: float | None,
    fee_schedule: dict[str, Any],
) -> dict[str, Any]:
    if target is None or target <= 0:
        return {"status": "UNKNOWN", "reason": "TARGET_INVALID", "targetNetShares": target}
    if not isinstance(asks, list) or not asks:
        return {"status": "UNKNOWN", "reason": "PM_ASKS_MISSING", "targetNetShares": target}
    for level in asks:
        if not isinstance(level, Mapping):
            return {"status": "UNKNOWN", "reason": "PM_LEVEL_INVALID", "targetNetShares": target}
        price = _finite(level.get("price"))
        size = _finite(level.get("size"))
        if price is None or size is None or not (0 < price <= 1) or size <= 0:
            return {"status": "UNKNOWN", "reason": "PM_LEVEL_INVALID", "targetNetShares": target}
    walk = r23._walk_pm_book_for_net_shares(asks, target, fee_schedule)
    if walk.get("filled") is not True:
        return {"status": "UNKNOWN", "reason": "PM_DEPTH_INSUFFICIENT", "targetNetShares": target, "walk": walk}
    return {
        "status": "COMPLETE",
        "targetNetShares": target,
        "pmCost": float(walk["cost"]),
        "walk": walk,
    }


def _combined_cost(input_cash: float, walk: dict[str, Any]) -> float | None:
    target = _finite(walk.get("targetNetShares"))
    pm_cost = _finite(walk.get("pmCost"))
    if walk.get("status") != "COMPLETE" or target is None or target <= 0 or pm_cost is None:
        return None
    return (input_cash + pm_cost) / target


def _economics(
    *,
    quote: Mapping[str, Any],
    trigger_pm: Mapping[str, Any] | None,
    response_pm: Mapping[str, Any] | None,
    fee_schedule: dict[str, Any],
    integrity_verified: bool,
    radar_indicative: float | None,
    cash_decimals: int,
) -> dict[str, Any]:
    raw_in = _finite(quote.get("inAmount"))
    qout = _finite(quote.get("outputUiShares"))
    qmin = _finite(quote.get("minOutputUiShares"))
    if (
        quote.get("success") is not True
        or raw_in is None
        or raw_in <= 0
        or qout is None
        or qmin is None
        or qout <= 0
        or qmin <= 0
    ):
        return {"status": "UNKNOWN", "reason": "WORLD_QUOTE_INVALID"}
    input_cash = raw_in / (10 ** cash_decimals)
    if abs(input_cash - AMOUNT_CASH) > 1e-9:
        return {"status": "UNKNOWN", "reason": "WORLD_INPUT_AMOUNT_MISMATCH", "actualInputCash": input_cash}
    if not integrity_verified:
        return {"status": "UNKNOWN", "reason": "PM_INTEGRITY_UNKNOWN", "actualInputCash": input_cash}

    response_asks = None if not isinstance(response_pm, Mapping) else response_pm.get("asks")
    trigger_asks = None if not isinstance(trigger_pm, Mapping) else trigger_pm.get("asks")
    response_expected = _walk_cost(response_asks, qout, fee_schedule)
    response_min = _walk_cost(response_asks, qmin, fee_schedule)
    trigger_expected = _walk_cost(trigger_asks, qout, fee_schedule)
    trigger_min = _walk_cost(trigger_asks, qmin, fee_schedule)

    c_response_expected = _combined_cost(input_cash, response_expected)
    c_response_min = _combined_cost(input_cash, response_min)
    c_trigger_expected = _combined_cost(input_cash, trigger_expected)
    c_trigger_min = _combined_cost(input_cash, trigger_min)
    complete = all(
        x is not None
        for x in (c_response_expected, c_response_min, c_trigger_expected, c_trigger_min)
    )
    return {
        "status": "QUALIFIED" if complete else "UNKNOWN",
        "reason": "FULL_DEPTH_EXPECTED_AND_MINOUT" if complete else "PM_DEPTH_OR_TRIGGER_STATE_UNKNOWN",
        "actualInputCash": input_cash,
        "worldExpectedOutputShares": qout,
        "worldMinOutputShares": qmin,
        "responseExpected": response_expected,
        "responseMinOut": response_min,
        "triggerExpected": trigger_expected,
        "triggerMinOut": trigger_min,
        "combinedCostExpectedResponse": c_response_expected,
        "combinedCostMinOutResponse": c_response_min,
        "combinedCostExpectedTriggerPm": c_trigger_expected,
        "combinedCostMinOutTriggerPm": c_trigger_min,
        "expectedResponseMinusTriggerPm": None if c_response_expected is None or c_trigger_expected is None else c_response_expected - c_trigger_expected,
        "minOutResponseMinusTriggerPm": None if c_response_min is None or c_trigger_min is None else c_response_min - c_trigger_min,
        "expectedResponseMinusRadar": None if c_response_expected is None or radar_indicative is None else c_response_expected - radar_indicative,
        "minOutResponseMinusRadar": None if c_response_min is None or radar_indicative is None else c_response_min - radar_indicative,
        "expectedBelowOne": bool(c_response_expected is not None and c_response_expected < 1.0),
        "minOutBelowOne": bool(c_response_min is not None and c_response_min < 1.0),
    }


def _is_timeout(row: Mapping[str, Any]) -> bool:
    text = " ".join(str(row.get(k) or "") for k in ("error", "transportError", "reason"))
    return "timeout" in text.lower()


class PairWorker:
    def __init__(self, pair: str) -> None:
        self.pair = pair
        self.busy = False
        self._persistent = EvidenceSession(
            session_id=f"{pair}:persistent:{uuid.uuid4().hex[:12]}",
            mode="persistent",
        )
        self._task: asyncio.Task[dict[str, Any]] | None = None

    def diagnostics(self) -> dict[str, Any]:
        return {
            "pair": self.pair,
            "persistentSessionId": self._persistent.session_id,
            "persistentSessionUseCount": self._persistent.use_count,
            "persistentLastCompletedPerfNs": self._persistent.last_completed_perf_ns,
            "busy": self.busy,
        }

    def close(self) -> None:
        self._persistent.close()

    def dispatch(
        self,
        *,
        event: dict[str, Any],
        assignment: dict[str, Any],
        pm_feed: r2.RollingTimelineBook,
        dflow: GapRadar,
        world_decimals: int,
        pm_fee_schedule: dict[str, Any],
        cash_decimals: int,
        http_timeout_seconds: float,
        clock_offset_seconds: float | None,
        window_end_ts: int,
    ) -> asyncio.Task[dict[str, Any]] | None:
        if self.busy:
            return None
        self.busy = True
        self._task = asyncio.create_task(self._run(
            event=event,
            assignment=assignment,
            pm_feed=pm_feed,
            dflow=dflow,
            world_decimals=world_decimals,
            pm_fee_schedule=pm_fee_schedule,
            cash_decimals=cash_decimals,
            http_timeout_seconds=http_timeout_seconds,
            clock_offset_seconds=clock_offset_seconds,
            window_end_ts=window_end_ts,
        ))
        return self._task

    async def _run(self, **kwargs: Any) -> dict[str, Any]:
        assignment = dict(kwargs["assignment"])
        event = dict(kwargs["event"])
        group = assignment["group"]
        side = "yes" if event["pair"] == "WORLD_YES+PM_DOWN" else "no"
        session = self._persistent if group == "persistent" else EvidenceSession(
            session_id=f"{event['eventId']}:fresh:{uuid.uuid4().hex[:12]}",
            mode="fresh",
        )
        pm_session = requests.Session()
        dflow: GapRadar = kwargs["dflow"]
        pre_radar: dict[str, Any] = {}
        response_radar: dict[str, Any] = {}
        updates: list[dict[str, Any]] = []
        overflow = False
        try:
            trigger_pm = event.get("pmTriggerState")
            pre_radar = dflow.capture(side)
            start_sequence = int(pre_radar.get("sequence") or 0)
            capture = await r3._capture_anchor(
                event=event,
                target_at=float(event["triggeredAt"]),
                label="T0",
                world_session=session,
                pm_feed=kwargs["pm_feed"],
                world_decimals=kwargs["world_decimals"],
                pm_fee_schedule=kwargs["pm_fee_schedule"],
                cash_decimals=kwargs["cash_decimals"],
                http_timeout_seconds=kwargs["http_timeout_seconds"],
                clock_offset_seconds=kwargs["clock_offset_seconds"],
                window_end_ts=kwargs["window_end_ts"],
            )
            response_radar = dflow.capture(side)
            end_sequence = int(response_radar.get("sequence") or 0)
            updates, overflow = dflow.interval(start_sequence, end_sequence, side)
            verification = await r3._verify_anchor(
                capture=capture,
                event=event,
                pm_session=pm_session,
                pm_feed=kwargs["pm_feed"],
                pm_fee_schedule=kwargs["pm_fee_schedule"],
                http_timeout_seconds=kwargs["http_timeout_seconds"],
                clock_offset_seconds=kwargs["clock_offset_seconds"],
            )
            quote = dict(capture.get("worldQuote") or {})
            http = copy.deepcopy(session.last_http)
            economics = _economics(
                quote=quote,
                trigger_pm=trigger_pm if isinstance(trigger_pm, Mapping) else None,
                response_pm=capture.get("responseState") if isinstance(capture.get("responseState"), Mapping) else None,
                fee_schedule=kwargs["pm_fee_schedule"],
                integrity_verified=verification.get("verified") is True,
                radar_indicative=_finite(event.get("indicativeSum")),
                cash_decimals=kwargs["cash_decimals"],
            )
            return {
                **event,
                "assignment": assignment,
                "assignedGroup": group,
                "attemptStatus": "ATTEMPTED",
                "requestCountForEpisode": 1,
                "worldHttp": http,
                "worldQuote": quote,
                "worldRadarPreRequest": pre_radar,
                "worldRadarAtResponse": response_radar,
                "worldRadarUpdatesDuringRequest": updates,
                "worldRadarTraceOverflow": overflow,
                "worldResponseSnapshotAcquiredAt": response_radar.get("observedAt"),
                "pmResponseAnchor": capture.get("responseState"),
                "pmResponseAnchorDigest": capture.get("responseAnchorStateDigest"),
                "pmAnchorGeneration": capture.get("anchorGeneration"),
                "pmAnchorGenerationHealthy": capture.get("anchorGenerationHealthy"),
                "pmIntegrityVerification": verification,
                "economics": economics,
                "persistentFirstUse": bool(group == "persistent" and http.get("sessionFirstUse") is True),
                "connectionReuseVerified": http.get("connectionReuseVerified", "unknown"),
                "timeout": _is_timeout({**http, **quote}),
            }
        except Exception as exc:
            return {
                **event,
                "assignment": assignment,
                "assignedGroup": group,
                "attemptStatus": "UNKNOWN",
                "requestCountForEpisode": 1,
                "error": f"{type(exc).__name__}:{exc}",
                "worldHttp": copy.deepcopy(session.last_http),
                "worldRadarPreRequest": pre_radar,
                "worldRadarAtResponse": response_radar,
                "worldRadarUpdatesDuringRequest": updates,
                "worldRadarTraceOverflow": overflow,
                "persistentFirstUse": bool(
                    group == "persistent" and session.last_http.get("sessionFirstUse") is True
                ),
                "connectionReuseVerified": session.last_http.get("connectionReuseVerified", "unknown"),
                "timeout": "timeout" in f"{type(exc).__name__}:{exc}".lower(),
                "economics": {"status": "UNKNOWN", "reason": "REQUEST_OR_VERIFICATION_EXCEPTION"},
            }
        finally:
            pm_session.close()
            if group == "fresh":
                session.close()
            self.busy = False


def _window_summary(row: Mapping[str, Any]) -> dict[str, Any]:
    assigned = [e for e in row.get("events", []) if isinstance(e, Mapping) and e.get("assignedGroup") in {"fresh", "persistent"}]
    return {
        "assignedCount": len(assigned),
        "busySkippedCount": sum(e.get("attemptStatus") == "BUSY_SKIPPED" for e in assigned),
        "attemptedCount": sum(e.get("attemptStatus") == "ATTEMPTED" for e in assigned),
        "unknownCount": sum((e.get("economics") or {}).get("status") != "QUALIFIED" for e in assigned),
        "qualifiedCount": sum((e.get("economics") or {}).get("status") == "QUALIFIED" for e in assigned),
        "expectedBelowOneCount": sum((e.get("economics") or {}).get("expectedBelowOne") is True for e in assigned),
        "minOutBelowOneCount": sum((e.get("economics") or {}).get("minOutBelowOne") is True for e in assigned),
    }


def _group_summary(windows: list[dict[str, Any]], group: str) -> dict[str, Any]:
    rows = [
        e for w in windows for e in w.get("events", [])
        if isinstance(e, Mapping) and e.get("assignedGroup") == group
    ]
    attempted = [e for e in rows if e.get("attemptStatus") == "ATTEMPTED"]
    qualified = [e for e in rows if (e.get("economics") or {}).get("status") == "QUALIFIED"]
    latency = [
        float((e.get("worldHttp") or {})["transportElapsedMonotonicMs"])
        for e in attempted
        if _finite((e.get("worldHttp") or {}).get("transportElapsedMonotonicMs")) is not None
    ]
    expected = [
        float((e.get("economics") or {})["combinedCostExpectedResponse"])
        for e in qualified
        if _finite((e.get("economics") or {}).get("combinedCostExpectedResponse")) is not None
    ]
    minout = [
        float((e.get("economics") or {})["combinedCostMinOutResponse"])
        for e in qualified
        if _finite((e.get("economics") or {}).get("combinedCostMinOutResponse")) is not None
    ]
    unresolved = len(rows) - len(qualified)
    windows_with_latency = {
        int(e["windowIndex"]) for e in attempted
        if _finite((e.get("worldHttp") or {}).get("transportElapsedMonotonicMs")) is not None
    }
    windows_with_qualified = {int(e["windowIndex"]) for e in qualified}
    return {
        "assigned": len(rows),
        "attempted": len(attempted),
        "busySkipped": sum(e.get("attemptStatus") == "BUSY_SKIPPED" for e in rows),
        "success": sum((e.get("worldQuote") or {}).get("success") is True for e in attempted),
        "timeout": sum(e.get("timeout") is True for e in rows),
        "unknownOrUnqualified": unresolved,
        "unknownShare": unresolved / len(rows) if rows else None,
        "latencyMs": {
            "count": len(latency),
            "min": min(latency) if latency else None,
            "median": statistics.median(latency) if latency else None,
            "max": max(latency) if latency else None,
        },
        "qualified": len(qualified),
        "expectedCost": {
            "count": len(expected),
            "min": min(expected) if expected else None,
            "median": statistics.median(expected) if expected else None,
            "max": max(expected) if expected else None,
        },
        "minOutCost": {
            "count": len(minout),
            "min": min(minout) if minout else None,
            "median": statistics.median(minout) if minout else None,
            "max": max(minout) if minout else None,
        },
        "expectedBelowOne": sum((e.get("economics") or {}).get("expectedBelowOne") is True for e in qualified),
        "minOutBelowOne": sum((e.get("economics") or {}).get("minOutBelowOne") is True for e in qualified),
        "independentWindowsWithLatency": len(windows_with_latency),
        "independentWindowsQualified": len(windows_with_qualified),
        "persistentFirstUse": sum(e.get("persistentFirstUse") is True for e in rows),
        "persistentSubsequentUse": sum(group == "persistent" and e.get("persistentFirstUse") is False and e.get("attemptStatus") == "ATTEMPTED" for e in rows),
    }


def _summary(windows: list[dict[str, Any]]) -> dict[str, Any]:
    fresh = _group_summary(windows, "fresh")
    persistent = _group_summary(windows, "persistent")
    latency_sufficient = all(
        g["attempted"] >= 12
        and g["independentWindowsWithLatency"] >= 4
        and (g["unknownShare"] is not None and g["unknownShare"] <= 0.5)
        for g in (fresh, persistent)
    )
    cost_sufficient = all(
        g["qualified"] >= 12
        and g["independentWindowsQualified"] >= 4
        and (g["unknownShare"] is not None and g["unknownShare"] <= 0.5)
        for g in (fresh, persistent)
    )
    return {
        "completedWindows": len(windows),
        "groups": {"fresh": fresh, "persistent": persistent},
        "latencyComparisonStatus": "DESCRIPTIVE_EVIDENCE_AVAILABLE" if latency_sufficient else "UNABLE_TO_DETERMINE",
        "costComparisonStatus": "DESCRIPTIVE_EVIDENCE_AVAILABLE" if cost_sufficient else "UNABLE_TO_DETERMINE",
        "expectedBelowOneTotal": fresh["expectedBelowOne"] + persistent["expectedBelowOne"],
        "minOutBelowOneTotal": fresh["minOutBelowOne"] + persistent["minOutBelowOne"],
        "independentWindowsExpectedBelowOne": len({
            int(e["windowIndex"]) for w in windows for e in w.get("events", [])
            if (e.get("economics") or {}).get("expectedBelowOne") is True
        }),
        "independentWindowsMinOutBelowOne": len({
            int(e["windowIndex"]) for w in windows for e in w.get("events", [])
            if (e.get("economics") or {}).get("minOutBelowOne") is True
        }),
        "interpretation": "INTENTION_TO_TREAT_BY_ORIGINAL_ASSIGNMENT; WITHIN_WINDOW_EPISODES_NOT_INDEPENDENT",
    }


def _runtime_environment() -> dict[str, Any]:
    return {
        "pythonVersion": sys.version,
        "platform": platform.platform(),
        "runnerOs": os.environ.get("RUNNER_OS"),
        "runnerName": os.environ.get("RUNNER_NAME"),
        "githubRunId": os.environ.get("GITHUB_RUN_ID"),
        "githubSha": os.environ.get("GITHUB_SHA"),
        "requestsVersion": requests.__version__,
    }


def _source_hashes() -> dict[str, str]:
    paths = [
        Path(__file__),
        Path(r3.__file__),
        Path(r2.__file__),
        Path(r23.__file__),
    ]
    return {p.as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    temp.replace(path)


async def _run_window(
    *,
    window_index: int,
    start_ts: int,
    args: argparse.Namespace,
    cash_decimals: int,
    clock_offset_seconds: float | None,
    assignments: AssignmentCursor,
    workers: dict[str, PairWorker],
) -> dict[str, Any]:
    end_ts = start_ts + r23.WINDOW_SECONDS
    if time.time() < start_ts:
        await asyncio.sleep(start_ts - time.time())
    pm_market = await asyncio.to_thread(r23.wp.fetch_polymarket_market, start_ts)
    stop = asyncio.Event()
    pm_feed = r2.RollingTimelineBook([pm_market.up_token, pm_market.down_token])
    pm_task = asyncio.create_task(pm_feed.run(stop))
    try:
        world_market = await asyncio.to_thread(
            r23.radar._discover_world_market,
            start_ts,
            args.rpc_url,
            min(float(end_ts), time.time() + args.world_discovery_timeout_seconds),
        )
        world_error = None
    except Exception as exc:
        world_market = None
        world_error = f"{type(exc).__name__}:{exc}"

    row: dict[str, Any] = {
        "windowIndex": window_index,
        "startTs": start_ts,
        "endTs": end_ts,
        "startedAt": time.time(),
        "worldDiscoveryError": world_error,
        "events": [],
        "radarPollCount": 0,
        "healthyPollCount": {"WORLD_YES+PM_DOWN": 0, "WORLD_NO+PM_UP": 0},
        "radarEpisodeCount": {"WORLD_YES+PM_DOWN": 0, "WORLD_NO+PM_UP": 0},
        "persistentSessionDiagnosticsBeforeWindow": {
            pair: worker.diagnostics() for pair, worker in workers.items()
        },
    }
    if world_market is None:
        while time.time() < end_ts:
            await asyncio.sleep(min(1.0, max(0.0, end_ts - time.time())))
        stop.set()
        pm_task.cancel()
        await asyncio.gather(pm_task, return_exceptions=True)
        row["completedAt"] = time.time()
        row["summary"] = _window_summary(row)
        return row

    yes_decimals, no_decimals = await asyncio.gather(
        asyncio.to_thread(r2._retry_token_decimals, world_market.yes_mint, args.rpc_url),
        asyncio.to_thread(r2._retry_token_decimals, world_market.no_mint, args.rpc_url),
    )
    row["worldMarket"] = {
        "market": world_market.market,
        "yesMint": world_market.yes_mint,
        "noMint": world_market.no_mint,
    }
    row["pmMarket"] = {
        "conditionId": pm_market.condition_id,
        "upToken": pm_market.up_token,
        "downToken": pm_market.down_token,
        "feeSchedule": pm_market.fee_schedule,
    }
    dflow = GapRadar(world_market.yes_mint, world_market.no_mint)
    dflow_task = asyncio.create_task(dflow.run(stop))
    active = {"WORLD_YES+PM_DOWN": False, "WORLD_NO+PM_UP": False}
    last_sampled = {"WORLD_YES+PM_DOWN": -float("inf"), "WORLD_NO+PM_UP": -float("inf")}
    sampled = {"WORLD_YES+PM_DOWN": 0, "WORLD_NO+PM_UP": 0}
    pending: list[asyncio.Task[dict[str, Any]]] = []
    seq = 0
    candidates = [
        ("WORLD_YES+PM_DOWN", "yes", world_market.yes_mint, yes_decimals, pm_market.down_token),
        ("WORLD_NO+PM_UP", "no", world_market.no_mint, no_decimals, pm_market.up_token),
    ]

    try:
        while time.time() < end_ts:
            loop_at = time.time()
            row["radarPollCount"] += 1
            world_snapshot = dflow.snapshot(loop_at)
            for pair, side, mint, decimals, token in candidates:
                pm_snapshot = pm_feed.snapshot(token, clock_offset_seconds=clock_offset_seconds)
                leg = world_snapshot.get(side) or {}
                world_ask = _finite(leg.get("ask"))
                world_age = _finite(leg.get("quoteAgeSeconds"))
                pm_ask = _finite(pm_snapshot.get("bestAsk"))
                healthy = bool(
                    pm_snapshot.get("healthy") is True
                    and world_snapshot.get("healthy") is True
                    and world_ask is not None
                    and pm_ask is not None
                    and world_age is not None
                    and world_age <= r23.MAX_WORLD_RADAR_AGE_SECONDS
                )
                if healthy:
                    row["healthyPollCount"][pair] += 1
                effective = r23._pm_effective_top_cost(pm_ask, pm_market.fee_schedule) if healthy else None
                below = bool(effective is not None and world_ask is not None and world_ask + effective < 1.0)
                if below and not active[pair]:
                    active[pair] = True
                    seq += 1
                    row["radarEpisodeCount"][pair] += 1
                    event = {
                        "eventId": f"w{window_index}-e{seq}",
                        "windowIndex": window_index,
                        "pair": pair,
                        "triggeredAt": loop_at,
                        "worldMint": mint,
                        "pmToken": token,
                        "indicativeSum": world_ask + effective,
                        "worldIndicativeAsk": world_ask,
                        "pmLocalBestAsk": pm_ask,
                        "radarTrigger": {
                            **dict(leg),
                            "healthy": world_snapshot.get("healthy") is True,
                            "observedAt": loop_at,
                        },
                        "pmTriggerState": copy.deepcopy(pm_snapshot),
                        "samplingEligibility": "COOLDOWN_OR_CAP",
                    }
                    row["events"].append(event)
                    eligible = (
                        sampled[pair] < r3.MAX_T0_SAMPLED_EPISODES_PER_PAIR_PER_WINDOW
                        and loop_at - last_sampled[pair] >= r3.SAMPLE_COOLDOWN_SECONDS
                    )
                    if eligible:
                        assignment = assignments.next()
                        sampled[pair] += 1
                        last_sampled[pair] = loop_at
                        event["samplingEligibility"] = "ELIGIBLE_ASSIGNED"
                        event["assignment"] = assignment
                        event["assignedGroup"] = assignment["group"]
                        task = workers[pair].dispatch(
                            event=event,
                            assignment=assignment,
                            pm_feed=pm_feed,
                            dflow=dflow,
                            world_decimals=decimals,
                            pm_fee_schedule=pm_market.fee_schedule,
                            cash_decimals=cash_decimals,
                            http_timeout_seconds=args.http_timeout_seconds,
                            clock_offset_seconds=clock_offset_seconds,
                            window_end_ts=end_ts,
                        )
                        if task is None:
                            event.update({
                                "attemptStatus": "BUSY_SKIPPED",
                                "requestCountForEpisode": 0,
                                "economics": {"status": "UNKNOWN", "reason": "BUSY_SKIPPED"},
                                "timeout": False,
                                "connectionReuseVerified": "unknown",
                            })
                        else:
                            pending.append(task)
                elif not below:
                    active[pair] = False
            await asyncio.sleep(max(0.0, r3.RADAR_POLL_SECONDS - (time.time() - loop_at)))
    finally:
        results = await asyncio.gather(*pending, return_exceptions=True)
        by_id = {e["eventId"]: e for e in row["events"]}
        for result in results:
            if isinstance(result, Mapping):
                by_id[str(result["eventId"])].update(result)
        row["radarFrameCount"] = dflow.frame_count
        row["radarConnectionError"] = dflow.connection_error
        row["pmFeedDiagnostics"] = {
            "frameCount": pm_feed.frame_count,
            "reconnectCount": pm_feed.reconnect_count,
            "errorHistory": pm_feed.error_history,
            "rawQueueHighWatermark": pm_feed.raw_queue_high_watermark,
        }
        stop.set()
        dflow_task.cancel()
        pm_task.cancel()
        await asyncio.gather(dflow_task, pm_task, return_exceptions=True)
    row["persistentSessionDiagnosticsAfterWindow"] = {
        pair: worker.diagnostics() for pair, worker in workers.items()
    }

    row["healthyObservationSeconds"] = {
        pair: min(r23.WINDOW_SECONDS, count * r3.RADAR_POLL_SECONDS)
        for pair, count in row["healthyPollCount"].items()
    }
    row["completedAt"] = time.time()
    row["summary"] = _window_summary(row)
    return row


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    if args.windows < 1 or args.windows > MAX_WINDOWS_PER_BATCH:
        raise ValueError("windows outside allowed batch range")
    max_assignments = args.windows * 2 * r3.MAX_T0_SAMPLED_EPISODES_PER_PAIR_PER_WINDOW
    assignments = AssignmentCursor(args.seed, max_assignments, args.assignment_offset)
    workers = {
        "WORLD_YES+PM_DOWN": PairWorker("WORLD_YES+PM_DOWN"),
        "WORLD_NO+PM_UP": PairWorker("WORLD_NO+PM_UP"),
    }
    report: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "protocol": PROTOCOL,
        "mode": "PUBLIC_READ_ONLY_NO_TRADE",
        "baseQuoteGapCommit": "657e5a05e5e99c115714aaa53d79f0f73fe5ebde",
        "parentR1Commit": "38b64952c6f272855aff1be0ad3badc6f4df2235",
        "priorR1Stage1RunId": "35451921107",
        "globalBudgetConsumedBeforeBatch": 6,
        "seed": args.seed,
        "assignmentOffset": args.assignment_offset,
        "assignmentPlan": assignments.plan,
        "amountCash": AMOUNT_CASH,
        "requestedWindows": args.windows,
        "requestedFirstWindowStartTs": args.first_window_start_ts,
        "batchLabel": args.batch_label,
        "runtime": _runtime_environment(),
        "sourceSha256": _source_hashes(),
        "windows": [],
        "runState": "RUNNING",
    }
    _write_json(args.out, report)
    try:
        clock = await asyncio.to_thread(r23._calibrate_pm_clock)
        clock_offset = _finite(clock.get("medianServerMinusLocalSeconds"))
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
        report["firstWindowStartTs"] = first
        cash_decimals = await asyncio.to_thread(r2._retry_token_decimals, r23.wp.CASH_MINT, args.rpc_url)
        report["cashDecimals"] = cash_decimals
        for i in range(args.windows):
            try:
                row = await _run_window(
                    window_index=i + 1,
                    start_ts=first + i * r23.WINDOW_SECONDS,
                    args=args,
                    cash_decimals=cash_decimals,
                    clock_offset_seconds=clock_offset,
                    assignments=assignments,
                    workers=workers,
                )
            except Exception as exc:
                row = {
                    "windowIndex": i + 1,
                    "startTs": first + i * r23.WINDOW_SECONDS,
                    "endTs": first + (i + 1) * r23.WINDOW_SECONDS,
                    "error": f"{type(exc).__name__}:{exc}",
                    "events": [],
                    "summary": {"assignedCount": 0, "unknownCount": 0},
                }
            report["windows"].append(row)
            report["summary"] = _summary(report["windows"])
            checkpoint = args.out.with_name(f"{args.out.stem}.window-{i + 1:02d}{args.out.suffix}")
            _write_json(checkpoint, {
                "schemaVersion": SCHEMA_VERSION,
                "batchLabel": args.batch_label,
                "seed": args.seed,
                "assignmentOffset": args.assignment_offset,
                "window": row,
                "cumulativeSummary": report["summary"],
            })
            _write_json(args.out, report)
            print(json.dumps({
                "completedWindow": i + 1,
                "summary": report["summary"],
            }, sort_keys=True), flush=True)
        report["runState"] = "COMPLETED"
    except BaseException as exc:
        report["runState"] = "FAILED_OR_INTERRUPTED"
        report["error"] = f"{type(exc).__name__}:{exc}"
        raise
    finally:
        report["persistentSessionDiagnosticsAtBatchEnd"] = {
            pair: worker.diagnostics() for pair, worker in workers.items()
        }
        for worker in workers.values():
            worker.close()
        report["completedAt"] = time.time()
        report["assignmentsConsumed"] = assignments.index
        report["summary"] = _summary(report["windows"])
        _write_json(args.out, report)
    return report


def _self_test() -> None:
    a = _assignment_plan(7, 8)
    b = _assignment_plan(7, 8)
    assert a == b
    assert all(
        sorted(x["group"] for x in a[i:i + 4]) == ["fresh", "fresh", "persistent", "persistent"]
        for i in range(0, 8, 4)
    )

    route = {
        "venue": "venue-a",
        "marketKey": "market-1",
        "leg": {"inputMint": "A", "outputMint": "B", "inAmount": "10", "outAmount": "11"},
        "transaction": "DROP",
    }
    safe = _sanitize_public(route)
    assert "transaction" not in safe
    facts = _extract_route_facts(safe)
    assert facts["venueValues"] == ["venue-a"]
    assert facts["marketKeyValues"] == ["market-1"]
    assert facts["legs"]

    fee = {"rate": 0.07, "exponent": 1}
    quote = {
        "success": True,
        "inAmount": "10000000",
        "outputUiShares": 12.0,
        "minOutputUiShares": 10.0,
    }
    trigger_pm = {"asks": [{"price": 0.30, "size": 8.0}, {"price": 0.40, "size": 100.0}]}
    response_pm = {"asks": [{"price": 0.31, "size": 8.0}, {"price": 0.42, "size": 100.0}]}
    econ = _economics(
        quote=quote,
        trigger_pm=trigger_pm,
        response_pm=response_pm,
        fee_schedule=fee,
        integrity_verified=True,
        radar_indicative=0.99,
        cash_decimals=6,
    )
    assert econ["status"] == "QUALIFIED"
    assert econ["responseExpected"]["walk"]["targetNetShares"] == 12.0
    assert econ["responseMinOut"]["walk"]["targetNetShares"] == 10.0
    assert econ["responseExpected"]["pmCost"] != econ["responseMinOut"]["pmCost"]

    windows = [{
        "windowIndex": 1,
        "events": [
            {"windowIndex": 1, "assignedGroup": "fresh", "attemptStatus": "BUSY_SKIPPED", "economics": {"status": "UNKNOWN", "reason": "BUSY_SKIPPED"}},
            {"windowIndex": 1, "assignedGroup": "persistent", "attemptStatus": "UNKNOWN", "economics": {"status": "UNKNOWN", "reason": "REQUEST_OR_VERIFICATION_EXCEPTION"}},
        ],
    }]
    summary = _summary(windows)
    assert summary["groups"]["fresh"]["assigned"] == 1
    assert summary["groups"]["fresh"]["busySkipped"] == 1
    assert summary["groups"]["persistent"]["assigned"] == 1
    print("WORLD_PM_WORLD_SESSION_RANDOMIZED_R2_SELF_TEST_PASS")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--windows", type=int, default=6)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--assignment-offset", type=int, default=0)
    parser.add_argument("--first-window-start-ts", type=int)
    parser.add_argument("--batch-label", required=True)
    parser.add_argument("--rpc-url", default=r23.radar.DEFAULT_RPC)
    parser.add_argument("--world-discovery-timeout-seconds", type=float, default=90.0)
    parser.add_argument("--http-timeout-seconds", type=float, default=8.0)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        _self_test()
        return 0
    if args.out is None:
        parser.error("--out is required unless --self-test is used")
    if args.out.exists():
        parser.error("Output exists; use a new path to preserve evidence")
    asyncio.run(_run(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

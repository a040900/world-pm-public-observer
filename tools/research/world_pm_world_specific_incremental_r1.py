"""Prospective World-specific incremental lead diagnostic R1.

Tests whether World.xyz short-horizon repricing contains information about
Polymarket BTC 5m beyond same-time directional BTC movement jointly observed
on Binance and OKX.

Public read-only. NO_TRADE. No credentials, signing, orders, or capital.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import deque
import hashlib
import json
import math
import time
from pathlib import Path
from typing import Any, Mapping

import websockets

from tools.research import world_pm_edge_lifetime_r2 as r2
from tools.research import world_pm_qualification_runtime_r1 as qualification
from tools.research import world_pm_world_quote_gap_r1 as gap
from tools.research import world_polymarket_btc5m_executable_sync_r23 as r23

SCHEMA_VERSION = "WORLD_PM_WORLD_SPECIFIC_INCREMENTAL_LEAD_R1"
PROTOCOL_PATH = "docs/decisions/2026-09-28-world-pm-world-specific-incremental-lead-r1.md"
WINDOW_SECONDS = 300
WINDOW_COUNT = 6
POLL_SECONDS = 0.1
LOOKBACK_SECONDS = 1.0
FUTURE_HORIZONS_SECONDS = (1.0, 3.0, 5.0)
PRIMARY_HORIZON_SECONDS = 3.0
WORLD_THRESHOLDS = (0.0025, 0.0050, 0.0100, 0.0200)
EPISODE_COOLDOWN_SECONDS = 2.0
WORLD_POINT_MAX_OFFSET_SECONDS = 0.25
CEX_CURRENT_MAX_AGE_SECONDS = 0.5
CEX_LOOKBACK_MAX_OFFSET_SECONDS = 0.5
PM_SOURCE_FUTURE_TOLERANCE_MS = 250.0
PM_HEALTH_MAX_OFFSET_SECONDS = 0.25
BINANCE_WS = "wss://data-stream.binance.vision/ws/btcusdt@aggTrade"
OKX_WS = "wss://ws.okx.com:8443/ws/v5/public"
MAX_CEX_POINTS = 12000
MAX_WORLD_POINTS = 6000


def _finite(value: Any) -> float | None:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _sign(value: float) -> int:
    return 1 if value > 0 else -1 if value < 0 else 0


def _normalized_two_side(yes_or_up: float, no_or_down: float) -> float | None:
    if not math.isfinite(yes_or_up) or not math.isfinite(no_or_down):
        return None
    if yes_or_up <= 0 or no_or_down <= 0:
        return None
    total = yes_or_up + no_or_down
    return None if total <= 0 else yes_or_up / total


def _world_proxy(snapshot: Mapping[str, Any]) -> float | None:
    if snapshot.get("healthy") is not True:
        return None
    legs = []
    for side in ("yes", "no"):
        leg = snapshot.get(side)
        if not isinstance(leg, Mapping):
            return None
        ask = _finite(leg.get("ask"))
        age = _finite(leg.get("quoteAgeSeconds"))
        if (
            ask is None
            or ask <= 0
            or age is None
            or age < 0
            or age > r23.MAX_WORLD_RADAR_AGE_SECONDS
            or leg.get("error") not in (None, "", 0)
        ):
            return None
        legs.append(ask)
    return _normalized_two_side(legs[0], legs[1])


def _pm_proxy_from_states(rows: list[Mapping[str, Any]], *, cutoff: float) -> dict[str, Any]:
    mids = []
    effective_ats = []
    for row in rows:
        bids = row.get("bids")
        asks = row.get("asks")
        source = _finite(row.get("sourceTimestampMs"))
        effective_at = _finite(row.get("effectiveAt"))
        if (
            not isinstance(bids, list)
            or not bids
            or not isinstance(asks, list)
            or not asks
            or source is None
            or effective_at is None
            or effective_at > cutoff
        ):
            return {"valid": False, "reason": "PM_STATE_INVALID_OR_FUTURE_RECEIPT"}
        bid = max((_finite(level.get("price")) for level in bids if isinstance(level, Mapping)), default=None)
        ask = min((_finite(level.get("price")) for level in asks if isinstance(level, Mapping)), default=None)
        if bid is None or ask is None or bid <= 0 or ask <= 0 or bid > ask:
            return {"valid": False, "reason": "PM_BBO_INVALID"}
        mids.append((bid + ask) / 2.0)
        effective_ats.append(effective_at)
    generations = [int(row.get("connectionGeneration") or -1) for row in rows]
    if generations[0] < 0 or generations[0] != generations[1]:
        return {"valid": False, "reason": "PM_CONNECTION_GENERATION_MISMATCH", "generations": generations}
    proxy = _normalized_two_side(mids[0], mids[1])
    return {
        "valid": proxy is not None,
        "proxy": proxy,
        "cutoffAt": cutoff,
        "upMid": mids[0],
        "downMid": mids[1],
        "upEffectiveAt": effective_ats[0],
        "downEffectiveAt": effective_ats[1],
        "upSourceTimestampMs": rows[0].get("sourceTimestampMs"),
        "downSourceTimestampMs": rows[1].get("sourceTimestampMs"),
        "connectionGeneration": generations[0],
    }


def _pm_proxy_at(
    pm_feed: CausalTimelineBook,
    up_token: str,
    down_token: str,
    *,
    cutoff: float,
) -> dict[str, Any]:
    up = pm_feed.state_at(up_token, cutoff)
    down = pm_feed.state_at(down_token, cutoff)
    if up is None or down is None:
        return {"valid": False, "reason": "PM_STATE_MISSING_AT_CUTOFF", "cutoffAt": cutoff}
    health = pm_feed.health_at(cutoff)
    if health is None:
        return {"valid": False, "reason": "PM_HEALTH_MISSING_AT_CUTOFF", "cutoffAt": cutoff}
    health_offset = cutoff - float(health["receivedAt"])
    generation = int(up.get("connectionGeneration") or -1)
    if (
        health_offset < 0
        or health_offset > PM_HEALTH_MAX_OFFSET_SECONDS
        or health.get("healthy") is not True
        or int(health.get("connectionGeneration") or -2) != generation
    ):
        return {
            "valid": False,
            "reason": "PM_FEED_NOT_HEALTHY_AT_CUTOFF",
            "cutoffAt": cutoff,
            "healthOffsetSeconds": health_offset,
            "health": health,
        }
    result = _pm_proxy_from_states([up, down], cutoff=cutoff)
    result["healthAt"] = health
    result["healthOffsetSeconds"] = health_offset
    return result


def _at_or_before(points: deque[dict[str, Any]], target: float) -> dict[str, Any] | None:
    candidate = None
    for point in points:
        if float(point["receivedAt"]) > target:
            break
        candidate = point
    return candidate


class CausalTimelineBook(r2.RollingTimelineBook):
    """Preserve exact observer-time BBO states and an independent feed-liveness timeline."""

    def __init__(self, token_ids: list[str]) -> None:
        super().__init__(token_ids)
        self.health_history: deque[dict[str, Any]] = deque(maxlen=512)

    def _state_point(self, token: str, effective_at: float) -> dict[str, Any] | None:
        state = self.books.get(token)
        if not isinstance(state, Mapping) or state.get("ready") is not True:
            return None
        bids = dict(state.get("bids") or {})
        asks = dict(state.get("asks") or {})
        return {
            "effectiveAt": effective_at,
            "connectionGeneration": self.connection_generation,
            "eventCount": int(state.get("eventCount") or 0),
            "sourceTimestampMs": r23._to_int(state.get("sourceTimestampMs")),
            "localDigest": r23._book_digest(bids, asks),
            "bids": r23._book_rows(bids, asks=False),
            "asks": r23._book_rows(asks, asks=True),
        }

    def _record(self, token: str, at: float) -> None:
        point = self._state_point(token, at)
        if point is None:
            return
        rows = self.history[token]
        rows.append(point)
        cutoff = at - r2.HISTORY_SECONDS
        while len(rows) > 1 and float(rows[1].get("effectiveAt") or 0.0) < cutoff:
            rows.pop(0)

    def record_health(self, at: float, token_ids: tuple[str, str]) -> None:
        snapshots = [self.snapshot(token) for token in token_ids]
        generations = [int(row.get("connectionGeneration") or -1) for row in snapshots]
        self.health_history.append({
            "receivedAt": at,
            "healthy": all(row.get("healthy") is True for row in snapshots),
            "connectionGeneration": generations[0] if generations[0] >= 0 and generations[0] == generations[1] else -1,
        })

    def health_at(self, cutoff: float) -> dict[str, Any] | None:
        candidate = None
        for point in self.health_history:
            if float(point["receivedAt"]) > cutoff:
                break
            candidate = point
        if candidate is None:
            return None
        return dict(candidate)


class CexTape:
    def __init__(self) -> None:
        self.points = {
            "binance": deque(maxlen=MAX_CEX_POINTS),
            "okx": deque(maxlen=MAX_CEX_POINTS),
        }
        self.connected = {"binance": False, "okx": False}
        self.errors: dict[str, list[str]] = {"binance": [], "okx": []}
        self.message_count = {"binance": 0, "okx": 0}

    def _append(self, venue: str, price: Any, source_ts_ms: Any) -> None:
        value = _finite(price)
        if value is None or value <= 0:
            return
        now = time.time()
        self.points[venue].append(
            {
                "receivedAt": now,
                "price": value,
                "sourceTimestampMs": _finite(source_ts_ms),
            }
        )
        self.message_count[venue] += 1

    async def run_binance(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                async with websockets.connect(
                    BINANCE_WS,
                    open_timeout=10,
                    close_timeout=5,
                    ping_interval=20,
                    ping_timeout=20,
                    max_size=2_000_000,
                    max_queue=4096,
                ) as ws:
                    self.connected["binance"] = True
                    while not stop.is_set():
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                        except asyncio.TimeoutError:
                            continue
                        payload = json.loads(raw)
                        if isinstance(payload, Mapping):
                            self._append("binance", payload.get("p"), payload.get("T") or payload.get("E"))
            except Exception as exc:
                self.connected["binance"] = False
                self.errors["binance"].append(f"{type(exc).__name__}:{exc}")
                if not stop.is_set():
                    await asyncio.sleep(1.0)
        self.connected["binance"] = False

    async def run_okx(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                async with websockets.connect(
                    OKX_WS,
                    open_timeout=10,
                    close_timeout=5,
                    ping_interval=20,
                    ping_timeout=20,
                    max_size=2_000_000,
                    max_queue=4096,
                ) as ws:
                    await ws.send(
                        json.dumps(
                            {
                                "op": "subscribe",
                                "args": [{"channel": "trades", "instId": "BTC-USDT"}],
                            },
                            separators=(",", ":"),
                        )
                    )
                    self.connected["okx"] = True
                    while not stop.is_set():
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                        except asyncio.TimeoutError:
                            continue
                        payload = json.loads(raw)
                        if not isinstance(payload, Mapping):
                            continue
                        data = payload.get("data")
                        if not isinstance(data, list):
                            continue
                        for row in data:
                            if isinstance(row, Mapping):
                                self._append("okx", row.get("px"), row.get("ts"))
            except Exception as exc:
                self.connected["okx"] = False
                self.errors["okx"].append(f"{type(exc).__name__}:{exc}")
                if not stop.is_set():
                    await asyncio.sleep(1.0)
        self.connected["okx"] = False

    def venue_return(self, venue: str, at: float) -> dict[str, Any]:
        rows = self.points[venue]
        current = _at_or_before(rows, at)
        prior = _at_or_before(rows, at - LOOKBACK_SECONDS)
        if current is None or prior is None:
            return {"valid": False, "reason": "CEX_ANCHOR_MISSING"}
        current_age = at - float(current["receivedAt"])
        prior_offset = (at - LOOKBACK_SECONDS) - float(prior["receivedAt"])
        if (
            current_age < 0
            or current_age > CEX_CURRENT_MAX_AGE_SECONDS
            or prior_offset < 0
            or prior_offset > CEX_LOOKBACK_MAX_OFFSET_SECONDS
        ):
            return {
                "valid": False,
                "reason": "CEX_ANCHOR_STALE",
                "currentAgeSeconds": current_age,
                "lookbackOffsetSeconds": prior_offset,
            }
        move = math.log(float(current["price"]) / float(prior["price"]))
        return {
            "valid": True,
            "return": move,
            "sign": _sign(move),
            "currentPrice": current["price"],
            "priorPrice": prior["price"],
            "currentReceivedAt": current["receivedAt"],
            "priorReceivedAt": prior["receivedAt"],
            "currentSourceTimestampMs": current.get("sourceTimestampMs"),
            "priorSourceTimestampMs": prior.get("sourceTimestampMs"),
        }

    def consensus(self, at: float) -> dict[str, Any]:
        b = self.venue_return("binance", at)
        o = self.venue_return("okx", at)
        if b.get("valid") is not True or o.get("valid") is not True:
            return {"valid": False, "binance": b, "okx": o, "sign": 0}
        bs, os = int(b["sign"]), int(o["sign"])
        sign = bs if bs != 0 and bs == os else 0
        return {"valid": True, "binance": b, "okx": o, "sign": sign}


def _crossing_should_trigger(previous_above: bool | None, current_above: bool, elapsed_since_trigger: float) -> bool:
    return bool(
        current_above
        and previous_above is False
        and elapsed_since_trigger >= EPISODE_COOLDOWN_SECONDS
    )


def _conditioning_class(world_sign: int, cex_sign: int) -> str:
    if world_sign not in (-1, 1):
        return "UNSCORABLE"
    if cex_sign == 0:
        return "CEX_NO_CONSENSUS"
    if cex_sign == world_sign:
        return "WORLD_CEX_SAME"
    if cex_sign == -world_sign:
        return "WORLD_CEX_DISAGREE"
    return "UNSCORABLE"


def _score_future(base: float, future: float | None, world_sign: int, cex_sign: int) -> str:
    if future is None or not math.isfinite(future):
        return "UNSCORABLE"
    sign = _sign(future - base)
    if sign == 0:
        return "PM_FLAT"
    if sign == world_sign:
        return "PM_FOLLOWS_WORLD"
    if cex_sign != 0 and sign == cex_sign:
        return "PM_FOLLOWS_CEX"
    return "PM_OTHER"


def _point_near(points: deque[dict[str, Any]], target: float) -> dict[str, Any] | None:
    point = _at_or_before(points, target)
    if point is None:
        return None
    if target - float(point["observedAt"]) > WORLD_POINT_MAX_OFFSET_SECONDS:
        return None
    return point


async def _finish_episode(
    event: dict[str, Any],
    *,
    pm_feed: CausalTimelineBook,
    up_token: str,
    down_token: str,
    clock_offset_seconds: float | None,
    window_end_ts: int,
) -> dict[str, Any]:
    base = float(event["pmBaseProxy"])
    for horizon in FUTURE_HORIZONS_SECONDS:
        target = float(event["triggeredAt"]) + horizon
        if target >= window_end_ts:
            event["future"][str(int(horizon))] = {"score": "UNSCORABLE", "reason": "WINDOW_ENDED"}
            continue
        wait = target - time.time()
        if wait > 0:
            await asyncio.sleep(wait)
        row = _pm_proxy_at(pm_feed, up_token, down_token, cutoff=target)
        if row.get("valid") is not True:
            event["future"][str(int(horizon))] = {"score": "UNSCORABLE", "pm": row}
            continue
        value = _finite(row.get("proxy"))
        event["future"][str(int(horizon))] = {
            "score": _score_future(base, value, int(event["worldSign"]), int(event["cexConsensusSign"])),
            "pm": row,
            "delta": None if value is None else value - base,
        }
    return event


def _classify_episode(
    *,
    threshold: float,
    at: float,
    world_current: float,
    world_prior: float,
    cex: Mapping[str, Any],
    pm: Mapping[str, Any],
    window_index: int,
    sequence: int,
) -> dict[str, Any] | None:
    delta = world_current - world_prior
    if abs(delta) < threshold:
        return None
    world_sign = _sign(delta)
    if cex.get("valid") is not True or pm.get("valid") is not True:
        return None
    cex_sign = int(cex.get("sign") or 0)
    cls = _conditioning_class(world_sign, cex_sign)
    return {
        "eventId": f"w{window_index}-t{threshold:.4f}-e{sequence}",
        "windowIndex": window_index,
        "threshold": threshold,
        "triggeredAt": at,
        "worldCurrentProxy": world_current,
        "worldPriorProxy": world_prior,
        "worldMove": delta,
        "worldSign": world_sign,
        "cexConsensusSign": cex_sign,
        "conditioningClass": cls,
        "cex": dict(cex),
        "pmBaseProxy": pm.get("proxy"),
        "pmBase": dict(pm),
        "future": {},
    }


def _continuation_gate(threshold_rows: list[dict[str, Any]]) -> dict[str, Any]:
    disagreement = [x for x in threshold_rows if x.get("conditioningClass") == "WORLD_CEX_DISAGREE"]
    scored = []
    windows = set()
    for row in disagreement:
        h = (row.get("future") or {}).get("3") or {}
        score = h.get("score")
        if score in {"PM_FOLLOWS_WORLD", "PM_FOLLOWS_CEX", "PM_FLAT", "PM_OTHER"}:
            scored.append(score)
            windows.add(row.get("windowIndex"))
    nonflat = [x for x in scored if x in {"PM_FOLLOWS_WORLD", "PM_FOLLOWS_CEX", "PM_OTHER"}]
    world_n = sum(x == "PM_FOLLOWS_WORLD" for x in nonflat)
    cex_n = sum(x == "PM_FOLLOWS_CEX" for x in nonflat)
    world_rate = None if not nonflat else world_n / len(nonflat)
    cex_rate = None if not nonflat else cex_n / len(nonflat)

    def opposite_majority(horizon: str) -> bool:
        labels = []
        for row in disagreement:
            score = ((row.get("future") or {}).get(horizon) or {}).get("score")
            if score in {"PM_FOLLOWS_WORLD", "PM_FOLLOWS_CEX"}:
                labels.append(score)
        if not labels:
            return False
        return sum(x == "PM_FOLLOWS_CEX" for x in labels) > len(labels) / 2

    passed = bool(
        len(scored) >= 20
        and len(windows) >= 4
        and world_rate is not None
        and cex_rate is not None
        and world_rate >= 0.60
        and world_rate - cex_rate >= 0.10
        and not opposite_majority("1")
        and not opposite_majority("5")
    )
    return {
        "scorableDisagreementAt3s": len(scored),
        "independentWindowCount": len(windows),
        "nonFlatAt3s": len(nonflat),
        "worldFollowCountAt3s": world_n,
        "cexFollowCountAt3s": cex_n,
        "worldFollowRateAt3sNonFlat": world_rate,
        "cexFollowRateAt3sNonFlat": cex_rate,
        "oppositeMajorityAt1s": opposite_majority("1"),
        "oppositeMajorityAt5s": opposite_majority("5"),
        "continuationGatePassed": passed,
    }


def _summary(windows: list[dict[str, Any]]) -> dict[str, Any]:
    events = [e for w in windows for e in w.get("events", [])]
    by_threshold: dict[str, Any] = {}
    for threshold in WORLD_THRESHOLDS:
        rows = [e for e in events if e.get("threshold") == threshold]
        classes: dict[str, Any] = {}
        for cls in ("WORLD_CEX_SAME", "WORLD_CEX_DISAGREE", "CEX_NO_CONSENSUS"):
            selected = [e for e in rows if e.get("conditioningClass") == cls]
            horizon_counts = {}
            for horizon in FUTURE_HORIZONS_SECONDS:
                key = str(int(horizon))
                counts: dict[str, int] = {}
                for event in selected:
                    label = ((event.get("future") or {}).get(key) or {}).get("score", "UNSCORABLE")
                    counts[label] = counts.get(label, 0) + 1
                horizon_counts[key] = counts
            classes[cls] = {
                "episodeCount": len(selected),
                "independentWindowCount": len({e.get("windowIndex") for e in selected}),
                "future": horizon_counts,
                "perWindow": {
                    str(w["windowIndex"]): sum(
                        e.get("windowIndex") == w["windowIndex"] and e.get("conditioningClass") == cls
                        for e in rows
                    )
                    for w in windows
                },
            }
        by_threshold[f"{threshold:.4f}"] = {
            "episodeCount": len(rows),
            "classes": classes,
            "continuation": _continuation_gate(rows),
        }

    valid_windows = sum(w.get("qualificationValid") is True for w in windows)
    passed_thresholds = [
        key for key, value in by_threshold.items()
        if value["continuation"]["continuationGatePassed"] is True
    ]
    return {
        "requestedWindowCount": WINDOW_COUNT,
        "observedWindowCount": len(windows),
        "validWindowCount": valid_windows,
        "invalidWindowCount": len(windows) - valid_windows,
        "eventCount": len(events),
        "byThreshold": by_threshold,
        "continuationCandidateThresholds": passed_thresholds,
        "verdict": (
            "R1_CANDIDATE_GENERATION_GATE_MET_REQUIRES_UNTOUCHED_R2"
            if valid_windows == WINDOW_COUNT and passed_thresholds
            else "R1_NO_PROMOTION"
            if valid_windows == WINDOW_COUNT
            else "R1_NO_RESULT_INCOMPLETE_EXECUTION"
        ),
    }


async def _run_window(
    *,
    window_index: int,
    start_ts: int,
    args: argparse.Namespace,
    clock_offset_seconds: float | None,
) -> dict[str, Any]:
    end_ts = start_ts + WINDOW_SECONDS
    if time.time() < start_ts:
        await asyncio.sleep(start_ts - time.time())

    row: dict[str, Any] = {
        "windowIndex": window_index,
        "startTs": start_ts,
        "endTs": end_ts,
        "events": [],
        "errors": [],
        "healthyOverlapPollCount": 0,
        "measurementLoopCompleted": False,
    }

    try:
        pm_market = await asyncio.to_thread(r23.wp.fetch_polymarket_market, start_ts)
        row["pmMarket"] = {
            "conditionId": pm_market.condition_id,
            "upToken": pm_market.up_token,
            "downToken": pm_market.down_token,
        }
    except Exception as exc:
        row["errors"].append(f"PM_DISCOVERY:{type(exc).__name__}:{exc}")
        row["qualificationValid"] = False
        return row

    stop = asyncio.Event()
    pm_feed = CausalTimelineBook([pm_market.up_token, pm_market.down_token])
    pm_task = asyncio.create_task(pm_feed.run(stop))

    try:
        world_market = await asyncio.to_thread(
            r23.radar._discover_world_market,
            start_ts,
            args.rpc_url,
            min(float(end_ts), time.time() + args.world_discovery_timeout_seconds),
        )
        row["worldMarket"] = {
            "market": world_market.market,
            "yesMint": world_market.yes_mint,
            "noMint": world_market.no_mint,
        }
    except Exception as exc:
        row["errors"].append(f"WORLD_DISCOVERY:{type(exc).__name__}:{exc}")
        stop.set()
        pm_task.cancel()
        await asyncio.gather(pm_task, return_exceptions=True)
        row["qualificationValid"] = False
        return row

    dflow = gap.GapRadar(world_market.yes_mint, world_market.no_mint)
    cex = CexTape()
    world_task = asyncio.create_task(dflow.run(stop))
    binance_task = asyncio.create_task(cex.run_binance(stop))
    okx_task = asyncio.create_task(cex.run_okx(stop))

    world_points: deque[dict[str, Any]] = deque(maxlen=MAX_WORLD_POINTS)
    # None means crossing continuity is unknown after startup or any unhealthy poll.\n    above: dict[float, bool | None] = {threshold: None for threshold in WORLD_THRESHOLDS}
    last_trigger = {threshold: -float("inf") for threshold in WORLD_THRESHOLDS}
    sequence = {threshold: 0 for threshold in WORLD_THRESHOLDS}
    pending: list[asyncio.Task[dict[str, Any]]] = []

    try:
        while time.time() < end_ts:
            poll_started_at = time.time()
            world_snapshot = dflow.snapshot(poll_started_at)
            world_value = _world_proxy(world_snapshot)
            anchor_at = time.time()
            pm_feed.record_health(anchor_at, (pm_market.up_token, pm_market.down_token))
            pm_value = _pm_proxy_at(
                pm_feed,
                pm_market.up_token,
                pm_market.down_token,
                cutoff=anchor_at,
            )
            cex_state = cex.consensus(anchor_at)
            if world_value is not None:
                world_points.append({"observedAt": anchor_at, "receivedAt": anchor_at, "proxy": world_value})

            prior = _point_near(world_points, anchor_at - LOOKBACK_SECONDS)
            healthy = (
                world_value is not None
                and prior is not None
                and pm_value.get("valid") is True
                and cex_state.get("valid") is True
            )
            if healthy:
                row["healthyOverlapPollCount"] += 1
                world_prior = float(prior["proxy"])
                delta = world_value - world_prior
                for threshold in WORLD_THRESHOLDS:
                    is_above = abs(delta) >= threshold
                    if _crossing_should_trigger(
                        above[threshold],
                        is_above,
                        anchor_at - last_trigger[threshold],
                    ):
                        sequence[threshold] += 1
                        event = _classify_episode(
                            threshold=threshold,
                            at=anchor_at,
                            world_current=world_value,
                            world_prior=world_prior,
                            cex=cex_state,
                            pm=pm_value,
                            window_index=window_index,
                            sequence=sequence[threshold],
                        )
                        if event is not None:
                            row["events"].append(event)
                            pending.append(
                                asyncio.create_task(
                                    _finish_episode(
                                        event,
                                        pm_feed=pm_feed,
                                        up_token=pm_market.up_token,
                                        down_token=pm_market.down_token,
                                        clock_offset_seconds=clock_offset_seconds,
                                        window_end_ts=end_ts,
                                    )
                                )
                            )
                            last_trigger[threshold] = anchor_at
                    above[threshold] = is_above
            else:
                # A crossing cannot be established across a period where the four-source
                # conditioning state was unavailable. Re-arm only after a healthy
                # below-threshold observation is seen.
                for threshold in WORLD_THRESHOLDS:
                    above[threshold] = None

            await asyncio.sleep(max(0.0, POLL_SECONDS - (time.time() - poll_started_at)))

        row["measurementLoopCompleted"] = True
    finally:
        finished = await asyncio.gather(*pending, return_exceptions=True)
        by_id = {e["eventId"]: e for e in row["events"]}
        for item in finished:
            if isinstance(item, Mapping) and item.get("eventId") in by_id:
                by_id[str(item["eventId"])].update(item)
            elif isinstance(item, Exception):
                row["errors"].append(f"EPISODE:{type(item).__name__}:{item}")

        row["collectorDiagnostics"] = {
            "worldFrameCount": dflow.frame_count,
            "worldConnectionError": dflow.connection_error,
            "pmFrameCount": pm_feed.frame_count,
            "pmReconnectCount": pm_feed.reconnect_count,
            "pmErrors": pm_feed.error_history,
            "cexMessageCount": cex.message_count,
            "cexErrors": cex.errors,
        }
        stop.set()
        for task in (world_task, binance_task, okx_task, pm_task):
            task.cancel()
        await asyncio.gather(world_task, binance_task, okx_task, pm_task, return_exceptions=True)

    row["qualificationValid"] = bool(
        row.get("worldMarket")
        and row.get("pmMarket")
        and row.get("measurementLoopCompleted") is True
        and row.get("healthyOverlapPollCount", 0) > 0
        and cex.message_count["binance"] > 0
        and cex.message_count["okx"] > 0
    )
    return row


def _save(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    temp.replace(path)


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    clock = await asyncio.to_thread(r23._calibrate_pm_clock)
    offset = _finite(clock.get("medianServerMinusLocalSeconds"))
    server_now = _finite(clock.get("serverNowEstimate")) or time.time()
    schedule = qualification.select_schedule(server_now, args.first_window_start_ts)

    report: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "mode": "PUBLIC_READ_ONLY_NO_TRADE",
        "protocolAuthority": args.protocol_authority,
        "sourceCommitRecordedByWorkflow": args.source_commit,
        "observerTimeSemantics": {
            "crossSourceAvailabilityAuthority": "LOCAL_RECEIPT_TIME_ONLY",
            "sourceTimestamps": "METADATA_AND_STALENESS_ONLY",
            "futurePmRule": "LATEST_PM_STATE_WITH_EFFECTIVE_AT_LE_CUTOFF_AND_FEED_HEALTHY_AT_CUTOFF",
            "pmFreshnessRule": "BOOK_STATE_AGE_DOES_NOT_IMPLY_FEED_STALENESS; LIVENESS_IS_TRACKED_SEPARATELY",
            "futureReceiptLeakageAllowed": False,
        },
        "clockCalibration": clock,
        "transports": {"binance": BINANCE_WS, "okx": OKX_WS},
        "schedule": schedule,
        "thresholds": list(WORLD_THRESHOLDS),
        "lookbackSeconds": LOOKBACK_SECONDS,
        "futureHorizonsSeconds": list(FUTURE_HORIZONS_SECONDS),
        "windows": [],
        "runState": "RUNNING",
        "sourceSha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    _save(args.out, report)

    first = int(schedule["firstWindowStartTs"])
    try:
        for i in range(args.windows):
            start = first + i * WINDOW_SECONDS
            try:
                row = await _run_window(
                    window_index=i + 1,
                    start_ts=start,
                    args=args,
                    clock_offset_seconds=offset,
                )
            except Exception as exc:
                row = {
                    "windowIndex": i + 1,
                    "startTs": start,
                    "endTs": start + WINDOW_SECONDS,
                    "events": [],
                    "qualificationValid": False,
                    "errors": [f"WINDOW:{type(exc).__name__}:{exc}"],
                }
            report["windows"].append(row)
            report["summary"] = _summary(report["windows"])
            _save(args.out, report)
            print(
                json.dumps(
                    {
                        "completedWindows": len(report["windows"]),
                        "latestWindowValid": row.get("qualificationValid"),
                        "summary": report["summary"],
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
        report["runState"] = "COMPLETED"
    except BaseException as exc:
        report["runState"] = "FAILED_OR_INTERRUPTED"
        report["fatalError"] = f"{type(exc).__name__}:{exc}"
        raise
    finally:
        report["completedAt"] = time.time()
        report["summary"] = _summary(report["windows"])
        _save(args.out, report)
    return report


def _self_test() -> None:
    assert abs(_normalized_two_side(0.6, 0.4) - 0.6) < 1e-12
    assert _conditioning_class(1, -1) == "WORLD_CEX_DISAGREE"
    assert _conditioning_class(-1, -1) == "WORLD_CEX_SAME"
    assert _conditioning_class(1, 0) == "CEX_NO_CONSENSUS"
    assert _score_future(0.5, 0.51, 1, -1) == "PM_FOLLOWS_WORLD"
    assert _score_future(0.5, 0.49, 1, -1) == "PM_FOLLOWS_CEX"
    assert _score_future(0.5, 0.5, 1, -1) == "PM_FLAT"
    synthetic = []
    for window in range(1, 5):
        for i in range(5):
            synthetic.append(
                {
                    "windowIndex": window,
                    "conditioningClass": "WORLD_CEX_DISAGREE",
                    "future": {
                        "1": {"score": "PM_FOLLOWS_WORLD"},
                        "3": {"score": "PM_FOLLOWS_WORLD" if i < 4 else "PM_FOLLOWS_CEX"},
                        "5": {"score": "PM_FOLLOWS_WORLD"},
                    },
                }
            )
    gate = _continuation_gate(synthetic)
    assert gate["scorableDisagreementAt3s"] == 20
    assert gate["independentWindowCount"] == 4
    assert gate["continuationGatePassed"] is True
    print("WORLD_PM_WORLD_SPECIFIC_INCREMENTAL_LEAD_R1_SELF_TEST_PASS")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rpc-url", default=r23.radar.DEFAULT_RPC)
    parser.add_argument("--world-discovery-timeout-seconds", type=float, default=60.0)
    parser.add_argument("--windows", type=int, choices=range(1, WINDOW_COUNT + 1), default=WINDOW_COUNT)
    parser.add_argument("--first-window-start-ts", type=int)
    parser.add_argument("--protocol-authority", required=False, default="UNBOUND_FOR_LOCAL_SELF_TEST_ONLY")
    parser.add_argument("--source-commit", required=False, default="UNRECORDED")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        _self_test()
        return 0
    if args.out is None:
        parser.error("--out is required unless --self-test is used")
    if args.protocol_authority == "UNBOUND_FOR_LOCAL_SELF_TEST_ONLY":
        parser.error("--protocol-authority is required for prospective capture")
    if args.out.exists():
        parser.error("output exists; use a new path to preserve evidence")
    asyncio.run(_run(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Balanced response-anchored World.xyz <-> Polymarket BTC5M edge lifetime R2.

Implements frozen protocol:
docs/decisions/2026-09-17-world-pm-edge-lifetime-r2-protocol.json

Radar is trigger-only. For each radar episode, six independent exact probes are
scheduled at T+0/50/100/150/250/500ms from the trigger. Primary classification
uses the PM executable state active when the World exact-size quote response is
received. R2.4 worst-state-over-request-interval is retained as a conservative
control only.

Public read-only. NO_TRADE.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from typing import Any, Mapping

import requests

from tools.research import world_polymarket_btc5m_executable_sync_r23 as r23
from tools.research import world_polymarket_btc5m_executable_sync_r24 as r24

SCHEMA_VERSION = "WORLD_PM_EDGE_LIFETIME_R2"
PROBE_OFFSETS_MS = (0, 50, 100, 150, 250, 500)
RADAR_POLL_SECONDS = 0.05
WINDOW_COUNT = 6
HISTORY_SECONDS = 8.0


class RollingTimelineBook(r24.PolymarketTimelineBook):
    def __init__(self, token_ids: list[str]) -> None:
        super().__init__(token_ids)
        self.history: dict[str, list[dict[str, Any]]] = {token: [] for token in token_ids}

    def _record(self, token: str, at: float) -> None:
        point = self._state_point(token, at)
        if point is None:
            return
        rows = self.history[token]
        if rows and rows[-1].get("localDigest") == point.get("localDigest"):
            rows[-1] = point
        else:
            rows.append(point)
        cutoff = at - HISTORY_SECONDS
        while len(rows) > 1 and float(rows[1].get("effectiveAt") or 0.0) < cutoff:
            rows.pop(0)

    def _apply_book(self, message: Mapping[str, Any], received_at: float) -> None:
        super()._apply_book(message, received_at)
        token = str(message.get("asset_id") or "")
        if token in self.history:
            self._record(token, received_at)

    def _apply_price_change(self, message: Mapping[str, Any], received_at: float) -> None:
        touched = {str(change.get("asset_id") or "") for change in (message.get("price_changes") or []) if isinstance(change, Mapping)}
        super()._apply_price_change(message, received_at)
        for token in touched:
            if token in self.history:
                self._record(token, received_at)

    def state_at(self, token: str, at: float, generation: int | None = None) -> dict[str, Any] | None:
        candidate = None
        for point in self.history.get(token, []):
            if float(point.get("effectiveAt") or 0.0) > at:
                break
            if generation is not None and int(point.get("connectionGeneration") or -1) != generation:
                continue
            candidate = point
        return None if candidate is None else dict(candidate)

    def states_between(self, token: str, start: float, end: float, generation: int | None = None) -> list[dict[str, Any]]:
        rows = self.history.get(token, [])
        baseline = self.state_at(token, start, generation)
        out: list[dict[str, Any]] = []
        if baseline is not None:
            out.append(baseline)
        for point in rows:
            at = float(point.get("effectiveAt") or 0.0)
            if at <= start or at > end:
                continue
            if generation is not None and int(point.get("connectionGeneration") or -1) != generation:
                continue
            out.append(dict(point))
        return r24._dedup_points(out)


def _retry_token_decimals(mint: str, rpc_url: str, attempts: int = 6) -> int:
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return r23._token_decimals(mint, rpc_url)
        except Exception as exc:
            last = exc
            if attempt < attempts:
                time.sleep(min(12.0, 2.0 ** (attempt - 1)))
    raise RuntimeError(f"TOKEN_DECIMALS_RETRY_EXHAUSTED:{mint}:{last}")


def _classify_primary(*, world_quote: Mapping[str, Any], pm_state: Mapping[str, Any] | None, rest_ok: bool, fee_schedule: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {"measurementQualified": False, "belowOne": False, "reason": "UNKNOWN"}
    if world_quote.get("success") is not True:
        result["reason"] = "WORLD_QUOTE_FAILED"
        return result
    if pm_state is None:
        result["reason"] = "PM_RESPONSE_ANCHOR_STATE_MISSING"
        return result
    if not rest_ok:
        result["reason"] = "REST_RECONCILIATION_FAILED"
        return result
    target = r23._to_float(world_quote.get("minOutputUiShares"))
    asks = pm_state.get("asks")
    if target is None or target <= 0:
        result["reason"] = "WORLD_MINOUT_INVALID"
        return result
    if not isinstance(asks, list) or not asks:
        result["reason"] = "PM_DEPTH_INVALID"
        return result
    walk = r23._walk_pm_book_for_net_shares(asks, target, fee_schedule)
    if walk.get("filled") is not True:
        result["reason"] = "PM_DEPTH_INSUFFICIENT"
        result["pmWalk"] = walk
        return result
    total_cost = r23.PRIMARY_SIZE_CASH + float(walk["cost"])
    unit = total_cost / target
    result.update({
        "measurementQualified": True,
        "reason": "RESPONSE_ANCHORED_EXECUTION_LIKE_STATE",
        "worldMinOutShares": target,
        "totalAcquisitionCostNominal": total_cost,
        "unitCostPerConditionalPayout": unit,
        "conditionalSpreadNominal": target - total_cost,
        "belowOne": unit < 1.0,
        "pmWalk": walk,
    })
    return result


def _classify_control(*, world_quote: Mapping[str, Any], interval_states: list[dict[str, Any]], rest_ok: bool, rest_digest: str | None, fee_schedule: dict[str, Any]) -> dict[str, Any]:
    return r24._evaluate_interval(
        world_quote=world_quote,
        interval_states=interval_states,
        rest_match=rest_ok,
        rest_digest=rest_digest,
        fee_schedule=fee_schedule,
        base_reason="INTERVAL_READY" if interval_states else "PM_INTERVAL_STATE_COVERAGE_MISSING",
    )


async def _probe(*, event: dict[str, Any], offset_ms: int, world_session: requests.Session, pm_session: requests.Session, pm_feed: RollingTimelineBook, world_decimals: int, pm_fee_schedule: dict[str, Any], cash_decimals: int, http_timeout_seconds: float, clock_offset_seconds: float | None, window_end_ts: int) -> dict[str, Any]:
    target_start = float(event["triggeredAt"]) + offset_ms / 1000.0
    wait = target_start - time.time()
    if wait > 0:
        await asyncio.sleep(wait)
    actual_start = time.time()
    if actual_start >= window_end_ts:
        return {"offsetMs": offset_ms, "scheduledStartAt": target_start, "actualStartAt": actual_start, "skipped": "WINDOW_ENDED"}

    pre = pm_feed.snapshot(str(event["pmToken"]), clock_offset_seconds=clock_offset_seconds)
    generation = int(pre.get("connectionGeneration") or -1)
    world_task = asyncio.to_thread(
        r23._world_anonymous_quote,
        world_session,
        output_mint=str(event["worldMint"]),
        cash_decimals=cash_decimals,
        outcome_decimals=world_decimals,
        timeout_seconds=http_timeout_seconds,
    )
    rest_task = asyncio.to_thread(r23._fetch_pm_rest_book, pm_session, str(event["pmToken"]), http_timeout_seconds)
    gathered = await asyncio.gather(world_task, rest_task, return_exceptions=True)
    world_quote = gathered[0] if not isinstance(gathered[0], Exception) else {"success": False, "exception": f"{type(gathered[0]).__name__}:{gathered[0]}"}
    pm_rest = gathered[1] if not isinstance(gathered[1], Exception) else None
    if isinstance(pm_rest, Mapping):
        await asyncio.sleep(r24.REST_RECONCILE_GRACE_SECONDS)
    post = pm_feed.snapshot(str(event["pmToken"]), clock_offset_seconds=clock_offset_seconds)

    response_at = r23._to_float((world_quote if isinstance(world_quote, Mapping) else {}).get("receivedAt"))
    request_at = r23._to_float((world_quote if isinstance(world_quote, Mapping) else {}).get("requestStartedAt"))
    same_generation = pre.get("healthy") is True and post.get("healthy") is True and generation == int(post.get("connectionGeneration") or -2)
    response_state = None if response_at is None or not same_generation else pm_feed.state_at(str(event["pmToken"]), response_at, generation)

    rest_ok = False
    rest_digest = None
    if isinstance(pm_rest, Mapping) and same_generation:
        rest_digest = str(pm_rest.get("localDigest") or "") or None
        rs = r23._to_float(pm_rest.get("requestStartedAt"))
        re = r23._to_float(pm_rest.get("receivedAt"))
        if rs is not None and re is not None and rest_digest:
            states = pm_feed.states_between(str(event["pmToken"]), rs, re + r24.REST_RECONCILE_GRACE_SECONDS, generation)
            rest_ok = any(state.get("localDigest") == rest_digest for state in states)

    if not same_generation:
        primary = {"measurementQualified": False, "belowOne": False, "reason": "PM_TRANSPORT_INVALID"}
        control = dict(primary)
        interval_states: list[dict[str, Any]] = []
    else:
        primary = _classify_primary(world_quote=world_quote if isinstance(world_quote, Mapping) else {}, pm_state=response_state, rest_ok=rest_ok, fee_schedule=pm_fee_schedule)
        interval_states = [] if request_at is None or response_at is None else pm_feed.states_between(str(event["pmToken"]), request_at, response_at, generation)
        control = _classify_control(world_quote=world_quote if isinstance(world_quote, Mapping) else {}, interval_states=interval_states, rest_ok=rest_ok, rest_digest=rest_digest, fee_schedule=pm_fee_schedule)

    return {
        "offsetMs": offset_ms,
        "scheduledStartAt": target_start,
        "actualStartAt": actual_start,
        "startDelayFromTriggerMs": (actual_start - float(event["triggeredAt"])) * 1000.0,
        "worldResponseAt": response_at,
        "worldResponseDelayFromTriggerMs": None if response_at is None else (response_at - float(event["triggeredAt"])) * 1000.0,
        "worldQuoteElapsedMs": (world_quote if isinstance(world_quote, Mapping) else {}).get("elapsedMs"),
        "pmRestElapsedMs": None if not isinstance(pm_rest, Mapping) else pm_rest.get("elapsedMs"),
        "pmSameGenerationHealthy": same_generation,
        "restReconciled": rest_ok,
        "responseAnchorStateDigest": None if response_state is None else response_state.get("localDigest"),
        "intervalStateCount": len(interval_states),
        "primary": primary,
        "conservativeControl": control,
    }


async def _run_window(*, window_index: int, start_ts: int, args: argparse.Namespace, cash_decimals: int, clock_offset_seconds: float | None) -> dict[str, Any]:
    end_ts = start_ts + r23.WINDOW_SECONDS
    if time.time() < start_ts:
        await asyncio.sleep(start_ts - time.time())
    pm_market = await asyncio.to_thread(r23.wp.fetch_polymarket_market, start_ts)
    stop = asyncio.Event()
    pm_feed = RollingTimelineBook([pm_market.up_token, pm_market.down_token])
    pm_task = asyncio.create_task(pm_feed.run(stop))
    try:
        world_market = await asyncio.to_thread(r23.radar._discover_world_market, start_ts, args.rpc_url, min(float(end_ts), time.time() + args.world_discovery_timeout_seconds))
        world_error = None
    except Exception as exc:
        world_market = None
        world_error = f"{type(exc).__name__}:{exc}"

    row: dict[str, Any] = {"windowIndex": window_index, "startTs": start_ts, "endTs": end_ts, "worldDiscoveryError": world_error, "radarPollCount": 0, "radarEpisodeCount": {"WORLD_YES+PM_DOWN": 0, "WORLD_NO+PM_UP": 0}, "events": []}
    if world_market is None:
        while time.time() < end_ts:
            await asyncio.sleep(min(1.0, max(0.0, end_ts - time.time())))
        stop.set(); pm_task.cancel(); await asyncio.gather(pm_task, return_exceptions=True)
        return row

    yes_decimals, no_decimals = await asyncio.gather(
        asyncio.to_thread(_retry_token_decimals, world_market.yes_mint, args.rpc_url),
        asyncio.to_thread(_retry_token_decimals, world_market.no_mint, args.rpc_url),
    )
    dflow = r23.RobustDflowRadar(world_market.yes_mint, world_market.no_mint)
    dflow_task = asyncio.create_task(dflow.run(stop))
    world_sessions = [requests.Session() for _ in range(12)]
    pm_sessions = [requests.Session() for _ in range(12)]
    pending: list[tuple[dict[str, Any], list[asyncio.Task[dict[str, Any]]]]] = []
    active = {"WORLD_YES+PM_DOWN": False, "WORLD_NO+PM_UP": False}
    event_seq = 0
    candidates = [
        ("WORLD_YES+PM_DOWN", "yes", world_market.yes_mint, yes_decimals, pm_market.down_token),
        ("WORLD_NO+PM_UP", "no", world_market.no_mint, no_decimals, pm_market.up_token),
    ]
    try:
        while time.time() < end_ts:
            loop_at = time.time(); row["radarPollCount"] += 1; world_snapshot = dflow.snapshot(loop_at)
            for pair_index, (pair, world_side, world_mint, world_decimals, pm_token) in enumerate(candidates):
                pm_snapshot = pm_feed.snapshot(pm_token, clock_offset_seconds=clock_offset_seconds)
                leg = world_snapshot.get(world_side) or {}; world_ask = r23._to_float(leg.get("ask")); age = r23._to_float(leg.get("quoteAgeSeconds")); pm_ask = r23._to_float(pm_snapshot.get("bestAsk"))
                below = False; indicative_sum = None
                if pm_snapshot.get("healthy") is True and world_snapshot.get("healthy") is True and world_ask is not None and pm_ask is not None and age is not None and age <= r23.MAX_WORLD_RADAR_AGE_SECONDS:
                    pm_effective = r23._pm_effective_top_cost(pm_ask, pm_market.fee_schedule)
                    if pm_effective is not None:
                        indicative_sum = world_ask + pm_effective; below = indicative_sum < 1.0
                if below and not active[pair]:
                    active[pair] = True; event_seq += 1; row["radarEpisodeCount"][pair] += 1
                    event = {"eventId": f"w{window_index}-e{event_seq}", "pair": pair, "triggeredAt": loop_at, "worldMint": world_mint, "pmToken": pm_token, "indicativeSum": indicative_sum, "worldIndicativeAsk": world_ask, "pmLocalBestAsk": pm_ask}
                    tasks = []
                    for i, offset in enumerate(PROBE_OFFSETS_MS):
                        session_index = pair_index * 6 + i
                        tasks.append(asyncio.create_task(_probe(event=event, offset_ms=offset, world_session=world_sessions[session_index], pm_session=pm_sessions[session_index], pm_feed=pm_feed, world_decimals=world_decimals, pm_fee_schedule=pm_market.fee_schedule, cash_decimals=cash_decimals, http_timeout_seconds=args.http_timeout_seconds, clock_offset_seconds=clock_offset_seconds, window_end_ts=end_ts)))
                    pending.append((event, tasks))
                elif not below:
                    active[pair] = False
            sleep_for = RADAR_POLL_SECONDS - (time.time() - loop_at)
            if sleep_for > 0: await asyncio.sleep(sleep_for)
    finally:
        for event, tasks in pending:
            probes = await asyncio.gather(*tasks, return_exceptions=True)
            clean = []
            for probe in probes:
                clean.append({"exception": f"{type(probe).__name__}:{probe}"} if isinstance(probe, Exception) else probe)
            exact = [p for p in clean if ((p.get("primary") or {}).get("measurementQualified") is True and (p.get("primary") or {}).get("belowOne") is True)]
            event_row = dict(event); event_row["probes"] = clean
            response_delays = [float(p["worldResponseDelayFromTriggerMs"]) for p in exact if p.get("worldResponseDelayFromTriggerMs") is not None]
            event_row["lifetime"] = {
                "exactBelowOneProbeCount": len(exact),
                "firstExactBelowOneResponseMs": min(response_delays) if response_delays else None,
                "lastExactBelowOneResponseMs": max(response_delays) if response_delays else None,
                "observedExactBelowOneSpanMs": (max(response_delays) - min(response_delays)) if len(response_delays) >= 2 else (0.0 if len(response_delays) == 1 else None),
            }
            row["events"].append(event_row)
        stop.set(); dflow_task.cancel(); pm_task.cancel(); await asyncio.gather(dflow_task, pm_task, return_exceptions=True)
        for s in world_sessions + pm_sessions: s.close()
    row["pmFeedDiagnostics"] = {"reconnectCount": pm_feed.reconnect_count, "errorHistory": pm_feed.error_history, "frameCount": pm_feed.frame_count, "rawQueueHighWatermark": pm_feed.raw_queue_high_watermark}
    return row


def _summary(windows: list[dict[str, Any]]) -> dict[str, Any]:
    events = [e for w in windows for e in (w.get("events") or [])]
    offsets = {str(x): {"scheduled": 0, "qualified": 0, "belowOne": 0, "controlBelowOne": 0, "measurementFailures": 0} for x in PROBE_OFFSETS_MS}
    primary_reasons: dict[str, int] = {}
    for event in events:
        for probe in event.get("probes") or []:
            if "offsetMs" not in probe: continue
            row = offsets[str(int(probe["offsetMs"]))]; row["scheduled"] += 1
            primary = probe.get("primary") or {}; reason = str(primary.get("reason") or "UNKNOWN"); primary_reasons[reason] = primary_reasons.get(reason, 0) + 1
            if primary.get("measurementQualified") is True:
                row["qualified"] += 1
                if primary.get("belowOne") is True: row["belowOne"] += 1
            else: row["measurementFailures"] += 1
            control = probe.get("conservativeControl") or {}
            if control.get("measurementQualified") is True and control.get("belowOne") is True: row["controlBelowOne"] += 1
    for row in offsets.values():
        row["captureRateAmongQualified"] = row["belowOne"] / row["qualified"] if row["qualified"] else None
        row["controlPassRateAmongQualified"] = row["controlBelowOne"] / row["qualified"] if row["qualified"] else None
    return {
        "windowCount": len(windows),
        "radarEpisodeCount": sum(sum((w.get("radarEpisodeCount") or {}).values()) for w in windows),
        "eventsWithAnyExactBelowOne": sum(1 for e in events if int((e.get("lifetime") or {}).get("exactBelowOneProbeCount") or 0) > 0),
        "probeOffsetsMs": offsets,
        "primaryReasonCounts": dict(sorted(primary_reasons.items())),
        "interpretation": "PRIMARY=response-time anchored exact quote + PM depth; CONTROL=R2.4 worst-state interval. Radar alone never counts as exact edge.",
    }


def _self_test() -> None:
    fee = {"rate": 0.07, "exponent": 1}
    quote = {"success": True, "minOutputUiShares": 20.0}
    state = {"asks": [{"price": 0.45, "size": 100.0}]}
    primary = _classify_primary(world_quote=quote, pm_state=state, rest_ok=True, fee_schedule=fee)
    assert primary["measurementQualified"] is True
    # Anti-optimism: reconciliation failure cannot become executable evidence.
    assert _classify_primary(world_quote=quote, pm_state=state, rest_ok=False, fee_schedule=fee)["measurementQualified"] is False
    # Anti-optimism: missing response-time PM state cannot count.
    assert _classify_primary(world_quote=quote, pm_state=None, rest_ok=True, fee_schedule=fee)["measurementQualified"] is False
    # Anti-pessimism: primary classification is independent of a worse earlier interval state.
    bad_earlier = {"effectiveAt": 1.0, "asks": [{"price": 0.99, "size": 100.0}], "localDigest": "a"}
    assert primary["measurementQualified"] is True and bad_earlier["asks"][0]["price"] == 0.99
    # Fee/depth path is still enforced.
    shallow = {"asks": [{"price": 0.01, "size": 0.001}]}
    assert _classify_primary(world_quote=quote, pm_state=shallow, rest_ok=True, fee_schedule=fee)["measurementQualified"] is False
    print("SELF_TEST_PASS")


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    try:
        clock = await asyncio.to_thread(r23._calibrate_pm_clock); clock_offset = r23._to_float(clock.get("medianServerMinusLocalSeconds")); server_now = r23._to_float(clock.get("serverNowEstimate")) or time.time()
    except Exception as exc:
        clock = {"error": f"{type(exc).__name__}:{exc}", "role": "DIAGNOSTIC_ONLY"}; clock_offset = None; server_now = time.time()
    first_start = r23._next_full_window(server_now)
    cash_decimals = await asyncio.to_thread(_retry_token_decimals, r23.wp.CASH_MINT, args.rpc_url)
    windows: list[dict[str, Any]] = []
    for index in range(1, WINDOW_COUNT + 1):
        windows.append(await _run_window(window_index=index, start_ts=first_start + (index - 1) * r23.WINDOW_SECONDS, args=args, cash_decimals=cash_decimals, clock_offset_seconds=clock_offset))
        partial = {"schemaVersion": SCHEMA_VERSION, "windowCountCompleted": len(windows), "windows": windows, "summary": _summary(windows)}
        args.out.parent.mkdir(parents=True, exist_ok=True); args.out.write_text(json.dumps(partial, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps({"progress": f"WINDOW_{index}_COMPLETE", "summary": partial["summary"]}, sort_keys=True), flush=True)
    return {"schemaVersion": SCHEMA_VERSION, "mode": "PUBLIC_READ_ONLY_NO_TRADE", "protocol": "docs/decisions/2026-09-17-world-pm-edge-lifetime-r2-protocol.json", "firstWindowStartTs": first_start, "windowCount": WINDOW_COUNT, "probeOffsetsMs": list(PROBE_OFFSETS_MS), "windows": windows, "summary": _summary(windows), "completedAt": time.time()}


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--rpc-url", default=r23.radar.DEFAULT_RPC); parser.add_argument("--world-discovery-timeout-seconds", type=float, default=90.0); parser.add_argument("--http-timeout-seconds", type=float, default=8.0); parser.add_argument("--self-test", action="store_true"); parser.add_argument("--out", type=Path); args = parser.parse_args()
    if args.self_test: _self_test(); return 0
    if args.out is None: parser.error("--out is required unless --self-test is used")
    result = asyncio.run(_run(args)); args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"); print(json.dumps({"schemaVersion": SCHEMA_VERSION, "summary": result["summary"]}, indent=2, sort_keys=True)); return 0


if __name__ == "__main__": raise SystemExit(main())

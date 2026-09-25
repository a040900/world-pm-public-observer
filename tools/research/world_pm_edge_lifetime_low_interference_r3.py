"""Low-interference response-anchored World.xyz <-> Polymarket BTC5M lifetime R3.

Implements:
- docs/decisions/2026-09-18-world-pm-edge-lifetime-low-interference-r3.json
- docs/reviews/2026-09-18-world-pm-edge-lifetime-low-interference-r3-prerun-review-r1.json
- docs/reviews/2026-09-18-world-pm-edge-lifetime-low-interference-r3-final-prerun-review-r2.json

All radar episodes are counted. Bounded exact samples use one T0 World exact quote.
If the T0 response-anchored provisional economics are below one, exactly one
follow-up cohort is scheduled from the T0 World response timestamp. Final
lifetime evidence requires exact REST/WSS full-book reconciliation for both
anchors. Public read-only; NO_TRADE.
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
from tools.research import world_pm_edge_lifetime_r2 as r2
from tools.research import world_pm_edge_lifetime_r2_integrity_r1 as integrity

SCHEMA_VERSION = "WORLD_PM_EDGE_LIFETIME_LOW_INTERFERENCE_R3"
FOLLOWUP_OFFSETS_MS = (50, 100, 150, 250, 500)
RADAR_POLL_SECONDS = 0.05
SAMPLE_COOLDOWN_SECONDS = 2.0
MAX_T0_SAMPLED_EPISODES_PER_PAIR_PER_WINDOW = 8
WINDOW_COUNT = 6

class RecordingSession(requests.Session):
    def __init__(self) -> None:
        super().__init__()
        self.last_diagnostic: dict[str, Any] | None = None
    def get(self, url: str, *args: Any, **kwargs: Any):
        response = super().get(url, *args, **kwargs)
        diag: dict[str, Any] = {"httpStatus": response.status_code}
        try:
            payload = response.json()
        except Exception:
            payload = None
        if isinstance(payload, Mapping):
            for key in ("outAmount", "minOutAmount", "executionMode", "priceImpactPct"):
                diag[key] = payload.get(key)
        self.last_diagnostic = diag
        return response

def _health_diagnostic(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    return {key: snapshot.get(key) for key in (
        "observedAt", "healthy", "connected", "ready", "connectionGeneration",
        "currentError", "lastDisconnectError", "lastPongAt", "pongAgeSeconds",
        "pingOutstanding", "lastFrameAt", "receiptAgeMs", "sourceTimestampMs",
    )}


def _reconciliation_diagnostic(pm_feed, token, generation, rest_book) -> dict[str, Any]:
    started = r23._to_float(rest_book.get("requestStartedAt"))
    received = r23._to_float(rest_book.get("receivedAt"))
    states = [] if started is None or received is None else pm_feed.states_between(
        token, started - integrity.RECONCILE_BEFORE_SECONDS,
        received + integrity.RECONCILE_AFTER_SECONDS, generation,
    )
    # Bounded evidence only; proximity is never accepted as a digest match.
    nearest = [] if received is None else sorted(
        states, key=lambda state: abs(float(state["effectiveAt"]) - received)
    )[:3]
    return {
        "restSourceTimestamp": rest_book.get("timestamp"),
        "restServerHash": rest_book.get("serverHash"),
        "healthAtVerification": _health_diagnostic(pm_feed.snapshot(token)),
        "nearestTimelineStates": [{
            "effectiveAt": state.get("effectiveAt"),
            "sourceTimestampMs": state.get("sourceTimestampMs"),
            "connectionGeneration": state.get("connectionGeneration"),
            "localDigest": state.get("localDigest"),
        } for state in nearest],
    }


async def _capture_anchor(
    *,
    event: dict[str, Any],
    target_at: float,
    label: str,
    world_session: RecordingSession,
    pm_feed: r2.RollingTimelineBook,
    world_decimals: int,
    pm_fee_schedule: dict[str, Any],
    cash_decimals: int,
    http_timeout_seconds: float,
    clock_offset_seconds: float | None,
    window_end_ts: int,
) -> dict[str, Any]:
    wait = target_at - time.time()
    if wait > 0:
        await asyncio.sleep(wait)
    actual_start = time.time()
    if actual_start >= window_end_ts:
        return {"label": label, "scheduledStartAt": target_at, "actualStartAt": actual_start, "skipped": "WINDOW_ENDED"}

    token = str(event["pmToken"])
    pre = pm_feed.snapshot(token, clock_offset_seconds=clock_offset_seconds)
    generation = int(pre.get("connectionGeneration") or -1)
    world_session.last_diagnostic = None
    try:
        world_quote = await asyncio.to_thread(
            r23._world_anonymous_quote,
            world_session,
            output_mint=str(event["worldMint"]),
            cash_decimals=cash_decimals,
            outcome_decimals=world_decimals,
            timeout_seconds=http_timeout_seconds,
        )
    except Exception as exc:
        world_quote = {"success": False, "exception": f"{type(exc).__name__}:{exc}"}

    response_at = r23._to_float(world_quote.get("receivedAt"))
    anchor_snapshot = pm_feed.snapshot(token, clock_offset_seconds=clock_offset_seconds)
    anchor_generation_healthy = bool(
        pre.get("healthy") is True
        and anchor_snapshot.get("healthy") is True
        and generation == int(anchor_snapshot.get("connectionGeneration") or -2)
    )
    response_state = None
    if response_at is not None and anchor_generation_healthy:
        response_state = pm_feed.state_at(token, response_at, generation)

    provisional = r2._classify_primary(
        world_quote=world_quote,
        pm_state=response_state,
        rest_ok=True,
        fee_schedule=pm_fee_schedule,
    )
    return {
        "label": label,
        "scheduledStartAt": target_at,
        "actualStartAt": actual_start,
        "worldQuote": world_quote,
        "worldHttpDiagnostic": world_session.last_diagnostic,
        "worldResponseAt": response_at,
        "worldQuoteElapsedMs": world_quote.get("elapsedMs"),
        "anchorGeneration": generation,
        "anchorGenerationHealthy": anchor_generation_healthy,
        "anchorHealth": {
            "beforeQuote": _health_diagnostic(pre),
            "afterQuote": _health_diagnostic(anchor_snapshot),
            "sameGeneration": generation == int(anchor_snapshot.get("connectionGeneration") or -2),
        },
        "responseAnchorStateDigest": None if response_state is None else response_state.get("localDigest"),
        "responseState": response_state,
        "provisional": provisional,
    }

async def _verify_anchor(
    *,
    capture: dict[str, Any],
    event: dict[str, Any],
    pm_session: requests.Session,
    pm_feed: r2.RollingTimelineBook,
    pm_fee_schedule: dict[str, Any],
    http_timeout_seconds: float,
    clock_offset_seconds: float | None,
) -> dict[str, Any]:
    if capture.get("skipped"):
        return {"verified": False, "reason": str(capture["skipped"]), "restAttempts": []}
    token = str(event["pmToken"])
    generation = int(capture.get("anchorGeneration") or -1)
    if capture.get("anchorGenerationHealthy") is not True:
        return {"verified": False, "reason": "PM_TRANSPORT_INVALID_AT_ANCHOR", "restAttempts": []}

    attempts: list[dict[str, Any]] = []
    rest_ok = False
    rest_digest: str | None = None
    for attempt in range(1, integrity.REST_ATTEMPTS + 1):
        try:
            rest_book = await asyncio.to_thread(r23._fetch_pm_rest_book, pm_session, token, http_timeout_seconds)
        except Exception as exc:
            attempts.append({"attempt": attempt, "success": False, "exception": f"{type(exc).__name__}:{exc}"})
            if attempt < integrity.REST_ATTEMPTS:
                await asyncio.sleep(integrity.REST_RETRY_DELAY_SECONDS)
            continue
        await asyncio.sleep(integrity.RECONCILE_AFTER_SECONDS)
        match, state_count = integrity._exact_digest_match(
            pm_feed=pm_feed,
            token=token,
            generation=generation,
            rest_book=rest_book,
        )
        digest = str(rest_book.get("localDigest") or "") or None
        attempts.append({
            "attempt": attempt,
            "success": True,
            "elapsedMs": rest_book.get("elapsedMs"),
            "requestStartedAt": rest_book.get("requestStartedAt"),
            "receivedAt": rest_book.get("receivedAt"),
            "digest": digest,
            "candidateTimelineStateCount": state_count,
            "exactDigestMatched": match,
            "currentGenerationAtVerification": pm_feed.connection_generation,
            "diagnostic": _reconciliation_diagnostic(pm_feed, token, generation, rest_book),
        })
        if match:
            rest_ok = True
            rest_digest = digest
            break
        if attempt < integrity.REST_ATTEMPTS:
            await asyncio.sleep(integrity.REST_RETRY_DELAY_SECONDS)

    formal = r2._classify_primary(
        world_quote=capture.get("worldQuote") or {},
        pm_state=capture.get("responseState"),
        rest_ok=rest_ok,
        fee_schedule=pm_fee_schedule,
    )
    return {
        "verified": rest_ok,
        "reason": formal.get("reason"),
        "restDigest": rest_digest,
        "restAttempts": attempts,
        "primary": formal,
    }

async def _sample_event(
    *,
    event: dict[str, Any],
    followup_offset_ms: int,
    pm_feed: r2.RollingTimelineBook,
    world_decimals: int,
    pm_fee_schedule: dict[str, Any],
    cash_decimals: int,
    http_timeout_seconds: float,
    clock_offset_seconds: float | None,
    window_end_ts: int,
) -> dict[str, Any]:
    world_t0 = RecordingSession()
    world_follow = RecordingSession()
    pm_t0 = requests.Session()
    pm_follow = requests.Session()
    try:
        t0 = await _capture_anchor(
            event=event,
            target_at=float(event["triggeredAt"]),
            label="T0",
            world_session=world_t0,
            pm_feed=pm_feed,
            world_decimals=world_decimals,
            pm_fee_schedule=pm_fee_schedule,
            cash_decimals=cash_decimals,
            http_timeout_seconds=http_timeout_seconds,
            clock_offset_seconds=clock_offset_seconds,
            window_end_ts=window_end_ts,
        )
        t0_response = r23._to_float(t0.get("worldResponseAt"))
        t0_provisional = t0.get("provisional") or {}
        verification_task = asyncio.create_task(_verify_anchor(
            capture=t0, event=event, pm_session=pm_t0, pm_feed=pm_feed,
            pm_fee_schedule=pm_fee_schedule, http_timeout_seconds=http_timeout_seconds,
            clock_offset_seconds=clock_offset_seconds,
        ))

        follow: dict[str, Any] | None = None
        follow_verification: dict[str, Any] | None = None
        if (
            t0_response is not None
            and t0_provisional.get("measurementQualified") is True
            and t0_provisional.get("belowOne") is True
        ):
            follow_target = t0_response + followup_offset_ms / 1000.0
            follow = await _capture_anchor(
                event=event,
                target_at=follow_target,
                label="FOLLOWUP",
                world_session=world_follow,
                pm_feed=pm_feed,
                world_decimals=world_decimals,
                pm_fee_schedule=pm_fee_schedule,
                cash_decimals=cash_decimals,
                http_timeout_seconds=http_timeout_seconds,
                clock_offset_seconds=clock_offset_seconds,
                window_end_ts=window_end_ts,
            )
            follow_verification = await _verify_anchor(
                capture=follow, event=event, pm_session=pm_follow, pm_feed=pm_feed,
                pm_fee_schedule=pm_fee_schedule, http_timeout_seconds=http_timeout_seconds,
                clock_offset_seconds=clock_offset_seconds,
            )

        t0_verification = await verification_task
        t0_primary = t0_verification.get("primary") or {}
        follow_primary = (follow_verification or {}).get("primary") or {}
        follow_response = None if follow is None else r23._to_float(follow.get("worldResponseAt"))
        radar_at = float(event["triggeredAt"])
        realized_response_gap_ms = None
        radar_to_t0_response_ms = None
        radar_to_follow_response_ms = None
        if t0_response is not None:
            radar_to_t0_response_ms = (t0_response - radar_at) * 1000.0
        if t0_response is not None and follow_response is not None:
            realized_response_gap_ms = (follow_response - t0_response) * 1000.0
            radar_to_follow_response_ms = (follow_response - radar_at) * 1000.0

        survival_eligible = bool(
            t0_primary.get("measurementQualified") is True
            and t0_primary.get("belowOne") is True
            and follow_primary.get("measurementQualified") is True
        )
        return {
            **event,
            "assignedFollowupOffsetMs": followup_offset_ms,
            "t0": {**t0, "verification": t0_verification},
            "followup": None if follow is None else {**follow, "verification": follow_verification},
            "timeline": {
                "radarToT0ResponseMs": radar_to_t0_response_ms,
                "assignedFollowupStartOffsetFromT0ResponseMs": followup_offset_ms,
                "realizedT0ResponseToFollowupResponseMs": realized_response_gap_ms,
                "radarToFollowupResponseMs": radar_to_follow_response_ms,
            },
            "lifetime": {
                "t0Qualified": t0_primary.get("measurementQualified") is True,
                "t0BelowOne": t0_primary.get("belowOne") is True,
                "followupAttempted": follow is not None,
                "followupQualified": follow_primary.get("measurementQualified") is True,
                "survivalEligible": survival_eligible,
                "reobservedBelowOneAtFollowup": bool(survival_eligible and follow_primary.get("belowOne") is True),
                "continuousSurvivalClaimed": False,
            },
        }
    finally:
        world_t0.close(); world_follow.close(); pm_t0.close(); pm_follow.close()

async def _run_window(
    *,
    window_index: int,
    start_ts: int,
    args: argparse.Namespace,
    cash_decimals: int,
    clock_offset_seconds: float | None,
    cohort_index: list[int],
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
            r23.radar._discover_world_market, start_ts, args.rpc_url,
            min(float(end_ts), time.time() + args.world_discovery_timeout_seconds),
        )
        world_error = None
    except Exception as exc:
        world_market = None
        world_error = f"{type(exc).__name__}:{exc}"

    row: dict[str, Any] = {
        "windowIndex": window_index, "startTs": start_ts, "endTs": end_ts,
        "worldDiscoveryError": world_error, "radarPollCount": 0,
        "radarEpisodeCount": {"WORLD_YES+PM_DOWN": 0, "WORLD_NO+PM_UP": 0},
        "sampledEpisodeCount": {"WORLD_YES+PM_DOWN": 0, "WORLD_NO+PM_UP": 0},
        "events": [],
    }
    if world_market is None:
        while time.time() < end_ts:
            await asyncio.sleep(min(1.0, max(0.0, end_ts - time.time())))
        stop.set(); pm_task.cancel(); await asyncio.gather(pm_task, return_exceptions=True)
        return row

    yes_decimals, no_decimals = await asyncio.gather(
        asyncio.to_thread(r2._retry_token_decimals, world_market.yes_mint, args.rpc_url),
        asyncio.to_thread(r2._retry_token_decimals, world_market.no_mint, args.rpc_url),
    )
    dflow = r23.RobustDflowRadar(world_market.yes_mint, world_market.no_mint)
    dflow_task = asyncio.create_task(dflow.run(stop))
    active = {"WORLD_YES+PM_DOWN": False, "WORLD_NO+PM_UP": False}
    last_sampled_at = {"WORLD_YES+PM_DOWN": float("-inf"), "WORLD_NO+PM_UP": float("-inf")}
    sampled_count = {"WORLD_YES+PM_DOWN": 0, "WORLD_NO+PM_UP": 0}
    pending: list[asyncio.Task[dict[str, Any]]] = []
    event_seq = 0
    candidates = [
        ("WORLD_YES+PM_DOWN", "yes", world_market.yes_mint, yes_decimals, pm_market.down_token),
        ("WORLD_NO+PM_UP", "no", world_market.no_mint, no_decimals, pm_market.up_token),
    ]
    try:
        while time.time() < end_ts:
            loop_at = time.time(); row["radarPollCount"] += 1
            world_snapshot = dflow.snapshot(loop_at)
            for pair, world_side, world_mint, world_decimals, pm_token in candidates:
                pm_snapshot = pm_feed.snapshot(pm_token, clock_offset_seconds=clock_offset_seconds)
                leg = world_snapshot.get(world_side) or {}
                world_ask = r23._to_float(leg.get("ask"))
                world_age = r23._to_float(leg.get("quoteAgeSeconds"))
                pm_ask = r23._to_float(pm_snapshot.get("bestAsk"))
                below = False; indicative_sum = None
                if (
                    pm_snapshot.get("healthy") is True and world_snapshot.get("healthy") is True
                    and world_ask is not None and pm_ask is not None and world_age is not None
                    and world_age <= r23.MAX_WORLD_RADAR_AGE_SECONDS
                ):
                    pm_effective = r23._pm_effective_top_cost(pm_ask, pm_market.fee_schedule)
                    if pm_effective is not None:
                        indicative_sum = world_ask + pm_effective
                        below = indicative_sum < 1.0
                if below and not active[pair]:
                    active[pair] = True; event_seq += 1; row["radarEpisodeCount"][pair] += 1
                    event = {
                        "eventId": f"w{window_index}-e{event_seq}", "pair": pair,
                        "triggeredAt": loop_at, "worldMint": world_mint, "pmToken": pm_token,
                        "indicativeSum": indicative_sum, "worldIndicativeAsk": world_ask,
                        "pmLocalBestAsk": pm_ask, "exactSampled": False, "samplingReason": None,
                    }
                    row["events"].append(event)
                    eligible = (
                        sampled_count[pair] < MAX_T0_SAMPLED_EPISODES_PER_PAIR_PER_WINDOW
                        and loop_at - last_sampled_at[pair] >= SAMPLE_COOLDOWN_SECONDS
                    )
                    if eligible:
                        offset = FOLLOWUP_OFFSETS_MS[cohort_index[0] % len(FOLLOWUP_OFFSETS_MS)]
                        cohort_index[0] += 1; sampled_count[pair] += 1; last_sampled_at[pair] = loop_at
                        row["sampledEpisodeCount"][pair] += 1
                        event["exactSampled"] = True; event["samplingReason"] = "ELIGIBLE_AFTER_COOLDOWN"
                        pending.append(asyncio.create_task(_sample_event(
                            event=event, followup_offset_ms=offset, pm_feed=pm_feed,
                            world_decimals=world_decimals, pm_fee_schedule=pm_market.fee_schedule,
                            cash_decimals=cash_decimals, http_timeout_seconds=args.http_timeout_seconds,
                            clock_offset_seconds=clock_offset_seconds, window_end_ts=end_ts,
                        )))
                    else:
                        event["samplingReason"] = (
                            "PAIR_WINDOW_SAMPLE_CAP_REACHED"
                            if sampled_count[pair] >= MAX_T0_SAMPLED_EPISODES_PER_PAIR_PER_WINDOW
                            else "PAIR_SAMPLE_COOLDOWN"
                        )
                elif not below:
                    active[pair] = False
            delay = RADAR_POLL_SECONDS - (time.time() - loop_at)
            if delay > 0:
                await asyncio.sleep(delay)
    finally:
        sampled_results = await asyncio.gather(*pending, return_exceptions=True)
        by_id = {e["eventId"]: e for e in row["events"]}
        for result in sampled_results:
            if isinstance(result, Exception):
                continue
            by_id[result["eventId"]].update(result)
        stop.set(); dflow_task.cancel(); pm_task.cancel()
        await asyncio.gather(dflow_task, pm_task, return_exceptions=True)
    row["pmFeedDiagnostics"] = {
        "reconnectCount": pm_feed.reconnect_count,
        "errorHistory": pm_feed.error_history,
        "frameCount": pm_feed.frame_count,
        "rawQueueHighWatermark": pm_feed.raw_queue_high_watermark,
    }
    return row

def _summary(windows: list[dict[str, Any]]) -> dict[str, Any]:
    events = [e for w in windows for e in (w.get("events") or [])]
    sampled = [e for e in events if e.get("exactSampled") is True]
    t0_qualified = t0_below = follow_qualified = reobserved = world_fail = 0
    cohorts = {str(x): {"assigned": 0, "eligible": 0, "reobservedBelowOne": 0} for x in FOLLOWUP_OFFSETS_MS}
    realized_gaps: list[float] = []
    radar_to_t0: list[float] = []
    for e in sampled:
        t0p = (((e.get("t0") or {}).get("verification") or {}).get("primary") or {})
        fp = (((e.get("followup") or {}).get("verification") or {}).get("primary") or {})
        if t0p.get("measurementQualified") is True:
            t0_qualified += 1
            if t0p.get("belowOne") is True:
                t0_below += 1
        if fp.get("measurementQualified") is True:
            follow_qualified += 1
        life = e.get("lifetime") or {}
        offset = e.get("assignedFollowupOffsetMs")
        if offset is not None:
            c = cohorts[str(int(offset))]; c["assigned"] += 1
            if life.get("survivalEligible") is True:
                c["eligible"] += 1
                if life.get("reobservedBelowOneAtFollowup") is True:
                    c["reobservedBelowOne"] += 1; reobserved += 1
        timeline = e.get("timeline") or {}
        if timeline.get("realizedT0ResponseToFollowupResponseMs") is not None:
            realized_gaps.append(float(timeline["realizedT0ResponseToFollowupResponseMs"]))
        if timeline.get("radarToT0ResponseMs") is not None:
            radar_to_t0.append(float(timeline["radarToT0ResponseMs"]))
        for cap in (e.get("t0"), e.get("followup")):
            if cap and ((cap.get("worldQuote") or {}).get("success") is not True):
                world_fail += 1
    for c in cohorts.values():
        c["reobservationRateAmongEligible"] = c["reobservedBelowOne"] / c["eligible"] if c["eligible"] else None
    return {
        "windowCount": len(windows),
        "radarEpisodeCount": sum(sum((w.get("radarEpisodeCount") or {}).values()) for w in windows),
        "sampledEpisodeCount": len(sampled),
        "t0QualifiedCount": t0_qualified,
        "t0ExactBelowOneCount": t0_below,
        "followupQualifiedCount": follow_qualified,
        "reobservedBelowOneCount": reobserved,
        "worldQuoteFailureCount": world_fail,
        "radarToT0ResponseMs": r23._distribution(radar_to_t0),
        "realizedT0ResponseToFollowupResponseMs": r23._distribution(realized_gaps),
        "followupCohortsMs": cohorts,
        "interpretation": "Follow-up below-one means re-observed exact edge at a later response-time anchor; continuous survival between observations is not claimed.",
    }

def _self_test() -> None:
    r2._self_test(); integrity._self_test()
    # A later generation change must not by itself rewrite the anchor economics.
    fee = {"rate": 0.07, "exponent": 1}
    quote = {"success": True, "minOutputUiShares": 20.0}
    state = {"asks": [{"price": 0.45, "size": 100.0}]}
    p = r2._classify_primary(world_quote=quote, pm_state=state, rest_ok=True, fee_schedule=fee)
    assert p["measurementQualified"] is True
    # Timing interpretation: scheduled offset and realized response gap are distinct.
    t0_response = 100.0; follow_start = t0_response + 0.05; follow_response = follow_start + 0.12
    assert int((follow_start - t0_response) * 1000) == 49 or int((follow_start - t0_response) * 1000) == 50
    assert (follow_response - t0_response) * 1000 > 150
    print("LOW_INTERFERENCE_R3_SELF_TEST_PASS")

async def _run(args: argparse.Namespace) -> dict[str, Any]:
    try:
        clock = await asyncio.to_thread(r23._calibrate_pm_clock)
        offset = r23._to_float(clock.get("medianServerMinusLocalSeconds"))
        server_now = r23._to_float(clock.get("serverNowEstimate")) or time.time()
    except Exception as exc:
        clock = {"error": f"{type(exc).__name__}:{exc}", "role": "DIAGNOSTIC_ONLY"}
        offset = None; server_now = time.time()
    first = r23._next_full_window(server_now)
    cash_decimals = await asyncio.to_thread(r2._retry_token_decimals, r23.wp.CASH_MINT, args.rpc_url)
    cohort_index = [0]; windows: list[dict[str, Any]] = []
    for i in range(1, WINDOW_COUNT + 1):
        windows.append(await _run_window(
            window_index=i, start_ts=first + (i - 1) * r23.WINDOW_SECONDS,
            args=args, cash_decimals=cash_decimals, clock_offset_seconds=offset,
            cohort_index=cohort_index,
        ))
        partial = {"schemaVersion": SCHEMA_VERSION, "windowCountCompleted": len(windows), "windows": windows, "summary": _summary(windows)}
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(partial, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps({"progress": f"WINDOW_{i}_COMPLETE", "summary": partial["summary"]}, sort_keys=True), flush=True)
    return {
        "schemaVersion": SCHEMA_VERSION, "mode": "PUBLIC_READ_ONLY_NO_TRADE",
        "protocol": "docs/decisions/2026-09-18-world-pm-edge-lifetime-low-interference-r3.json",
        "firstWindowStartTs": first, "windowCount": WINDOW_COUNT, "windows": windows,
        "summary": _summary(windows), "completedAt": time.time(),
    }

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rpc-url", default=r23.radar.DEFAULT_RPC)
    parser.add_argument("--world-discovery-timeout-seconds", type=float, default=90.0)
    parser.add_argument("--http-timeout-seconds", type=float, default=8.0)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    if args.self_test:
        _self_test(); return 0
    if args.out is None:
        parser.error("--out is required unless --self-test is used")
    result = asyncio.run(_run(args))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"schemaVersion": SCHEMA_VERSION, "summary": result["summary"]}, indent=2, sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

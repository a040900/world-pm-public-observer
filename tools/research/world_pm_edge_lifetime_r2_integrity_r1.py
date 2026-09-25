"""Measurement-integrity repair for World/Polymarket edge lifetime R2.

Only the REST/WSS integrity verification path is changed. Economic semantics,
probe offsets, World minOutAmount, PM depth/fee math, response-time price anchor,
and below-one classification remain inherited from world_pm_edge_lifetime_r2.

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
from tools.research import world_pm_edge_lifetime_r2 as r2

SCHEMA_VERSION = "WORLD_PM_EDGE_LIFETIME_R2_INTEGRITY_R1"
RECONCILE_BEFORE_SECONDS = 0.250
RECONCILE_AFTER_SECONDS = 0.500
REST_ATTEMPTS = 2
REST_RETRY_DELAY_SECONDS = 0.100


def _exact_digest_match(
    *,
    pm_feed: r2.RollingTimelineBook,
    token: str,
    generation: int,
    rest_book: Mapping[str, Any],
) -> tuple[bool, int]:
    digest = str(rest_book.get("localDigest") or "")
    started = r23._to_float(rest_book.get("requestStartedAt"))
    received = r23._to_float(rest_book.get("receivedAt"))
    if not digest or started is None or received is None:
        return False, 0
    states = pm_feed.states_between(
        token,
        started - RECONCILE_BEFORE_SECONDS,
        received + RECONCILE_AFTER_SECONDS,
        generation,
    )
    return any(str(state.get("localDigest") or "") == digest for state in states), len(states)


async def _probe_integrity_r1(
    *,
    event: dict[str, Any],
    offset_ms: int,
    world_session: requests.Session,
    pm_session: requests.Session,
    pm_feed: r2.RollingTimelineBook,
    world_decimals: int,
    pm_fee_schedule: dict[str, Any],
    cash_decimals: int,
    http_timeout_seconds: float,
    clock_offset_seconds: float | None,
    window_end_ts: int,
) -> dict[str, Any]:
    target_start = float(event["triggeredAt"]) + offset_ms / 1000.0
    wait = target_start - time.time()
    if wait > 0:
        await asyncio.sleep(wait)
    actual_start = time.time()
    if actual_start >= window_end_ts:
        return {
            "offsetMs": offset_ms,
            "scheduledStartAt": target_start,
            "actualStartAt": actual_start,
            "skipped": "WINDOW_ENDED",
        }

    token = str(event["pmToken"])
    pre = pm_feed.snapshot(token, clock_offset_seconds=clock_offset_seconds)
    generation = int(pre.get("connectionGeneration") or -1)

    # Critical-path repair: World exact quote is completed and timestamped first.
    # REST verification is intentionally deferred until after the response anchor.
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
        world_quote = {
            "success": False,
            "exception": f"{type(exc).__name__}:{exc}",
        }

    response_at = r23._to_float(world_quote.get("receivedAt"))
    request_at = r23._to_float(world_quote.get("requestStartedAt"))
    anchor_snapshot = pm_feed.snapshot(token, clock_offset_seconds=clock_offset_seconds)
    same_generation_at_anchor = bool(
        pre.get("healthy") is True
        and anchor_snapshot.get("healthy") is True
        and generation == int(anchor_snapshot.get("connectionGeneration") or -2)
    )
    response_state = (
        None
        if response_at is None or not same_generation_at_anchor
        else pm_feed.state_at(token, response_at, generation)
    )

    attempts: list[dict[str, Any]] = []
    rest_ok = False
    rest_digest: str | None = None
    matched_state_count = 0

    if same_generation_at_anchor:
        for attempt in range(1, REST_ATTEMPTS + 1):
            try:
                rest_book = await asyncio.to_thread(
                    r23._fetch_pm_rest_book,
                    pm_session,
                    token,
                    http_timeout_seconds,
                )
            except Exception as exc:
                attempts.append({
                    "attempt": attempt,
                    "success": False,
                    "exception": f"{type(exc).__name__}:{exc}",
                })
                if attempt < REST_ATTEMPTS:
                    await asyncio.sleep(REST_RETRY_DELAY_SECONDS)
                continue

            # Allow the independent WSS transport to deliver the exact state that
            # REST observed. This does not alter the frozen World response anchor.
            await asyncio.sleep(RECONCILE_AFTER_SECONDS)
            post_attempt = pm_feed.snapshot(token, clock_offset_seconds=clock_offset_seconds)
            same_generation = bool(
                post_attempt.get("healthy") is True
                and generation == int(post_attempt.get("connectionGeneration") or -2)
            )
            match = False
            state_count = 0
            if same_generation:
                match, state_count = _exact_digest_match(
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
                "sameGeneration": same_generation,
                "candidateTimelineStateCount": state_count,
                "exactDigestMatched": match,
            })
            if match:
                rest_ok = True
                rest_digest = digest
                matched_state_count = state_count
                break
            if attempt < REST_ATTEMPTS:
                await asyncio.sleep(REST_RETRY_DELAY_SECONDS)

    final_snapshot = pm_feed.snapshot(token, clock_offset_seconds=clock_offset_seconds)
    same_generation_final = bool(
        same_generation_at_anchor
        and final_snapshot.get("healthy") is True
        and generation == int(final_snapshot.get("connectionGeneration") or -2)
    )

    if not same_generation_final:
        primary = {
            "measurementQualified": False,
            "belowOne": False,
            "reason": "PM_TRANSPORT_INVALID",
        }
        control = dict(primary)
        interval_states: list[dict[str, Any]] = []
    else:
        primary = r2._classify_primary(
            world_quote=world_quote,
            pm_state=response_state,
            rest_ok=rest_ok,
            fee_schedule=pm_fee_schedule,
        )
        interval_states = (
            []
            if request_at is None or response_at is None
            else pm_feed.states_between(token, request_at, response_at, generation)
        )
        control = r2._classify_control(
            world_quote=world_quote,
            interval_states=interval_states,
            rest_ok=rest_ok,
            rest_digest=rest_digest,
            fee_schedule=pm_fee_schedule,
        )

    return {
        "offsetMs": offset_ms,
        "scheduledStartAt": target_start,
        "actualStartAt": actual_start,
        "startDelayFromTriggerMs": (actual_start - float(event["triggeredAt"])) * 1000.0,
        "worldResponseAt": response_at,
        "worldResponseDelayFromTriggerMs": None if response_at is None else (response_at - float(event["triggeredAt"])) * 1000.0,
        "worldQuoteElapsedMs": world_quote.get("elapsedMs"),
        "pmSameGenerationHealthy": same_generation_final,
        "restReconciled": rest_ok,
        "restAttempts": attempts,
        "restMatchedCandidateStateCount": matched_state_count,
        "responseAnchorStateDigest": None if response_state is None else response_state.get("localDigest"),
        "intervalStateCount": len(interval_states),
        "primary": primary,
        "conservativeControl": control,
        "measurementIntegrityRevision": "R1_WORLD_FIRST_EXACT_DIGEST_RETRY",
    }


def _self_test() -> None:
    # Preserve all parent anti-optimism/anti-pessimism economic checks.
    r2._self_test()

    # Exact digest only: same state passes, adjacent-but-different state fails.
    feed = r2.RollingTimelineBook(["t"])
    feed.connection_generation = 3
    feed.history["t"] = [
        {"effectiveAt": 10.00, "connectionGeneration": 3, "localDigest": "A", "asks": []},
        {"effectiveAt": 10.20, "connectionGeneration": 3, "localDigest": "B", "asks": []},
        {"effectiveAt": 10.40, "connectionGeneration": 3, "localDigest": "C", "asks": []},
    ]
    ok, count = _exact_digest_match(
        pm_feed=feed,
        token="t",
        generation=3,
        rest_book={"requestStartedAt": 10.21, "receivedAt": 10.25, "localDigest": "B"},
    )
    assert ok is True and count >= 2
    bad, _ = _exact_digest_match(
        pm_feed=feed,
        token="t",
        generation=3,
        rest_book={"requestStartedAt": 10.21, "receivedAt": 10.25, "localDigest": "D"},
    )
    assert bad is False
    print("INTEGRITY_R1_SELF_TEST_PASS")


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    original_probe = r2._probe
    r2._probe = _probe_integrity_r1
    try:
        result = await r2._run(args)
    finally:
        r2._probe = original_probe
    result["schemaVersion"] = SCHEMA_VERSION
    result["measurementIntegrity"] = {
        "revision": "R1",
        "worldQuoteCriticalPath": "REST_DEFERRED_UNTIL_AFTER_WORLD_RESPONSE",
        "restAttempts": REST_ATTEMPTS,
        "retryDelayMs": int(REST_RETRY_DELAY_SECONDS * 1000),
        "exactDigestOnly": True,
        "reconciliationBeforeRestRequestMs": int(RECONCILE_BEFORE_SECONDS * 1000),
        "reconciliationAfterRestResponseMs": int(RECONCILE_AFTER_SECONDS * 1000),
        "economicsChanged": False,
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rpc-url", default=r23.radar.DEFAULT_RPC)
    parser.add_argument("--world-discovery-timeout-seconds", type=float, default=90.0)
    parser.add_argument("--http-timeout-seconds", type=float, default=8.0)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    if args.self_test:
        _self_test()
        return 0
    if args.out is None:
        parser.error("--out is required unless --self-test is used")
    result = asyncio.run(_run(args))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"schemaVersion": SCHEMA_VERSION, "summary": result.get("summary")}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

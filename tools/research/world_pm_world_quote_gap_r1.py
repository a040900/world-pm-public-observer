"""Bounded World.xyz quote-input/response gap collector (public read-only).

One response-anchored T0 quote is collected for each sampled radar episode.
No follow-ups, fills, credentials, signing, or trading are performed.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import deque
import copy
import json
import math
import hashlib
import random
import threading
import time
from pathlib import Path
from typing import Any, Callable, Mapping

import requests

from tools.research import world_pm_edge_lifetime_low_interference_r3 as r3
from tools.research import world_pm_edge_lifetime_r2 as r2
from tools.research import world_polymarket_btc5m_executable_sync_r23 as r23

SCHEMA_VERSION = "WORLD_PM_WORLD_QUOTE_GAP_R1"
PROTOCOL = "docs/decisions/2026-09-19-world-radar-exact-gap-r1.md"
RADAR_POLL_SECONDS = r3.RADAR_POLL_SECONDS
SAMPLE_COOLDOWN_SECONDS = r3.SAMPLE_COOLDOWN_SECONDS
MAX_SAMPLED_EPISODES_PER_PAIR_PER_WINDOW = r3.MAX_T0_SAMPLED_EPISODES_PER_PAIR_PER_WINDOW
WINDOW_COUNT = 6
AMOUNT_CHOICES_CASH = (1.0, 5.0, 10.0)
MAX_CAPTURED_RADAR_UPDATES = 2048


class _UpdateHistory(dict):
    def __init__(self, callback: Callable[[str, dict[str, Any]], None], lock) -> None:
        super().__init__(); self._callback = callback; self._lock = lock

    def __setitem__(self, key: str, value: dict[str, Any]) -> None:
        with self._lock:
            super().__setitem__(key, value)
            self._callback(key, copy.deepcopy(value))


class GapRadar(r23.RobustDflowRadar):
    """R3 radar with bounded per-update history, without changing its parser."""
    def __init__(self, yes_mint: str, no_mint: str) -> None:
        super().__init__(yes_mint, no_mint)
        self.update_history: deque[dict[str, Any]] = deque(maxlen=MAX_CAPTURED_RADAR_UPDATES)
        self.update_overflow = False
        self.update_sequence = 0
        self.lock = threading.RLock()
        self.latest = _UpdateHistory(self._record_update, self.lock)

    def snapshot(self, observed_at):
        with self.lock:
            return super().snapshot(observed_at)

    def capture(self, side):
        with self.lock:
            now = time.time()
            snapshot = self.snapshot(now)
            return {**copy.deepcopy(snapshot.get(side) or {}), "side": side,
                    "observedAt": now, "sequence": self.update_sequence,
                    "healthy": snapshot.get("healthy") is True}

    def interval(self, start_sequence, end_sequence, side):
        with self.lock:
            oldest = self.update_history[0]["sequence"] if self.update_history else self.update_sequence+1
            overflow = end_sequence > start_sequence and oldest > start_sequence+1
            updates = [copy.deepcopy(u) for u in self.update_history
                       if start_sequence < u["sequence"] <= end_sequence and u["side"] == side]
            return updates, overflow

    def _record_update(self, side: str, update: dict[str, Any]) -> None:
        self.update_sequence += 1
        if len(self.update_history) == self.update_history.maxlen:
            self.update_overflow = True
        update["side"] = side
        update["sequence"] = self.update_sequence
        self.update_history.append(update)


class AmountSession(r3.RecordingSession):
    def __init__(self, amount_cash: float, cash_decimals: int, radar: GapRadar, side="yes") -> None:
        super().__init__(); self.amount_cash = amount_cash; self.cash_decimals = cash_decimals; self.radar = radar
        self.side = side; self.pre_snapshot = {}; self.response_snapshot = {}; self.world_updates = []; self.trace_overflow = False
        self.request_identity = {}; self.response_details = {}
        self.requested_in_amount: int | None = None; self.request_started_at: float | None = None; self.request_finished_at: float | None = None; self.request_start_sequence = 0; self.request_end_sequence = 0

    def get(self, url: str, *args: Any, **kwargs: Any):
        params = dict(kwargs.get("params") or {})
        requested = int(round(self.amount_cash * (10 ** self.cash_decimals)))
        params["amount"] = str(requested); kwargs["params"] = params
        self.request_identity = {k: params.get(k) for k in ("inputMint", "outputMint", "amount", "slippageBps", "predictionMarketSlippageBps")}
        self.requested_in_amount = requested
        self.request_started_at = time.time()
        self.pre_snapshot = self.radar.capture(self.side)
        self.request_start_sequence = self.pre_snapshot["sequence"]
        try:
            response = super().get(url, *args, **kwargs)
            return response
        finally:
            self.request_finished_at = time.time()
            with self.radar.lock:
                self.response_snapshot = self.radar.capture(self.side)
                self.request_end_sequence = self.response_snapshot["sequence"]
                self.world_updates, self.trace_overflow = self.radar.interval(
                    self.request_start_sequence, self.request_end_sequence, self.side)


def _number(value: Any) -> float | None:
    try:
        value = float(value)
        return value if math.isfinite(value) and value >= 0 else None
    except (TypeError, ValueError):
        return None


def _gap_economics(quote: Mapping[str, Any], pm_state: Mapping[str, Any] | None,
                   fee_schedule: dict[str, Any], cash_decimals: int) -> dict[str, Any]:
    """Classify using World response inAmount, never the R23 10 CASH constant."""
    result: dict[str, Any] = {"status": "UNKNOWN", "belowOne": None,
                              "actualInputCash": None, "worldMinOutShares": None,
                              "pmWalk": None, "reason": "MISSING_OR_INVALID_INPUT"}
    if not isinstance(quote, Mapping) or quote.get("success") is not True or not isinstance(pm_state, Mapping):
        return result
    raw_in = _number(quote.get("inAmount")); target = _number(quote.get("minOutputUiShares"))
    asks = pm_state.get("asks")
    if raw_in is None or raw_in <= 0 or not raw_in.is_integer() or target is None or target <= 0 or not isinstance(asks, list) or not asks:
        return result
    for level in asks:
        price, size = _number(level.get("price")), _number(level.get("size"))
        if price is None or not 0 < price <= 1 or size is None or size <= 0:
            return result
    rate, exponent = _number(fee_schedule.get("rate")), _number(fee_schedule.get("exponent"))
    if rate is None or rate > 1 or exponent is None or exponent <= 0:
        return result
    walk = r23._walk_pm_book_for_net_shares(asks, target, fee_schedule)
    if walk.get("filled") is not True:
        result.update({"reason": "PM_DEPTH_INSUFFICIENT", "actualInputCash": raw_in / (10 ** cash_decimals),
                       "worldMinOutShares": target, "pmWalk": walk})
        return result
    actual_input = raw_in / (10 ** cash_decimals)
    total = actual_input + float(walk["cost"])
    unit = total / target
    result.update({"status": "QUALIFIED", "reason": "RESPONSE_ANCHORED_GAP", "belowOne": unit < 1.0,
                   "actualInputCash": actual_input, "worldMinOutShares": target,
                   "unitCostPerConditionalPayout": unit, "conditionalSpreadNominal": target - total,
                   "pmWalk": walk})
    return result


def _decompose(event, quote, economics, pre, response, updates, overflow):
    result = {"status": "UNKNOWN", "reason": "INCOMPLETE_WORLD_TRACE"}
    if overflow:
        return {**result, "reason": "WORLD_TRACE_OVERFLOW"}
    if economics.get("status") != "QUALIFIED":
        return {**result, "reason": "ECONOMICS_UNQUALIFIED"}
    for snapshot in (event.get("radarTrigger", {}), pre, response):
        ask = _number(snapshot.get("ask"))
        age = _number(snapshot.get("quoteAgeSeconds"))
        if (snapshot.get("healthy") is not True or ask is None or ask <= 0 or age is None
                or age > r23.MAX_WORLD_RADAR_AGE_SECONDS or snapshot.get("error") not in (None, "", 0)
                or any(snapshot.get(k) is None for k in ("receivedAt", "sourceTimestamp", "slot"))):
            return result
    for update in updates:
        if (update.get("error") not in (None, "", 0) or _number(update.get("ask")) is None
                or any(update.get(k) is None for k in ("receivedAt", "sourceTimestamp", "slot"))):
            return result
    out = _number(quote.get("outputUiShares")); minimum = _number(quote.get("minOutputUiShares"))
    if out is None or minimum is None or minimum <= 0 or out < minimum:
        return result
    indicative = _number(event.get("indicativeSum")); trigger = _number(event.get("worldIndicativeAsk"))
    if indicative is None or trigger is None:
        return result
    amount = economics["actualInputCash"]
    expected = amount/out; worst = amount/minimum
    trigger_to_pre = pre["ask"]-trigger
    during = response["ask"]-pre["ask"]
    residual = expected-response["ask"]
    pm_change = economics["pmWalk"]["cost"]/minimum - (indicative-trigger)
    total = economics["unitCostPerConditionalPayout"]-indicative
    return {"status": "COMPLETE", "reason": "ACCOUNTING_NOT_CAUSAL",
            "worldExpectedCost": expected, "worldMinCost": worst,
            "triggerToRequestRadarChange": trigger_to_pre,
            "requestToResponseRadarChange": during, "responseRadarToExactExpectedGap": residual,
            "worldExpectedMinusTrigger": expected-trigger, "minOutBuffer": worst-expected,
            "pmCostChange": pm_change, "totalGap": total,
            "identityResidual": total-(trigger_to_pre+during+residual+worst-expected+pm_change),
            "worldUpdatesDuringRequest": len(updates)}


async def _sample_event(event: dict[str, Any], amount_cash: float, *, pm_feed: r2.RollingTimelineBook,
                        dflow: GapRadar,
                        world_decimals: int, pm_fee_schedule: dict[str, Any], cash_decimals: int,
                        http_timeout_seconds: float, clock_offset_seconds: float | None,
                        window_end_ts: int) -> dict[str, Any]:
    side = "yes" if event["pair"] == "WORLD_YES+PM_DOWN" else "no"
    session = AmountSession(amount_cash, cash_decimals, dflow, side)
    pm_session = requests.Session()
    token = str(event["pmToken"])
    try:
        capture = await r3._capture_anchor(event=event, target_at=float(event["triggeredAt"]), label="T0",
            world_session=session, pm_feed=pm_feed, world_decimals=world_decimals,
            pm_fee_schedule=pm_fee_schedule, cash_decimals=cash_decimals,
            http_timeout_seconds=http_timeout_seconds, clock_offset_seconds=clock_offset_seconds,
            window_end_ts=window_end_ts)
        quote = dict(capture.get("worldQuote") or {})
        quote.update({"requestSizeCash": amount_cash, "requestedInputCash": amount_cash, "requestedInAmount": session.requested_in_amount,
                      "requestIdentity": session.request_identity,
                      "requestParameterSource": "PER_EVENT_RANDOM_ASSIGNMENT"})
        capture["worldQuote"] = quote
        verification = await r3._verify_anchor(capture=capture, event=event, pm_session=pm_session,
            pm_feed=pm_feed, pm_fee_schedule=pm_fee_schedule,
            http_timeout_seconds=http_timeout_seconds, clock_offset_seconds=clock_offset_seconds)
        response_state = capture.get("responseState")
        economics = _gap_economics(quote, response_state, pm_fee_schedule, cash_decimals)
        requested = session.requested_in_amount
        response_in = _number(quote.get("inAmount"))
        integrity_ok = verification.get("verified") is True
        input_valid = requested is not None and response_in is not None and response_in == requested
        if not input_valid or not integrity_ok:
            economics["status"] = "UNKNOWN"
            economics["belowOne"] = None
            economics["reason"] = "PM_INTEGRITY_OR_INPUT_UNKNOWN"
        verification["primary"] = economics
        decomposition = _decompose(event, quote, economics, session.pre_snapshot, session.response_snapshot,
                                   session.world_updates, session.trace_overflow)
        return {**event, "sampled": True, "amountCash": amount_cash, "worldQuote": quote,
                "responseAnchor": {"worldResponseAt": capture.get("worldResponseAt"),
                    "responseAnchorStateDigest": capture.get("responseAnchorStateDigest"),
                    "worldRequestStartedAt": session.request_started_at,
                    "worldRequestFinishedAt": session.request_finished_at,
                    "worldUpdatesDuringRequest": session.world_updates,
                    "worldTraceOverflow": session.trace_overflow},
                "radarPreRequest": session.pre_snapshot, "radarResponse": session.response_snapshot,
                "decomposition": decomposition,
                "integrityVerification": verification, "economics": economics,
                "provenance": {"schema": SCHEMA_VERSION, "mode": "PUBLIC_READ_ONLY_NO_TRADE",
                    "actualFillResolved": False, "settlementResolved": False}}
    except Exception as exc:
        return {**event, "sampled": True, "amountCash": amount_cash, "status": "UNKNOWN",
                "error": f"{type(exc).__name__}:{exc}", "provenance": {"schema": SCHEMA_VERSION}}
    finally:
        session.close(); pm_session.close()


async def _run_window(*, window_index: int, start_ts: int, args: argparse.Namespace,
                      cash_decimals: int, clock_offset_seconds: float | None,
                      rng: random.Random) -> dict[str, Any]:
    end_ts = start_ts + r23.WINDOW_SECONDS
    if time.time() < start_ts:
        await asyncio.sleep(start_ts - time.time())
    pm_market = await asyncio.to_thread(r23.wp.fetch_polymarket_market, start_ts)
    stop = asyncio.Event(); pm_feed = r2.RollingTimelineBook([pm_market.up_token, pm_market.down_token])
    pm_task = asyncio.create_task(pm_feed.run(stop))
    try:
        world_market = await asyncio.to_thread(r23.radar._discover_world_market, start_ts, args.rpc_url,
            min(float(end_ts), time.time() + args.world_discovery_timeout_seconds))
        world_error = None
    except Exception as exc:
        world_market = None; world_error = f"{type(exc).__name__}:{exc}"
    row: dict[str, Any] = {"windowIndex": window_index, "startTs": start_ts, "endTs": end_ts,
        "worldDiscoveryError": world_error, "radarPollCount": 0,
        "radarEpisodeCount": {"WORLD_YES+PM_DOWN": 0, "WORLD_NO+PM_UP": 0}, "events": []}
    if world_market is None:
        stop.set(); pm_task.cancel(); await asyncio.gather(pm_task, return_exceptions=True)
        return row
    try:
        yes_decimals, no_decimals = await asyncio.gather(
            asyncio.to_thread(r2._retry_token_decimals, world_market.yes_mint, args.rpc_url),
            asyncio.to_thread(r2._retry_token_decimals, world_market.no_mint, args.rpc_url))
    except BaseException:
        stop.set(); pm_task.cancel(); await asyncio.gather(pm_task, return_exceptions=True)
        raise
    row["worldMarket"] = {"market": world_market.market, "yesMint": world_market.yes_mint, "noMint": world_market.no_mint}
    row["pmMarket"] = {"conditionId": pm_market.condition_id, "upToken": pm_market.up_token, "downToken": pm_market.down_token, "feeSchedule": pm_market.fee_schedule}
    dflow = GapRadar(world_market.yes_mint, world_market.no_mint); dflow_task = asyncio.create_task(dflow.run(stop))
    active = {"WORLD_YES+PM_DOWN": False, "WORLD_NO+PM_UP": False}; last_sampled = {"WORLD_YES+PM_DOWN": -float("inf"), "WORLD_NO+PM_UP": -float("inf")}
    sampled = {"WORLD_YES+PM_DOWN": 0, "WORLD_NO+PM_UP": 0}; pending: list[asyncio.Task[dict[str, Any]]] = []; seq = 0
    candidates = [("WORLD_YES+PM_DOWN", "yes", world_market.yes_mint, yes_decimals, pm_market.down_token), ("WORLD_NO+PM_UP", "no", world_market.no_mint, no_decimals, pm_market.up_token)]
    try:
        while time.time() < end_ts:
            loop_at = time.time(); row["radarPollCount"] += 1; ws = dflow.snapshot(loop_at)
            for pair, side, mint, decimals, token in candidates:
                ps = pm_feed.snapshot(token, clock_offset_seconds=clock_offset_seconds); leg = ws.get(side) or {}
                wa, pa, age = r23._to_float(leg.get("ask")), r23._to_float(ps.get("bestAsk")), r23._to_float(leg.get("quoteAgeSeconds"))
                effective = r23._pm_effective_top_cost(pa, pm_market.fee_schedule) if pa is not None else None
                below = bool(ps.get("healthy") is True and ws.get("healthy") is True and wa is not None and effective is not None and age is not None and age <= r23.MAX_WORLD_RADAR_AGE_SECONDS and wa + effective < 1.0)
                if below and not active[pair]:
                    active[pair] = True; seq += 1; row["radarEpisodeCount"][pair] += 1
                    event = {"eventId": f"w{window_index}-e{seq}", "pair": pair, "triggeredAt": loop_at,
                        "worldMint": mint, "pmToken": token, "indicativeSum": wa + effective,
                        "worldIndicativeAsk": wa, "pmLocalBestAsk": pa,
                        "radarTrigger": {**dict(leg), "healthy": ws.get("healthy") is True, "observedAt": loop_at},
                        "sampled": False, "samplingReason": "COOLDOWN_OR_CAP"}
                    row["events"].append(event)
                    eligible = sampled[pair] < MAX_SAMPLED_EPISODES_PER_PAIR_PER_WINDOW and loop_at - last_sampled[pair] >= SAMPLE_COOLDOWN_SECONDS
                    if eligible:
                        sampled[pair] += 1; last_sampled[pair] = loop_at
                        amount = rng.choice(AMOUNT_CHOICES_CASH)
                        event.update(sampled=True, amountCash=amount, samplingReason="ELIGIBLE_RANDOM_SIZE")
                        pending.append(asyncio.create_task(_sample_event(event, amount, pm_feed=pm_feed, dflow=dflow, world_decimals=decimals, pm_fee_schedule=pm_market.fee_schedule, cash_decimals=cash_decimals, http_timeout_seconds=args.http_timeout_seconds, clock_offset_seconds=clock_offset_seconds, window_end_ts=end_ts)))
                elif not below: active[pair] = False
            await asyncio.sleep(max(0.0, RADAR_POLL_SECONDS - (time.time() - loop_at)))
    finally:
        results = await asyncio.gather(*pending, return_exceptions=True); by_id = {e["eventId"]: e for e in row["events"]}
        for result in results:
            if isinstance(result, Mapping): by_id[result["eventId"]].update(result)
        row["radarFrameCount"] = dflow.frame_count
        row["radarConnectionError"] = dflow.connection_error
        row["pmFeedDiagnostics"] = {"frameCount": pm_feed.frame_count, "reconnectCount": pm_feed.reconnect_count, "errorHistory": pm_feed.error_history}
        stop.set(); dflow_task.cancel(); pm_task.cancel(); await asyncio.gather(dflow_task, pm_task, return_exceptions=True)
    return row


def _self_test() -> None:
    quote = {"success": True, "inAmount": "5000000", "minOutputUiShares": 6.0}
    state = {"asks": [{"price": 0.4, "size": 100.0}]}
    result = _gap_economics(quote, state, {"rate": 0.07, "exponent": 1}, 6)
    assert result["status"] == "QUALIFIED" and result["actualInputCash"] == 5.0
    assert _gap_economics({"success": True}, state, {}, 6)["status"] == "UNKNOWN"
    print("WORLD_PM_WORLD_QUOTE_GAP_R1_SELF_TEST_PASS")


def _summary(windows):
    events = [e for w in windows for e in w.get("events", [])]
    sampled = [e for e in events if e.get("sampled")]
    groups = {}
    for amount in AMOUNT_CHOICES_CASH:
        selected = [e for e in sampled if e.get("amountCash") == amount]
        complete = [e for e in selected if e.get("decomposition", {}).get("status") == "COMPLETE"]
        groups[str(int(amount))] = {"sampled": len(selected), "complete": len(complete),
            "unknown": len(selected)-len(complete),
            "exactBelowOne": sum(e.get("economics", {}).get("belowOne") is True for e in complete),
            "worldResidual": r23._distribution([e["decomposition"]["responseRadarToExactExpectedGap"] for e in complete]),
            "worldMovementDuringRequest": r23._distribution([e["decomposition"]["requestToResponseRadarChange"] for e in complete])}
    completed = sum(g["complete"] for g in groups.values())
    return {"radarEpisodes": len(events), "sampled": len(sampled), "complete": completed,
            "unknown": len(sampled)-completed, "bySizeCash": groups,
            "reference10Cash": groups["10"], "independentWindows": len(windows),
            "verdict": "FEASIBILITY_DATA_COLLECTED_NOT_CAUSAL" if completed else "NO_COMPLETE_DECOMPOSITIONS"}


def _save(path, report):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    temporary.replace(path)


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    windows = []
    report = {"schemaVersion": SCHEMA_VERSION, "protocol": PROTOCOL, "mode": "PUBLIC_READ_ONLY_NO_TRADE",
              "baseR3Commit": "20b3afd6efd587a06bb3eda17b42cd355c6ade6d",
              "seed": args.seed, "amountChoicesCash": list(AMOUNT_CHOICES_CASH), "windowCount": args.windows,
              "btcReference": "UNAVAILABLE_NOT_COLLECTED", "windows": windows, "runState": "RUNNING",
              "sourceSha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob("*.py")}}
    _save(args.out, report)
    try:
        clock = await asyncio.to_thread(r23._calibrate_pm_clock)
        offset = r23._to_float(clock.get("medianServerMinusLocalSeconds"))
        server_now = r23._to_float(clock.get("serverNowEstimate")) or time.time()
        first = r23._next_full_window(server_now)
        report.update(clockCalibration=clock, firstWindowStartTs=first)
        cash_decimals = await asyncio.to_thread(r2._retry_token_decimals, r23.wp.CASH_MINT, args.rpc_url)
        rng = random.Random(args.seed)
        for i in range(args.windows):
            try:
                row = await _run_window(window_index=i+1, start_ts=first+i*r23.WINDOW_SECONDS,
                    args=args, cash_decimals=cash_decimals, clock_offset_seconds=offset, rng=rng)
            except Exception as exc:
                row = {"windowIndex": i+1, "error": f"{type(exc).__name__}:{exc}", "events": []}
            windows.append(row)
            report["summary"] = _summary(windows)
            _save(args.out, report)
            print(json.dumps({"completedWindows": len(windows), "summary": report["summary"]}), flush=True)
        report["runState"] = "COMPLETED"
    except BaseException as exc:
        report["runState"] = "FAILED_OR_INTERRUPTED"
        report["error"] = f"{type(exc).__name__}:{exc}"
        raise
    finally:
        report.update(completedAt=time.time(), summary=_summary(windows))
        _save(args.out, report)
    return report


def main() -> int:
    p = argparse.ArgumentParser(); p.add_argument("--rpc-url", default=r23.radar.DEFAULT_RPC); p.add_argument("--world-discovery-timeout-seconds", type=float, default=90.0); p.add_argument("--http-timeout-seconds", type=float, default=8.0); p.add_argument("--windows", type=int, choices=range(1, 7), default=6); p.add_argument("--seed", type=int, required=True); p.add_argument("--self-test", action="store_true"); p.add_argument("--out", type=Path)
    args = p.parse_args()
    if args.self_test: _self_test(); return 0
    if args.out is None: p.error("--out is required unless --self-test is used")
    if args.out.exists(): p.error("Output exists; use a new filename to preserve evidence")
    asyncio.run(_run(args)); return 0


if __name__ == "__main__": raise SystemExit(main())

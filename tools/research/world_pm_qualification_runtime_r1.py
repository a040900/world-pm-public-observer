"""Execution validity only. Does not classify fills or change economic evidence."""
from __future__ import annotations
import math
import os
from typing import Any


def select_schedule(server_now: float, requested: int | None = None, buffer_seconds: int = 120) -> dict[str, Any]:
    if not math.isfinite(server_now) or buffer_seconds < 120:
        raise ValueError("INVALID_SCHEDULING_CLOCK_OR_BUFFER")
    earliest = math.ceil((server_now + buffer_seconds) / 300) * 300
    if requested is not None and requested % 300:
        raise ValueError("FIRST_WINDOW_START_NOT_ALIGNED")
    first = max(earliest, requested or earliest)
    return {"firstWindowStartTs": first, "requestedFirstWindowStartTs": requested,
            "serverTimeAtSelection": server_now, "safetyBufferSeconds": buffer_seconds,
            "selectionRule": "CEIL_SERVER_NOW_PLUS_FIXED_BUFFER_TO_300_SECONDS",
            "marketStateUsed": False, "rescheduledForDelay": requested is not None and first != requested}


# These are the existing radar cadence/freshness and PM heartbeat budgets.
# No price-availability percentage is a qualification threshold.
POLL_SECONDS = 0.05
WORLD_FRESH_SECONDS = 2.0
PM_LIVENESS_SECONDS = 15.0
COVERAGE_SCHEMA = "WORLD_PM_OBSERVATION_EXPOSURE_R1"


def _number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def observation_state(world, pm, side, now):
    """Availability of the frozen radar decision, not permission to trade.

    An unchanged PM book may fail the frozen entry TTL while its subscribed feed
    is live. That is a known non-admission state. This function never changes the
    entry TTL or permits a candidate. World no_route is known quote unavailability;
    missing messages and other server errors remain UNKNOWN.
    """
    leg = world.get(side) or {}
    received = _number(leg.get("receivedAt"))
    source = _number(leg.get("sourceTimestamp"))
    if (world.get("healthy") is not True or received is None or source is None
            or not 0 <= now - received <= WORLD_FRESH_SECONDS):
        return "UNKNOWN_WORLD", now
    if leg.get("error") == "no_route":
        state = "KNOWN_NO_ROUTE"
    elif leg.get("error") in (None, "", 0) and _number(leg.get("ask")) is not None:
        state = "KNOWN_QUOTE"
    else:
        return "UNKNOWN_WORLD_RESPONSE", now
    last_frame = _number(pm.get("lastFrameAt"))
    if (pm.get("healthy") is not True or pm.get("ready") is not True
            or _number(pm.get("sourceTimestampMs")) is None or last_frame is None
            or not 0 <= now - last_frame <= PM_LIVENESS_SECONDS):
        return "UNKNOWN_PM", now
    return state, min(received + WORLD_FRESH_SECONDS, last_frame + PM_LIVENESS_SECONDS)


class ObservationCoverage:
    """Wall-time exposure with bounded freshness leases, including both tails.

    A later snapshot never fills a prior UNKNOWN gap. The independent sampler runs
    during asynchronous candidate evaluation. A blocked event loop cannot extend
    coverage beyond the last observed source's existing freshness lease.
    """
    def __init__(self, start, end):
        self.start, self.end = float(start), float(end)
        self.last = None
        self.previous = {}
        self.intervals = {side: [] for side in ("yes", "no")}
        self.state_seconds = {side: {} for side in ("yes", "no")}
        self.ready_at = None
        self.samples = 0

    def _advance(self, now):
        if self.last is None:
            return
        lo, hi = max(self.start, self.last), min(self.end, now)
        if hi <= lo:
            return
        for side, (state, expiry) in self.previous.items():
            known_end = min(hi, expiry) if state.startswith("KNOWN_") else lo
            if known_end > lo:
                rows = self.intervals[side]
                if rows and lo <= rows[-1][1] + 1e-9:
                    rows[-1][1] = max(rows[-1][1], known_end)
                else:
                    rows.append([lo, known_end])
                totals = self.state_seconds[side]
                totals[state] = totals.get(state, 0.0) + known_end - lo

    def observe(self, now, world, pm_by_side):
        if self.last is not None and now < self.last:
            raise ValueError("OBSERVATION_CLOCK_MOVED_BACKWARDS")
        self._advance(now)
        self.previous = {side: observation_state(world, pm_by_side[side], side, now)
                         for side in ("yes", "no")}
        self.last = now
        self.samples += 1
        if self.ready_at is None and all(s.startswith("KNOWN_") for s, _ in self.previous.values()):
            self.ready_at = now

    def finish(self, now):
        self._advance(min(now, self.end))
        self.last = min(now, self.end)
        pairs = {}
        for side, rows in self.intervals.items():
            known = sum(b-a for a, b in rows)
            cursor, gaps = self.start, []
            for a, b in rows:
                if a > cursor + 1e-9:
                    gaps.append([cursor, a])
                cursor = max(cursor, b)
            if cursor < self.end - 1e-9:
                gaps.append([cursor, self.end])
            pairs[side] = {"observedSeconds": known,
                           "unknownSeconds": max(0.0, self.end-self.start-known),
                           "unknownIntervals": gaps,
                           "stateSeconds": self.state_seconds[side]}
        return {"schemaVersion": COVERAGE_SCHEMA, "startTs": self.start, "endTs": self.end,
                "requestedSeconds": self.end-self.start, "sampleCount": self.samples,
                "readinessObservedAt": self.ready_at, "pairs": pairs,
                "complete": all(not x["unknownIntervals"] for x in pairs.values()),
                "rule": "KNOWN_QUOTE_OR_EXPLICIT_NO_ROUTE_WITH_LIVE_PM_NO_UNKNOWN_INTERVALS"}


def observe_health(row, world_snapshot, pm_snapshots, observed_at):
    # Legacy poll diagnostics retained. Qualification uses the independent time trace.
    row.setdefault("measurementLoopEnteredAt", observed_at)
    world_ok = world_snapshot.get("healthy") is True and all(
        (world_snapshot.get(side) or {}).get("error") in (None, "", 0)
        and (world_snapshot.get(side) or {}).get("ask") is not None
        and (world_snapshot.get(side) or {}).get("quoteAgeSeconds") is not None
        and 0 <= world_snapshot[side]["quoteAgeSeconds"] <= 2.0 for side in ("yes", "no"))
    pm_ok = all(s.get("healthy") is True and s.get("sourceTimestampMs") is not None
                and -250 <= observed_at * 1000 - float(s["sourceTimestampMs"]) <= 1000
                for s in pm_snapshots)
    if world_ok and pm_ok:
        row["measurementAuthorityHealthyPollCount"] = row.get("measurementAuthorityHealthyPollCount", 0) + 1


def qualify_window(row):
    reasons = []
    if not row.get("worldMarket"):
        reasons.append("WORLD_DISCOVERY_UNAVAILABLE")
    if not row.get("pmMarket"):
        reasons.append("POLYMARKET_DISCOVERY_UNAVAILABLE")
    if row.get("worldDiscoveryError"):
        reasons.append("WORLD_DISCOVERY_ERROR:" + row["worldDiscoveryError"])
    if row.get("executionError"):
        reasons.append("EXECUTION_ERROR:" + row["executionError"])
    if not row.get("radarPollCount"):
        reasons.append("MEASUREMENT_LOOP_NOT_ENTERED")
    coverage = row.get("observationCoverage") or {}
    if coverage.get("schemaVersion") != COVERAGE_SCHEMA or coverage.get("complete") is not True:
        reasons.append("OBSERVATION_EXPOSURE_INCOMPLETE")
    ready = _number(coverage.get("readinessObservedAt"))
    start = _number(row.get("startTs"))
    entered = _number(row.get("measurementLoopEnteredAt"))
    if ready is None or start is None or ready > start:
        reasons.append("PRESTART_READINESS_NOT_ESTABLISHED")
    if entered is None or start is None or not start <= entered <= start + POLL_SECONDS:
        reasons.append("MEASUREMENT_START_NOT_ON_SCHEDULE")
    if row.get("measurementLoopCompleted") is not True:
        reasons.append("MEASUREMENT_LOOP_NOT_COMPLETED")
    row.update(qualificationValid=not reasons,
               executionStatus="VALID_QUALIFICATION_WINDOW" if not reasons else "INVALID_EXECUTION_WINDOW",
               invalidExecutionReasons=reasons,
               candidateCountEligibleForDenominator=not reasons)
    return row


def qualify_batch(windows, requested, *, phase_a_eligible=None):
    if phase_a_eligible is None:
        phase_a_eligible = os.environ.get("WORLD_PM_PHASE_A_ELIGIBLE", "1") == "1"
    # Missing rows are explicitly execution-invalid, never a smaller silent denominator.
    valid = sum(w.get("qualificationValid") is True for w in windows)
    complete = len(windows) == requested and valid == requested
    return {"requestedWindowCount": requested, "observedWindowCount": len(windows),
            "validQualificationWindowCount": valid,
            "invalidExecutionWindowCount": sum(w.get("qualificationValid") is not True for w in windows),
            "missingExecutionWindowCount": max(0, requested - len(windows)),
            "qualificationValid": complete,
            "qualificationStatus": "COMPLETE_QUALIFICATION_BATCH" if complete else "INCOMPLETE_QUALIFICATION_BATCH",
            "qualificationPurposeEligible": phase_a_eligible,
            "phaseAEligibleWindowCount": requested if complete and phase_a_eligible else 0,
            "invalidExecutionReasons": [{"startTs": w.get("startTs"), "reasons": w.get("invalidExecutionReasons", ["VALIDITY_NOT_ESTABLISHED"])}
                                        for w in windows if w.get("qualificationValid") is not True]}

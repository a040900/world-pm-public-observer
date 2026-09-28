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
COVERAGE_SCHEMA = "WORLD_PM_OBSERVATION_EXPOSURE_R2"
PAIR_NAMES = {"yes": "WORLD_YES+PM_DOWN", "no": "WORLD_NO+PM_UP"}


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
        self.accounting_error = None

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
        if not math.isfinite(now) or (self.last is not None and now < self.last):
            self.accounting_error = "OBSERVATION_CLOCK_MOVED_BACKWARDS_OR_INVALID"
            raise ValueError("OBSERVATION_CLOCK_MOVED_BACKWARDS")
        self._advance(now)
        self.previous = {side: observation_state(world, pm_by_side[side], side, now)
                         for side in ("yes", "no")}
        self.last = now
        self.samples += 1
        if self.ready_at is None and all(s.startswith("KNOWN_") for s, _ in self.previous.values()):
            self.ready_at = now

    def finish(self, now):
        if not math.isfinite(now) or (self.last is not None and now < self.last):
            raise ValueError("OBSERVATION_CLOCK_INVALID_AT_FINISH")
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
                           "knownIntervals": [list(r) for r in rows],
                           "scheduledSeconds": self.end-self.start,
                           "observationAvailability": known/(self.end-self.start),
                           "unknownIntervals": gaps,
                           "stateSeconds": self.state_seconds[side]}
        joint = _exposure(_intersection(self.intervals["yes"], self.intervals["no"]), self.end-self.start)
        return {"schemaVersion": COVERAGE_SCHEMA, "startTs": self.start, "endTs": self.end,
                **joint, "accountingError": self.accounting_error,
                "requestedSeconds": self.end-self.start, "sampleCount": self.samples,
                "readinessObservedAt": self.ready_at, "pairs": pairs,
                "complete": all(not x["unknownIntervals"] for x in pairs.values()),
                "rule": "KNOWN_QUOTE_OR_EXPLICIT_NO_ROUTE_WITH_LIVE_PM_PARTIAL_EXPOSURE_ALLOWED"}


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


def _intersection(left, right):
    result = []
    i = j = 0
    while i < len(left) and j < len(right):
        lo, hi = max(left[i][0], right[j][0]), min(left[i][1], right[j][1])
        if hi > lo:
            result.append([lo, hi])
        if left[i][1] <= right[j][1]:
            i += 1
        else:
            j += 1
    return result


def _exposure(intervals, scheduled):
    observed = sum(b-a for a, b in intervals)
    return {"observedSeconds": observed, "unknownSeconds": max(0.0, scheduled-observed),
            "scheduledSeconds": scheduled,
            "observationAvailability": observed/scheduled if scheduled else None,
            "knownIntervals": intervals}


def _validated_intervals(rows, start, end, entered):
    if not isinstance(rows, list):
        raise ValueError("MISSING_KNOWN_INTERVALS")
    result, previous = [], start
    for interval in rows:
        if not isinstance(interval, list) or len(interval) != 2:
            raise ValueError("INVALID_INTERVAL")
        a, b = map(_number, interval)
        if a is None or b is None or not start <= a < b <= end or a < previous:
            raise ValueError("INVALID_INTERVAL_BOUNDS")
        previous = b
        if b > entered:
            result.append([max(a, entered), b])
    return result


def _candidate_time(candidate):
    for key in ("radarObservedAt", "triggeredAt", "placedAt"):
        if key in candidate:
            return _number(candidate[key])
    return None


def _frequency(candidates, exposure):
    observed, scheduled = exposure["observedSeconds"], exposure["scheduledSeconds"]
    matched = sum(any(a <= when < b for a, b in exposure["knownIntervals"])
                  for c in candidates if (when := _candidate_time(c)) is not None)
    return {"strategyCandidateCount": matched,
            "candidateCountOutsideObservedExposure": len(candidates)-matched,
            "strategyCandidatesPerObservedSecond": matched/observed if observed else None,
            "operationalCandidateCount": len(candidates),
            "operationalCandidatesPerScheduledSecond": len(candidates)/scheduled if scheduled else None}


def qualify_window(row):
    reasons = []
    start = _number(row.get("startTs"))
    end = _number(row.get("endTs"))
    if start is not None and end is None:
        end = start + 300
    entered = _number(row.get("measurementLoopEnteredAt"))
    if start is None or end != start + 300:
        reasons.append("INVALID_SCHEDULED_WINDOW")
    if not row.get("worldMarket"):
        reasons.append("WORLD_DISCOVERY_UNAVAILABLE")
    if not row.get("pmMarket"):
        reasons.append("POLYMARKET_DISCOVERY_UNAVAILABLE")
    if row.get("worldDiscoveryError"):
        reasons.append("WORLD_DISCOVERY_ERROR:" + row["worldDiscoveryError"])
    if row.get("executionError"):
        reasons.append("EXECUTION_ERROR:" + row["executionError"])
    if not row.get("radarPollCount") or entered is None or start is None or not start <= entered < end:
        reasons.append("MEASUREMENT_LOOP_NOT_ENTERED")
    if row.get("measurementLoopCompleted") is not True:
        reasons.append("MEASUREMENT_LOOP_NOT_COMPLETED")
    coverage = row.get("observationCoverage") or {}
    pairs = {side: _exposure([], 300.0) for side in PAIR_NAMES}
    try:
        if (coverage.get("schemaVersion") != COVERAGE_SCHEMA or coverage.get("accountingError")
                or coverage.get("startTs") != start or coverage.get("endTs") != end
                or entered is None or not coverage.get("sampleCount")):
            raise ValueError("UNTRUSTWORTHY_COVERAGE")
        pairs = {side: _exposure(_validated_intervals(coverage["pairs"][side]["knownIntervals"],
                                                     start, end, entered), 300.0)
                 for side in PAIR_NAMES}
    except (KeyError, TypeError, ValueError):
        reasons.append("OBSERVATION_ACCOUNTING_FAILED")
    if not any(p["observedSeconds"] > 0 for p in pairs.values()):
        reasons.append("NO_KNOWN_OBSERVATION")
    if reasons:
        # Raw coverage and candidate records remain evidence. An execution-invalid
        # row cannot contribute trusted strategy exposure or a zero-candidate claim.
        pairs = {side: _exposure([], 300.0) for side in PAIR_NAMES}
    joint = _exposure(_intersection(pairs["yes"]["knownIntervals"], pairs["no"]["knownIntervals"]), 300.0)
    candidates = row.get("shadowCandidates", row.get("candidates", []))
    for side, name in PAIR_NAMES.items():
        pairs[side].update(_frequency([c for c in candidates if c.get("pair") == name], pairs[side]))
    joint.update(_frequency(candidates, joint))
    row.update(joint)
    row.update(qualificationValid=not reasons,
               executionStatus="VALID_QUALIFICATION_WINDOW" if not reasons else "INVALID_EXECUTION_WINDOW",
               invalidExecutionReasons=reasons, candidateCountEligibleForDenominator=not reasons,
               denominatorPolicy="DUAL_OBSERVED_AND_SCHEDULED_SECONDS_R1",
               observationScope="BOTH_PAIRS_SIMULTANEOUS; SINGLE_PAIR_EXPOSURE_REPORTED_SEPARATELY",
               pairDenominators=pairs)
    return row


def _aggregate_exposure(windows, scheduled):
    observed = sum(w.get("observedSeconds", 0.0) for w in windows if w.get("qualificationValid") is True)
    strategy_count = sum(w.get("strategyCandidateCount", 0) for w in windows if w.get("qualificationValid") is True)
    operational_count = sum(w.get("operationalCandidateCount", 0) for w in windows)
    return {"scheduledSeconds": scheduled, "observedSeconds": observed,
            "unknownSeconds": scheduled-observed,
            "observationAvailability": observed/scheduled if scheduled else None,
            "strategyCandidateCount": strategy_count,
            "strategyCandidatesPerObservedSecond": strategy_count/observed if observed else None,
            "operationalCandidateCount": operational_count,
            "operationalCandidatesPerScheduledSecond": operational_count/scheduled if scheduled else None}


def qualify_batch(windows, requested, *, phase_a_eligible=None):
    if phase_a_eligible is None:
        phase_a_eligible = os.environ.get("WORLD_PM_PHASE_A_ELIGIBLE", "1") == "1"
    valid = sum(w.get("qualificationValid") is True for w in windows)
    complete = len(windows) == requested and valid == requested
    result = {"requestedWindowCount": requested, "observedWindowCount": len(windows),
              "validQualificationWindowCount": valid,
              "invalidExecutionWindowCount": sum(w.get("qualificationValid") is not True for w in windows),
              "missingExecutionWindowCount": max(0, requested-len(windows)),
              "qualificationValid": complete,
              "qualificationStatus": "COMPLETE_QUALIFICATION_BATCH" if complete else "INCOMPLETE_QUALIFICATION_BATCH",
              "qualificationPurposeEligible": phase_a_eligible,
              "phaseAEligibleWindowCount": requested if complete and phase_a_eligible else 0,
              "denominatorPolicy": "DUAL_OBSERVED_AND_SCHEDULED_SECONDS_R1",
              "observationScope": "BOTH_PAIRS_SIMULTANEOUS; SINGLE_PAIR_EXPOSURE_REPORTED_SEPARATELY",
              "invalidExecutionReasons": [{"startTs": w.get("startTs"), "reasons": w.get("invalidExecutionReasons", ["VALIDITY_NOT_ESTABLISHED"])}
                                          for w in windows if w.get("qualificationValid") is not True]}
    result.update(_aggregate_exposure(windows, requested*300.0))
    result["pairDenominators"] = {
        side: _aggregate_exposure([{**w.get("pairDenominators", {}).get(side, {}),
                                    "qualificationValid": w.get("qualificationValid")} for w in windows], requested*300.0)
        for side in PAIR_NAMES}
    return result


def qualify_paired_batch(reports, expected, *, phase_a_eligible=True):
    """Keep role frequencies separate; the shared wall clock is counted once."""
    paired = []
    for start in expected:
        role_rows = {role: [w for w in report.get("windows", []) if w.get("startTs") == start]
                     for role, report in reports.items()}
        valid = bool(role_rows) and all(len(rows) == 1 and rows[0].get("qualificationValid") is True
                                       for rows in role_rows.values())
        pair_exposure = {}
        for side in PAIR_NAMES:
            intervals = [[start, start+300]] if valid else []
            for rows in role_rows.values():
                if valid:
                    intervals = _intersection(intervals, rows[0]["pairDenominators"][side]["knownIntervals"])
            pair_exposure[side] = _exposure(intervals, 300.0)
        joint = _exposure(_intersection(pair_exposure["yes"]["knownIntervals"],
                                        pair_exposure["no"]["knownIntervals"]), 300.0)
        paired.append({"startTs": start, "qualificationValid": valid, **joint,
                       "pairDenominators": pair_exposure,
                       "invalidExecutionReasons": [f"{role}:{rows[0].get('invalidExecutionReasons') if len(rows) == 1 else 'MISSING_OR_DUPLICATE_WINDOW'}"
                                                   for role, rows in role_rows.items()
                                                   if len(rows) != 1 or rows[0].get("qualificationValid") is not True]})
    result = qualify_batch(paired, len(expected), phase_a_eligible=phase_a_eligible)
    # Maker and taker are different strategies. Combining their candidate numerators
    # with their common calendar exposure would manufacture an economic frequency.
    for target in [result, *result["pairDenominators"].values()]:
        for key in ("strategyCandidateCount", "strategyCandidatesPerObservedSecond",
                    "operationalCandidateCount", "operationalCandidatesPerScheduledSecond"):
            target[key] = None
    result["frequencyScope"] = "SEPARATE_ROLE_REPORTS_ONLY"
    result["windows"] = paired
    result["roles"] = {role: report.get("qualification") for role, report in reports.items()}
    return result

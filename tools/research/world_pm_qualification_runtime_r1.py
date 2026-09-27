"""Execution validity only. Does not classify fills or change economic evidence."""
from __future__ import annotations
import math
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


def observe_health(row, world_snapshot, pm_snapshots, observed_at):
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
    if not row.get("measurementAuthorityHealthyPollCount"):
        reasons.append("MEASUREMENT_AUTHORITY_NOT_OBSERVED_HEALTHY")
    if row.get("measurementLoopCompleted") is not True:
        reasons.append("MEASUREMENT_LOOP_NOT_COMPLETED")
    row.update(qualificationValid=not reasons,
               executionStatus="VALID_QUALIFICATION_WINDOW" if not reasons else "INVALID_EXECUTION_WINDOW",
               invalidExecutionReasons=reasons,
               candidateCountEligibleForDenominator=not reasons)
    return row


def qualify_batch(windows, requested):
    # Missing rows are explicitly execution-invalid, never a smaller silent denominator.
    valid = sum(w.get("qualificationValid") is True for w in windows)
    complete = len(windows) == requested and valid == requested
    return {"requestedWindowCount": requested, "observedWindowCount": len(windows),
            "validQualificationWindowCount": valid,
            "invalidExecutionWindowCount": sum(w.get("qualificationValid") is not True for w in windows),
            "missingExecutionWindowCount": max(0, requested - len(windows)),
            "qualificationValid": complete,
            "qualificationStatus": "COMPLETE_QUALIFICATION_BATCH" if complete else "INCOMPLETE_QUALIFICATION_BATCH",
            "phaseAEligibleWindowCount": requested if complete else 0,
            "invalidExecutionReasons": [{"startTs": w.get("startTs"), "reasons": w.get("invalidExecutionReasons", ["VALIDITY_NOT_ESTABLISHED"])}
                                        for w in windows if w.get("qualificationValid") is not True]}

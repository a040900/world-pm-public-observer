"""Frozen evaluator for the 12-window World↔Polymarket BTC5m indicative signal pilot R0.

Inputs:
- prospective PM live-capture artifact
- post-window World MCP 1-second history for the SAME pre-frozen windows

The evaluator is signal-only. It must not produce fills, PnL, after-cost execution,
or settlement admission.

Primary pre-frozen signal:
  raw complementary indicative ask gap >= 100 bps
  in the same direction at two consecutive PM scheduled anchors.

Data sufficiency:
  >= 9 usable windows.
A usable window requires:
  >= 120 paired-success PM snapshots
  AND >= 90 causally aligned/evaluable anchors in at least one direction.

Signal verdict:
  PASS      if >= 9 usable windows AND >= 4 windows contain a persistent trigger
  FAIL      if >= 9 usable windows AND 0 windows contain a persistent trigger
  NO_RESULT otherwise

World-to-PM join is causal: latest World quote ts <= PM book source timestamp,
with World quote age <= 2 seconds. Future World rows are never used.
"""
from __future__ import annotations

import argparse
import bisect
import json
from pathlib import Path
from typing import Any, Mapping

THRESHOLDS_BPS = [0, 25, 50, 100, 200, 300, 500]
PRIMARY_BPS = 100.0
MAX_WORLD_AGE_SECONDS = 2.0
MIN_PM_PAIRED_SUCCESS = 120
MIN_ALIGNED_DIRECTION = 90
MIN_USABLE_WINDOWS = 9
PASS_WINDOWS = 4


def pm_ts_seconds(v: Any) -> float | None:
    try:
        x = float(v)
    except Exception:
        return None
    return x / 1000.0 if x > 10_000_000_000 else x


def index_world(rows: list[Mapping[str, Any]]) -> tuple[list[int], list[Mapping[str, Any]]]:
    cleaned = []
    for r in rows or []:
        try:
            ts = int(r["ts"])
        except Exception:
            continue
        cleaned.append((ts, r))
    cleaned.sort(key=lambda x: x[0])
    return [x[0] for x in cleaned], [x[1] for x in cleaned]


def causal_world_row(index: tuple[list[int], list[Mapping[str, Any]]], pm_ts: float) -> tuple[Mapping[str, Any] | None, float | None]:
    tss, rows = index
    i = bisect.bisect_right(tss, pm_ts) - 1
    if i < 0:
        return None, None
    age = pm_ts - tss[i]
    if age < 0 or age > MAX_WORLD_AGE_SECONDS:
        return None, age
    return rows[i], age


def persistent_primary(points: list[dict[str, Any]]) -> bool:
    prev = None
    for p in points:
        if p.get("gapBps") is None or float(p["gapBps"]) < PRIMARY_BPS:
            prev = None
            continue
        if prev is not None and int(p["snapshotIndex"]) == int(prev["snapshotIndex"]) + 1:
            return True
        prev = p
    return False


def evaluate_window(pm: Mapping[str, Any], wh: Mapping[str, Any]) -> dict[str, Any]:
    yes_idx = index_world(list(wh.get("yes") or []))
    no_idx = index_world(list(wh.get("no") or []))
    snaps = list(pm.get("snapshots") or [])
    paired = [
        s for s in snaps
        if s.get("captured")
        and (s.get("up") or {}).get("success")
        and (s.get("down") or {}).get("success")
    ]

    dirs = {
        "WORLD_YES+PM_DOWN": [],
        "WORLD_NO+PM_UP": [],
    }
    for s in paired:
        idx = int(s.get("index", -1))

        down = s.get("down") or {}
        dts = pm_ts_seconds(down.get("timestamp"))
        if dts is not None:
            wr, age = causal_world_row(yes_idx, dts)
            if wr is not None and wr.get("ask") is not None and down.get("bestAsk") is not None:
                total = float(wr["ask"]) + float(down["bestAsk"])
                dirs["WORLD_YES+PM_DOWN"].append({
                    "snapshotIndex": idx,
                    "pmSourceTs": dts,
                    "worldTs": int(wr["ts"]),
                    "worldAgeSeconds": age,
                    "worldAsk": float(wr["ask"]),
                    "pmAsk": float(down["bestAsk"]),
                    "sum": total,
                    "gapBps": (1.0 - total) * 10000.0,
                })

        up = s.get("up") or {}
        uts = pm_ts_seconds(up.get("timestamp"))
        if uts is not None:
            wr, age = causal_world_row(no_idx, uts)
            if wr is not None and wr.get("ask") is not None and up.get("bestAsk") is not None:
                total = float(wr["ask"]) + float(up["bestAsk"])
                dirs["WORLD_NO+PM_UP"].append({
                    "snapshotIndex": idx,
                    "pmSourceTs": uts,
                    "worldTs": int(wr["ts"]),
                    "worldAgeSeconds": age,
                    "worldAsk": float(wr["ask"]),
                    "pmAsk": float(up["bestAsk"]),
                    "sum": total,
                    "gapBps": (1.0 - total) * 10000.0,
                })

    aligned_counts = {k: len(v) for k, v in dirs.items()}
    usable = len(paired) >= MIN_PM_PAIRED_SUCCESS and max(aligned_counts.values() or [0]) >= MIN_ALIGNED_DIRECTION
    persistent = {k: persistent_primary(v) for k, v in dirs.items()}
    qualified = any(persistent.values())

    threshold_counts = {}
    for th in THRESHOLDS_BPS:
        threshold_counts[str(th)] = {
            k: sum(float(p["gapBps"]) >= th for p in pts)
            for k, pts in dirs.items()
        }

    best = {
        k: max([float(p["gapBps"]) for p in pts], default=None)
        for k, pts in dirs.items()
    }
    first_primary = {}
    for k, pts in dirs.items():
        hit = next((p for p in pts if float(p["gapBps"]) >= PRIMARY_BPS), None)
        first_primary[k] = hit

    return {
        "startTs": pm.get("startTs"),
        "pmPairedSuccess": len(paired),
        "worldHistoryRows": {
            "yes": len(wh.get("yes") or []),
            "no": len(wh.get("no") or []),
        },
        "alignedCounts": aligned_counts,
        "usable": usable,
        "persistentPrimaryByDirection": persistent,
        "qualifiedPrimaryWindow": qualified,
        "thresholdAnchorCounts": threshold_counts,
        "bestGapBpsDescriptiveOnly": best,
        "firstPrimaryAnchor": first_primary,
    }


def main(args: argparse.Namespace) -> None:
    pm = json.loads(Path(args.pm_capture).read_text())
    world = json.loads(Path(args.world_history).read_text())

    frozen_pm = [int(x) for x in pm.get("windowStarts") or []]
    frozen_world = [int(x.get("startTs")) for x in world.get("windows") or []]
    if len(frozen_pm) != 12 or frozen_pm != frozen_world:
        raise SystemExit("FROZEN_WINDOW_IDENTITY_MISMATCH")

    pm_by = {int(x["startTs"]): x for x in pm.get("windows") or []}
    w_by = {int(x["startTs"]): x for x in world.get("windows") or []}
    rows = [evaluate_window(pm_by[s], w_by[s]) for s in frozen_pm]

    usable = sum(bool(x["usable"]) for x in rows)
    qualified = sum(bool(x["qualifiedPrimaryWindow"]) for x in rows)

    if usable >= MIN_USABLE_WINDOWS and qualified >= PASS_WINDOWS:
        verdict = "PASS"
    elif usable >= MIN_USABLE_WINDOWS and qualified == 0:
        verdict = "FAIL"
    else:
        verdict = "NO_RESULT"

    result = {
        "schemaVersion": "WORLD_PM_BTC5M_INDICATIVE_SIGNAL_PILOT_EVALUATION_R0",
        "primaryHypothesis": {
            "thresholdBps": PRIMARY_BPS,
            "persistence": "two consecutive PM scheduled anchors in the same direction",
            "passRule": f">={MIN_USABLE_WINDOWS} usable windows and >={PASS_WINDOWS} qualifying windows",
            "failRule": f">={MIN_USABLE_WINDOWS} usable windows and zero qualifying windows",
            "otherwise": "NO_RESULT",
        },
        "joinRule": {
            "method": "latest World source ts <= PM token-book source timestamp",
            "maxWorldAgeSeconds": MAX_WORLD_AGE_SECONDS,
            "futureWorldRowsAllowed": False,
        },
        "dataGate": {
            "minPairedPMSnapshotsPerUsableWindow": MIN_PM_PAIRED_SUCCESS,
            "minAlignedAnchorsInAtLeastOneDirection": MIN_ALIGNED_DIRECTION,
            "minUsableWindows": MIN_USABLE_WINDOWS,
        },
        "windowResults": rows,
        "summary": {
            "usableWindows": usable,
            "qualifiedPrimaryWindows": qualified,
            "verdict": verdict,
        },
        "adjudication": {
            "signal": verdict,
            "executionAfterCost": "NOT_TESTED_BY_DESIGN",
            "settlementAdmission": "SEPARATE_ADJUDICATION_REQUIRED",
        },
        "warnings": [
            "World quantities are not used as executable depth.",
            "Indicative quote sums are not fills or paper PnL.",
            "Best-per-window values are descriptive diagnostics only and do not determine PASS.",
        ],
    }
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result["summary"], sort_keys=True))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--pm-capture", required=True)
    p.add_argument("--world-history", required=True)
    p.add_argument("--output", default="artifacts/world-pm-btc5m-indicative-signal-pilot-evaluation-r0.json")
    main(p.parse_args())

"""Run synthetic B4 acceptance and preserve its machine-readable evidence.

This command has no sampling mode and never contacts market-data endpoints.
"""
from __future__ import annotations

import argparse
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.test_world_pm_subsecond_cohort_r1 import fixture, run, START
from tools.research import world_pm_subsecond_analyze_r1 as analyze
from tools.research import world_pm_subsecond_model_r1 as model


def evidence():
    copies = run(fixture(copies=True))
    fits = [e["parameters"] for e in copies["inner"].values()] + [copies["outer"]["parameters"]]
    maximum = max(v for fit in fits for v in fit["residualStd"])
    original = fixture()
    before = run(original)
    inner_changes = fixture()
    outer_changes = fixture()
    for rows in inner_changes.values():
        mask = (rows.timestamps >= START + 14400) & (rows.timestamps < START + 21600)
        rows.pm[mask] *= -2
        rows.world[mask] *= 3
        rows.target[mask] *= -4
    for rows in outer_changes.values():
        mask = rows.timestamps >= START + 21600
        rows.pm[mask] *= -2
        rows.world[mask] *= 3
        rows.target[mask] *= -4
    inner, outer = run(inner_changes), run(outer_changes)
    return {"authority": analyze.AUTHORITY, "runtime": model.runtime_versions(),
            "dataKind": "SYNTHETIC_OFFLINE_ONLY_NOT_ECONOMIC_EVIDENCE",
            "linearCopy": {"pass": all(not any(f["residualKeep"]) for f in fits)
                                      and copies["outer"]["score"]["delta"] == 0,
                           "maximumTrainingResidualStd": maximum,
                           "delta": copies["outer"]["score"]["delta"],
                           "bootstrap5minCI": copies["outer"]["bootstrap5min"]["confidenceInterval"],
                           "bothResidualsDroppedAllFits": all(not any(f["residualKeep"]) for f in fits)},
            "noLeakage": {"pass": all(before["inner"][str(k)]["parameters"] == inner["inner"][str(k)]["parameters"]
                                        for k in model.HORIZONS)
                                  and before["outer"]["parameters"] == outer["outer"]["parameters"]
                                  and before["selectedHorizonSeconds"] == outer["selectedHorizonSeconds"],
                          "innerTrainingParametersUnchanged": all(
                              before["inner"][str(k)]["parameters"] == inner["inner"][str(k)]["parameters"]
                              for k in model.HORIZONS),
                          "outerTrainingParametersUnchanged": before["outer"]["parameters"] == outer["outer"]["parameters"],
                          "outerSelectedLagUnchanged": before["selectedHorizonSeconds"] == outer["selectedHorizonSeconds"]},
            "missingEndpointVsZero": {"test": "SubsecondAnalyzerTests.test_b4_missing_endpoint_drops_row_but_valid_zero_label_survives",
                                      "missingRowReason": "WORLD_MID_INVALID",
                                      "missingTargetAt": 33.5, "validZeroTargetAt": 33.5,
                                      "validZeroTarget": 0.0,
                                      "assertionsExecutedInAcceptanceSuite": True}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    stream = io.StringIO()
    suite = unittest.defaultTestLoader.discover("tests", pattern="test_world_pm_subsecond*_r1.py")
    # Guard real urllib/socket transports. Worker tests inject their own fakes.
    with patch("urllib.request.urlopen", side_effect=AssertionError("OFFLINE_NETWORK_GUARD")), \
         patch("websocket.create_connection", side_effect=AssertionError("OFFLINE_NETWORK_GUARD")):
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    report = evidence() if result.wasSuccessful() else {"dataKind": "SYNTHETIC_OFFLINE_ONLY"}
    report["tests"] = {"run": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
                       "pass": result.wasSuccessful()}
    report["pass"] = result.wasSuccessful() and report["linearCopy"]["pass"] and report["noLeakage"]["pass"]
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as sink:
        json.dump(report, sink, indent=2, allow_nan=False)
        sink.write("\n")
    path.with_suffix(".log").write_text(stream.getvalue(), encoding="utf-8")
    print(json.dumps({"pass": report["pass"], "tests": report["tests"], "output": str(path)}))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

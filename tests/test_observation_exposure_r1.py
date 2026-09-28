from __future__ import annotations

import asyncio
import copy
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tools.research import world_pm_qualification_runtime_r1 as q
from tools.research import world_pm_window_preparation_r1 as preparation


def _leg(now: float, state: str) -> dict:
    if state == "no_route":
        return {"error": "no_route", "receivedAt": now,
                "sourceTimestamp": now, "ask": None}
    if state == "quote":
        return {"error": None, "receivedAt": now,
                "sourceTimestamp": now, "ask": 0.5}
    if state == "server_error":
        return {"error": "HTTP_502", "receivedAt": now,
                "sourceTimestamp": now, "ask": None}
    if state == "stale":
        old = now - q.WORLD_FRESH_SECONDS - 0.1
        return {"error": None, "receivedAt": old, "sourceTimestamp": old, "ask": 0.5}
    if state == "disconnected":
        return {"error": None, "receivedAt": now, "sourceTimestamp": now, "ask": 0.5}
    raise ValueError(state)


def world(now: float, *, state: str = "quote") -> dict:
    return {"healthy": state != "disconnected", "yes": _leg(now, state), "no": _leg(now, state)}


def world_sides(now: float, *, yes: str = "quote", no: str = "quote") -> dict:
    return {"healthy": True, "yes": _leg(now, yes), "no": _leg(now, no)}


def pm(now: float, *, live: bool = True, last_frame_age: float = 0.0) -> dict:
    return {"healthy": live, "ready": True, "sourceTimestampMs": 1,
            "lastFrameAt": now - last_frame_age}


def full_known_coverage(start: float, end: float, *, state: str = "quote",
                        last_sample_at: float | None = None):
    """Build deterministic exposure; freshness leases cap both tails."""
    coverage = q.ObservationCoverage(start, end)
    last_sample_at = end if last_sample_at is None else last_sample_at
    steps = int(round((last_sample_at-start)/q.POLL_SECONDS))
    for i in range(steps+1):
        t = start + i*q.POLL_SECONDS
        coverage.observe(t, world(t, state=state), {"yes": pm(t), "no": pm(t)})
    return coverage


def qualification_row(start: float, coverage: dict, *, loop_entered: float | None = None,
                      completed: bool = True, execution_error: str | None = None,
                      evidence: dict | None = None, candidates: list | None = None) -> dict:
    row = {"startTs": start, "endTs": start+300,
           "measurementLoopEnteredAt": start if loop_entered is None else loop_entered,
           "worldMarket": {"market": "w"}, "pmMarket": {"conditionId": "p"},
           "radarPollCount": 1, "measurementLoopCompleted": completed,
           "observationCoverage": coverage, "shadowCandidates": candidates or []}
    if execution_error is not None:
        row["executionError"] = execution_error
    if evidence is not None:
        row["candidate"] = evidence
    return row


class ObservationExposureTests(unittest.TestCase):
    def test_282_observed_18_unknown_is_valid_and_reports_dual_denominators(self):
        start, end = 1000.0, 1300.0
        result = full_known_coverage(start, end, last_sample_at=start+280).finish(end)
        row = q.qualify_window(qualification_row(start, result))
        self.assertTrue(row["qualificationValid"])
        self.assertEqual(row["scheduledSeconds"], 300.0)
        self.assertAlmostEqual(row["observedSeconds"], 282.0)
        self.assertAlmostEqual(row["unknownSeconds"], 18.0)
        for side in ("yes", "no"):
            pair = row["pairDenominators"][side]
            self.assertAlmostEqual(pair["scheduledSeconds"], 300.0)
            self.assertAlmostEqual(pair["observedSeconds"], 282.0)
            self.assertAlmostEqual(pair["unknownSeconds"], 18.0)

    def test_only_point_one_second_after_late_readiness_is_not_300_seconds(self):
        start, end = 0.0, 300.0
        coverage = q.ObservationCoverage(start, end)
        coverage.observe(299.9, world(299.9),
                         {"yes": pm(299.9), "no": pm(299.9)})
        row = q.qualify_window(qualification_row(start, coverage.finish(end), loop_entered=299.9))
        self.assertTrue(row["qualificationValid"])
        self.assertEqual(row["scheduledSeconds"], 300.0)
        self.assertAlmostEqual(row["observedSeconds"], 0.1)
        self.assertAlmostEqual(row["unknownSeconds"], 299.9)

    def test_preheated_coverage_before_late_loop_entry_is_clipped_from_strategy_exposure(self):
        start, end = 0.0, 300.0
        result = full_known_coverage(start, end).finish(end)
        row = q.qualify_window(qualification_row(start, result, loop_entered=299.9))
        self.assertTrue(row["qualificationValid"])
        self.assertAlmostEqual(row["observedSeconds"], 0.1)
        self.assertAlmostEqual(row["unknownSeconds"], 299.9)
        self.assertEqual(row["scheduledSeconds"], 300.0)

    def test_fresh_no_route_is_observed_exposure_even_with_no_ask(self):
        start, end = 1000.0, 1300.0
        result = full_known_coverage(start, end, state="no_route").finish(end)
        row = q.qualify_window(qualification_row(start, result))
        self.assertTrue(row["qualificationValid"])
        self.assertAlmostEqual(row["observedSeconds"], 300.0)
        self.assertAlmostEqual(row["unknownSeconds"], 0.0)
        for side in ("yes", "no"):
            self.assertAlmostEqual(result["pairs"][side]["stateSeconds"]["KNOWN_NO_ROUTE"], 300.0)
            self.assertAlmostEqual(row["pairDenominators"][side]["observedSeconds"], 300.0)

    def test_joint_and_pair_frequency_denominators_do_not_include_unknown_time(self):
        start, end = 0.0, 300.0
        coverage = q.ObservationCoverage(start, end)
        for i in range(5601):
            t = start + i*q.POLL_SECONDS
            no_state = "quote" if t <= 100.0 else "stale"
            coverage.observe(t, world_sides(t, no=no_state),
                             {"yes": pm(t), "no": pm(t)})
        candidates = [
            {"pair": "WORLD_YES+PM_DOWN", "radarObservedAt": 150.0},
            {"pair": "WORLD_NO+PM_UP", "radarObservedAt": 90.0},
            {"pair": "WORLD_NO+PM_UP", "radarObservedAt": 150.0},
        ]
        row = q.qualify_window(qualification_row(start, coverage.finish(end), candidates=candidates))
        self.assertTrue(row["qualificationValid"])
        yes = row["pairDenominators"]["yes"]
        no = row["pairDenominators"]["no"]
        self.assertAlmostEqual(yes["observedSeconds"], 282.0)
        self.assertAlmostEqual(no["observedSeconds"], 100.05)
        self.assertAlmostEqual(row["observedSeconds"], 100.05)  # simultaneous BOTH_PAIRS
        self.assertEqual(row["strategyCandidateCount"], 1)  # only t=90 is joint-known
        self.assertEqual(yes["strategyCandidateCount"], 1)  # t=150 is yes-known
        self.assertEqual(no["strategyCandidateCount"], 1)  # t=90 is no-known
        self.assertEqual(row["candidateCountOutsideObservedExposure"], 2)
        self.assertEqual(row["operationalCandidateCount"], 3)
        self.assertAlmostEqual(row["strategyCandidatesPerObservedSecond"], 1/100.05)
        self.assertAlmostEqual(yes["strategyCandidatesPerObservedSecond"], 1/282.0)
        self.assertAlmostEqual(row["operationalCandidatesPerScheduledSecond"], 3/300.0)

    def test_stale_error_and_disconnect_are_unknown(self):
        now = 20.0
        for state, expected in (("stale", "UNKNOWN_WORLD"),
                                ("server_error", "UNKNOWN_WORLD_RESPONSE"),
                                ("disconnected", "UNKNOWN_WORLD")):
            with self.subTest(state=state):
                observed, _ = q.observation_state(world(now, state=state), pm(now), "yes", now)
                self.assertEqual(observed, expected)
        observed, _ = q.observation_state(world(now), pm(now, live=False), "yes", now)
        self.assertEqual(observed, "UNKNOWN_PM")

    def test_sampler_gap_cannot_extend_last_freshness_lease_to_window_end(self):
        coverage = q.ObservationCoverage(0.0, 300.0)
        coverage.observe(0.0, world(0.0), {"yes": pm(0.0), "no": pm(0.0)})
        result = coverage.finish(300.0)
        row = q.qualify_window(qualification_row(0.0, result))
        self.assertTrue(row["qualificationValid"])
        self.assertEqual(row["scheduledSeconds"], 300.0)
        self.assertAlmostEqual(row["observedSeconds"], 2.0)
        self.assertAlmostEqual(row["unknownSeconds"], 298.0)

    def test_backward_wall_clock_observation_makes_accounting_invalid(self):
        coverage = q.ObservationCoverage(0.0, 300.0)
        coverage.observe(10.0, world(10.0), {"yes": pm(10.0), "no": pm(10.0)})
        with self.assertRaisesRegex(ValueError, "OBSERVATION_CLOCK_MOVED_BACKWARDS"):
            coverage.observe(9.0, world(9.0), {"yes": pm(9.0), "no": pm(9.0)})
        coverage_error = "ValueError:OBSERVATION_CLOCK_MOVED_BACKWARDS"
        result = coverage.finish(300.0)
        result["accountingError"] = coverage_error
        row = q.qualify_window(qualification_row(0.0, result))
        self.assertFalse(row["qualificationValid"])
        self.assertIn("OBSERVATION_ACCOUNTING_FAILED", row["invalidExecutionReasons"])
        self.assertEqual(row["observedSeconds"], 0.0)

    def test_unchanged_pm_book_with_live_heartbeat_remains_known(self):
        now = 100.0
        observed, expiry = q.observation_state(
            world(now), pm(now, last_frame_age=14.0), "yes", now)
        self.assertEqual(observed, "KNOWN_QUOTE")
        self.assertEqual(expiry, min(now + q.WORLD_FRESH_SECONDS,
                                     now - 14.0 + q.PM_LIVENESS_SECONDS))
        stale, _ = q.observation_state(world(now), pm(now, last_frame_age=15.1), "yes", now)
        self.assertEqual(stale, "UNKNOWN_PM")

    def test_crash_and_invalid_coverage_have_operational_but_zero_strategy_exposure(self):
        start, end = 0.0, 300.0
        coverage = full_known_coverage(start, end).finish(end)
        crashed = q.qualify_window(qualification_row(
            start, coverage, completed=False, execution_error="RuntimeError: sampler crash",
            candidates=[{"radarObservedAt": 10.0}]))
        self.assertFalse(crashed["qualificationValid"])
        self.assertEqual(crashed["observedSeconds"], 0.0)
        self.assertEqual(crashed["scheduledSeconds"], 300.0)
        self.assertEqual(crashed["strategyCandidateCount"], 0)
        self.assertEqual(crashed["operationalCandidateCount"], 1)
        self.assertEqual(crashed["operationalCandidatesPerScheduledSecond"], 1/300.0)

        corrupt = {"schemaVersion": "corrupt", "startTs": start, "endTs": end,
                   "sampleCount": 1, "pairs": {}}
        failed = q.qualify_window(qualification_row(start, corrupt))
        self.assertFalse(failed["qualificationValid"])
        self.assertIn("OBSERVATION_ACCOUNTING_FAILED", failed["invalidExecutionReasons"])
        self.assertEqual(failed["observedSeconds"], 0.0)

        batch = q.qualify_batch([crashed], requested=2, phase_a_eligible=True)
        self.assertEqual(batch["scheduledSeconds"], 600.0)
        self.assertEqual(batch["observedSeconds"], 0.0)
        self.assertEqual(batch["unknownSeconds"], 600.0)
        self.assertEqual(batch["missingExecutionWindowCount"], 1)
        self.assertEqual(batch["strategyCandidateCount"], 0)
        self.assertEqual(batch["operationalCandidateCount"], 1)

    def test_qualification_preserves_fill_inventory_and_hedge_evidence(self):
        evidence = {"confirmedFillLowerBoundShares": 1.0,
                    "inventoryLowerBoundShares": 1.0,
                    "inventoryUpperBoundShares": 1.0,
                    "fillEvents": [{"shares": 1.0}],
                    "hedgeEvents": [{"covered": 0.98125}],
                    "residualExposure": {"lower": 0.01875, "upper": 0.01875}}
        before = copy.deepcopy(evidence)
        start, end = 0.0, 300.0
        coverage = full_known_coverage(start, end, last_sample_at=280.0).finish(end)
        row = qualification_row(start, coverage, evidence=evidence)
        q.qualify_window(row)
        self.assertEqual(row["candidate"], before)
        self.assertAlmostEqual(row["observedSeconds"], 282.0)
        self.assertAlmostEqual(row["unknownSeconds"], 18.0)

    def test_preparation_failure_is_fail_closed_and_cleans_started_feed(self):
        class Feed:
            def __init__(self):
                self.started = asyncio.Event()

            async def run(self, stop):
                self.started.set()
                await stop.wait()

        feed = Feed()
        market = SimpleNamespace(condition_id="condition", up_token="up", down_token="down")
        args = SimpleNamespace(rpc_url="https://public.invalid",
                               world_discovery_timeout_seconds=5.0)

        async def run_case():
            with patch.object(preparation.wp, "fetch_polymarket_market", return_value=market), \
                    patch.object(preparation.wp, "discover_world_market",
                                 side_effect=RuntimeError("bounded discovery failure")):
                prepared = await preparation.prepare(
                    int(time.time()) + 3, args, lambda _: feed,
                    lambda *_: self.fail("radar must not start after failed discovery"))
            return prepared

        prepared = asyncio.run(run_case())
        self.assertTrue(prepared.closed)
        self.assertTrue(prepared.stop.is_set())
        self.assertTrue(prepared.row["executionError"].startswith("PREPARATION:"))
        self.assertTrue(feed.started.is_set())
        self.assertTrue(all(task.done() for task in prepared.tasks))
        self.assertTrue(all(task.cancelled() for task in prepared.tasks))


if __name__ == "__main__":
    unittest.main()

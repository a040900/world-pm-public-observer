from __future__ import annotations

import asyncio
import copy
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tools.research import world_pm_qualification_runtime_r1 as q
from tools.research import world_pm_window_preparation_r1 as preparation


def world(now: float, *, state: str = "quote") -> dict:
    if state == "no_route":
        leg = {"error": "no_route", "receivedAt": now,
               "sourceTimestamp": now, "ask": None}
        return {"healthy": True, "yes": dict(leg), "no": dict(leg)}
    if state == "quote":
        leg = {"error": None, "receivedAt": now,
               "sourceTimestamp": now, "ask": 0.5}
        return {"healthy": True, "yes": dict(leg), "no": dict(leg)}
    if state == "server_error":
        leg = {"error": "HTTP_502", "receivedAt": now,
               "sourceTimestamp": now, "ask": None}
        return {"healthy": True, "yes": dict(leg), "no": dict(leg)}
    if state == "stale":
        leg = {"error": None, "receivedAt": now - q.WORLD_FRESH_SECONDS - 0.1,
               "sourceTimestamp": now - q.WORLD_FRESH_SECONDS - 0.1, "ask": 0.5}
        return {"healthy": True, "yes": dict(leg), "no": dict(leg)}
    if state == "disconnected":
        leg = {"error": None, "receivedAt": now, "sourceTimestamp": now, "ask": 0.5}
        return {"healthy": False, "yes": dict(leg), "no": dict(leg)}
    raise ValueError(state)


def pm(now: float, *, live: bool = True, last_frame_age: float = 0.0) -> dict:
    return {"healthy": live, "ready": True, "sourceTimestampMs": 1,
            "lastFrameAt": now - last_frame_age}


class ObservationExposureTests(unittest.TestCase):
    def test_one_known_poll_among_500_does_not_imply_full_window_coverage(self):
        coverage = q.ObservationCoverage(0.0, 300.0)
        coverage.observe(-0.1, world(-0.1, state="disconnected"),
                         {"yes": pm(-0.1), "no": pm(-0.1)})
        for i in range(1, 501):
            now = i * q.POLL_SECONDS
            state = "quote" if i == 1 else "disconnected"
            coverage.observe(now, world(now, state=state),
                             {"yes": pm(now), "no": pm(now)})

        result = coverage.finish(300.0)
        for side in ("yes", "no"):
            self.assertAlmostEqual(result["pairs"][side]["observedSeconds"],
                                   q.POLL_SECONDS)
            self.assertAlmostEqual(result["pairs"][side]["unknownSeconds"],
                                   300.0 - q.POLL_SECONDS)
            self.assertFalse(result["complete"])

    def test_first_valid_observation_at_t0_plus_299_9_is_not_prestart_ready(self):
        coverage = q.ObservationCoverage(0.0, 300.0)
        coverage.observe(299.9, world(299.9),
                         {"yes": pm(299.9), "no": pm(299.9)})
        result = coverage.finish(300.0)
        self.assertAlmostEqual(result["pairs"]["yes"]["observedSeconds"], 0.1)
        self.assertAlmostEqual(result["pairs"]["yes"]["unknownSeconds"], 299.9)
        row = {"startTs": 0.0, "measurementLoopEnteredAt": 0.0,
               "worldMarket": {"market": "w"}, "pmMarket": {"conditionId": "p"},
               "radarPollCount": 1, "measurementLoopCompleted": True,
               "observationCoverage": result}
        qualified = q.qualify_window(row)
        self.assertFalse(qualified["qualificationValid"])
        self.assertIn("PRESTART_READINESS_NOT_ESTABLISHED",
                      qualified["invalidExecutionReasons"])

    def test_fresh_no_route_is_observed_exposure_when_zero_ask_is_healthy(self):
        start, end = 1000.0, 1300.0
        coverage = q.ObservationCoverage(start, end)
        first = start - q.POLL_SECONDS
        coverage.observe(first, world(first, state="no_route"),
                         {"yes": pm(first), "no": pm(first)})
        for i in range(1, 6002):
            now = first + i * q.POLL_SECONDS
            if now > end:
                break
            coverage.observe(now, world(now, state="no_route"),
                             {"yes": pm(now), "no": pm(now)})
        result = coverage.finish(end)
        self.assertTrue(result["complete"])
        for side in ("yes", "no"):
            pair = result["pairs"][side]
            self.assertAlmostEqual(pair["observedSeconds"], 300.0)
            self.assertAlmostEqual(pair["unknownSeconds"], 0.0)
            self.assertAlmostEqual(pair["stateSeconds"]["KNOWN_NO_ROUTE"], 300.0)

    def test_stale_error_and_disconnect_are_unknown(self):
        now = 20.0
        for state, expected in (("stale", "UNKNOWN_WORLD"),
                                ("server_error", "UNKNOWN_WORLD_RESPONSE"),
                                ("disconnected", "UNKNOWN_WORLD")):
            with self.subTest(state=state):
                observed, _ = q.observation_state(
                    world(now, state=state), pm(now), "yes", now)
                self.assertEqual(observed, expected)
        observed, _ = q.observation_state(world(now), pm(now, live=False), "yes", now)
        self.assertEqual(observed, "UNKNOWN_PM")

    def test_sampler_gap_cannot_extend_last_freshness_lease_to_window_end(self):
        coverage = q.ObservationCoverage(0.0, 300.0)
        coverage.observe(0.0, world(0.0), {"yes": pm(0.0), "no": pm(0.0)})
        # Simulate the sampler/event loop becoming blocked until the window closes.
        result = coverage.finish(300.0)
        for side in ("yes", "no"):
            self.assertAlmostEqual(result["pairs"][side]["observedSeconds"], 2.0)
            self.assertAlmostEqual(result["pairs"][side]["unknownSeconds"], 298.0)
            self.assertFalse(result["complete"])

    def test_backward_wall_clock_observation_fails_closed(self):
        coverage = q.ObservationCoverage(0.0, 300.0)
        coverage.observe(10.0, world(10.0), {"yes": pm(10.0), "no": pm(10.0)})
        with self.assertRaisesRegex(ValueError, "OBSERVATION_CLOCK_MOVED_BACKWARDS"):
            coverage.observe(9.0, world(9.0), {"yes": pm(9.0), "no": pm(9.0)})
        result = coverage.finish(300.0)
        self.assertFalse(result["complete"])
        self.assertLess(result["pairs"]["yes"]["observedSeconds"], 300.0)

    def test_unchanged_pm_book_with_live_heartbeat_remains_known(self):
        now = 100.0
        observed, expiry = q.observation_state(
            world(now), pm(now, last_frame_age=14.0), "yes", now)
        self.assertEqual(observed, "KNOWN_QUOTE")
        self.assertEqual(expiry, min(now + q.WORLD_FRESH_SECONDS,
                                     now - 14.0 + q.PM_LIVENESS_SECONDS))
        stale, _ = q.observation_state(
            world(now), pm(now, last_frame_age=15.1), "yes", now)
        self.assertEqual(stale, "UNKNOWN_PM")

    def test_leading_and_trailing_unknown_gaps_are_preserved(self):
        coverage = q.ObservationCoverage(0.0, 10.0)
        coverage.observe(1.0, world(1.0), {"yes": pm(1.0), "no": pm(1.0)})
        coverage.observe(7.0, world(7.0), {"yes": pm(7.0), "no": pm(7.0)})
        result = coverage.finish(10.0)
        for side in ("yes", "no"):
            pair = result["pairs"][side]
            self.assertEqual(pair["unknownIntervals"],
                             [[0.0, 1.0], [3.0, 7.0], [9.0, 10.0]])
            self.assertAlmostEqual(pair["unknownSeconds"], 6.0)

    def test_qualification_preserves_fill_inventory_and_hedge_evidence(self):
        evidence = {"confirmedFillLowerBoundShares": 1.0,
                    "inventoryLowerBoundShares": 1.0,
                    "inventoryUpperBoundShares": 1.0,
                    "fillEvents": [{"shares": 1.0}],
                    "hedgeEvents": [{"covered": 0.98125}],
                    "residualExposure": {"lower": 0.01875, "upper": 0.01875}}
        before = copy.deepcopy(evidence)
        row = {"startTs": 0.0, "measurementLoopEnteredAt": 0.0,
               "worldMarket": {"market": "w"}, "pmMarket": {"conditionId": "p"},
               "radarPollCount": 1, "measurementLoopCompleted": True,
               "observationCoverage": {"schemaVersion": q.COVERAGE_SCHEMA,
                                       "complete": False, "readinessObservedAt": 0.0},
               "candidate": evidence}
        q.qualify_window(row)
        self.assertEqual(row["candidate"], before)
        self.assertIn("OBSERVATION_EXPOSURE_INCOMPLETE", row["invalidExecutionReasons"])

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
                    int(time.time()) + 10, args, lambda _: feed,
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

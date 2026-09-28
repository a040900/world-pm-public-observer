import asyncio
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tools.research import world_pm_qualification_runtime_r1 as q
from tools.research import world_pm_window_preparation_r1 as prep
from tools.research import world_pm_pm_maker_first_shadow_r1 as maker
from tools.research import world_polymarket_btc5m_leadlag_probe_r1 as wp


def window(start=0, offset=20, candidates=None, entered=None):
    coverage = q.ObservationCoverage(start, start+300)
    for now in range(start+offset, start+301):
        leg = {"receivedAt": now, "sourceTimestamp": now, "ask": .5}
        book = {"healthy": True, "ready": True, "lastFrameAt": now, "sourceTimestampMs": now*1000}
        coverage.observe(now, {"healthy": True, "yes": leg, "no": leg}, {"yes": book, "no": book})
    return q.qualify_window({"startTs": start, "endTs": start+300,
        "worldMarket": {"market": "w"}, "pmMarket": {"conditionId": "p"},
        "measurementLoopEnteredAt": start+offset if entered is None else entered,
        "measurementLoopCompleted": True, "radarPollCount": 100,
        "observationCoverage": coverage.finish(start+300), "shadowCandidates": candidates or []})


class DualDenominatorRuntimeTests(unittest.TestCase):
    def test_strategy_and_operational_rates_use_distinct_time(self):
        candidates = [{"pair": "WORLD_YES+PM_DOWN", "radarObservedAt": 10},
                      {"pair": "WORLD_YES+PM_DOWN", "radarObservedAt": 30}]
        before = copy.deepcopy(candidates)
        row = window(candidates=candidates)
        self.assertTrue(row["qualificationValid"])
        self.assertEqual((row["observedSeconds"], row["unknownSeconds"], row["scheduledSeconds"]), (280, 20, 300))
        self.assertEqual(row["strategyCandidateCount"], 1)
        self.assertEqual(row["strategyCandidatesPerObservedSecond"], 1/280)
        self.assertEqual(row["operationalCandidatesPerScheduledSecond"], 2/300)
        self.assertEqual(row["candidateCountOutsideObservedExposure"], 1)
        self.assertEqual(candidates, before)

    def test_warmed_feed_before_engine_entry_is_not_strategy_exposure(self):
        row = window(offset=0, entered=20)
        self.assertEqual(row["observedSeconds"], 280)
        self.assertEqual(row["unknownSeconds"], 20)
        self.assertTrue(row["qualificationValid"])

    def test_six_partial_windows_can_qualify_without_claiming_1800_observed_seconds(self):
        rows = [window(i*300, 18) for i in range(6)]
        batch = q.qualify_batch(rows, 6)
        self.assertEqual(batch["phaseAEligibleWindowCount"], 6)
        self.assertEqual((batch["observedSeconds"], batch["unknownSeconds"], batch["scheduledSeconds"]), (1692, 108, 1800))
        self.assertEqual(batch["observationAvailability"], .94)
        self.assertEqual(q.qualify_batch(rows, 6, phase_a_eligible=False)["phaseAEligibleWindowCount"], 0)

    def test_one_invalid_slot_remains_in_operational_clock_only(self):
        rows = [window(i*300, 18) for i in range(5)]
        bad = window(1500, 18)
        bad["executionError"] = "crash"
        rows.append(q.qualify_window(bad))
        batch = q.qualify_batch(rows, 6)
        self.assertEqual(batch["validQualificationWindowCount"], 5)
        self.assertEqual(batch["phaseAEligibleWindowCount"], 0)
        self.assertEqual(batch["observedSeconds"], 1410)
        self.assertEqual(batch["unknownSeconds"], 390)
        missing = q.qualify_batch(rows[:5], 6)
        self.assertEqual(missing["scheduledSeconds"], 1800)
        self.assertEqual(missing["unknownSeconds"], 390)

    def test_paired_roles_do_not_double_calendar_or_mix_candidate_frequencies(self):
        reports = {}
        for role, offset in (("maker", 18), ("taker", 20)):
            rows = [window(i*300, offset) for i in range(6)]
            reports[role] = {"windows": rows, "qualification": q.qualify_batch(rows, 6)}
        paired = q.qualify_paired_batch(reports, list(range(0, 1800, 300)))
        self.assertEqual(paired["phaseAEligibleWindowCount"], 6)
        self.assertEqual((paired["observedSeconds"], paired["scheduledSeconds"]), (1680, 1800))
        self.assertEqual(paired["roles"]["maker"]["observedSeconds"], 1692)
        self.assertIsNone(paired["strategyCandidatesPerObservedSecond"])
        reports["taker"]["windows"].pop()
        self.assertFalse(q.qualify_paired_batch(reports, list(range(0, 1800, 300)))["qualificationValid"])

    def test_poststart_discovery_enters_real_maker_loop_and_retains_unknown_startup(self):
        start = 1000
        clock = [start-120.0]
        original_sleep = asyncio.sleep
        async def sleep(seconds):
            clock[0] += max(0, seconds)
            await original_sleep(0)
        class Feed:
            def __init__(self, tokens): pass
            async def run(self, stop): await stop.wait()
            def snapshot(self, token):
                return {"healthy": True, "ready": True, "lastFrameAt": clock[0],
                        "sourceTimestampMs": clock[0]*1000, "receivedAt": clock[0],
                        "bestBid": .9, "bestAsk": .95, "connectionGeneration": 1}
        class Radar:
            def __init__(self, *args): pass
            async def run(self, stop): await stop.wait()
            def snapshot(self, now):
                leg = {"receivedAt": now, "sourceTimestamp": now, "ask": .99, "quoteAgeSeconds": 0}
                return {"healthy": True, "yes": leg, "no": leg}
        def discover(*args, **kwargs):
            self.assertEqual(kwargs["deadline"], start+60)
            clock[0] = start+20.0
            return SimpleNamespace(market="w", yes_mint="yes", no_mint="no")
        async def run():
            args = maker.parser().parse_args([])
            args.world_discovery_timeout_seconds = 60
            p = await prep.prepare(start, args, Feed, Radar)
            self.assertNotIn("executionError", p.row)
            async def prepared(): return p
            return await maker._run_window(start_ts=start, args=args, cash_decimals=6, prepared=prepared())
        market = SimpleNamespace(condition_id="p", up_token="up", down_token="down")
        with patch.object(prep.time, "time", side_effect=lambda: clock[0]), \
             patch.object(prep.asyncio, "sleep", sleep), \
             patch.object(wp, "fetch_polymarket_market", return_value=market), \
             patch.object(wp, "_cached_world_market", return_value=None), \
             patch.object(wp, "discover_world_market", side_effect=discover), \
             patch.object(prep, "token_decimals", return_value=6):
            row = asyncio.run(run())
        self.assertTrue(row["qualificationValid"], row["invalidExecutionReasons"])
        self.assertGreater(row["radarPollCount"], 0)
        self.assertAlmostEqual(row["unknownSeconds"], row["measurementLoopEnteredAt"]-start)
        self.assertGreaterEqual(row["unknownSeconds"], 20)
        self.assertGreater(row["observedSeconds"], 279)
        self.assertEqual(row["summary"]["candidateCount"], 0)

    def test_monitor_failure_invalidates_exposure_without_erasing_evidence(self):
        class Radar:
            def snapshot(self, now): raise ValueError("broken coverage")
        async def run():
            p = prep.PreparedWindow(1000, radar=Radar(), coverage=q.ObservationCoverage(1000, 1300))
            await prep._monitor(p)
            await p.close()
            return p
        p = asyncio.run(run())
        self.assertIn("broken coverage", p.row["observationCoverage"]["accountingError"])
        row = window(1000)
        row["observationCoverage"] = p.row["observationCoverage"]
        self.assertFalse(q.qualify_window(row)["qualificationValid"])
        self.assertEqual(row["observedSeconds"], 0)


if __name__ == "__main__":
    unittest.main()

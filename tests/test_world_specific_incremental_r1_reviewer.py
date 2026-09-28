"""Independent reviewer regression for R1 crossing continuity after unhealthy gaps."""
import asyncio
import types
import unittest
from unittest import mock

from tools.research import world_pm_world_specific_incremental_r1 as r
from tools.research import world_polymarket_btc5m_executable_sync_r23 as r23


class _Clock:
    def __init__(self, start: float):
        self.now = float(start)

    def time(self):
        return self.now

    async def sleep(self, delay):
        self.now += max(0.0, float(delay))


class _PmMarket:
    condition_id = "0x" + "cd" * 32
    up_token = "up"
    down_token = "down"


class _WorldMarket:
    market = "world"
    yes_mint = "yes"
    no_mint = "no"


class _Radar:
    def __init__(self, t0):
        self.t0 = float(t0)
        self.frame_count = 1
        self.connection_error = None

    async def run(self, stop):
        return

    def snapshot(self, at):
        t = at - self.t0
        if 10.0 <= t < 20.0:
            return {"healthy": False}
        level = 0.60
        if 5.0 <= t < 6.0 or 21.0 <= t < 22.0 or 24.0 <= t < 25.0:
            level = 0.63
        return {
            "healthy": True,
            "yes": {"ask": level, "quoteAgeSeconds": 0.01, "error": None},
            "no": {"ask": 1.0 - level, "quoteAgeSeconds": 0.01, "error": None},
        }


class _Cex:
    def __init__(self):
        self.message_count = {"binance": 5, "okx": 5}
        self.errors = {"binance": [], "okx": []}

    async def run_binance(self, stop):
        return

    async def run_okx(self, stop):
        return

    def consensus(self, at):
        return {"valid": True, "sign": 1, "binance": {"valid": True}, "okx": {"valid": True}}


class _PmFeed:
    def __init__(self, token_ids):
        self.frame_count = 1
        self.reconnect_count = 0
        self.error_history = []

    async def run(self, stop):
        return

    def record_health(self, at, token_ids):
        return


async def _finish_immediately(event, **kwargs):
    return event


class ReviewerRuntimeTests(unittest.TestCase):
    def test_unhealthy_gap_does_not_manufacture_crossing(self):
        t0 = 1790400000
        clock = _Clock(t0)
        radar = _Radar(t0)
        args = types.SimpleNamespace(rpc_url="https://stub.invalid", world_discovery_timeout_seconds=60.0)

        with (
            mock.patch("time.time", clock.time),
            mock.patch("asyncio.sleep", clock.sleep),
            mock.patch.object(r23.wp, "fetch_polymarket_market", lambda start_ts: _PmMarket()),
            mock.patch.object(r23.radar, "_discover_world_market", lambda *a, **k: _WorldMarket()),
            mock.patch.object(r.gap, "GapRadar", lambda yes_mint, no_mint: radar),
            mock.patch.object(r, "CexTape", _Cex),
            mock.patch.object(r, "CausalTimelineBook", _PmFeed),
            mock.patch.object(r, "_pm_proxy_at", lambda *a, **k: {"valid": True, "proxy": 0.5}),
            mock.patch.object(r, "_finish_episode", _finish_immediately),
        ):
            row = asyncio.run(
                r._run_window(
                    window_index=1,
                    start_ts=t0,
                    args=args,
                    clock_offset_seconds=None,
                )
            )

        self.assertTrue(row["measurementLoopCompleted"])
        self.assertTrue(row["qualificationValid"])
        self.assertEqual(row["errors"], [])

        times = [float(e["triggeredAt"]) - t0 for e in row["events"]]
        self.assertTrue(any(4.8 <= x <= 5.3 for x in times), times)
        # Gap is [10,20). At t~21 a valid above-threshold delta exists, but prior
        # crossing continuity is unknown, so that sample must initialize only.
        self.assertFalse(any(20.0 <= x < 23.8 for x in times), times)
        # A healthy below-threshold state around t~23 re-arms the detector, so the
        # subsequent real below->above crossing around t~24 is admissible.
        self.assertTrue(any(23.8 <= x <= 24.3 for x in times), times)


if __name__ == "__main__":
    unittest.main()

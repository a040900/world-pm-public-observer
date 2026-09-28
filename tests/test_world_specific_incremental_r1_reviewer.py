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
        level = 0.60
        # First ordinary crossing around t=5.
        if 5.0 <= t < 6.0:
            level = 0.63
        # During the conditioning-source gap below, World remains healthy and
        # moves above threshold near the end of the gap. This preserves the
        # one-second World lookback needed to expose a synthetic recovery crossing.
        if 19.5 <= t < 22.0:
            level = 0.63
        # After a healthy below-threshold re-arm, create a genuine crossing.
        if 24.0 <= t < 25.0:
            level = 0.63
        return {
            "healthy": True,
            "yes": {"ask": level, "quoteAgeSeconds": 0.01, "error": None},
            "no": {"ask": 1.0 - level, "quoteAgeSeconds": 0.01, "error": None},
        }


class _Cex:
    t0 = 0.0

    def __init__(self):
        self.message_count = {"binance": 5, "okx": 5}
        self.errors = {"binance": [], "okx": []}

    async def run_binance(self, stop):
        return

    async def run_okx(self, stop):
        return

    def consensus(self, at):
        t = at - self.t0
        if 10.0 <= t < 20.0:
            return {"valid": False, "sign": 0, "binance": {"valid": False}, "okx": {"valid": True}}
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
        _Cex.t0 = float(t0)
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
        # CEX conditioning is unhealthy on [10,20), while World remains healthy.
        # World moved during the gap, so the first healthy post-gap sample has a
        # valid above-threshold one-second delta. Prior crossing continuity is
        # unknown, therefore that recovery sample must initialize only.
        self.assertFalse(any(20.0 <= x < 21.5 for x in times), times)
        # Once conditioning is healthy again, a later observed below-threshold
        # delta may re-arm the detector. The high->low crossing around t~22 and
        # the later crossing after the second World move are both admissible.
        self.assertTrue(any(21.8 <= x <= 22.4 for x in times), times)
        self.assertTrue(any(24.8 <= x <= 25.4 for x in times), times)


if __name__ == "__main__":
    unittest.main()

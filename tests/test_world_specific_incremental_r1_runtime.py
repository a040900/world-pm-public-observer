"""Regression test: _run_window's measurement loop must actually execute.

Run 36379323776 failed 6/6 windows with
    WINDOW:NameError:name 'above' is not defined
because a single-line edit collapsed the crossing-state `above` assignment into
a `#` comment (the line carried a literal backslash-n instead of a newline).
`py_compile` stayed green and the pure-function unit tests stayed green because
nothing ever executed `_run_window`'s measurement loop.

This test drives `_run_window` end to end with stubbed transports under a fake
clock. Any future NameError, unbound local, or state-initialization slip in the
measurement loop fails loudly here instead of in a live run. It does not alter
research protocol, thresholds, gates, signal definitions, or sample policy.
"""
import asyncio
import types
import unittest
from unittest import mock

from tools.research import world_pm_world_specific_incremental_r1 as r
from tools.research import world_polymarket_btc5m_executable_sync_r23 as r23


class _FakeClock:
    """Advances a fake epoch on every sleep; performs no real waiting."""

    def __init__(self, start: float) -> None:
        self.now = float(start)

    def time(self) -> float:
        return self.now

    async def sleep(self, delay: float) -> None:
        # Deliberately never touches the real asyncio.sleep: this coroutine
        # replaces asyncio.sleep for the duration of the test.
        self.now += max(0.0, float(delay))


class _ProxyPlan:
    """Deterministic World proxy timeline (fake seconds since window start).

    Alternates 0.60/0.63 every 5s so |delta| = 0.03 clears every threshold in
    WORLD_THRESHOLDS; goes unhealthy on [30, 40) to exercise the unhealthy-gap
    continuity reset (frozen rule: UNKNOWN_AFTER_STARTUP_OR_UNHEALTHY_GAP,
    first healthy sample initializes only).
    """

    def snapshot(self, t: float) -> dict:
        if 30.0 <= t < 40.0:
            return {"healthy": False}
        level = 0.63 if int(t // 5) % 2 == 1 else 0.60
        return {
            "healthy": True,
            "yes": {"ask": level, "quoteAgeSeconds": 0.1, "error": None},
            "no": {"ask": round(1.0 - level, 2), "quoteAgeSeconds": 0.1, "error": None},
        }


class _StubPmMarket:
    condition_id = "0x" + "ab" * 32
    up_token = "up-token-stub"
    down_token = "down-token-stub"


class _StubWorldMarket:
    market = "world-market-stub"
    yes_mint = "yes-mint-stub"
    no_mint = "no-mint-stub"


class _StubGapRadar:
    def __init__(self, plan: _ProxyPlan) -> None:
        self._plan = plan
        self.frame_count = 0
        self.connection_error = None

    async def run(self, stop: asyncio.Event) -> None:
        return

    def snapshot(self, at: float) -> dict:
        # at is fake-clock seconds; convert to seconds-since-window-start.
        return self._plan.snapshot(at - self._t0)

    _t0 = 0.0


class _StubCexTape:
    def __init__(self) -> None:
        self.message_count = {"binance": 7, "okx": 7}
        self.errors = {"binance": [], "okx": []}

    async def run_binance(self, stop: asyncio.Event) -> None:
        return

    async def run_okx(self, stop: asyncio.Event) -> None:
        return

    def consensus(self, at: float) -> dict:
        return {"valid": True, "sign": 1, "binance": {"valid": True}, "okx": {"valid": True}}


class _StubPmFeed:
    def __init__(self, token_ids: list) -> None:
        self.frame_count = 0
        self.reconnect_count = 0
        self.error_history: list = []

    async def run(self, stop: asyncio.Event) -> None:
        return

    def record_health(self, at: float, token_ids: tuple) -> None:
        return


class RunWindowMeasurementLoopTests(unittest.TestCase):
    def test_measurement_loop_executes_crossing_branch_and_rearms_after_unhealthy_gap(self):
        t0 = 1790360000
        clock = _FakeClock(t0)
        plan = _ProxyPlan()
        radar = _StubGapRadar(plan)
        radar._t0 = float(t0)
        args = types.SimpleNamespace(
            rpc_url="https://stub.invalid",
            world_discovery_timeout_seconds=60.0,
        )
        with (
            mock.patch("time.time", clock.time),
            mock.patch("asyncio.sleep", clock.sleep),
            mock.patch.object(r23.wp, "fetch_polymarket_market", lambda start_ts: _StubPmMarket()),
            mock.patch.object(r23.radar, "_discover_world_market", lambda *a, **k: _StubWorldMarket()),
            mock.patch.object(r.gap, "GapRadar", lambda yes_mint, no_mint: radar),
            mock.patch.object(r, "CexTape", _StubCexTape),
            mock.patch.object(r, "CausalTimelineBook", _StubPmFeed),
            mock.patch.object(r, "_pm_proxy_at", lambda *a, **k: {"valid": True, "proxy": 0.5}),
        ):
            # Before the fix this raised NameError: name 'above' is not defined
            # on the first healthy poll and never returned.
            row = asyncio.run(
                r._run_window(
                    window_index=1,
                    start_ts=t0,
                    args=args,
                    clock_offset_seconds=None,
                )
            )

        # The measurement loop ran to completion under the fake clock.
        self.assertTrue(row["measurementLoopCompleted"])
        self.assertGreater(row["healthyOverlapPollCount"], 0)
        self.assertEqual(row["errors"], [])
        self.assertTrue(row["qualificationValid"])

        # The crossing branch (the `above[threshold]` read) executed: episodes
        # were classified, so the state variable the bug deleted was live.
        events = row["events"]
        self.assertGreater(len(events), 0)
        pre_gap = [e for e in events if e["triggeredAt"] < t0 + 30]
        post_gap = [e for e in events if e["triggeredAt"] > t0 + 40]
        self.assertGreater(len(pre_gap), 0)
        # Unhealthy gap reset continuity to unknown; the first healthy sample
        # only re-initialized, and crossings fired again afterwards.
        self.assertGreater(len(post_gap), 0)

        # Episode futures resolved through the stubbed PM proxy.
        scored = [e for e in events if ((e.get("future") or {}).get("3") or {}).get("score")]
        self.assertGreater(len(scored), 0)


if __name__ == "__main__":
    unittest.main()

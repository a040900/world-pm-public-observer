"""Offline unit tests for the bounded subsecond capture primitives."""

import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from tools.research import world_pm_subsecond_capture_r1 as capture


class CapturePrimitiveTests(unittest.TestCase):
    def journal(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        journal = capture.Journal(Path(td.name) / "journal.jsonl")
        self.addCleanup(journal.close)
        return journal

    def market(self, start=1000):
        return {
            "startTs": start,
            "endTs": start + 300,
            "worldTicker": "WXBTC5M-TEST-5",
            "pmUpToken": "up-token",
            "pmDownToken": "down-token",
        }

    def binding(self):
        return {
            "sidePaths": {"yes": ["yes"], "no": ["no"]},
            "fields": {"source_time": "ts", "bid": "bid", "ask": "ask"},
            "identityFields": ["ts", "slotId"],
        }

    def test_clock_reliability_records_resolution_and_fails_closed_on_wall_regression(self):
        class FakeClock:
            def time(self):
                return 100.0

            def monotonic(self):
                return 10.0

        clock = capture.ClockReliability(FakeClock())
        clock.observe(100.0, 10.0)
        clock.observe(99.0, 11.0)
        report = clock.report()
        self.assertFalse(report["reliable"])
        self.assertIn("WALL_CLOCK_BACKWARD", report["errors"])
        self.assertIn("wallResolution", report)
        self.assertIn("monotonicResolution", report)

    def test_clock_reliability_marks_long_frozen_wall_interval(self):
        clock = capture.ClockReliability()
        clock.observe(100.0, 10.0)
        clock.observe(100.0, 12.0)
        self.assertIn("WALL_CLOCK_FROZEN_INTERVAL", clock.report()["errors"])

    def test_journal_records_wall_and_monotonic_receipt_stamps(self):
        journal = self.journal()
        row = journal.emit("raw", receive_time=10.0, payload={})
        self.assertIsInstance(row["wall_time"], float)
        self.assertIsInstance(row["monotonic_time"], float)
        self.assertEqual(journal.clock.report()["samples"], 1)

    def test_valid_price_accepts_bounded_bbo_and_rejects_invalid(self):
        self.assertEqual(capture.valid_price("0.4", "0.6"),
                         {"bid": 0.4, "ask": 0.6, "mid": 0.5, "spread": 0.19999999999999996})
        for bid, ask in ((-0.1, 0.2), (0.7, 0.6), (0.2, 1.1), ("bad", 0.5)):
            self.assertEqual(capture.valid_price(bid, ask),
                             {"bid": None, "ask": None, "mid": None, "spread": None})

    def test_next_anchor_skips_missed_targets_without_catchup(self):
        self.assertEqual(capture.next_anchor(1000, 0.5, 0, 1000.1), 1)
        self.assertEqual(capture.next_anchor(1000, 0.5, 3, 1000.6), 4)
        self.assertEqual(capture.next_anchor(1000, 1.0, 3, 1007.2), 8)

    def test_world_history_first_appearance_deduplicates_identity_and_filters_window(self):
        journal = self.journal()
        history = capture.WorldHistory(journal, self.market(), self.binding())
        payload = {
            "yes": [
                {"ts": 1001, "slotId": 1, "bid": 0.4, "ask": 0.6},
                {"ts": 1001, "slotId": 1, "bid": 0.4, "ask": 0.6},
                {"ts": 1002, "slotId": 3, "bid": 0.35, "ask": 0.65},
                {"ts": 1300, "slotId": 2, "bid": 0.3, "ask": 0.7},
            ],
            "no": [{"ts": 1002, "slotId": 2, "bid": 0.2, "ask": 0.8}],
        }
        items = history.normalize(payload)
        # normalize preserves duplicate rows; first-appearance filtering is
        # applied by ingest via the identity set.
        self.assertEqual(len(items), 4)
        history.ingest(items, now=1003, latency=0.25)
        history.ingest(items, now=1004, latency=0.25)
        rows = [json.loads(line) for line in journal.path.read_text().splitlines()]
        quotes = [row for row in rows if row["type"] == "quote"]
        self.assertEqual(len(quotes), 3)
        self.assertEqual({row["side"] for row in quotes}, {"yes", "no"})
        self.assertTrue(all(row["venue"] == "world" for row in quotes))

    def test_journal_latest_requires_strict_receive_before_target(self):
        journal = self.journal()
        journal.emit("quote", market_start=1000, venue="pm", side="up",
                     receive_time=10.0, bid=0.4, ask=0.6)
        journal.emit("quote", market_start=1000, venue="pm", side="up",
                     receive_time=11.0, bid=0.5, ask=0.7)
        self.assertEqual(journal.latest(1000, "pm", "up", 11.0)["receive_time"], 10.0)
        self.assertIsNone(journal.latest(1000, "pm", "up", 10.0))

    def test_feed_health_initial_gap_and_pong_do_not_restore_missing_snapshot(self):
        journal = self.journal()
        health = capture.FeedHealth(journal, 1000, "pm", began=1000.0)
        self.assertEqual(health.gap_start, 1000.0)
        self.assertFalse(health.complete)
        health.ping(1010.0)
        self.assertTrue(health.expired(1020.0))
        health.pong(1020.1)
        self.assertIsNone(health.ping_sent)
        self.assertFalse(health.complete)
        self.assertEqual(health.gap_start, 1000.0)

    def test_feed_health_fail_starts_gap_at_last_healthy_and_healthy_closes_it(self):
        journal = self.journal()
        health = capture.FeedHealth(journal, 1000, "pm", began=1000.0)
        health.healthy(1001.0)
        self.assertIsNone(health.gap_start)
        health.fail("TIMEOUT")
        self.assertEqual(health.gap_start, 1001.0)
        health.healthy(1003.0)
        self.assertIsNone(health.gap_start)
        self.assertTrue(health.complete)
        rows = [json.loads(line) for line in journal.path.read_text().splitlines()]
        self.assertEqual([row["type"] for row in rows],
                         ["gap_open", "gap_close", "gap_open", "gap_close"])

    def test_pm_books_require_snapshots_then_apply_deltas(self):
        journal = self.journal()
        books = capture.PMBooks(self.market(), journal)
        with self.assertRaisesRegex(ValueError, "DELTA_BEFORE_FULL_SNAPSHOT"):
            books.ingest({"event_type": "price_change", "price_changes": [
                {"asset_id": "up-token", "side": "BUY", "price": "0.4", "size": "1"}
            ]}, now=1000.0)

        books.ingest({"event_type": "book", "asset_id": "up-token",
                      "bids": [{"price": "0.4", "size": "2"}],
                      "asks": [{"price": "0.6", "size": "3"}]}, now=1000.0)
        books.ingest({"event_type": "book", "asset_id": "down-token",
                      "bids": [{"price": "0.3", "size": "4"}],
                      "asks": [{"price": "0.7", "size": "5"}]}, now=1000.0)
        self.assertTrue(books.complete)
        books.ingest({"event_type": "price_change", "price_changes": [
            {"asset_id": "up-token", "side": "BUY", "price": "0.4", "size": "0"},
            {"asset_id": "up-token", "side": "SELL", "price": "0.5", "size": "1"},
        ], "timestamp": "1000500"}, now=1000.5)
        self.assertNotIn(0.4, books.books["up-token"]["bids"])
        self.assertEqual(books.books["up-token"]["asks"][0.5], 1.0)
        rows = [json.loads(line) for line in journal.path.read_text().splitlines()]
        quotes = [row for row in rows if row["type"] == "quote"]
        self.assertEqual(len(quotes), 3)
        latest = quotes[-1]
        self.assertEqual(latest["side"], "up")
        self.assertEqual(latest["bid"], None)
        # Removing the only bid leaves a one-sided book; valid_price is
        # fail-closed and emits a null BBO rather than inventing a midpoint.
        self.assertEqual(latest["ask"], None)

    def test_ws_timeout_marks_gap_and_reconnect_requires_both_fresh_books(self):
        journal = self.journal()
        health = capture.FeedHealth(journal, 1000, "pm", began=1000.0)
        health.ping(1010.0)
        self.assertTrue(health.expired(1020.0))
        health.fail("APPLICATION_PONG_TIMEOUT")
        self.assertFalse(health.complete)

        reconnected = capture.PMBooks(self.market(), journal)
        with self.assertRaisesRegex(ValueError, "DELTA_BEFORE_FULL_SNAPSHOT"):
            reconnected.ingest({"event_type": "price_change", "price_changes": [
                {"asset_id": "up-token", "side": "BUY", "price": "0.4", "size": "1"}
            ]}, now=1021.0)
        for token, bid, ask in (("up-token", "0.4", "0.6"), ("down-token", "0.3", "0.7")):
            reconnected.ingest({"event_type": "book", "asset_id": token,
                                "bids": [{"price": bid, "size": "1"}],
                                "asks": [{"price": ask, "size": "1"}]}, now=1021.0)
        self.assertTrue(reconnected.complete)

    def test_world_retry_records_raw_before_usable_quote_and_uses_bounded_backoff(self):
        journal = self.journal()
        market = self.market()
        calls = []

        class Stop:
            def __init__(self):
                self.stopped = False
                self.waits = []

            def is_set(self):
                return self.stopped

            def wait(self, seconds):
                self.waits.append(seconds)
                if len(self.waits) >= 3:
                    self.stopped = True
                return False

        stop = Stop()
        payload = {"yes": [{"ts": 1000, "slotId": 1, "bid": 0.4, "ask": 0.6}],
                   "no": [{"ts": 1000, "slotId": 1, "bid": 0.3, "ask": 0.7}]}

        def fake_http(url, *, timeout):
            calls.append((url, timeout))
            if len(calls) == 1:
                raise TimeoutError("fixture")
            return payload

        with patch.object(capture.time, "time", return_value=1000.0), \
             patch.object(capture, "http_json", side_effect=fake_http):
            capture.world_worker({"world": {
                "url": "https://world.invalid/history", "query": {"ticker": "{ticker}"},
                "sidePaths": {"yes": ["yes"], "no": ["no"]},
                "fields": {"source_time": "ts", "bid": "bid", "ask": "ask"},
                "identityFields": ["ts", "slotId"], "lookbackSeconds": 1,
            }}, market, journal, stop)

        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][1], 5)
        self.assertIn(2, stop.waits)
        rows = [json.loads(line) for line in journal.path.read_text().splitlines()]
        kinds = [row["type"] for row in rows]
        self.assertIn("error", kinds)
        self.assertLess(kinds.index("raw"), kinds.index("quote"))


if __name__ == "__main__":
    unittest.main()

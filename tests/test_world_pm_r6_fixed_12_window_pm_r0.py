"""Offline boundary regressions; fake clock and HTTP, no prospective sample."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tools.research import world_pm_r6_fixed_12_window_pm_r0 as capture


class Clock:
    def __init__(self, now):
        self.now = now

    def time(self):
        return self.now

    def sleep(self, seconds):
        assert seconds >= 0
        self.now += seconds


class CaptureBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock(1000)
        self.identities = []
        self.http_times = []
        self.latency = 0.05
        self.original_get_json = capture.get_json
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(capture, 'time', self.clock))
        self.stack.enter_context(patch.object(capture, 'market', self.market))
        self.stack.enter_context(patch.object(capture, 'get_json', self.http))
        self.urlopen_guard = self.stack.enter_context(patch.object(capture.urllib.request, 'urlopen', side_effect=AssertionError('NETWORK_FORBIDDEN_IN_OFFLINE_TEST')))
        self.stack.enter_context(patch.object(capture, 'settlement', lambda slug: {'success': True}))

    def market(self, start, **kwargs):
        self.identities.append(start)
        return {'slug': f'btc-updown-5m-{start}', 'upToken': f'{start}-up', 'downToken': f'{start}-down'}

    def http(self, url, timeout=8):
        self.http_times.append((self.clock.time(), timeout))
        self.clock.sleep(self.latency)
        return {'timestamp': self.clock.time(), 'hash': 'fixture',
                'asks': [{'price': '0.4', 'size': '10'}], 'bids': [{'price': '0.3', 'size': '9'}]}

    def test_original_failure_reproduced_at_exact_start(self):
        rec = capture.capture_window(1000, 2)
        self.assertIn('market', rec)
        self.assertEqual(self.identities, [1000])
        self.assertEqual(len(rec['snapshots']), 150)

    def test_all_twelve_consecutive_windows_discover_identity(self):
        self.clock.now = 979
        starts = [1000 + i * 300 for i in range(12)]
        with tempfile.TemporaryDirectory() as td:
            args = SimpleNamespace(min_lead_seconds=45, cadence=2, output=str(Path(td) / 'capture.json'))
            with patch.object(capture, 'choose_start', return_value=1000) as choose, contextlib.redirect_stdout(io.StringIO()):
                capture.main(args)
            data = json.loads(Path(args.output).read_text())
        choose.assert_called_once_with(45)
        self.assertEqual(data['frozenStarts'], starts)
        self.assertEqual(self.identities, starts)
        self.assertEqual(len(data['windows']), 12)
        self.assertEqual(data['summary']['windowsWithIdentity'], 12)
        self.assertEqual(data['summary']['captureDataStatus'], 'PM_CAPTURE_RECORDED_PENDING_QUALIFICATION')
        for w in data['windows']:
            self.assertGreaterEqual(len(w['snapshots']), 120)
            for s in w['snapshots']:
                self.assertTrue(w['startTs'] <= s['capturedAt'] < w['endTs'])
                self.assertTrue(s['capturedAt'] >= s['targetAt'])

    def test_late_start_skips_elapsed_targets(self):
        self.clock.now = 1007.25
        rec = capture.capture_window(1000, 2)
        first = rec['snapshots'][0]
        self.assertEqual(first['index'], 3)
        self.assertEqual(first['targetAt'], 1006)
        self.assertEqual(first['capturedAt'], 1007.25)
        self.assertEqual(rec['skippedTargets'], 3)

    def test_slow_http_does_not_burst_catch_up_or_cross_window(self):
        self.latency = 3
        rec = capture.capture_window(1000, 2)
        times = [s['capturedAt'] for s in rec['snapshots']]
        self.assertTrue(all(b - a >= 6 for a, b in zip(times, times[1:])))
        self.assertGreater(rec['skippedTargets'], 0)
        self.assertTrue(all(at < 1300 for at, _ in self.http_times))
        self.assertTrue(all(timeout <= 1300-at for at, timeout in self.http_times))

    def test_cross_boundary_reply_is_not_success_and_down_is_not_requested(self):
        self.clock.now = 1299
        self.latency = 2
        rec = capture.capture_window(1000, 2)
        self.assertEqual(len(self.http_times), 1)
        snap = rec['snapshots'][0]
        self.assertFalse(snap['up']['success'])
        self.assertFalse(snap['down']['success'])
        self.assertEqual(snap['up']['error'], 'RECEIVED_AFTER_WINDOW')

    def test_expired_window_never_discovers_or_requests(self):
        self.clock.now = 1300
        rec = capture.capture_window(1000, 2)
        self.assertEqual(self.identities, [])
        self.assertEqual(self.http_times, [])
        self.assertEqual(rec['snapshots'], [])
        self.assertEqual(rec['identityError'], 'WINDOW_EXPIRED_BEFORE_IDENTITY')

    def test_identity_failure_retains_reason_and_bounds_retries(self):
        self.clock.now = 1295
        with patch.object(capture, 'market', side_effect=RuntimeError('EVENT_NOT_UNIQUE')) as market:
            rec = capture.capture_window(1000, 2)
        self.assertEqual(market.call_count, 3)
        self.assertEqual(rec['identityError'], 'RuntimeError:EVENT_NOT_UNIQUE')
        self.assertEqual(rec['snapshots'], [])

    def test_identity_reply_after_end_is_rejected(self):
        self.clock.now = 1299
        def slow_identity(start, **kwargs):
            self.clock.sleep(2)
            return self.market(start)
        with patch.object(capture, 'market', side_effect=slow_identity):
            rec = capture.capture_window(1000, 2)
        self.assertNotIn('market', rec)
        self.assertEqual(rec['identityError'], 'IDENTITY_RECEIVED_AFTER_WINDOW')
        self.assertEqual(self.http_times, [])

    def test_invalid_cadence_rejected_before_requests(self):
        for cadence in (0, -1, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                capture.capture_window(1000, cadence)
        self.assertEqual(self.identities, [])
        self.assertEqual(self.http_times, [])

    def test_incomplete_window_remains_in_fixed_denominator(self):
        self.clock.now = 979
        ordinary_market = self.market
        def missing_second(start, **kwargs):
            if start == 1300:
                raise RuntimeError('EVENT_NOT_UNIQUE')
            return ordinary_market(start, **kwargs)
        with tempfile.TemporaryDirectory() as td:
            args = SimpleNamespace(min_lead_seconds=45, cadence=2, output=str(Path(td) / 'capture.json'))
            with patch.object(capture, 'choose_start', return_value=1000), patch.object(capture, 'market', side_effect=missing_second), contextlib.redirect_stdout(io.StringIO()):
                capture.main(args)
            data = json.loads(Path(args.output).read_text())
        self.assertEqual([w['startTs'] for w in data['windows']], [1000 + i*300 for i in range(12)])
        self.assertEqual(data['windows'][1]['snapshots'], [])
        self.assertEqual(data['windows'][1]['identityError'], 'RuntimeError:EVENT_NOT_UNIQUE')
        self.assertEqual(data['summary']['windowsWithIdentity'], 11)
        self.assertEqual(data['summary']['captureDataStatus'], 'NO_RESULT_PM_CAPTURE_INCOMPLETE')

    def test_book_timeout_preserves_failure_and_actual_times(self):
        def timeout(url, **kwargs):
            self.clock.sleep(1)
            raise TimeoutError('fixture')
        with patch.object(capture, 'get_json', side_effect=timeout):
            rec = capture.book('fixture-up', 1300)
        self.assertFalse(rec['success'])
        self.assertEqual(rec['startedAt'], 1000)
        self.assertEqual(rec['receivedAt'], 1001)
        self.assertEqual(rec['error'], 'TimeoutError:fixture')

    def test_push_workflow_only_runs_offline_tests(self):
        root = Path(__file__).resolve().parents[1]
        workflow = (root / '.github/workflows/world-pm-r6-fixed-12-window-pm-r0.yml').read_text()
        self.assertIn('python -m unittest discover', workflow)
        self.assertNotIn('python tools/research/world_pm_r6_fixed_12_window_pm_r0.py', workflow)
        self.assertNotIn('python -m tools.research.world_pm_r6_fixed_12_window_pm_r0', workflow)
        self.assertNotIn('workflow_dispatch:', workflow)

    def test_real_transport_is_stopped_at_network_guard(self):
        with self.assertRaisesRegex(AssertionError, 'NETWORK_FORBIDDEN_IN_OFFLINE_TEST'):
            self.original_get_json('https://offline.invalid/')
        self.urlopen_guard.assert_called_once()


if __name__ == '__main__':
    unittest.main()

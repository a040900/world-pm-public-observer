import asyncio
import copy
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch, AsyncMock
from urllib.error import HTTPError
from tools.research import world_polymarket_btc5m_leadlag_probe_r1 as wp
from tools.research import world_pm_qualification_runtime_r1 as q
from tools.research import world_pm_pm_maker_first_shadow_r1 as maker
from tools.research import world_pm_two_leg_paper_bot_r1 as taker


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(os.environ, {'WORLD_IDENTITY_CACHE': str(Path(self.tmp.name) / 'identity.json')})
        self.env.start(); self.addCleanup(self.env.stop)
        self.start = 1790523600

    def tx(self):
        return {'transaction': {'message': {'instructions': [
            {'programId': wp.PREDICT_PROGRAM, 'data': 'GQPb2YiSTh1nv24SrfG9bM', 'accounts': ['x', 'ledger', wp.CASH_MINT, 'yes', 'no'] + ['x'] * 6}]}},
            'meta': {'err': None, 'logMessages': []}}

    def success(self, url, **kwargs):
        payload = kwargs.get('payload') or {}
        method = payload.get('method')
        if method == 'getSignaturesForAddress':
            return {'result': [{'signature': 'sig', 'blockTime': self.start, 'err': None}]}
        if method == 'getTransaction':
            return {'result': self.tx()}
        return {'description': wp.world_description('BTC', self.start, self.start + 300)}

    def test_split_discriminator_works_without_debug_log_and_rejects_other_instruction(self):
        instruction = self.tx()['transaction']['message']['instructions'][0]
        self.assertTrue(wp._is_split_instruction(instruction))
        instruction['data'] = '1111111111111111'
        self.assertFalse(wp._is_split_instruction(instruction))

    def error429(self):
        return HTTPError(wp.DEFAULT_SOLANA_RPC, 429, 'Too Many Requests', {}, None)

    def test_first_429_then_discovery_success_and_fallback_identity(self):
        calls = []
        def transport(url, **kwargs):
            calls.append((url, (kwargs.get('payload') or {}).get('method')))
            if len(calls) == 1:
                raise self.error429()
            return self.success(url, **kwargs)
        with patch.object(wp, '_json_request', side_effect=transport), patch.object(wp.time, 'sleep'):
            market = wp.discover_world_market(self.start, rpc_url=wp.DEFAULT_SOLANA_RPC)
        self.assertEqual((market.market, market.yes_mint, market.no_mint), ('ledger', 'yes', 'no'))
        self.assertEqual(calls[1][0], wp.PUBLIC_RPC_FALLBACK)
        self.assertEqual(wp.DISCOVERY_TRACES[self.start]['workingEndpoint'], wp.PUBLIC_RPC_FALLBACK)

    def test_primary_connection_reset_fallback_matches_direct_identity(self):
        def transport(url, **kwargs):
            if url == wp.DEFAULT_SOLANA_RPC:
                raise ConnectionResetError('reset')
            return self.success(url, **kwargs)
        with patch.object(wp, '_json_request', side_effect=transport), patch.object(wp.time, 'sleep'):
            fallback = wp.discover_world_market(self.start, rpc_url=wp.DEFAULT_SOLANA_RPC)
        Path(os.environ['WORLD_IDENTITY_CACHE']).unlink()
        with patch.object(wp, '_json_request', side_effect=self.success), patch.object(wp.time, 'sleep'):
            direct = wp.discover_world_market(self.start, rpc_url=wp.PUBLIC_RPC_FALLBACK)
        self.assertEqual(fallback, direct)

    def test_persistent_cache_hit_has_no_network_discovery(self):
        with patch.object(wp, '_json_request', side_effect=self.success), patch.object(wp.time, 'sleep'):
            first = wp.discover_world_market(self.start, rpc_url=wp.DEFAULT_SOLANA_RPC)
        with patch.object(wp, '_json_request', side_effect=AssertionError('cache must avoid network')):
            cached = wp.discover_world_market(self.start, rpc_url=wp.DEFAULT_SOLANA_RPC)
        self.assertEqual(first, cached)
        self.assertTrue(wp.DISCOVERY_TRACES[self.start]['cacheHit'])

    def test_all_429_bounded_then_real_maker_window_invalid(self):
        args = maker.parser().parse_args([])
        pm = wp.PolymarketMarket(self.start, 'condition', 'up', 'down', {}, {}, '')
        async def feed(self, stop):
            await stop.wait()
        with patch.object(wp, '_json_request', side_effect=self.error429()) as request, \
             patch.object(wp.time, 'sleep'), patch.object(wp, 'fetch_polymarket_market', return_value=pm), \
             patch.object(maker.TradeAwareBook, 'run', feed):
            # Historical time only in a deterministic fixture, never prospective qualification.
            with patch.object(maker.time, 'time', return_value=self.start - 1):
                row = asyncio.run(maker._run_window(start_ts=self.start, args=args, cash_decimals=6))
        self.assertEqual(request.call_count, 4)
        self.assertEqual(row['executionStatus'], 'INVALID_EXECUTION_WINDOW')
        self.assertFalse(row['qualificationValid'])
        self.assertEqual(row['radarPollCount'], 0)
        self.assertFalse(row['candidateCountEligibleForDenominator'])

    def valid(self):
        coverage = q.ObservationCoverage(self.start, self.start+300)
        for now in range(self.start-1, self.start+301):
            leg = {"receivedAt": now, "sourceTimestamp": now, "ask": .5}
            pm = {"healthy": True, "ready": True, "lastFrameAt": now, "sourceTimestampMs": now*1000}
            coverage.observe(now, {"healthy": True, "yes": leg, "no": leg}, {"yes": pm, "no": pm})
        return q.qualify_window({'startTs': self.start, 'endTs': self.start+300,
            'worldMarket': {'market': 'x'}, 'pmMarket': {'conditionId': 'p'},
            'radarPollCount': 100, 'measurementAuthorityHealthyPollCount': 50,
            'measurementLoopEnteredAt': self.start,
            'observationCoverage': coverage.finish(self.start+300), 'measurementLoopCompleted': True})

    def test_taker_discovery_failure_zero_polls_is_invalid(self):
        import argparse
        now = [self.start - 1]
        async def sleep(_):
            return
        async def feed(self, stop):
            await stop.wait()
        pm = wp.PolymarketMarket(self.start, 'condition', 'up', 'down', {}, {}, '')
        with patch.object(wp, 'fetch_polymarket_market', return_value=pm), \
             patch.object(wp, 'discover_world_market', side_effect=RuntimeError('HTTP 429')), \
             patch.object(taker.r2s.r2.RollingTimelineBook, 'run', feed), \
             patch.object(taker.time, 'time', side_effect=lambda: now[0]), \
             patch.object(taker.asyncio, 'sleep', sleep):
            row = asyncio.run(taker._run_window(window_index=1, start_ts=self.start,
                args=argparse.Namespace(rpc_url=wp.DEFAULT_SOLANA_RPC, world_discovery_timeout_seconds=45),
                spec=None, ledger=None, cash_decimals=6, pm_clock_offset_seconds=0))
        self.assertFalse(row['qualificationValid'])
        self.assertEqual(row['radarPollCount'], 0)
        self.assertEqual(row['executionStatus'], 'INVALID_EXECUTION_WINDOW')

    def test_concurrent_cache_reader_waits_and_reuses_writer_identity(self):
        from concurrent.futures import ThreadPoolExecutor
        import threading
        entered = threading.Event()
        def slow(url, **kwargs):
            entered.set()
            return self.success(url, **kwargs)
        with patch.object(wp, '_json_request', side_effect=slow) as network:
            with ThreadPoolExecutor(max_workers=2) as pool:
                one = pool.submit(wp.discover_world_market, self.start, rpc_url=wp.DEFAULT_SOLANA_RPC)
                self.assertTrue(entered.wait(timeout=3))
                two = pool.submit(wp.discover_world_market, self.start, rpc_url=wp.DEFAULT_SOLANA_RPC)
                self.assertEqual(one.result(timeout=5), two.result(timeout=5))
        self.assertEqual(network.call_count, 3)

    def test_five_valid_one_discovery_failure_batch_incomplete(self):
        invalid = q.qualify_window({'startTs': self.start + 1500, 'radarPollCount': 0,
                                   'worldDiscoveryError': 'HTTP 429', 'summary': {'candidateCount': 0}})
        batch = q.qualify_batch([self.valid() for _ in range(5)] + [invalid], 6)
        self.assertEqual(batch['validQualificationWindowCount'], 5)
        self.assertEqual(batch['invalidExecutionWindowCount'], 1)
        self.assertEqual(batch['qualificationStatus'], 'INCOMPLETE_QUALIFICATION_BATCH')
        self.assertFalse(batch['qualificationValid'])
        self.assertEqual(batch['phaseAEligibleWindowCount'], 0)

    def test_zero_polls_or_no_authority_invalid_even_with_discovery(self):
        for key in ('radarPollCount', 'observationCoverage', 'measurementLoopCompleted'):
            row = self.valid(); row[key] = 0
            self.assertFalse(q.qualify_window(row)['qualificationValid'])

    def test_delayed_runner_moves_start_future_aligned_without_market_input(self):
        for delay in (0, 45, 3600, 86400):
            now = self.start + delay + .1
            s = q.select_schedule(now, self.start)
            self.assertGreaterEqual(s['firstWindowStartTs'], now + 120)
            self.assertEqual(s['firstWindowStartTs'] % 300, 0)
            self.assertFalse(s['marketStateUsed'])
            self.assertEqual(s, q.select_schedule(now, self.start))

    def test_validity_does_not_mutate_confirmed_inventory_hedge_evidence(self):
        evidence = {'confirmedFillLowerBoundShares': .1, 'inventoryLowerBoundShares': .1,
                    'inventoryUpperBoundShares': 5, 'fillEvents': [{'shares': .1}],
                    'hedgeEvents': [{'covered': .1}], 'residualExposure': {'lower': 0, 'upper': 4.9}}
        row = {'shadowCandidates': [copy.deepcopy(evidence)], 'radarPollCount': 0}
        q.qualify_window(row)
        self.assertEqual(row['shadowCandidates'], [evidence])

    def test_all_six_valid_batch_can_count(self):
        self.assertEqual(q.qualify_batch([self.valid() for _ in range(6)], 6)['phaseAEligibleWindowCount'], 6)

    def test_missing_rows_are_explicit(self):
        b = q.qualify_batch([], 6)
        self.assertFalse(b['qualificationValid'])
        self.assertEqual(b['missingExecutionWindowCount'], 6)

    def test_semantic_rpc_error_does_not_retry(self):
        with patch.object(wp, '_json_request', return_value={'error': {'code': -32602, 'message': 'invalid params'}}) as call:
            with self.assertRaises(ValueError):
                wp._rpc(wp.DEFAULT_SOLANA_RPC, 'getTransaction', [])
        self.assertEqual(call.call_count, 1)

    def test_deadline_prevents_network(self):
        with patch.object(wp, '_json_request') as call:
            with self.assertRaises(RuntimeError):
                wp._rpc(wp.DEFAULT_SOLANA_RPC, 'getTransaction', [], deadline=time.time() - 1)
        call.assert_not_called()

    def test_mismatched_metadata_cannot_populate_cache(self):
        def wrong(url, **kwargs):
            if not kwargs.get('payload'):
                raise ValueError('metadata unavailable')
            return self.success(url, **kwargs)
        with patch.object(wp, '_json_request', side_effect=wrong), patch.object(wp.time, 'sleep'):
            with self.assertRaises(ValueError):
                wp.discover_world_market(self.start, rpc_url=wp.DEFAULT_SOLANA_RPC)
        self.assertFalse(Path(os.environ['WORLD_IDENTITY_CACHE']).exists())

    def test_maker_pair_observable_poll_counts_do_not_establish_time_coverage(self):
        row = {"radarPollCount": 100, "measurementAuthorityHealthyPollCount": 1,
               "measurementLoopCompleted": True, "worldMarket": {"market": "x"},
               "pmMarket": {"conditionId": "p"}, "pairObservablePollCount": {
                   "WORLD_YES+PM_DOWN": 25, "WORLD_NO+PM_UP": 75}}
        before = copy.deepcopy(row["pairObservablePollCount"])
        out = q.qualify_window(row)
        self.assertFalse(out["qualificationValid"])
        self.assertEqual(out["pairObservablePollCount"], before)


    def test_diagnostic_never_contributes_phase_a_windows(self):
        result = q.qualify_batch([self.valid() for _ in range(6)], 6, phase_a_eligible=False)
        self.assertTrue(result['qualificationValid'])
        self.assertEqual(result['phaseAEligibleWindowCount'], 0)

    def test_prestartexisting_split_requires_exact_metadata(self):
        def earlier(url, **kwargs):
            result = self.success(url, **kwargs)
            if (kwargs.get('payload') or {}).get('method') == 'getSignaturesForAddress':
                result['result'][0]['blockTime'] = self.start - 30
            return result
        with patch.object(wp.time, 'time', return_value=self.start-10), \
             patch.object(wp.time, 'sleep'), patch.object(wp, '_json_request', side_effect=earlier):
            market = wp.discover_world_market(self.start, rpc_url=wp.DEFAULT_SOLANA_RPC, deadline=self.start)
        self.assertEqual(market.start_ts, self.start)
        self.assertEqual(wp.DISCOVERY_TRACES[self.start]['signatureBlockTime'], self.start-30)


if __name__ == '__main__':
    unittest.main()

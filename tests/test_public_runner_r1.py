import unittest
from tools.research import world_pm_pm_maker_first_shadow_r1 as maker
from tools.research import world_polymarket_btc5m_leadlag_probe_r1 as wp

class T(unittest.TestCase):
 def _trade(self, *, ws_price, ws_size, fills):
  return {
   "side":"SELL","price":ws_price,"size":ws_size,"sourceTimestampMs":2000,
   "transactionHash":"0xabc",
   "executionEvidence":{
    "status":"QUALIFIED_MULTI_PRICE" if len({x[0] for x in fills}) > 1 else "QUALIFIED_MULTI_MAKER_SAME_PRICE",
    "makerLogs":[{"price":p,"shares":q} for p,q in fills],
   },
  }

 def test_a7_aggregate_ws_size_does_not_create_false_full_fill(self):
  # Public WS says size=5, but only 0.1 share actually traded below our .92 bid.
  r=maker.queue_shadow_classification(
   maker_bid=.92,queue_ahead=100,maker_size=5,
   trades=[self._trade(ws_price=.84,ws_size=5,fills=[(.93,4.9),(.84,.1)])],
   queue_timeline=[],placed_at=1)
  self.assertEqual(r["definite"]["status"],"PARTIAL_SHADOW_FILL")
  self.assertAlmostEqual(r["definite"]["fillShares"],.1)

 def test_exact_bid_uses_matched_volume_not_aggregate_ws_size(self):
  r=maker.queue_shadow_classification(
   maker_bid=.92,queue_ahead=1,maker_size=5,
   trades=[self._trade(ws_price=.92,ws_size=10,fills=[(.93,9.5),(.92,.5)])],
   queue_timeline=[],placed_at=1)
  self.assertEqual(r["definite"]["status"],"NO_SHADOW_FILL")
  self.assertAlmostEqual(r["definite"]["samePriceSellVolume"],.5)

 def test_unavailable_onchain_evidence_fails_closed(self):
  r=maker.queue_shadow_classification(
   maker_bid=.48,queue_ahead=1000,maker_size=5,
   trades=[{"side":"SELL","price":.47,"size":5,"sourceTimestampMs":2000,"transactionHash":"0xabc"}],
   queue_timeline=[],placed_at=1)
  self.assertEqual(r["definite"]["status"],"NO_SHADOW_FILL")

 def test_cache(self):
  m=wp.discover_world_market(1790350200,rpc_url="https://invalid.example")
  self.assertEqual(m.market,"52dBfCPUPeggatbZvw4Cf4oQ88mGR9HPq1a4Jsi9DAn5")

if __name__=="__main__":
 unittest.main()

import unittest
from tools.research import world_pm_pm_maker_first_shadow_r1 as maker
from tools.research import world_polymarket_btc5m_leadlag_probe_r1 as wp
class T(unittest.TestCase):
 def test_partial(self):
  r=maker.queue_shadow_classification(maker_bid=.48,queue_ahead=1000,maker_size=5,trades=[{"side":"SELL","price":.47,"size":.2,"sourceTimestampMs":2000}],placed_at=1)
  self.assertEqual(r["status"],"PARTIAL_SHADOW_FILL"); self.assertAlmostEqual(r["fillShares"],.2)
 def test_cache(self):
  m=wp.discover_world_market(1790350200,rpc_url="https://invalid.example"); self.assertEqual(m.market,"52dBfCPUPeggatbZvw4Cf4oQ88mGR9HPq1a4Jsi9DAn5")
if __name__=="__main__": unittest.main()

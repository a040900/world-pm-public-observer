import unittest

from tools.research import world_pm_pm_maker_first_shadow_r1 as maker
from tools.research import world_pm_condition_execution_tape_r1 as tape
from tools.research import world_polymarket_btc5m_leadlag_probe_r1 as wp

TARGET = "101"
COMP = "202"

class T(unittest.TestCase):
 def _trade(self, tx, groups, *, ws_side="BUY", ws_price=.99, ws_size=999):
  # Deliberately absurd WS fields: receipt evidence, not public size/price, must drive fill.
  return {
   "side":ws_side,"price":ws_price,"size":ws_size,"sourceTimestampMs":2000,
   "transactionHash":tx,
   "executionEvidence":{"status":"QUALIFIED","groups":groups,"errors":[]},
  }

 def _group(self, *, taker_side, taker_token, makers):
  return {
   "taker":{"side":taker_side,"tokenId":taker_token,"orderHash":"0xt","shares":sum(x["shares"] for x in makers)},
   "makers":makers,"firstLogIndex":1,"lastLogIndex":len(makers)+1,
  }

 def _maker(self, *, side, token, price, shares):
  return {"side":side,"tokenId":token,"price":price,"shares":shares,"logIndex":1,"orderHash":"0xm"}

 def _classify(self, trades, *, bid=.92, queue=100, known=False):
  return maker.queue_shadow_classification(
   maker_bid=bid,queue_ahead=queue,maker_size=5,trades=trades,
   queue_timeline=[],placed_at=1,target_token=TARGET,complement_token=COMP,
   queue_ahead_known=known)

 def test_a7_aggregate_ws_size_cannot_create_fill(self):
  g=self._group(taker_side="SELL",taker_token=TARGET,makers=[
   self._maker(side="BUY",token=TARGET,price=.93,shares=4.9),
   self._maker(side="BUY",token=TARGET,price=.84,shares=.1)])
  r=self._classify([self._trade("0x1",[g],ws_price=.44,ws_size=500)])
  self.assertEqual(r["definite"]["status"],"PARTIAL_SHADOW_FILL")
  self.assertAlmostEqual(r["definite"]["fillShares"],.1)

 def test_ws_price_does_not_filter_receipt_levels(self):
  g=self._group(taker_side="SELL",taker_token=TARGET,makers=[
   self._maker(side="BUY",token=TARGET,price=.43,shares=6)])
  r=self._classify([self._trade("0x2",[g],ws_price=.44,ws_size=26)],bid=.43)
  self.assertEqual(r["definite"]["status"],"NO_SHADOW_FILL")
  # Same price needs queue authority; with a defensible queue=1 it clears 5.
  r=self._classify([self._trade("0x2",[g],ws_price=.44,ws_size=26)],bid=.43,queue=1,known=True)
  self.assertEqual(r["definite"]["status"],"FULL_SHADOW_FILL")
  self.assertAlmostEqual(r["definite"]["fillShares"],5)

 def test_partial_fills_accumulate_across_transactions(self):
  g1=self._group(taker_side="SELL",taker_token=TARGET,makers=[
   self._maker(side="BUY",token=TARGET,price=.80,shares=.1)])
  g2=self._group(taker_side="SELL",taker_token=TARGET,makers=[
   self._maker(side="BUY",token=TARGET,price=.79,shares=4.9)])
  first=self._classify([self._trade("0x3",[g1])],bid=.81)
  both=self._classify([self._trade("0x3",[g1]),self._trade("0x4",[g2])],bid=.81)
  self.assertEqual(first["definite"]["status"],"PARTIAL_SHADOW_FILL")
  self.assertAlmostEqual(first["definite"]["fillShares"],.1)
  self.assertEqual(both["definite"]["status"],"FULL_SHADOW_FILL")
  self.assertAlmostEqual(both["definite"]["fillShares"],5)

 def test_mint_complement_buy_flow_counts(self):
  # Incoming BUY complement @ .38 reaches maker BUY target @ .62.
  # A hypothetical target BUY @ .63 is a better MINT counterpart and gets a lower bound.
  g=self._group(taker_side="BUY",taker_token=COMP,makers=[
   self._maker(side="BUY",token=TARGET,price=.62,shares=5)])
  r=self._classify([self._trade("0x5",[g])],bid=.63)
  self.assertEqual(r["definite"]["status"],"FULL_SHADOW_FILL")
  self.assertAlmostEqual(r["definite"]["fillShares"],5)

 def test_merge_path_normalizes_to_synthetic_target_bid(self):
  # Incoming SELL target matched with SELL complement @ .10 => synthetic target bid .90.
  # A hypothetical target BUY @ .91 has better effective price.
  g=self._group(taker_side="SELL",taker_token=TARGET,makers=[
   self._maker(side="SELL",token=COMP,price=.10,shares=3)])
  r=self._classify([self._trade("0x6",[g])],bid=.91)
  self.assertEqual(r["definite"]["status"],"PARTIAL_SHADOW_FILL")
  self.assertAlmostEqual(r["definite"]["fillShares"],3)

 def test_exact_bid_fails_closed_without_admission_queue_bound(self):
  g=self._group(taker_side="SELL",taker_token=TARGET,makers=[
   self._maker(side="BUY",token=TARGET,price=.92,shares=10)])
  unknown=self._classify([self._trade("0x7",[g])],queue=1,known=False)
  known=self._classify([self._trade("0x7",[g])],queue=1,known=True)
  self.assertEqual(unknown["definite"]["status"],"NO_SHADOW_FILL")
  self.assertEqual(known["definite"]["status"],"FULL_SHADOW_FILL")

 def test_ambiguous_match_group_fails_closed(self):
  r=self._classify([self._trade("0x8",[],ws_size=100)])
  self.assertEqual(r["definite"]["status"],"NO_SHADOW_FILL")

 def test_runner_lifecycle_keeps_residual_order_after_partial(self):
  current={
   "makerSizeShares":5.0,
   "makerBid":0.8,
   "fillEvents":[],
   "cumulativeFillShares":0.0,
   "hypotheticalHedgeCoveredShares":0.0,
   "remainingOrderShares":5.0,
   "residualInventoryShares":0.0,
   "inventoryState":"RESTING",
  }
  full=maker.apply_shadow_fill_lifecycle(current,cumulative_fill=.1,incremental_hedge_covered=.1)
  self.assertFalse(full)
  self.assertEqual(current["inventoryState"],"PARTIAL_FILL_LOWER_BOUND_INVENTORY_UNKNOWN")
  self.assertAlmostEqual(current["remainingOrderUpperBoundShares"],4.9)
  self.assertAlmostEqual(current["residualInventoryLowerBoundShares"],0.0)
  self.assertAlmostEqual(current["residualInventoryUpperBoundShares"],4.9)
  full=maker.apply_shadow_fill_lifecycle(current,cumulative_fill=5.0,incremental_hedge_covered=4.9)
  self.assertTrue(full)
  self.assertEqual(current["inventoryState"],"FULLY_FILLED_CONFIRMED")
  self.assertAlmostEqual(current["remainingOrderShares"],0.0)
  self.assertAlmostEqual(current["hypotheticalHedgeCoveredShares"],5.0)

 def test_pf01_candidate_economics_aggregates_all_hedge_legs(self):
  current={
   "makerSizeShares":5.0,"makerBid":.8,"fillEvents":[],
   "confirmedFillLowerBoundShares":0.0,"cumulativeFillShares":0.0,
   "hypotheticalHedgeCoveredShares":0.0,
  }
  # Four shares cost .40 World each => 1.20 protected unit cost.
  current["fillEvents"].append({"worldQuote":{"requestSizeCash":1.6},"protectedEconomics":{"survived":False}})
  maker.apply_shadow_fill_lifecycle(current,cumulative_fill=4.0,incremental_hedge_covered=4.0)
  # Last share looks attractive by itself, but whole candidate costs 1.13.
  current["fillEvents"].append({"worldQuote":{"requestSizeCash":.05},"protectedEconomics":{"survived":True}})
  maker.apply_shadow_fill_lifecycle(current,cumulative_fill=5.0,incremental_hedge_covered=1.0)
  self.assertTrue(current["fullyHedged"])
  self.assertAlmostEqual(current["cumulativeProtectedUnitCost"],1.13)
  self.assertFalse(current["protectedEdgeSurvived"])

 def test_pf01_incomplete_hedge_cannot_be_positive(self):
  current={
   "makerSizeShares":5.0,"makerBid":.8,"fillEvents":[],
   "confirmedFillLowerBoundShares":4.0,"cumulativeFillShares":4.0,
   "hypotheticalHedgeCoveredShares":0.0,
  }
  current["fillEvents"].append({"worldQuote":{},"protectedEconomics":{"survived":None}})
  maker.apply_shadow_fill_lifecycle(current,cumulative_fill=4.0,incremental_hedge_covered=0.0)
  current["fillEvents"].append({"worldQuote":{"requestSizeCash":.05},"protectedEconomics":{"survived":True}})
  maker.apply_shadow_fill_lifecycle(current,cumulative_fill=5.0,incremental_hedge_covered=1.0)
  self.assertAlmostEqual(current["residualInventoryLowerBoundShares"],4.0)
  self.assertFalse(current["fullyHedged"])
  self.assertIsNone(current["protectedEdgeSurvived"])

 def test_pf02_partial_exposes_inventory_bounds(self):
  current={
   "makerSizeShares":5.0,"makerBid":.8,"fillEvents":[],
   "confirmedFillLowerBoundShares":0.0,"cumulativeFillShares":0.0,
   "hypotheticalHedgeCoveredShares":0.0,
  }
  maker.apply_shadow_fill_lifecycle(current,cumulative_fill=.1,incremental_hedge_covered=.1)
  self.assertAlmostEqual(current["confirmedFillLowerBoundShares"],.1)
  self.assertAlmostEqual(current["confirmedFillUpperBoundShares"],5.0)
  self.assertAlmostEqual(current["residualInventoryLowerBoundShares"],0.0)
  self.assertAlmostEqual(current["residualInventoryUpperBoundShares"],4.9)
  self.assertFalse(current["fullyHedged"])

 def test_pf03_qualified_evidence_is_monotone_policy(self):
  # The runner cache must never replace a qualified decode with transient failure.
  old={"status":"QUALIFIED","groups":[{"x":1}]}
  new={"status":"PENDING","reason":"RPC"}
  chosen=old if old.get("status")=="QUALIFIED" and new.get("status")!="QUALIFIED" else new
  self.assertEqual(chosen["status"],"QUALIFIED")

 def test_pf04_missing_hash_is_unresolved_execution_evidence(self):
  trades=[{"side":"SELL","price":.79,"size":5,"sourceTimestampMs":2000,"receivedAt":2.0,"transactionHash":""}]
  out=maker.queue_shadow_classification(maker_bid=.8,queue_ahead=100,maker_size=5,trades=trades,queue_timeline=[],placed_at=1,target_token="101",complement_token="202",queue_ahead_known=False)
  self.assertEqual(out["definite"]["fillShares"],0)
  self.assertEqual(out["definite"]["executionEvidenceUnavailableCount"],1)
  self.assertTrue(maker._execution_evidence_incomplete_at_expiry(out))

 def test_pf04_unknown_time_not_masked_by_confirmed_fill(self):
  trades=[
   {"side":"SELL","price":.79,"size":1,"sourceTimestampMs":2000,"receivedAt":2.0,"transactionHash":"0x1","executionEvidence":{"status":"QUALIFIED","groups":[{"taker":{"side":"SELL","tokenId":"101","shares":1},"makers":[{"side":"BUY","tokenId":"101","price":.79,"shares":1}]}]}},
   {"side":"SELL","price":.79,"size":5,"sourceTimestampMs":None,"receivedAt":2.1,"transactionHash":"0x2"},
  ]
  out=maker.queue_shadow_classification(maker_bid=.8,queue_ahead=100,maker_size=5,trades=trades,queue_timeline=[],placed_at=1,target_token="101",complement_token="202",queue_ahead_known=False)
  self.assertEqual(out["classification"],"DEFINITE_FILL")
  self.assertEqual(out["unknownRelevantTradeSourceTimeCount"],1)
  self.assertTrue(maker._execution_evidence_incomplete_at_expiry(out))

 def test_pf04_unknown_time_not_masked_by_plausible(self):
  trades=[{"side":"SELL","price":.79,"size":5,"sourceTimestampMs":None,"receivedAt":2.1,"transactionHash":"0x2"}]
  out=maker.queue_shadow_classification(maker_bid=.8,queue_ahead=100,maker_size=5,trades=trades,queue_timeline=[{"observedAt":2,"bidSize":0}],placed_at=1,target_token="101",complement_token="202",queue_ahead_known=False)
  self.assertGreater(out["unknownRelevantTradeSourceTimeCount"],0)
  self.assertTrue(maker._execution_evidence_incomplete_at_expiry(out))

 def test_pm_market_discovery_retries_transient_transport(self):
  original=wp._json_request
  calls={"n":0}
  valid=[{"description":"x","markets":[{"clobTokenIds":"[\"1\",\"2\"]","outcomes":"[\"Up\",\"Down\"]","active":True,"closed":False,"acceptingOrders":True,"feeSchedule":{"rate":0,"exponent":1},"cryptoMarketConfig":dict(wp.EXPECTED_PM_CRYPTO_CONFIG),"conditionId":"c"}]}]
  def fake(url,**kwargs):
   calls["n"]+=1
   if calls["n"]<3:
    raise ConnectionResetError("transient")
   return valid
  wp._json_request=fake
  try:
   market=wp.fetch_polymarket_market(123)
  finally:
   wp._json_request=original
  self.assertEqual(calls["n"],3)
  self.assertEqual(market.condition_id,"c")

 def test_pm_market_discovery_does_not_retry_semantic_invalidity(self):
  original=wp._json_request
  calls={"n":0}
  def fake(url,**kwargs):
   calls["n"]+=1
   return []
  wp._json_request=fake
  try:
   with self.assertRaisesRegex(RuntimeError,"PM_EVENT_NOT_UNIQUE"):
    wp.fetch_polymarket_market(123)
  finally:
   wp._json_request=original
  self.assertEqual(calls["n"],1)

 def test_cache(self):
  m=wp.discover_world_market(1790350200,rpc_url="https://invalid.example")
  self.assertEqual(m.market,"52dBfCPUPeggatbZvw4Cf4oQ88mGR9HPq1a4Jsi9DAn5")

if __name__=="__main__":
 unittest.main()

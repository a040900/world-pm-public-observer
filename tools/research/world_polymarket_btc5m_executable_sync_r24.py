"""Migrated R2.4 interval synchronization core.
Historical source blob: f4fd1acc58f58ab444a14c0ceea44a14cdceb6c7. Public read-only; NO_TRADE.
"""
from __future__ import annotations
import argparse, asyncio, json, time
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping
import websockets
from tools.research import world_polymarket_btc5m_executable_sync_r23 as r23
SCHEMA_VERSION="WORLD_POLYMARKET_BTC5M_EXECUTABLE_SYNC_R24"; RAW_QUEUE_MAX=32768; WS_INTERNAL_MAX_QUEUE=16384; PONG_LIVENESS_SECONDS=30.; REST_RECONCILE_GRACE_SECONDS=.150; PROCESSOR_YIELD_EVERY=32
_ORIGINAL_RUN=r23._run; _ORIGINAL_SUMMARY=r23._summary
class PolymarketTimelineBook(r23.PolymarketVerifiedBook):
 def __init__(self,token_ids:list[str])->None: super().__init__(token_ids); self._captures={}; self.source_timestamp_regression_count=0; self.raw_queue_high_watermark=0; self.ping_sent_count=0; self.pong_received_count=0
 def _state_point(self,token:str,effective_at:float):
  state=self.books.get(token)
  if not isinstance(state,Mapping) or state.get("ready") is not True:return None
  bids,asks=dict(state.get("bids") or {}),dict(state.get("asks") or {})
  return {"effectiveAt":effective_at,"connectionGeneration":self.connection_generation,"eventCount":int(state.get("eventCount") or 0),"sourceTimestampMs":r23._to_int(state.get("sourceTimestampMs")),"localDigest":r23._book_digest(bids,asks),"asks":r23._book_rows(asks,asks=True)}
 def begin_capture(self,token):
  capture=[]; baseline=self._state_point(token,time.time())
  if baseline is not None:capture.append(baseline)
  self._captures[token]=capture; return capture
 def end_capture(self,token):return list(self._captures.pop(token,[]))
 def _append_capture_state(self,token,effective_at):
  capture=self._captures.get(token)
  if capture is None:return
  point=self._state_point(token,effective_at)
  if point is None:return
  if capture and capture[-1].get("localDigest")==point.get("localDigest"):capture[-1]=point
  else:capture.append(point)
 def _apply_book(self,message,received_at):super()._apply_book(message,received_at); self._append_capture_state(str(message.get("asset_id") or ""),received_at)
 def _apply_price_change(self,message,received_at):
  super()._apply_price_change(message,received_at)
  for change in message.get("price_changes") or []:
   if isinstance(change,Mapping):self._append_capture_state(str(change.get("asset_id") or ""),received_at)
 def snapshot(self,token_id,*,clock_offset_seconds=None):
  row=super().snapshot(token_id,clock_offset_seconds=clock_offset_seconds); now=time.time(); pong_age=None if self.last_pong_at is None else now-self.last_pong_at; row["healthy"]=bool(self.connected and row.get("ready") is True and self.current_error is None and self.last_pong_at is not None and pong_age<=PONG_LIVENESS_SECONDS); row["pongAgeSeconds"]=pong_age; row["sourceTimestampRegressionCount"]=self.source_timestamp_regression_count; row["rawQueueHighWatermark"]=self.raw_queue_high_watermark; return row

def _dedup_points(points):
 out=[]
 for point in sorted(points,key=lambda r:float(r.get("effectiveAt") or 0)):
  if out and out[-1].get("localDigest")==point.get("localDigest"):out[-1]=point
  else:out.append(point)
 return out
def _states_active_during(capture,start,end,generation):
 eligible=[p for p in capture if int(p.get("connectionGeneration") or -1)==generation]; eligible.sort(key=lambda r:float(r.get("effectiveAt") or 0)); baseline=None; during=[]
 for p in eligible:
  at=float(p.get("effectiveAt") or 0)
  if at<=start:baseline=p
  elif at<=end:during.append(p)
 return [] if baseline is None else _dedup_points([baseline,*during])
def _evaluate_interval(*,world_quote,interval_states,rest_match,rest_digest,fee_schedule,base_reason):
 result={"measurementQualified":False,"syncQualified":False,"syncReason":base_reason,"worldQuoteSuccess":world_quote.get("success") is True,"belowOne":False,"restReconciled":rest_match,"restDigest":rest_digest,"intervalStateCount":len(interval_states)}
 if base_reason!="INTERVAL_READY" or world_quote.get("success") is not True or not rest_match or not interval_states:return result
 target=r23._to_float(world_quote.get("minOutputUiShares"))
 if target is None or target<=0:return result
 evals=[]
 for state in interval_states:
  asks=state.get("asks")
  if not isinstance(asks,list) or not asks:return result
  walk=r23._walk_pm_book_for_net_shares(asks,target,fee_schedule)
  if walk.get("filled") is not True:return result
  total=r23.PRIMARY_SIZE_CASH+float(walk["cost"]); evals.append({"unitCostPerConditionalPayout":total/target,"conditionalSpreadNominal":target-total,"pmWalk":walk})
 worst=max(evals,key=lambda x:x["unitCostPerConditionalPayout"]); best=min(evals,key=lambda x:x["unitCostPerConditionalPayout"]); unit=float(worst["unitCostPerConditionalPayout"]); result.update({"measurementQualified":True,"syncQualified":True,"syncReason":"CLEAN_INTERVAL_ROBUST_PM_STATE","worldMinOutShares":target,"unitCostPerConditionalPayout":unit,"worstIntervalUnitCost":unit,"bestIntervalUnitCost":float(best["unitCostPerConditionalPayout"]),"conditionalSpreadNominal":worst["conditionalSpreadNominal"],"belowOne":unit<1.,"walletSpecificWorldExecutionFeeResolved":False,"settlementEquivalenceResolved":False,"cashBasisResolved":False,"actualFillResolved":False}); return result
async def _one_interval_state(**kwargs):
 feed=kwargs["pm_feed"]; token=kwargs["pm_token"]; pre=feed.snapshot(token,clock_offset_seconds=kwargs["clock_offset_seconds"]); generation=int(pre.get("connectionGeneration") or -1); captured=feed.begin_capture(token); started=time.time(); world_task=asyncio.to_thread(r23._world_anonymous_quote,kwargs["world_session"],output_mint=kwargs["world_mint"],cash_decimals=kwargs["cash_decimals"],outcome_decimals=kwargs["world_decimals"],timeout_seconds=kwargs["http_timeout_seconds"]); rest_task=asyncio.to_thread(r23._fetch_pm_rest_book,kwargs["pm_session"],token,kwargs["http_timeout_seconds"]); gathered=await asyncio.gather(world_task,rest_task,return_exceptions=True); world={} if isinstance(gathered[0],Exception) else gathered[0]; rest=None if isinstance(gathered[1],Exception) else gathered[1]; await asyncio.sleep(REST_RECONCILE_GRACE_SECONDS); completed=time.time(); post=feed.snapshot(token,clock_offset_seconds=kwargs["clock_offset_seconds"]); captured=feed.end_capture(token); base="INTERVAL_READY" if pre.get("healthy") is True and post.get("healthy") is True and generation==int(post.get("connectionGeneration") or -2) else "PM_FEED_UNHEALTHY_OR_RECONNECTED"; ws=r23._to_float(world.get("requestStartedAt")); we=r23._to_float(world.get("receivedAt")); states=[] if ws is None or we is None else _states_active_during(captured,ws,we,generation); rest_digest=None if not isinstance(rest,Mapping) else str(rest.get("localDigest") or "") or None; rest_match=bool(rest_digest and any(s.get("localDigest")==rest_digest for s in captured)); evaluation=_evaluate_interval(world_quote=world,interval_states=states,rest_match=rest_match,rest_digest=rest_digest,fee_schedule=kwargs["pm_fee_schedule"],base_reason=base); return {"acquisitionStartedAt":started,"acquisitionCompletedAt":completed,"acquisitionSpanMs":(completed-started)*1000,"worldQuote":world,"evaluation":evaluation,"worldIntervalPmStateCount":len(states)}
def _summary(windows):
 base=dict(_ORIGINAL_SUMMARY(windows)); base["verdict"]=str(base.get("verdict") or "").replace("R23","R24"); base["intervalSynchronization"]={"method":"WORST_PM_STATE_OVER_WORLD_REQUEST_RESPONSE_INTERVAL"}; return base
def _self_test():
 fee={"rate":.07,"exponent":1}; capture=[{"effectiveAt":10.,"connectionGeneration":1,"localDigest":"a","asks":[{"price":.4,"size":100}]},{"effectiveAt":10.05,"connectionGeneration":1,"localDigest":"b","asks":[{"price":.41,"size":100}]}]; states=_states_active_during(capture,10.01,10.10,1); assert [r["localDigest"] for r in states]==["a","b"]; result=_evaluate_interval(world_quote={"success":True,"minOutputUiShares":30.},interval_states=states,rest_match=True,rest_digest="b",fee_schedule=fee,base_reason="INTERVAL_READY"); assert result["measurementQualified"] is True and result["worstIntervalUnitCost"]>=result["bestIntervalUnitCost"]
async def _run(args):
 r23.SCHEMA_VERSION=SCHEMA_VERSION; r23.PolymarketVerifiedBook=PolymarketTimelineBook; r23._one_synchronized_state=_one_interval_state; r23._summary=_summary; result=await _ORIGINAL_RUN(args); result["schemaVersion"]=SCHEMA_VERSION; return result
def main():
 parser=argparse.ArgumentParser(); parser.add_argument("--rpc-url",default=r23.radar.DEFAULT_RPC); parser.add_argument("--world-discovery-timeout-seconds",type=float,default=90); parser.add_argument("--http-timeout-seconds",type=float,default=8); parser.add_argument("--self-test",action="store_true"); parser.add_argument("--out",type=Path); args=parser.parse_args()
 if args.self_test:_self_test(); print("SELF_TEST_PASS"); return 0
 if args.out is None:parser.error("--out required")
 result=asyncio.run(_run(args)); args.out.parent.mkdir(parents=True,exist_ok=True); args.out.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n"); return 0
if __name__=="__main__":raise SystemExit(main())

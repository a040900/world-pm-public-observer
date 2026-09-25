"""Frozen R2.3 compatibility core for migrated World.xyz <-> Polymarket BTC5M R2.4.

Historical source blob: 3f3fe06a0f7341c88519b00ce599f8915923e9a8.
Public read-only; NO_TRADE. This migration preserves the active R2.4 runtime API.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import statistics
import time
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping

import requests
import websockets

from tools.research import btc5m_fair_value_kill_test_r1 as fee_math
from tools.research import world_limitless_polymarket_btc5m_relative_value_smoke_r0 as radar
from tools.research import world_polymarket_btc5m_leadlag_probe_r1 as wp

SCHEMA_VERSION = "WORLD_POLYMARKET_BTC5M_EXECUTABLE_SYNC_R23"
WINDOW_SECONDS = 300
WINDOW_COUNT = 6
PRIMARY_SIZE_CASH = 10.0
POLL_SECONDS = 0.05
TRIGGER_COOLDOWN_SECONDS = 2.0
MAX_TRIGGERS_PER_SIDE_PER_WINDOW = 8
MAX_WORLD_RADAR_AGE_SECONDS = 2.0
RECHECK_DELAY_SECONDS = 0.250
PM_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
PM_BOOK_URL = "https://clob.polymarket.com/book"
PM_TIME_URL = "https://clob.polymarket.com/time"
WORLD_PROXY_ORDER_URL = "https://aggregator-api-proxy.world-xyz.workers.dev/order"
APP_HEARTBEAT_SECONDS = 10.0
APP_PONG_TIMEOUT_SECONDS = 5.0
RECONNECT_DELAY_SECONDS = 0.25
MAX_WS_MESSAGE_BYTES = 16 * 1024 * 1024
WS_MAX_QUEUE = 4096
SOURCE_ISOLATION_AUTHORITY = "docs/decisions/2026-09-16-world-xyz-source-isolation-r1.json"
R22_REVIEW_AUTHORITY = "docs/reviews/2026-09-16-world-polymarket-btc5m-r22-source-isolated-review-r1.json"


def _to_float(value: Any) -> float | None:
    try: return None if value is None else float(value)
    except (TypeError, ValueError): return None

def _to_int(value: Any) -> int | None:
    try: return None if value is None else int(value)
    except (TypeError, ValueError): return None

def _canon_number(value: Any) -> str:
    number = Decimal(str(value))
    if not number.is_finite(): raise ValueError("NON_FINITE_BOOK_NUMBER")
    text = format(number.normalize(), "f")
    if "." in text: text = text.rstrip("0").rstrip(".")
    return "0" if text in {"-0", ""} else text

def _levels(rows: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    for row in rows or []:
        if not isinstance(row, Mapping): continue
        try:
            price, size = _canon_number(row.get("price")), _canon_number(row.get("size"))
            if Decimal(price) <= 0 or Decimal(size) <= 0: continue
        except Exception: continue
        out[price] = size
    return out

def _book_digest(bids: Mapping[str, str], asks: Mapping[str, str]) -> str:
    raw = json.dumps({"bids": sorted((str(p), str(s)) for p,s in bids.items()), "asks": sorted((str(p), str(s)) for p,s in asks.items())}, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(raw).hexdigest()

def _book_rows(levels: Mapping[str, str], *, asks: bool) -> list[dict[str, float]]:
    rows=[{"price":float(p),"size":float(s)} for p,s in levels.items() if float(s)>0]
    rows.sort(key=lambda r:r["price"], reverse=not asks); return rows

def _next_full_window(server_now: float) -> int: return (int(server_now)//WINDOW_SECONDS+1)*WINDOW_SECONDS

def _token_decimals(mint: str, rpc_url: str) -> int:
    result=wp._rpc(rpc_url,"getTokenSupply",[mint,{"commitment":"confirmed"}])
    if not isinstance(result,Mapping) or not isinstance(result.get("value"),Mapping): raise RuntimeError(f"TOKEN_SUPPLY_INVALID:{mint}")
    return int(result["value"]["decimals"])

def _calibrate_pm_clock(samples:int=5, timeout_seconds:float=5.0)->dict[str,Any]:
    rows=[]
    for _ in range(samples):
        started=time.time(); response=requests.get(PM_TIME_URL,timeout=timeout_seconds,headers={"Accept":"application/json"}); received=time.time(); response.raise_for_status(); server=float(response.json()); midpoint=(started+received)/2
        rows.append({"requestStartedAt":started,"receivedAt":received,"serverTime":server,"rttMs":(received-started)*1000,"serverMinusLocalMidpointSeconds":server-midpoint}); time.sleep(.05)
    offsets=[r["serverMinusLocalMidpointSeconds"] for r in rows]
    return {"samples":rows,"medianServerMinusLocalSeconds":statistics.median(offsets),"serverNowEstimate":time.time()+statistics.median(offsets),"role":"DIAGNOSTIC_AND_WINDOW_ALIGNMENT_ONLY_NOT_A_BOOK_FRESHNESS_GATE"}

class PolymarketVerifiedBook:
    def __init__(self,token_ids:list[str])->None:
        self.token_ids=set(token_ids); self.books={}; self.connected=False; self.connection_generation=0; self.current_error=None; self.last_disconnect_error=None; self.reconnect_count=0; self.frame_count=0; self.last_frame_at=None; self.last_pong_at=None; self.last_ping_sent_at=None; self.ping_outstanding=False; self.error_history=[]; self._reset_books()
    def _reset_books(self)->None:
        self.books={t:{"bids":{},"asks":{},"ready":False,"sourceTimestampMs":None,"serverHash":None,"receiptAt":None,"lastEventType":None,"eventCount":0} for t in self.token_ids}
    def _apply_book(self,message:Mapping[str,Any],received_at:float)->None:
        token=str(message.get("asset_id") or "")
        if token not in self.books:return
        s=self.books[token]; s["bids"]=_levels(message.get("bids")); s["asks"]=_levels(message.get("asks")); s["sourceTimestampMs"]=_to_int(message.get("timestamp")); s["serverHash"]=message.get("hash"); s["receiptAt"]=received_at; s["lastEventType"]="book"; s["eventCount"]+=1; s["ready"]=True
    def _apply_price_change(self,message:Mapping[str,Any],received_at:float)->None:
        timestamp=_to_int(message.get("timestamp")); touched=set()
        for change in message.get("price_changes") or []:
            if not isinstance(change,Mapping):continue
            token=str(change.get("asset_id") or "")
            if token not in self.books:continue
            s=self.books[token]
            if s.get("ready") is not True: raise RuntimeError(f"PM_INCREMENT_BEFORE_FULL_BOOK:{token}")
            try: price,size=_canon_number(change.get("price")),_canon_number(change.get("size"))
            except Exception:continue
            side=str(change.get("side") or "").upper()
            if side not in {"BUY","SELL"} or Decimal(price)<=0:continue
            levels=s["bids"] if side=="BUY" else s["asks"]
            if Decimal(size)<=0:levels.pop(price,None)
            else:levels[price]=size
            s["sourceTimestampMs"]=timestamp; s["receiptAt"]=received_at; s["lastEventType"]="price_change"; touched.add(token)
        for token in touched:self.books[token]["eventCount"]+=1
    async def run(self,stop:asyncio.Event)->None:
        while not stop.is_set():
            self.connection_generation+=1; generation=self.connection_generation; self._reset_books(); self.connected=False; self.current_error=None
            try:
                async with websockets.connect(PM_WS_URL,open_timeout=10,close_timeout=5,ping_interval=None,max_size=MAX_WS_MESSAGE_BYTES,max_queue=WS_MAX_QUEUE) as ws:
                    self.connected=True; self.last_pong_at=None; self.last_ping_sent_at=None; self.ping_outstanding=False
                    await ws.send(json.dumps({"assets_ids":sorted(self.token_ids),"type":"market"},separators=(",",":")))
                    ping_sent_monotonic = None
                    while not stop.is_set():
                        heartbeat_now = time.monotonic()
                        if self.ping_outstanding and ping_sent_monotonic is not None and heartbeat_now - ping_sent_monotonic >= APP_PONG_TIMEOUT_SECONDS:
                            raise RuntimeError("PM_PONG_TIMEOUT")
                        if ping_sent_monotonic is None or heartbeat_now - ping_sent_monotonic >= APP_HEARTBEAT_SECONDS:
                            await ws.send("PING")
                            ping_sent_monotonic = time.monotonic()
                            self.last_ping_sent_at = time.time()
                            self.ping_outstanding = True
                        try: raw=await asyncio.wait_for(ws.recv(),timeout=1)
                        except asyncio.TimeoutError: continue
                        received=time.time(); self.last_frame_at=received; self.frame_count+=1
                        if raw in {"PONG","pong"}: self.last_pong_at=received; self.ping_outstanding=False; continue
                        payload=json.loads(raw)
                        for message in payload if isinstance(payload,list) else [payload]:
                            if not isinstance(message,Mapping):continue
                            if message.get("event_type")=="book":self._apply_book(message,received)
                            elif message.get("event_type")=="price_change":self._apply_price_change(message,received)
            except Exception as exc:
                error=f"GEN{generation}:{type(exc).__name__}:{exc}"; self.current_error=error; self.last_disconnect_error=error; self.error_history.append(error); self.connected=False; self._reset_books()
                if not stop.is_set(): self.reconnect_count+=1; await asyncio.sleep(RECONNECT_DELAY_SECONDS)
        self.connected=False
    def snapshot(self,token_id:str,*,clock_offset_seconds:float|None=None)->dict[str,Any]:
        now=time.time(); s=self.books.get(token_id)
        if s is None:return {"healthy":False,"error":"TOKEN_NOT_SUBSCRIBED"}
        bids,asks=dict(s["bids"]),dict(s["asks"])
        return {"observedAt":now,"healthy":bool(self.connected and s.get("ready") is True and self.current_error is None),"connected":self.connected,"connectionGeneration":self.connection_generation,"currentError":self.current_error,"lastDisconnectError":self.last_disconnect_error,"reconnectCount":self.reconnect_count,"frameCount":self.frame_count,"lastPongAt":self.last_pong_at,"lastFrameAt":self.last_frame_at,"pingOutstanding":self.ping_outstanding,"ready":bool(s.get("ready")),"assetId":token_id,"bestBid":max((float(p) for p in bids),default=None),"bestAsk":min((float(p) for p in asks),default=None),"bids":_book_rows(bids,asks=False),"asks":_book_rows(asks,asks=True),"sourceTimestampMs":_to_int(s.get("sourceTimestampMs")),"receiptAgeMs":None if s.get("receiptAt") is None else (now-float(s["receiptAt"]))*1000,"serverHash":s.get("serverHash"),"localDigest":_book_digest(bids,asks),"eventCount":int(s.get("eventCount") or 0),"lastEventType":s.get("lastEventType")}

class RobustDflowRadar(radar.DflowQuotes):
    def snapshot(self,observed_at:float)->dict[str,Any]:
        row=super().snapshot(observed_at); row["healthy"]=bool(row.get("connected") is True and row.get("connectionError") is None); return row

def _world_anonymous_quote(session:requests.Session,*,output_mint:str,cash_decimals:int,outcome_decimals:int,timeout_seconds:float)->dict[str,Any]:
    params={"inputMint":wp.CASH_MINT,"outputMint":output_mint,"amount":str(int(round(PRIMARY_SIZE_CASH*(10**cash_decimals)))),"slippageBps":"200","predictionMarketSlippageBps":"200","allowSyncExec":"true","allowAsyncExec":"true"}; headers={"Accept":"application/json","Origin":"https://world.xyz","Referer":"https://world.xyz/","User-Agent":"prediction-market-relative-value-r24/1.0"}; started=time.time(); response=session.get(WORLD_PROXY_ORDER_URL,params=params,headers=headers,timeout=timeout_seconds); received=time.time()
    try:payload=response.json()
    except ValueError:payload={"rawText":response.text[:2000]}
    row={"requestStartedAt":started,"receivedAt":received,"elapsedMs":(received-started)*1000,"httpStatus":response.status_code,"requestSizeCash":PRIMARY_SIZE_CASH,"userPublicKeySupplied":False,"credentialUsed":False,"signingPerformed":False,"transactionSubmitted":False,"success":False}
    if not isinstance(payload,Mapping) or response.status_code!=200:return row
    out_amount=int(payload.get("outAmount") or 0); min_out=int(payload.get("minOutAmount") or 0); row.update({"success":out_amount>0 and min_out>0,"contextSlot":payload.get("contextSlot"),"executionMode":payload.get("executionMode"),"inAmount":payload.get("inAmount"),"outAmount":payload.get("outAmount"),"minOutAmount":payload.get("minOutAmount"),"priceImpactPct":payload.get("priceImpactPct"),"outputUiShares":out_amount/(10**outcome_decimals),"minOutputUiShares":min_out/(10**outcome_decimals),"platformFee":payload.get("platformFee")}); return row

def _fetch_pm_rest_book(session:requests.Session,token_id:str,timeout_seconds:float)->dict[str,Any]:
    started=time.time(); response=session.get(PM_BOOK_URL,params={"token_id":token_id},timeout=timeout_seconds,headers={"Accept":"application/json"}); received=time.time(); response.raise_for_status(); payload=response.json(); bids=_levels(payload.get("bids")); asks=_levels(payload.get("asks")); return {"requestStartedAt":started,"receivedAt":received,"elapsedMs":(received-started)*1000,"tokenId":token_id,"timestamp":payload.get("timestamp"),"serverHash":payload.get("hash"),"bids":_book_rows(bids,asks=False),"asks":_book_rows(asks,asks=True),"localDigest":_book_digest(bids,asks)}
def _pm_net_factor(price:float,fee_schedule:dict[str,Any])->float:
    if price<=0:return 0.0
    fee=fee_math.taker_fee_per_share(price,fee_schedule,endpoint="FEE_UPPER"); return max(0.0,1.0-fee/price)
def _pm_effective_top_cost(price:float|None,fee_schedule:dict[str,Any])->float|None:
    if price is None:return None
    factor=_pm_net_factor(price,fee_schedule); return None if factor<=0 else price/factor
def _walk_pm_book_for_net_shares(asks:list[dict[str,float]],target_net_shares:float,fee_schedule:dict[str,Any])->dict[str,Any]:
    remaining=float(target_net_shares); total_cost=total_gross=total_net=0.0; levels=[]
    for level in asks:
        if remaining<=1e-12:break
        price=float(level["price"]); gross=float(level["size"]); factor=_pm_net_factor(price,fee_schedule)
        if factor<=0:continue
        take_net=min(remaining,gross*factor); take_gross=take_net/factor; cost=take_gross*price; total_cost+=cost; total_gross+=take_gross; total_net+=take_net; remaining-=take_net; levels.append({"price":price,"grossShares":take_gross,"netShares":take_net,"feeShares":take_gross-take_net,"cost":cost})
    return {"targetNetShares":target_net_shares,"filled":remaining<=1e-9,"unfilledNetShares":max(0.0,remaining),"grossSharesBought":total_gross,"netSharesReceived":total_net,"cost":total_cost,"vwapGross":None if total_gross<=0 else total_cost/total_gross,"levels":levels}
def _distribution(values:list[float])->dict[str,Any]:
    rows=sorted(values); return {"count":len(rows),"min":rows[0] if rows else None,"median":rows[len(rows)//2] if rows else None,"max":rows[-1] if rows else None}

async def _confirm_event(**kwargs:Any)->dict[str,Any]:
    initial=await _one_synchronized_state(world_session=kwargs["world_session"],pm_session=kwargs["pm_session"],pm_feed=kwargs["pm_feed"],world_mint=kwargs["world_mint"],world_decimals=kwargs["world_decimals"],pm_token=kwargs["pm_token"],pm_fee_schedule=kwargs["pm_fee_schedule"],cash_decimals=kwargs["cash_decimals"],http_timeout_seconds=kwargs["http_timeout_seconds"],clock_offset_seconds=kwargs["clock_offset_seconds"])
    return {"pair":kwargs["pair"],"trigger":kwargs["trigger"],"initial":initial,"plus250ms":None,"sameEventPersistent250msQualified":False}

async def _one_synchronized_state(**kwargs:Any)->dict[str,Any]: raise RuntimeError("R23_BASE_STATE_REPLACED_BY_R24_AT_RUNTIME")

async def _run_window(*,window_index:int,start_ts:int,args:argparse.Namespace,cash_decimals:int,clock_offset_seconds:float|None)->dict[str,Any]:
    end_ts=start_ts+WINDOW_SECONDS
    if time.time()<start_ts:await asyncio.sleep(start_ts-time.time())
    pm_market=await asyncio.to_thread(wp.fetch_polymarket_market,start_ts); stop=asyncio.Event(); pm_feed=PolymarketVerifiedBook([pm_market.up_token,pm_market.down_token]); pm_task=asyncio.create_task(pm_feed.run(stop))
    try: world_market=await asyncio.to_thread(radar._discover_world_market,start_ts,args.rpc_url,min(float(end_ts),time.time()+args.world_discovery_timeout_seconds))
    except Exception: world_market=None
    row={"windowIndex":window_index,"startTs":start_ts,"endTs":end_ts,"worldMarket":None,"polymarket":{"conditionId":pm_market.condition_id,"upToken":pm_market.up_token,"downToken":pm_market.down_token,"feeSchedule":pm_market.fee_schedule,"cryptoConfig":pm_market.crypto_config},"radarPollCount":0,"radarPollBelowOneCount":{"WORLD_YES+PM_DOWN":0,"WORLD_NO+PM_UP":0},"confirmationTriggerCount":{"WORLD_YES+PM_DOWN":0,"WORLD_NO+PM_UP":0},"confirmations":[],"errors":[]}
    if world_market is None: stop.set(); pm_task.cancel(); return row
    yes_decimals,no_decimals=await asyncio.gather(asyncio.to_thread(_token_decimals,world_market.yes_mint,args.rpc_url),asyncio.to_thread(_token_decimals,world_market.no_mint,args.rpc_url)); row["worldMarket"]={"market":world_market.market,"yesMint":world_market.yes_mint,"noMint":world_market.no_mint,"yesDecimals":yes_decimals,"noDecimals":no_decimals,"description":world_market.description}
    dflow=RobustDflowRadar(world_market.yes_mint,world_market.no_mint); dflow_task=asyncio.create_task(dflow.run(stop)); world_session=requests.Session(); pm_session=requests.Session(); last_trigger={"WORLD_YES+PM_DOWN":-1e9,"WORLD_NO+PM_UP":-1e9}; trigger_count={"WORLD_YES+PM_DOWN":0,"WORLD_NO+PM_UP":0}
    try:
        while time.time()<end_ts:
            loop_at=time.time(); row["radarPollCount"]+=1; world_snapshot=dflow.snapshot(loop_at)
            for pair,world_side,world_mint,world_decimals,pm_token in [("WORLD_YES+PM_DOWN","yes",world_market.yes_mint,yes_decimals,pm_market.down_token),("WORLD_NO+PM_UP","no",world_market.no_mint,no_decimals,pm_market.up_token)]:
                pm_snapshot=pm_feed.snapshot(pm_token,clock_offset_seconds=clock_offset_seconds); world_leg=world_snapshot.get(world_side) or {}; world_ask=_to_float(world_leg.get("ask")); world_age=_to_float(world_leg.get("quoteAgeSeconds")); pm_ask=_to_float(pm_snapshot.get("bestAsk"))
                if pm_snapshot.get("healthy") is not True or world_snapshot.get("healthy") is not True or world_ask is None or pm_ask is None or world_age is None or world_age>MAX_WORLD_RADAR_AGE_SECONDS:continue
                pm_effective=_pm_effective_top_cost(pm_ask,pm_market.fee_schedule)
                if pm_effective is None or world_ask+pm_effective>=1:continue
                row["radarPollBelowOneCount"][pair]+=1; now=time.time()
                if trigger_count[pair]>=MAX_TRIGGERS_PER_SIDE_PER_WINDOW or now-last_trigger[pair]<TRIGGER_COOLDOWN_SECONDS:continue
                trigger_count[pair]+=1; last_trigger[pair]=now; row["confirmationTriggerCount"][pair]+=1
                trigger={"observedAt":now,"secondsIntoWindow":now-start_ts,"worldIndicativeAsk":world_ask,"worldIndicativeAgeSeconds":world_age,"pmLocalBestAsk":pm_ask,"indicativeSum":world_ask+pm_effective}
                try: row["confirmations"].append(await _confirm_event(pair=pair,trigger=trigger,world_session=world_session,pm_session=pm_session,pm_feed=pm_feed,world_mint=world_mint,world_decimals=world_decimals,pm_token=pm_token,pm_fee_schedule=pm_market.fee_schedule,cash_decimals=cash_decimals,http_timeout_seconds=args.http_timeout_seconds,clock_offset_seconds=clock_offset_seconds,window_end_ts=end_ts))
                except Exception as exc: row["errors"].append(f"CONFIRMATION:{pair}:{type(exc).__name__}:{exc}")
            await asyncio.sleep(max(0,POLL_SECONDS-(time.time()-loop_at)))
    finally:
        stop.set(); dflow_task.cancel(); pm_task.cancel(); world_session.close(); pm_session.close(); row["pmFeedDiagnostics"]={"reconnectCount":pm_feed.reconnect_count,"errorHistory":pm_feed.error_history,"frameCount":pm_feed.frame_count,"up":pm_feed.snapshot(pm_market.up_token),"down":pm_feed.snapshot(pm_market.down_token)}
    return row

def _summary(windows:list[dict[str,Any]])->dict[str,Any]:
    confirmations=[c for w in windows for c in w.get("confirmations") or []]; qualified=[]
    for c in confirmations:
        e=(c.get("initial") or {}).get("evaluation") or {}
        if e.get("measurementQualified") is True:qualified.append(e)
    units=[float(e["unitCostPerConditionalPayout"]) for e in qualified if e.get("unitCostPerConditionalPayout") is not None]; below=sum(1 for e in qualified if e.get("belowOne") is True); persistent=sum(1 for c in confirmations if c.get("sameEventPersistent250msQualified") is True)
    return {"worldMarketsDiscovered":sum(1 for w in windows if w.get("worldMarket")),"radarPollBelowOneCount":{p:sum(int((w.get("radarPollBelowOneCount") or {}).get(p,0)) for w in windows) for p in ("WORLD_YES+PM_DOWN","WORLD_NO+PM_UP")},"confirmationCount":len(confirmations),"worldAnonymousExactQuoteSuccessCount":sum(1 for c in confirmations if ((c.get("initial") or {}).get("worldQuote") or {}).get("success") is True),"cleanSynchronizedMeasurementCount":len(qualified),"cleanSynchronizedBelowOneCount":below,"sameEventPersistent250msQualifiedCount":persistent,"cleanUnitCost":_distribution(units),"bestCleanInitialBelowOne":None,"bestPersistent250ms":None,"verdict":"PROMOTE_R23_CLEAN_SYNCHRONIZED_PERSISTENT_250MS_CANDIDATE_SETTLEMENT_CASH_BASIS_WORLD_EXECUTION_FEE_AND_FILL_UNRESOLVED" if persistent else ("PARK_R23_CLEAN_BELOW_ONE_NOT_PERSISTENT_250MS" if below else "PARK_R23_NO_CLEAN_SYNCHRONIZED_BELOW_ONE_IN_SIX_WINDOWS")}

def _self_test()->None:
    assert fee_math.taker_fee_per_share(.49,{"rate":.07,"exponent":1},endpoint="FEE_UPPER")==.01749
    walk=_walk_pm_book_for_net_shares([{"price":.4,"size":100}],10,{"rate":.07,"exponent":1}); assert walk["filled"] is True

async def _run(args:argparse.Namespace)->dict[str,Any]:
    try: clock=await asyncio.to_thread(_calibrate_pm_clock); offset=_to_float(clock.get("medianServerMinusLocalSeconds")); server_now=_to_float(clock.get("serverNowEstimate")) or time.time()
    except Exception as exc: clock={"error":str(exc)}; offset=None; server_now=time.time()
    first=_next_full_window(server_now); cash_decimals=await asyncio.to_thread(_token_decimals,wp.CASH_MINT,args.rpc_url); windows=[]
    for i in range(1,WINDOW_COUNT+1): windows.append(await _run_window(window_index=i,start_ts=first+(i-1)*WINDOW_SECONDS,args=args,cash_decimals=cash_decimals,clock_offset_seconds=offset))
    return {"schemaVersion":SCHEMA_VERSION,"mode":"PUBLIC_READ_ONLY_NO_TRADE","sourceIsolationAuthority":SOURCE_ISOLATION_AUTHORITY,"supersedingReviewAuthority":R22_REVIEW_AUTHORITY,"firstWindowStartTs":first,"windowCount":WINDOW_COUNT,"windowSeconds":WINDOW_SECONDS,"primarySizeCash":PRIMARY_SIZE_CASH,"clockCalibration":clock,"windows":windows,"summary":_summary(windows)}

def main()->int:
    parser=argparse.ArgumentParser(); parser.add_argument("--rpc-url",default=radar.DEFAULT_RPC); parser.add_argument("--world-discovery-timeout-seconds",type=float,default=90); parser.add_argument("--http-timeout-seconds",type=float,default=8); parser.add_argument("--self-test",action="store_true"); parser.add_argument("--out",type=Path); args=parser.parse_args()
    if args.self_test:_self_test(); print("SELF_TEST_PASS"); return 0
    if args.out is None:parser.error("--out required")
    result=asyncio.run(_run(args)); args.out.parent.mkdir(parents=True,exist_ok=True); args.out.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n"); print(json.dumps({"summary":result["summary"]},indent=2)); return 0
if __name__=="__main__":raise SystemExit(main())

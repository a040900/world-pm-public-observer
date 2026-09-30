"""Bounded World <-> Polymarket BTC5m dev-order Economic POC R0.

Public read-only. Zero capital. No signing or transaction submission.
Collects a few live 5m windows. DFlow developer /order is used only as a
read-only executable-quote probe and may be rate limited.
"""
from __future__ import annotations
import argparse, asyncio, json, time
from pathlib import Path
from typing import Any, Mapping
import requests

from tools.research import world_discovery_liveness_r0 as live
from tools.research import world_limitless_polymarket_btc5m_relative_value_smoke_r0 as radar
from tools.research import world_polymarket_btc5m_executable_sync_r23 as r23
from tools.research import world_polymarket_btc5m_leadlag_probe_r1 as wp

DEV_ORDER="https://dev-quote-api.dflow.net/order"

def slot(ts:int)->int: return (ts//300)*300

def pm_book(session:requests.Session, token:str)->dict[str,Any]:
    started=time.time()
    r=session.get(r23.PM_BOOK_URL,params={"token_id":token},headers={"Accept":"application/json"},timeout=8)
    received=time.time()
    body=r.text[:4000]
    row={"startedAt":started,"receivedAt":received,"elapsedMs":(received-started)*1000,"status":r.status_code,"bodyPrefix":body}
    if r.status_code==200:
        p=r.json()
        asks=r23._book_rows(r23._levels(p.get("asks")),asks=True)
        row.update({"asks":asks,"bestAsk":asks[0]["price"] if asks else None,"timestamp":p.get("timestamp"),"hash":p.get("hash")})
    return row

def dev_quote(session:requests.Session, mint:str, cash_decimals:int, out_decimals:int, cash:float)->dict[str,Any]:
    return live.exact_quote(session,endpoint=DEV_ORDER,output_mint=mint,cash_decimals=cash_decimals,
                            outcome_decimals=out_decimals,request_cash=cash)

def econ(world:dict[str,Any], pm:dict[str,Any], fee:dict[str,Any], cash:float)->dict[str,Any]:
    if not world.get("success") or pm.get("status")!=200:
        return {"qualified":False}
    shares=float(world.get("minOutputUiShares") or 0)
    if shares<=0:return {"qualified":False}
    walk=r23._walk_pm_book_for_net_shares(pm.get("asks") or [],shares,fee)
    if not walk.get("filled"):return {"qualified":False,"worldMinShares":shares,"pmWalk":walk}
    total=cash+float(walk["cost"])
    payout=shares
    edge=payout-total
    return {"qualified":True,"worldMinShares":shares,"pmWalk":walk,"packageCost":total,
            "guaranteedPayoutIfPayoffsEquivalent":payout,"edgeUsdBeforeBasisRisk":edge,
            "edgePerShare":edge/shares if shares else None,"unitCost":total/shares if shares else None}

async def one_window(start:int,cash:float)->dict[str,Any]:
    out={"startTs":start,"endTs":start+300,"observedAt":time.time(),"researchScore":0}
    # bounded broad discovery avoids the old 80-signature false negative
    disc=await asyncio.to_thread(live.broad_discovery,int(time.time()),signature_limit=500,scan_cap=220,deadline_seconds=70)
    m=disc.get("matches",{}).get(str(start))
    out["discoverySummary"]={"matched":bool(m),"errors":disc.get("errors"),"transactionsFetched":disc.get("transactionsFetched"),
                             "scanOrdinal":None if not m else m.get("scanOrdinal")}
    if not isinstance(m,Mapping):
        out["status"]="WORLD_NOT_DISCOVERED"; return out
    wm=wp.WorldMarket(start,start+300,str(m["market"]),str(m["yesMint"]),str(m["noMint"]),str(m["description"]))
    out["worldMarket"]=dict(m)
    pm=await asyncio.to_thread(wp.fetch_polymarket_market,start)
    out["polymarket"]={"conditionId":pm.condition_id,"upToken":pm.up_token,"downToken":pm.down_token,
                       "feeSchedule":pm.fee_schedule,"cryptoConfig":pm.crypto_config}
    dflow=await live.collect_dflow(wm,seconds=8.0)
    out["dflowRadar"]=dflow
    cash_dec=await asyncio.to_thread(r23._token_decimals,wp.CASH_MINT,wp.DEFAULT_SOLANA_RPC)
    yes_dec,no_dec=await asyncio.gather(asyncio.to_thread(r23._token_decimals,wm.yes_mint,wp.DEFAULT_SOLANA_RPC),
                                        asyncio.to_thread(r23._token_decimals,wm.no_mint,wp.DEFAULT_SOLANA_RPC))
    s=requests.Session()
    pairs=[
      ("WORLD_YES+PM_DOWN",wm.yes_mint,yes_dec,pm.down_token),
      ("WORLD_NO+PM_UP",wm.no_mint,no_dec,pm.up_token),
    ]
    out["pairs"]={}
    for name,mint,dec,token in pairs:
        q=await asyncio.to_thread(dev_quote,s,mint,cash_dec,dec,cash)
        b=await asyncio.to_thread(pm_book,s,token)
        out["pairs"][name]={"worldDevOrder":q,"pmBook":b,"economics":econ(q,b,pm.fee_schedule,cash)}
    s.close()
    out["status"]="COMPLETE"; return out

async def main(args):
    result={"schemaVersion":"WORLD_PM_DEV_ORDER_ECONOMIC_POC_R0","mode":"PUBLIC_READ_ONLY_NO_TRADE",
            "researchScore":0,"requestedWindows":args.windows,"requestCash":args.request_cash,"windows":[],"startedAt":time.time()}
    for i in range(args.windows):
        now=int(time.time()); start=slot(now)
        # avoid starting at the very end of a slot
        if start+300-time.time()<100:
            await asyncio.sleep(start+300-time.time()+8)
            start+=300
        try: row=await one_window(start,args.request_cash)
        except Exception as e: row={"startTs":start,"status":"ERROR","error":f"{type(e).__name__}:{e}"}
        result["windows"].append(row)
        if i+1<args.windows:
            wait=max(0,start+300-time.time()+8)
            if wait: await asyncio.sleep(wait)
    result["finishedAt"]=time.time()
    # compact summary
    routes=[]; positives=[]
    for w in result["windows"]:
        for name,p in (w.get("pairs") or {}).items():
            q=p.get("worldDevOrder") or {}; e=p.get("economics") or {}
            routes.append({"startTs":w.get("startTs"),"pair":name,"status":q.get("httpStatus"),"success":q.get("success"),
                           "body":q.get("responseBody"),"qualified":e.get("qualified"),"edgeBps":None if e.get("edgePerShare") is None else e["edgePerShare"]*10000})
            if e.get("qualified") and e.get("edgePerShare",0)>0: positives.append(routes[-1])
    result["summary"]={"routeAttempts":len(routes),"routeSuccesses":sum(1 for x in routes if x["success"]),
                       "qualifiedEconomics":sum(1 for x in routes if x["qualified"]),"positiveEconomics":len(positives),
                       "attempts":routes}
    Path(args.output).parent.mkdir(parents=True,exist_ok=True)
    Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps(result["summary"],sort_keys=True))

if __name__=="__main__":
    p=argparse.ArgumentParser(); p.add_argument("--windows",type=int,default=3); p.add_argument("--request-cash",type=float,default=10.0)
    p.add_argument("--output",default="artifacts/world-pm-dev-order-economic-poc-r0.json")
    asyncio.run(main(p.parse_args()))

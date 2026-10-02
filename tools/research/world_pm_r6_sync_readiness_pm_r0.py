"""R6 synchronization-readiness PM capture.

Captures exactly one prospective BTC 5m Polymarket window at 2-second cadence.
Public read-only; NO_TRADE. World data is retrieved separately from the official MCP
after this frozen window closes.
"""
from __future__ import annotations
import argparse, json, time, urllib.parse, urllib.request
from pathlib import Path
from typing import Any, Mapping

GAMMA="https://gamma-api.polymarket.com/events"
BOOK="https://clob.polymarket.com/book"
UA="world-pm-r6-readiness/1.0"

def get_json(url:str, timeout:float=8.0)->Any:
    req=urllib.request.Request(url,headers={"Accept":"application/json","User-Agent":UA})
    with urllib.request.urlopen(req,timeout=timeout) as r:
        return json.loads(r.read().decode())

def parse_list(v:Any)->list[Any]:
    if isinstance(v,list): return v
    if isinstance(v,str):
        x=json.loads(v)
        if isinstance(x,list): return x
    raise ValueError("LIST_INVALID")

def pm_market(start:int)->dict[str,Any]:
    slug=f"btc-updown-5m-{start}"
    rows=get_json(f"{GAMMA}?"+urllib.parse.urlencode({"slug":slug}))
    if not isinstance(rows,list) or len(rows)!=1: raise RuntimeError(f"EVENT_NOT_UNIQUE:{slug}")
    ev=rows[0]; ms=ev.get("markets") or []
    if len(ms)!=1: raise RuntimeError(f"MARKET_NOT_UNIQUE:{slug}")
    m=ms[0]
    tokens=[str(x) for x in parse_list(m.get("clobTokenIds"))]
    outcomes=[str(x) for x in parse_list(m.get("outcomes"))]
    if outcomes!=["Up","Down"] or len(tokens)!=2: raise RuntimeError("BINARY_IDENTITY_INVALID")
    if m.get("active") is not True or m.get("closed") is True or m.get("acceptingOrders") is not True:
        raise RuntimeError("MARKET_NOT_TRADABLE")
    return {"slug":slug,"conditionId":m.get("conditionId"),"upToken":tokens[0],"downToken":tokens[1],
            "feeSchedule":m.get("feeSchedule"),"cryptoMarketConfig":m.get("cryptoMarketConfig"),
            "description":ev.get("description") or m.get("description")}

def book(token:str)->dict[str,Any]:
    started=time.time()
    try:
        body=get_json(BOOK+"?"+urllib.parse.urlencode({"token_id":token}))
        received=time.time()
        asks=body.get("asks") or []; bids=body.get("bids") or []
        def norm(rows,reverse=False):
            out=[]
            for x in rows:
                try: out.append({"price":float(x["price"]),"size":float(x["size"])})
                except Exception: pass
            out.sort(key=lambda z:z["price"], reverse=reverse)
            return out
        a=norm(asks); b=norm(bids,True)
        return {"success":True,"startedAt":started,"receivedAt":received,"elapsedMs":(received-started)*1000,
                "sourceTimestamp":body.get("timestamp"),"hash":body.get("hash"),
                "bestAsk":a[0]["price"] if a else None,"bestBid":b[0]["price"] if b else None,
                "asks":a,"bids":b}
    except Exception as e:
        return {"success":False,"startedAt":started,"receivedAt":time.time(),"error":f"{type(e).__name__}:{e}"}

def choose_start(min_lead:int)->int:
    now=int(time.time()); start=(now//300+1)*300
    if start-now<min_lead: start+=300
    return start

def main(args):
    start=choose_start(args.min_lead_seconds); end=start+300
    result={"schemaVersion":"WORLD_PM_R6_SYNC_READINESS_PM_R0","mode":"PUBLIC_READ_ONLY_NO_TRADE",
            "selectedAt":time.time(),"startTs":start,"endTs":end,"cadenceSeconds":args.cadence,
            "snapshots":[]}
    while time.time()<start-20: time.sleep(min(10,start-20-time.time()))
    market=None
    last=None
    while time.time()<start and market is None:
        try: market=pm_market(start)
        except Exception as e:
            last=f"{type(e).__name__}:{e}"; time.sleep(2)
    if market is None:
        result["fatalError"]=f"PM_IDENTITY_UNAVAILABLE:{last}"
    else:
        result["market"]=market
        target=float(start)
        idx=0
        while target<end:
            now=time.time()
            if now<target: time.sleep(target-now)
            captured=time.time()
            up=book(market["upToken"]); down=book(market["downToken"])
            result["snapshots"].append({"index":idx,"targetAt":target,"capturedAt":captured,"up":up,"down":down})
            idx+=1; target=start+idx*args.cadence
    result["finishedAt"]=time.time()
    ok=[x for x in result["snapshots"] if x["up"].get("success") and x["down"].get("success")]
    spacings=[ok[i]["capturedAt"]-ok[i-1]["capturedAt"] for i in range(1,len(ok))]
    def median(a):
        if not a:return None
        s=sorted(a); n=len(s); return s[n//2] if n%2 else (s[n//2-1]+s[n//2])/2
    result["summary"]={"attempted":len(result["snapshots"]),"pairedSuccess":len(ok),
                       "medianCaptureSpacing":median(spacings),"maxCaptureSpacing":max(spacings) if spacings else None}
    Path(args.output).parent.mkdir(parents=True,exist_ok=True)
    Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2,sort_keys=True)+"\n")
    print(json.dumps({"startTs":start,**result["summary"]},sort_keys=True))

if __name__=="__main__":
    p=argparse.ArgumentParser(); p.add_argument("--cadence",type=float,default=2.0)
    p.add_argument("--min-lead-seconds",type=int,default=45)
    p.add_argument("--output",default="artifacts/world-pm-r6-sync-readiness-pm-r0.json")
    main(p.parse_args())

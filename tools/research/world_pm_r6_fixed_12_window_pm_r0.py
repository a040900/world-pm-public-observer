"""R6 fixed 12-window prospective Polymarket capture.

Selection is frozen before the first window begins. Exactly 12 consecutive BTC 5m
windows remain in the denominator. Public read-only; NO_TRADE.
"""
from __future__ import annotations
import argparse, json, math, time, urllib.parse, urllib.request
from pathlib import Path
from typing import Any

GAMMA="https://gamma-api.polymarket.com/events"
BOOK="https://clob.polymarket.com/book"
UA="world-pm-r6-12window/1.0"

def get_json(url:str,timeout:float=8.0)->Any:
    req=urllib.request.Request(url,headers={"Accept":"application/json","User-Agent":UA})
    with urllib.request.urlopen(req,timeout=timeout) as r:return json.loads(r.read().decode())

def plist(v):
    if isinstance(v,list):return v
    if isinstance(v,str):
        x=json.loads(v)
        if isinstance(x,list):return x
    raise ValueError("LIST_INVALID")

def market(start:int,timeout:float=8.0):
    slug=f"btc-updown-5m-{start}"
    rows=get_json(GAMMA+"?"+urllib.parse.urlencode({"slug":slug}),timeout=timeout)
    if not isinstance(rows,list) or len(rows)!=1:raise RuntimeError(f"EVENT_NOT_UNIQUE:{slug}")
    ev=rows[0]; ms=ev.get("markets") or []
    if len(ms)!=1:raise RuntimeError(f"MARKET_NOT_UNIQUE:{slug}")
    m=ms[0]; tok=[str(x) for x in plist(m.get("clobTokenIds"))]; out=[str(x) for x in plist(m.get("outcomes"))]
    if out!=["Up","Down"] or len(tok)!=2:raise RuntimeError("BINARY_IDENTITY_INVALID")
    return {"slug":slug,"conditionId":m.get("conditionId"),"upToken":tok[0],"downToken":tok[1],
            "feeSchedule":m.get("feeSchedule"),"cryptoMarketConfig":m.get("cryptoMarketConfig"),
            "activeAtIdentity":m.get("active"),"closedAtIdentity":m.get("closed"),
            "acceptingOrdersAtIdentity":m.get("acceptingOrders"),
            "description":ev.get("description") or m.get("description")}

def book(token:str,end:float):
    st=time.time()
    if st>=end:
        return {"success":False,"startedAt":st,"receivedAt":st,"error":"WINDOW_ENDED_NO_REQUEST"}
    try:
        body=get_json(BOOK+"?"+urllib.parse.urlencode({"token_id":token}),timeout=min(8.0,end-st))
        rc=time.time()
        def norm(rows,rev=False):
            out=[]
            for x in rows or []:
                try:out.append({"price":float(x["price"]),"size":float(x["size"])})
                except Exception:pass
            out.sort(key=lambda z:z["price"],reverse=rev);return out
        a=norm(body.get("asks"));b=norm(body.get("bids"),True)
        result={"success":rc<end,"startedAt":st,"receivedAt":rc,"elapsedMs":(rc-st)*1000,
                "sourceTimestamp":body.get("timestamp"),"hash":body.get("hash"),
                "bestAsk":a[0]["price"] if a else None,"bestBid":b[0]["price"] if b else None,
                "asks":a,"bids":b}
        if rc>=end:result["error"]="RECEIVED_AFTER_WINDOW"
        return result
    except Exception as e:return {"success":False,"startedAt":st,"receivedAt":time.time(),"error":f"{type(e).__name__}:{e}"}

def settlement(slug:str):
    try:
        rows=get_json(GAMMA+"?"+urllib.parse.urlencode({"slug":slug}))
        if not isinstance(rows,list) or len(rows)!=1:return {"success":False,"error":"EVENT_NOT_UNIQUE"}
        ms=rows[0].get("markets") or []
        if len(ms)!=1:return {"success":False,"error":"MARKET_NOT_UNIQUE"}
        m=ms[0]
        return {"success":True,"checkedAt":time.time(),"closed":m.get("closed"),"active":m.get("active"),
                "outcomes":m.get("outcomes"),"outcomePrices":m.get("outcomePrices"),
                "resolutionSource":m.get("resolutionSource"),"question":m.get("question")}
    except Exception as e:return {"success":False,"checkedAt":time.time(),"error":f"{type(e).__name__}:{e}"}

def choose_start(min_lead:int):
    now=int(time.time()); s=(now//300+1)*300
    if s-now<min_lead:s+=300
    return s

def capture_window(start:int,cad:float):
    if not math.isfinite(cad) or cad<=0:raise ValueError("CADENCE_MUST_BE_POSITIVE_FINITE")
    end=start+300; cap_start=start+5; cap_end=end-5
    rec={"startTs":start,"endTs":end,"snapshots":[],"skippedTargets":0,
         "captureRegionStart":cap_start,"captureRegionEnd":cap_end}
    while time.time()<start-20:time.sleep(min(10,start-20-time.time()))
    m=None; err=None
    # The prior sequential window may finish after this frozen start. Keep the
    # exact identity, allow bounded late discovery, and record real capture time.
    while time.time()<end and m is None:
        try:
            m=market(start,timeout=min(8.0,end-time.time()))
            rec["identityReceivedAt"]=time.time()
            if rec["identityReceivedAt"]>=end:
                m=None;err="IDENTITY_RECEIVED_AFTER_WINDOW";break
        except Exception as e:
            err=f"{type(e).__name__}:{e}";time.sleep(max(0,min(2,end-time.time())))
    if m is None:
        rec["identityError"]=err or "WINDOW_EXPIRED_BEFORE_IDENTITY"
        while time.time()<end:time.sleep(min(10,end-time.time()))
        rec["settlement"]={"success":False,"error":"NO_MARKET_IDENTITY"}
        return rec
    rec["market"]=m
    i=0; target=cap_start+i*cad
    while target<cap_end:
        now=time.time()
        if now<target:time.sleep(target-now)
        cap=time.time()
        if cap>=cap_end:break
        # Do not burst through past targets with current books after a slow call.
        missed=max(0,math.floor((cap-target)/cad))
        rec["skippedTargets"]+=missed;i+=missed;target=cap_start+i*cad
        if target>=cap_end:break
        rec["snapshots"].append({"index":i,"targetAt":target,"capturedAt":cap,
                                 "up":book(m["upToken"],end),"down":book(m["downToken"],end)})
        i+=1;target=cap_start+i*cad
    time.sleep(max(0,min(2.0,end+2-time.time())))
    rec["settlement"]=settlement(m["slug"])
    return rec

def main(args):
    if not math.isfinite(args.cadence) or args.cadence<=0:raise ValueError("CADENCE_MUST_BE_POSITIVE_FINITE")
    first=choose_start(args.min_lead_seconds)
    starts=[first+i*300 for i in range(12)]
    out={"schemaVersion":"WORLD_PM_R6_FIXED_12_WINDOW_PM_R0","mode":"PUBLIC_READ_ONLY_NO_TRADE",
         "frozenAt":time.time(),"frozenStarts":starts,"cadenceSeconds":args.cadence,"windows":[]}
    for s in starts:
        rec=capture_window(s,args.cadence);out["windows"].append(rec)
        Path(args.output).parent.mkdir(parents=True,exist_ok=True)
        Path(args.output).write_text(json.dumps(out,ensure_ascii=False,indent=2,sort_keys=True)+"\n")
    out["finishedAt"]=time.time()
    out["summary"]={"windowsFrozen":12,"windowsRecorded":len(out["windows"]),
                    "windowsWithIdentity":sum("market" in w for w in out["windows"]),
                    "pairedSuccessSnapshots":sum(sum(1 for x in w.get("snapshots",[]) if x["up"].get("success") and x["down"].get("success")) for w in out["windows"])}
    # This is PM capture completeness only; full frozen qualification and signal
    # adjudication still belong to the analyzer/reviewer.
    sufficient=all("market" in w and sum(x["up"].get("success") and x["down"].get("success") for x in w["snapshots"])>=120 for w in out["windows"])
    out["summary"]["captureDataStatus"]="PM_CAPTURE_RECORDED_PENDING_QUALIFICATION" if sufficient else "NO_RESULT_PM_CAPTURE_INCOMPLETE"
    Path(args.output).write_text(json.dumps(out,ensure_ascii=False,indent=2,sort_keys=True)+"\n")
    print(json.dumps(out["summary"],sort_keys=True))

if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--cadence",type=float,default=2.0)
    p.add_argument("--min-lead-seconds",type=int,default=45)
    p.add_argument("--output",default="artifacts/world-pm-r6-fixed-12-window-pm-r0.json")
    main(p.parse_args())

"""Pre-start World BTC5M identity probe using the public frontend catalog. NO_TRADE."""
from __future__ import annotations
import argparse, datetime as dt, json, time
from collections.abc import Mapping, Sequence
from urllib.parse import urlencode
import requests

CATALOG="https://markets-api-proxy.world-xyz.workers.dev/api/v1/events"
META="https://m.world.xyz"
CASH="CASHx9KJUStyftLFWGvEVf59SGeG9sh5FfcnZMVPCASH"

def expected_description(start:int)->str:
    end=start+300
    a=time.strftime("%Y-%m-%d %H:%M:%S UTC",time.gmtime(start))
    b=time.strftime("%Y-%m-%d %H:%M:%S UTC",time.gmtime(end))
    return f"BTC/USD closes at or above its open between {a} and {b}."

def ts(v):
    if v is None:return None
    if isinstance(v,(int,float)):
        x=float(v); return x/1000 if x>10_000_000_000 else x
    if isinstance(v,str):
        try:return dt.datetime.fromisoformat(v.replace("Z","+00:00")).timestamp()
        except ValueError:
            try:
                x=float(v); return x/1000 if x>10_000_000_000 else x
            except ValueError:return None
    return None

def events(payload):
    if isinstance(payload,list): return [x for x in payload if isinstance(x,Mapping)]
    if not isinstance(payload,Mapping): return []
    for k in ("events","items"):
        v=payload.get(k)
        if isinstance(v,list): return [x for x in v if isinstance(x,Mapping)]
    d=payload.get("data")
    if d is not None:return events(d)
    return []

def markets(event):
    v=event.get("markets")
    if isinstance(v,Mapping):return [v]
    return [x for x in v or [] if isinstance(x,Mapping)] if isinstance(v,list) else []

def cursor(payload):
    if not isinstance(payload,Mapping):return None
    for k in ("cursor","nextCursor","next_cursor"):
        if payload.get(k):return str(payload[k])
    for k in ("pagination","data"):
        v=payload.get(k)
        if isinstance(v,Mapping):
            c=cursor(v)
            if c:return c
    return None

def get(url,params=None):
    r=requests.get(url,params=params,headers={"Accept":"application/json","Origin":"https://world.xyz",
        "Referer":"https://world.xyz/","User-Agent":"world-pm-catalog-prediscovery-r1/1"},timeout=15)
    r.raise_for_status(); return r.json()

def probe(start:int,max_pages:int=8):
    want=expected_description(start); end=start+300; cur=None; seen=set(); checked=0; candidates=[]
    for _ in range(max_pages):
        params={"withNestedMarkets":"true","status":"active","limit":"500"}
        if cur:params["cursor"]=cur
        payload=get(CATALOG,params)
        for ev in events(payload):
            for m in markets(ev):
                checked+=1
                ticker=str(m.get("ticker") or m.get("marketTicker") or "")
                if "BTC5M" not in ticker.upper():continue
                close=ts(m.get("closeTime") or m.get("close_time"))
                if close is not None and abs(close-end)>1.0:continue
                open_=ts(m.get("openTime") or m.get("open_time"))
                if open_ is not None and abs(open_-start)>1.0:continue
                amap=m.get("accounts")
                if not isinstance(amap,Mapping):continue
                a=amap.get(CASH)
                if not isinstance(a,Mapping) and len(amap)==1:
                    a=next(iter(amap.values()))
                if not isinstance(a,Mapping) or a.get("isInitialized") is False:continue
                market=str(a.get("marketLedger") or ""); yes=str(a.get("yesMint") or ""); no=str(a.get("noMint") or "")
                if not market or not yes or not no or yes==no:continue
                meta=get(f"{META}/{yes}")
                desc=str(meta.get("description") or "") if isinstance(meta,Mapping) else ""
                if desc!=want:continue
                candidates.append({"ticker":ticker,"marketLedger":market,"yesMint":yes,"noMint":no,
                    "description":desc,"openTime":open_,"closeTime":close})
        nxt=cursor(payload)
        if not nxt or nxt in seen:break
        seen.add(nxt);cur=nxt
    if len(candidates)!=1:
        return {"status":"UNKNOWN","reason":"CATALOG_IDENTITY_NOT_UNIQUE","startTs":start,
            "expectedDescription":want,"candidates":candidates,"marketsChecked":checked}
    return {"status":"FOUND","startTs":start,"observedAt":time.time(),"secondsBeforeStart":start-time.time(),
        "marketsChecked":checked,**candidates[0]}

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--start-ts",type=int,required=True);a=ap.parse_args()
    out=probe(a.start_ts);print(json.dumps(out,sort_keys=True));return 0 if out["status"]=="FOUND" else 2
if __name__=="__main__":raise SystemExit(main())

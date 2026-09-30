"""Zero-score liveness check for World's public market catalog.

Public read-only. No credentials, signing, orders, or capital.
"""
from __future__ import annotations
import json, time
from pathlib import Path
from typing import Any, Mapping
import requests

URL="https://markets-api-proxy.world-xyz.workers.dev/api/v1/markets"

def walk(obj: Any):
    if isinstance(obj, Mapping):
        yield obj
        for v in obj.values():
            yield from walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk(v)

def main():
    now=int(time.time())
    cur=(now//300)*300
    targets={
        cur-300: time.strftime("BTC/USD closes at or above its open between %Y-%m-%d %H:%M:%S UTC", time.gmtime(cur-300)),
        cur: time.strftime("BTC/USD closes at or above its open between %Y-%m-%d %H:%M:%S UTC", time.gmtime(cur)),
        cur+300: time.strftime("BTC/USD closes at or above its open between %Y-%m-%d %H:%M:%S UTC", time.gmtime(cur+300)),
    }
    headers={"Origin":"https://world.xyz","Accept":"application/json","User-Agent":"world-pm-catalog-liveness-r0/1.0"}
    out={"schemaVersion":"WORLD_MARKET_CATALOG_LIVENESS_R0","researchScore":0,"startedAt":time.time(),"targets":targets,"pages":[],"matches":{}}
    cursor=None
    for page in range(8):
        params={} if not cursor else {"cursor":cursor}
        r=requests.get(URL,params=params,headers=headers,timeout=15)
        row={"page":page,"status":r.status_code,"url":r.url,"contentType":r.headers.get("content-type")}
        text=r.text
        row["bodyPrefix"]=text[:1000]
        out["pages"].append(row)
        if r.status_code!=200:
            break
        data=r.json()
        for node in walk(data):
            desc=str(node.get("description") or node.get("question") or node.get("title") or "")
            if "BTC/USD closes at or above its open between" not in desc:
                continue
            for start in targets:
                exact=f"BTC/USD closes at or above its open between {time.strftime('%Y-%m-%d %H:%M:%S UTC',time.gmtime(start))} and {time.strftime('%Y-%m-%d %H:%M:%S UTC',time.gmtime(start+300))}."
                if desc==exact:
                    out["matches"][str(start)]=node
        cursor=None
        if isinstance(data,Mapping):
            cursor=data.get("nextCursor") or data.get("next_cursor") or data.get("cursor")
            if cursor and params.get("cursor")==cursor:
                cursor=None
        if len(out["matches"])>=2 or not cursor:
            break
    out["finishedAt"]=time.time()
    Path("artifacts").mkdir(exist_ok=True)
    Path("artifacts/world-market-catalog-liveness-r0.json").write_text(json.dumps(out,ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf-8")
    print(json.dumps({"matched":sorted(out["matches"]),"pageStatuses":[x["status"] for x in out["pages"]]},sort_keys=True))

if __name__=="__main__":
    main()

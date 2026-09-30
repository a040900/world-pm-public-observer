"""Read-only live probe of World's public aggregator proxy for current BTC 5m outcome tokens.

No wallet, signing, simulation, broadcast, order submission, or capital.
The /order endpoint is treated as a quote/build surface only.
"""
from __future__ import annotations
import argparse, json, time, urllib.parse, urllib.request
from pathlib import Path
from typing import Any, Mapping

from tools.research import world_pm_jupiter_economic_poc_r0 as jp

PROXY="https://aggregator-api-proxy.world-xyz.workers.dev/order"
CASH=jp.CASH_MINT

def get_json(url:str,timeout:float=12.0)->tuple[int,Any,dict[str,str]]:
    req=urllib.request.Request(url,headers={"Accept":"application/json","Origin":"https://world.xyz","Referer":"https://world.xyz/","User-Agent":"world-pm-public-observer/1.0"})
    try:
        with urllib.request.urlopen(req,timeout=timeout) as r:
            raw=r.read().decode("utf-8","replace")
            try: body=json.loads(raw)
            except Exception: body={"_raw":raw[:4000]}
            return r.status,body,dict(r.headers.items())
    except urllib.error.HTTPError as e:
        raw=e.read().decode("utf-8","replace")
        try: body=json.loads(raw)
        except Exception: body={"_raw":raw[:4000]}
        return e.code,body,dict(e.headers.items())

def summarize(body:Any)->dict[str,Any]:
    if not isinstance(body,Mapping): return {"bodyType":type(body).__name__}
    rp=body.get("routePlan")
    labels=[]
    if isinstance(rp,list):
        for row in rp:
            if not isinstance(row,Mapping): continue
            info=row.get("swapInfo") if isinstance(row.get("swapInfo"),Mapping) else row
            labels.append(info.get("label") or info.get("ammLabel") or info.get("venue") or info.get("provider"))
    tx_fields=[k for k in ("transaction","swapTransaction","serializedTransaction","tx") if body.get(k)]
    return {
        "keys":sorted(str(k) for k in body.keys()),
        "outAmount":body.get("outAmount"),
        "otherAmountThreshold":body.get("otherAmountThreshold") or body.get("minOutAmount"),
        "priceImpactPct":body.get("priceImpactPct"),
        "routeLabels":labels,
        "routePlanCount":len(rp) if isinstance(rp,list) else None,
        "transactionFieldsPresent":tx_fields,
        "errorCode":body.get("code"),
        "errorMessage":body.get("msg") or body.get("message") or body.get("error"),
    }

def main(args):
    deadline=time.time()+45
    start=(int(time.time())//300)*300
    markets=jp.describe_candidates(jp.program_markets_for_start(start,deadline),deadline)
    btc=jp.select_btc(markets)
    result={"schemaVersion":"WORLD_PUBLIC_AGGREGATOR_PROXY_BTC5M_PROBE_R0","mode":"PUBLIC_READ_ONLY_QUOTE_BUILD_ONLY","startedAt":time.time(),"startTs":start,"worldMarket":btc,"requests":[]}
    for side,mint in [("YES",btc["yesMint"]),("NO",btc["noMint"])]:
        params={"inputMint":CASH,"outputMint":mint,"amount":str(args.amount_atoms),"slippageBps":str(args.slippage_bps)}
        url=PROXY+"?"+urllib.parse.urlencode(params)
        t=time.time()
        status,body,headers=get_json(url)
        result["requests"].append({"side":side,"mint":mint,"httpStatus":status,"elapsedMs":(time.time()-t)*1000,"request":params,"summary":summarize(body),"body":body})
    result["summary"]={
      "successCount":sum(1 for x in result["requests"] if x["httpStatus"]==200 and x["summary"].get("outAmount") is not None),
      "transactionBuildCount":sum(1 for x in result["requests"] if x["summary"].get("transactionFieldsPresent")),
      "routeLabelsBySide":{x["side"]:x["summary"].get("routeLabels") for x in result["requests"]},
      "httpStatuses":{x["side"]:x["httpStatus"] for x in result["requests"]},
    }
    result["finishedAt"]=time.time()
    Path(args.output).parent.mkdir(parents=True,exist_ok=True)
    Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2,sort_keys=True)+"\n")
    print(json.dumps(result["summary"],sort_keys=True))

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--amount-atoms",type=int,default=1000000)
    p.add_argument("--slippage-bps",type=int,default=200)
    p.add_argument("--output",default="artifacts/world-public-aggregator-proxy-btc5m-probe-r0.json")
    main(p.parse_args())

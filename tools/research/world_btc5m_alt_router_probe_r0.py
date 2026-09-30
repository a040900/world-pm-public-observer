"""Read-only World BTC5m alternative-router probe.

Discovers the current World BTC5m market from public Solana state, then asks
Jupiter Lite for CASH<->YES/NO quotes. No wallet, signing, simulation, or send.
"""
from __future__ import annotations
import asyncio, json, time
from pathlib import Path
from urllib.parse import urlencode
import requests

from tools.research import world_discovery_liveness_r0 as wd
from tools.research import world_polymarket_btc5m_leadlag_probe_r1 as wp

JUP="https://lite-api.jup.ag/swap/v1/quote"

def quote(a,b,amount):
    params={"inputMint":a,"outputMint":b,"amount":str(amount),"slippageBps":"100"}
    r=requests.get(JUP,params=params,timeout=12,headers={"Accept":"application/json","User-Agent":"world-pm-alt-router-probe/1.0"})
    try: body=r.json()
    except Exception: body={"raw":r.text[:4000]}
    return {"status":r.status_code,"url":r.url,"body":body}

async def main():
    now=int(time.time()); cur=(now//300)*300
    d=await asyncio.to_thread(wd.broad_discovery,now,signature_limit=500,scan_cap=220,deadline_seconds=180)
    m=d.get("matches",{}).get(str(cur))
    out={"schemaVersion":"WORLD_BTC5M_ALT_ROUTER_PROBE_R0","mode":"PUBLIC_READ_ONLY","researchScore":0,
         "startedAt":time.time(),"currentSlotStart":cur,"discovery":d,"market":m,"jupiter":{}}
    if m:
        cash=wp.CASH_MINT; yes=str(m["yesMint"]); no=str(m["noMint"])
        amount=10_000_000
        for name,a,b in [("cashToYes",cash,yes),("cashToNo",cash,no),("yesToCash",yes,cash),("noToCash",no,cash)]:
            qamt=amount if name.startswith("cash") else 10_000_000
            out["jupiter"][name]=await asyncio.to_thread(quote,a,b,qamt)
    out["finishedAt"]=time.time()
    Path("artifacts").mkdir(exist_ok=True)
    Path("artifacts/world-btc5m-alt-router-probe-r0.json").write_text(json.dumps(out,indent=2,sort_keys=True)+"\n")
    print(json.dumps({"market":m,"jupiter":{k:{"status":v["status"],"outAmount":(v["body"] or {}).get("outAmount"),"routePlan":(v["body"] or {}).get("routePlan"),"error":(v["body"] or {}).get("error")} for k,v in out["jupiter"].items()}},sort_keys=True))

if __name__=="__main__": asyncio.run(main())

#!/usr/bin/env python3
import argparse, hashlib, json, time
from pathlib import Path
from typing import Any, Mapping
import requests

CATALOG="https://markets-api-proxy.world-xyz.workers.dev/api/v1/markets"
GAMMA="https://gamma-api.polymarket.com/events"
RPC="https://solana-rpc.publicnode.com"
PREDICT="prediCtPZCttYMvm2W3PtxmMxLmT1dtN7riU6Cxh6tM"
CASH="CASHx9KJUStyftLFWGvEVf59SGeG9sh5FfcnZMVPCASH"
REDEEM=bytes.fromhex("0011a762e91c6b34")
BURN=bytes.fromhex("b080ce016e205a2d")
B58="123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"

def get_json(url,params=None):
    r=requests.get(url,params=params,headers={"Accept":"application/json","Origin":"https://world.xyz","User-Agent":"world-pm-public-observer/1"},timeout=20)
    r.raise_for_status(); return r.json()

def rpc(method,params):
    r=requests.post(RPC,json={"jsonrpc":"2.0","id":1,"method":method,"params":params},timeout=25)
    r.raise_for_status(); p=r.json()
    if p.get("error"): raise RuntimeError(p["error"])
    return p.get("result")

def b58(s):
    n=0
    for c in s:n=n*58+B58.index(c)
    body=b"" if n==0 else n.to_bytes((n.bit_length()+7)//8,"big")
    return b"\0"*(len(s)-len(s.lstrip("1")))+body

def expected_description(start):
    end=start+300
    a=time.strftime("%Y-%m-%d %H:%M:%S UTC",time.gmtime(start))
    b=time.strftime("%Y-%m-%d %H:%M:%S UTC",time.gmtime(end))
    return f"BTC/USD closes at or above its open between {a} and {b}."

def rows_and_cursor(payload):
    if isinstance(payload,list): return payload,None
    if not isinstance(payload,Mapping): return [],None
    for k in ("markets","data","items","results"):
        if isinstance(payload.get(k),list):
            cur=payload.get("nextCursor") or payload.get("next_cursor") or payload.get("cursor")
            return payload[k],cur
    return [],None

def discover_world(start):
    want=expected_description(start); cursor=None
    for _ in range(100):
        params={} if not cursor else {"cursor":cursor}
        p=get_json(CATALOG,params); rows,nxt=rows_and_cursor(p)
        for m in rows:
            if not isinstance(m,Mapping): continue
            text=str(m.get("description") or m.get("rules") or m.get("question") or "")
            if text!=want: continue
            accounts=m.get("accounts")
            candidates=[]
            if isinstance(accounts,Mapping):
                candidates=list(accounts.values()) if all(isinstance(v,Mapping) for v in accounts.values()) else [accounts]
            for a in candidates:
                if not isinstance(a,Mapping): continue
                market=a.get("marketLedger") or a.get("market")
                yes=a.get("yesMint"); no=a.get("noMint")
                if market and yes and no and a.get("isInitialized",True):
                    return {"market":market,"yesMint":yes,"noMint":no,"description":text,"catalogId":m.get("id")}
        if not nxt or nxt==cursor: break
        cursor=nxt
    return None

def all_instructions(tx):
    out=[]
    msg=((tx.get("transaction") or {}).get("message") or {})
    out.extend(msg.get("instructions") or [])
    for group in ((tx.get("meta") or {}).get("innerInstructions") or []): out.extend(group.get("instructions") or [])
    return out

def world_settlement(m,end):
    sigs={}
    for mint in (m["yesMint"],m["noMint"]):
        for row in rpc("getSignaturesForAddress",[mint,{"limit":20,"commitment":"confirmed"}]) or []:
            if row.get("err") is None and int(row.get("blockTime") or 0)>=end: sigs[row["signature"]]=row
    for sig,row in sorted(sigs.items(),key=lambda x:int(x[1].get("blockTime") or 0)):
        tx=rpc("getTransaction",[sig,{"encoding":"jsonParsed","maxSupportedTransactionVersion":0,"commitment":"confirmed"}])
        if not isinstance(tx,Mapping) or (tx.get("meta") or {}).get("err") is not None: continue
        for ins in all_instructions(tx):
            if ins.get("programId")!=PREDICT: continue
            acc=[str(x) for x in ins.get("accounts") or []]
            if m["market"] not in acc or not isinstance(ins.get("data"),str): continue
            y=m["yesMint"] in acc; n=m["noMint"] in acc
            if y==n: continue
            d=b58(ins["data"])[:8]
            touched="Up" if y else "Down"
            if d==REDEEM: return {"status":"RESOLVED","outcome":touched,"signature":sig,"action":"REDEEM"}
            if d==BURN: return {"status":"RESOLVED","outcome":"Down" if y else "Up","signature":sig,"action":"BURN_WORTHLESS"}
    return {"status":"UNKNOWN","reason":"WORLD_SETTLEMENT_ACTION_NOT_FOUND"}

def pm_settlement(start):
    rows=get_json(GAMMA,{"slug":f"btc-updown-5m-{start}"})
    if not isinstance(rows,list) or len(rows)!=1:return {"status":"UNKNOWN","reason":"PM_EVENT_NOT_UNIQUE"}
    markets=rows[0].get("markets") or []
    if len(markets)!=1:return {"status":"UNKNOWN","reason":"PM_MARKET_NOT_UNIQUE"}
    m=markets[0]; labels=m.get("outcomes"); prices=m.get("outcomePrices")
    if isinstance(labels,str): labels=json.loads(labels)
    if isinstance(prices,str): prices=json.loads(prices)
    if not isinstance(labels,list) or not isinstance(prices,list) or len(labels)!=len(prices):return {"status":"UNKNOWN","reason":"PM_OUTCOME_INVALID"}
    winners=[str(labels[i]) for i,p in enumerate(prices) if abs(float(p)-1.0)<1e-9]
    if len(winners)!=1:return {"status":"UNKNOWN","reason":"PM_NOT_RESOLVED","outcomePrices":prices}
    return {"status":"RESOLVED","outcome":winners[0],"outcomePrices":prices}

def observe(start):
    m=discover_world(start)
    row={"schemaVersion":"WORLD_PM_PUBLIC_SETTLEMENT_OBSERVATION_R1","startTs":start,"endTs":start+300,"expectedWorldDescription":expected_description(start),"observedAt":time.time(),"noTrade":True}
    if not m: row["status"]="UNKNOWN"; row["reason"]="WORLD_MARKET_NOT_DISCOVERED"; return row
    row["worldMarket"]=m
    try: row["world"]=world_settlement(m,start+300)
    except Exception as e: row["world"]={"status":"UNKNOWN","reason":f"WORLD_READ_FAILED:{type(e).__name__}"}
    try: row["polymarket"]=pm_settlement(start)
    except Exception as e: row["polymarket"]={"status":"UNKNOWN","reason":f"PM_READ_FAILED:{type(e).__name__}"}
    if row["world"].get("status")=="RESOLVED" and row["polymarket"].get("status")=="RESOLVED":
        row["status"]="RESOLVED"; row["sameDirection"]=row["world"]["outcome"]==row["polymarket"]["outcome"]
    else: row["status"]="PENDING_OR_UNKNOWN"
    return row

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--start-ts",type=int); ap.add_argument("--output",default="artifacts/observation.json"); args=ap.parse_args()
    start=args.start_ts
    if start is None: start=(int(time.time())//300-1)*300
    row=observe(start); p=Path(args.output); p.parent.mkdir(parents=True,exist_ok=True)
    data=(json.dumps(row,indent=2,sort_keys=True)+"\n").encode(); p.write_bytes(data)
    (p.parent/(p.name+".sha256")).write_text(hashlib.sha256(data).hexdigest()+"  "+p.name+"\n")
    print(json.dumps({"status":row.get("status"),"startTs":start,"sameDirection":row.get("sameDirection"),"output":str(p)}))

if __name__=="__main__": main()

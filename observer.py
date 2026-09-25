#!/usr/bin/env python3
import argparse, hashlib, json, time
from pathlib import Path
from typing import Any, Mapping
import requests

GAMMA="https://gamma-api.polymarket.com/events"
RPC="https://solana-rpc.publicnode.com"
PREDICT="prediCtPZCttYMvm2W3PtxmMxLmT1dtN7riU6Cxh6tM"
INIT=bytes.fromhex("2323bdc19b30aacb")
REDEEM=bytes.fromhex("0011a762e91c6b34")
BURN=bytes.fromhex("b080ce016e205a2d")
B58="123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"

def get_json(url,params=None):
    r=requests.get(url,params=params,headers={"Accept":"application/json","User-Agent":"world-pm-public-observer/2"},timeout=20)
    r.raise_for_status()
    return r.json()

def rpc(method,params):
    last=None
    for attempt in range(2):
        try:
            r=requests.post(RPC,json={"jsonrpc":"2.0","id":1,"method":method,"params":params},
                            headers={"Accept":"application/json","Content-Type":"application/json",
                                     "User-Agent":"world-pm-public-observer/3"},timeout=20)
            r.raise_for_status()
            p=r.json()
            if not isinstance(p,Mapping) or p.get("error") is not None:
                raise RuntimeError(f"SOLANA_RPC_ERROR:{p.get('error') if isinstance(p,Mapping) else 'INVALID'}")
            return p.get("result")
        except Exception as e:
            last=e
            if attempt==0:
                time.sleep(0.5)
    raise last

def b58(s):
    n=0
    for c in s:
        i=B58.find(c)
        if i<0: raise ValueError("INVALID_BASE58")
        n=n*58+i
    body=b"" if n==0 else n.to_bytes((n.bit_length()+7)//8,"big")
    return b"\0"*(len(s)-len(s.lstrip("1")))+body

def expected_description(start):
    end=start+300
    a=time.strftime("%Y-%m-%d %H:%M:%S UTC",time.gmtime(start))
    b=time.strftime("%Y-%m-%d %H:%M:%S UTC",time.gmtime(end))
    return f"BTC/USD closes at or above its open between {a} and {b}."

def all_instructions(tx):
    out=[]
    msg=((tx.get("transaction") or {}).get("message") or {})
    out.extend(msg.get("instructions") or [])
    for group in ((tx.get("meta") or {}).get("innerInstructions") or []):
        out.extend(group.get("instructions") or [])
    return [x for x in out if isinstance(x,Mapping)]

def discover_world(start):
    want=expected_description(start)
    target_start_ms=start*1000
    target_end_ms=(start+300)*1000
    rows=rpc("getSignaturesForAddress",[PREDICT,{"limit":250,"commitment":"confirmed"}]) or []
    if not isinstance(rows,list):
        return {"status":"UNKNOWN","reason":"WORLD_SIGNATURE_LIST_INVALID"}
    checked=0
    init_seen=[]
    lower=start-900
    upper=start+600
    for row in rows:
        if not isinstance(row,Mapping) or row.get("err") is not None: continue
        bt=int(row.get("blockTime") or 0)
        if bt and bt<lower: break
        if bt and bt>upper: continue
        sig=str(row.get("signature") or "")
        if not sig: continue
        try:
            tx=rpc("getTransaction",[sig,{"encoding":"jsonParsed","maxSupportedTransactionVersion":0,"commitment":"confirmed"}])
        except Exception:
            continue
        if not isinstance(tx,Mapping) or (tx.get("meta") or {}).get("err") is not None: continue
        checked+=1
        logs=(tx.get("meta") or {}).get("logMessages") or []
        if not any("InitializeMarket" in str(x) or "Instruction: InitializeMarket" in str(x) for x in logs):
            continue
        for ins in all_instructions(tx):
            if ins.get("programId")!=PREDICT or not isinstance(ins.get("data"),str): continue
            try: raw=b58(ins["data"])
            except ValueError: continue
            if len(raw)<65 or raw[:8]!=INIT: continue
            start_ms=int.from_bytes(raw[49:57],"little")
            end_ms=int.from_bytes(raw[57:65],"little")
            acc=[str(x) for x in ins.get("accounts") or []]
            init_seen.append({
                "startMs":start_ms,"endMs":end_ms,
                "durationSeconds":(end_ms-start_ms)/1000.0,
                "blockTime":bt,"signature":sig,
            })
            if start_ms!=target_start_ms or end_ms!=target_end_ms or len(acc)<5: continue
            market=acc[1]; yes=acc[3]; no=acc[4]
            try: meta=get_json(f"https://m.world.xyz/{yes}")
            except Exception as e:
                return {"status":"UNKNOWN","reason":"WORLD_METADATA_READ_FAILED",
                        "market":market,"yesMint":yes,"noMint":no,
                        "error":f"{type(e).__name__}:{e}",
                        "discovery":"INITIALIZE_MARKET_EXACT_WINDOW"}
            desc=str(meta.get("description") or "") if isinstance(meta,Mapping) else ""
            if desc!=want:
                return {"status":"UNKNOWN","reason":"WORLD_DESCRIPTION_MISMATCH",
                        "market":market,"yesMint":yes,"noMint":no,
                        "description":desc,"expectedDescription":want,
                        "discovery":"INITIALIZE_MARKET_EXACT_WINDOW"}
            return {
                "status":"FOUND","market":market,"yesMint":yes,"noMint":no,
                "description":desc,"discovery":"INITIALIZE_MARKET_EXACT_WINDOW",
                "initializeSignature":sig,"transactionsChecked":checked,
            }
    recent=sorted(init_seen,key=lambda x:x["startMs"],reverse=True)[:20]
    return {"status":"NOT_FOUND","reason":"WORLD_EXACT_5M_INITIALIZE_NOT_FOUND",
            "transactionsChecked":checked,"recentInitializeMarkets":recent}

def validate_known_world_market(start, yes_mint):
    want=expected_description(start)
    rows=rpc("getSignaturesForAddress",[yes_mint,{"limit":1000,"commitment":"confirmed"}]) or []
    checked=0
    for row in rows:
        if not isinstance(row,Mapping) or row.get("err") is not None or not row.get("signature"): continue
        sig=str(row["signature"])
        tx=rpc("getTransaction",[sig,{"encoding":"jsonParsed","maxSupportedTransactionVersion":0,"commitment":"confirmed"}])
        if not isinstance(tx,Mapping) or (tx.get("meta") or {}).get("err") is not None: continue
        checked+=1
        logs=(tx.get("meta") or {}).get("logMessages") or []
        if not any("Instruction: Split" in str(x) for x in logs): continue
        for ins in all_instructions(tx):
            acc=[str(x) for x in ins.get("accounts") or []]
            if ins.get("programId")!=PREDICT or len(acc)<11 or yes_mint not in acc: continue
            try: meta=get_json(f"https://m.world.xyz/{yes_mint}")
            except Exception as e:
                return {"status":"UNKNOWN","reason":"WORLD_METADATA_READ_FAILED","error":f"{type(e).__name__}:{e}"}
            desc=str(meta.get("description") or "") if isinstance(meta,Mapping) else ""
            return {"status":"PASS" if desc==want else "FAIL","market":acc[1],"yesMint":acc[3],
                    "noMint":acc[4],"description":desc,"expectedDescription":want,
                    "splitSignature":sig,"transactionsChecked":checked}
    return {"status":"FAIL","reason":"KNOWN_YES_MINT_SPLIT_NOT_FOUND","transactionsChecked":checked}

def world_settlement(m,end):
    sigs={}
    for mint in (m["yesMint"],m["noMint"]):
        rows=rpc("getSignaturesForAddress",[mint,{"limit":20,"commitment":"confirmed"}]) or []
        if not isinstance(rows,list): continue
        for row in rows:
            if isinstance(row,Mapping) and row.get("err") is None and int(row.get("blockTime") or 0)>=end and row.get("signature"):
                sigs[str(row["signature"])]=row
    checked=0
    for sig,row in sorted(sigs.items(),key=lambda x:int(x[1].get("blockTime") or 0)):
        tx=rpc("getTransaction",[sig,{"encoding":"jsonParsed","maxSupportedTransactionVersion":0,"commitment":"confirmed"}])
        if not isinstance(tx,Mapping) or (tx.get("meta") or {}).get("err") is not None: continue
        checked+=1
        for ins in all_instructions(tx):
            if ins.get("programId")!=PREDICT: continue
            acc=[str(x) for x in ins.get("accounts") or []]
            if m["market"] not in acc or not isinstance(ins.get("data"),str): continue
            y=m["yesMint"] in acc
            n=m["noMint"] in acc
            if y==n: continue
            try: d=b58(ins["data"])[:8]
            except ValueError: continue
            touched="Up" if y else "Down"
            if d==REDEEM:
                return {"status":"RESOLVED","outcome":touched,"signature":sig,
                        "blockTime":int(row.get("blockTime") or 0),"action":"REDEEM",
                        "transactionsChecked":checked}
            if d==BURN:
                return {"status":"RESOLVED","outcome":"Down" if y else "Up","signature":sig,
                        "blockTime":int(row.get("blockTime") or 0),"action":"BURN_WORTHLESS",
                        "transactionsChecked":checked}
    return {"status":"UNKNOWN","reason":"WORLD_SETTLEMENT_ACTION_NOT_FOUND","transactionsChecked":checked}

def pm_settlement(start):
    rows=get_json(GAMMA,{"slug":f"btc-updown-5m-{start}"})
    if not isinstance(rows,list) or len(rows)!=1 or not isinstance(rows[0],Mapping):
        return {"status":"UNKNOWN","reason":"PM_EVENT_NOT_UNIQUE"}
    markets=rows[0].get("markets") or []
    if not isinstance(markets,list) or len(markets)!=1 or not isinstance(markets[0],Mapping):
        return {"status":"UNKNOWN","reason":"PM_MARKET_NOT_UNIQUE"}
    m=markets[0]
    labels=m.get("outcomes")
    prices=m.get("outcomePrices")
    if isinstance(labels,str):
        try: labels=json.loads(labels)
        except Exception: labels=None
    if isinstance(prices,str):
        try: prices=json.loads(prices)
        except Exception: prices=None
    if not isinstance(labels,list) or not isinstance(prices,list) or len(labels)!=len(prices):
        return {"status":"UNKNOWN","reason":"PM_OUTCOME_INVALID"}
    winners=[]
    for i,p in enumerate(prices):
        try:
            if abs(float(p)-1.0)<1e-9: winners.append(str(labels[i]))
        except Exception:
            pass
    if len(winners)!=1:
        return {"status":"UNKNOWN","reason":"PM_NOT_RESOLVED","outcomePrices":prices}
    return {"status":"RESOLVED","outcome":winners[0],"outcomePrices":prices}

def observe(start):
    row={
        "schemaVersion":"WORLD_PM_PUBLIC_SETTLEMENT_OBSERVATION_R2",
        "startTs":start,"endTs":start+300,
        "expectedWorldDescription":expected_description(start),
        "observedAt":time.time(),"sampleClass":"PROSPECTIVE_LIVE_IDENTITY_FREEZE","noTrade":True,
    }
    try:
        market=discover_world(start)
    except Exception as e:
        row["status"]="UNKNOWN"
        row["reason"]="WORLD_DISCOVERY_READ_FAILED"
        row["worldDiscoveryError"]=f"{type(e).__name__}:{e}"
        return row
    row["worldMarketDiscovery"]=market
    if market.get("status")!="FOUND":
        row["status"]="UNKNOWN"
        row["reason"]=market.get("reason") or "WORLD_MARKET_NOT_DISCOVERED"
        return row
    try:
        row["world"]=world_settlement(market,start+300)
    except Exception as e:
        row["world"]={"status":"UNKNOWN","reason":"WORLD_READ_FAILED","error":f"{type(e).__name__}:{e}"}
    try:
        row["polymarket"]=pm_settlement(start)
    except Exception as e:
        row["polymarket"]={"status":"UNKNOWN","reason":"PM_READ_FAILED","error":f"{type(e).__name__}:{e}"}
    if row["world"].get("status")=="RESOLVED" and row["polymarket"].get("status")=="RESOLVED":
        row["status"]="RESOLVED"
        row["sameDirection"]=row["world"]["outcome"]==row["polymarket"]["outcome"]
    else:
        row["status"]="PENDING_OR_UNKNOWN"
    return row

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--start-ts",type=int)
    ap.add_argument("--output",default="artifacts/observation.json")
    ap.add_argument("--known-yes-mint")
    args=ap.parse_args()
    start=args.start_ts
    if start is None:
        start=(int(time.time())//300)*300
    p=Path(args.output)
    p.parent.mkdir(parents=True,exist_ok=True)
    try:
        if args.known_yes_mint:
            row={"schemaVersion":"WORLD_PM_KNOWN_MARKET_REGRESSION_R1","startTs":start,
                 "observedAt":time.time(),"noTrade":True,
                 "regression":validate_known_world_market(start,args.known_yes_mint)}
            row["status"]=row["regression"].get("status")
        else:
            row=observe(start)
    except Exception as e:
        row={"schemaVersion":"WORLD_PM_PUBLIC_SETTLEMENT_OBSERVATION_R2","startTs":start,
             "endTs":start+300,"observedAt":time.time(),"status":"UNKNOWN",
             "reason":"OBSERVER_UNHANDLED_READ_FAILURE","error":f"{type(e).__name__}:{e}","noTrade":True}
    data=(json.dumps(row,indent=2,sort_keys=True)+"\n").encode()
    p.write_bytes(data)
    digest=hashlib.sha256(data).hexdigest()
    (p.parent/(p.name+".sha256")).write_text(digest+"  "+p.name+"\n",encoding="utf-8")
    print(json.dumps({"status":row.get("status"),"reason":row.get("reason"),
                      "startTs":start,"sameDirection":row.get("sameDirection"),"output":str(p)}))

if __name__=="__main__":
    main()

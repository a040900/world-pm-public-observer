"""R3 public derivability probe for the remaining DFlow/Bison route accounts.

Read-only. No quote/order endpoints, no simulation, signing, or transaction submission.
"""
from __future__ import annotations
import argparse, base64, json, time
from pathlib import Path
from typing import Any, Mapping

from tools.research import world_btc5m_dflow_bison_route_construction_analysis_r1 as r1
from tools.research import world_btc5m_dflow_bison_route_construction_analysis_r0 as r0
from tools.research import world_btc5m_dflow_bison_account_role_refinement_r2 as r2

CASH=r0.CASH; BISON=r0.BISON; TOKEN2022=r0.TOKEN2022
PREDTRS=b"PREDTRS\x00"

def rpc(method,params,deadline): return r0.rpc(method,params,deadline)

def token_accounts(owner:str,deadline:float):
    v=rpc("getTokenAccountsByOwner",[owner,{"mint":CASH},{"encoding":"jsonParsed","commitment":"confirmed"}],deadline)
    rows=v.get("value") if isinstance(v,Mapping) else []
    out=[]
    for x in rows or []:
        if not isinstance(x,Mapping):continue
        acc=x.get("account"); data=acc.get("data") if isinstance(acc,Mapping) else None
        info=((data.get("parsed") or {}).get("info") or {}) if isinstance(data,Mapping) else {}
        out.append({"pubkey":str(x.get("pubkey")),"mint":info.get("mint"),"owner":info.get("owner"),
                    "amount":((info.get("tokenAmount") or {}).get("amount"))})
    return out

def predtrs_accounts(deadline:float):
    rows=rpc("getProgramAccounts",[BISON,{"encoding":"base64","filters":[{"dataSize":128}],"commitment":"confirmed"}],deadline) or []
    out=[]
    for x in rows:
        if not isinstance(x,Mapping):continue
        acc=x.get("account"); data=acc.get("data") if isinstance(acc,Mapping) else None
        if not isinstance(data,list) or not data:continue
        try: raw=base64.b64decode(str(data[0]))
        except Exception:continue
        if len(raw)!=128 or raw[:8]!=PREDTRS:continue
        out.append({"pubkey":str(x.get("pubkey")),"magic":"PREDTRS","rawHex":raw.hex(),
                    "u64At8":int.from_bytes(raw[8:16],"little"),
                    "pubkeyAt16":r0.pub(raw[16:48]),"pubkeyAt48":r0.pub(raw[48:80]),
                    "tailHex":raw[80:].hex()})
    return out

def historical_owner_map(t:Mapping[str,Any]):
    out={}
    for field in ["preTokenBalances","postTokenBalances"]:
        for row in (t.get("meta") or {}).get(field) or []:
            if not isinstance(row,Mapping):continue
            try: idx=int(row["accountIndex"])
            except Exception:continue
            out.setdefault(idx,set()).add((str(row.get("mint") or ""),str(row.get("owner") or "")))
    return {str(k):[{"mint":m,"owner":o} for m,o in sorted(v)] for k,v in out.items()}

def inspect(sig:str,deadline:float):
    t=r0.tx(sig,deadline); analysis=r1.analyze(sig,deadline); roster=analysis["roster"]
    hist=historical_owner_map(t)
    selected={}
    for pos in [20,21,24,25,26,27,28,29]:
        if pos>=len(roster):continue
        row=roster[pos]; selected[str(pos)]={"pubkey":row["pubkey"],"roles":row.get("roles"),"historicalTokenOwners":hist.get(str(pos),[])}
    # NOTE accountIndex in token balances is message account index, not DFlow ix position.
    # Map by pubkey to avoid conflating the two index spaces.
    keys=r1.r0.account_keys(t) if hasattr(r1,"r0") else []
    bypk={}
    for idx,kr in enumerate(keys):
        vals=hist.get(str(idx),[])
        if vals:bypk[str(kr["pubkey"])]=vals
    for row in selected.values(): row["historicalTokenOwners"]=bypk.get(str(row["pubkey"]),[])
    # relationships among Bison CPI positions
    p24=selected.get("24",{}).get("pubkey")
    for p in ["25","26","27"]:
        rel=selected.get(p,{})
        rel["ownedByPosition24InHistoricalTokenBalances"]=any(x.get("owner")==p24 for x in rel.get("historicalTokenOwners") or [])
    return {"signature":sig,"selected":selected}

def main(args):
    deadline=time.time()+args.deadline_seconds
    trs=[inspect(s,deadline) for s in args.signatures]
    # reuse R2 to recover current token owners for fee/global accounts
    refs=[]
    owners=set()
    for sig in args.signatures:
        rr=r2.inspect(sig,deadline); refs.append(rr)
        for p in ["20","21","29"]:
            tok=(rr["selected"].get(p) or {}).get("token2022") or {}
            if tok.get("tokenOwner"):owners.add(str(tok["tokenOwner"]))
    lookups={o:token_accounts(o,deadline) for o in sorted(owners)}
    predtrs=predtrs_accounts(deadline)
    p28s=[str((r["selected"].get("28") or {}).get("pubkey")) for r in refs]
    p29s=[str((r["selected"].get("29") or {}).get("pubkey")) for r in refs]
    summary={
      "position24Owns252627AllTrades":all(all(bool((t["selected"].get(p) or {}).get("ownedByPosition24InHistoricalTokenBalances")) for p in ["25","26","27"]) for t in trs),
      "predtrsAccountCount":len(predtrs),
      "position28IsPredtrsAccountAllTrades":all(p in {x["pubkey"] for x in predtrs} for p in p28s),
      "position29ReferencedByPosition28State":all(any(x["pubkey"]==p28 and x["pubkeyAt48"]==p29 for x in predtrs) for p28,p29 in zip(p28s,p29s)),
      "cashTokenOwnerLookupCount":{o:len(v) for o,v in lookups.items()},
      "verdict":"PUBLIC_ACCOUNT_DERIVABILITY_REFINED"
    }
    result={"schemaVersion":"WORLD_BTC5M_DFLOW_BISON_PUBLIC_ACCOUNT_DERIVABILITY_R3","mode":"PUBLIC_READ_ONLY_NO_SIMULATION",
            "researchScore":0,"startedAt":time.time(),"transactions":trs,"referenceRoleData":refs,
            "cashTokenAccountsByOwner":lookups,"predtrsAccounts":predtrs,"summary":summary,"finishedAt":time.time()}
    Path(args.output).parent.mkdir(parents=True,exist_ok=True)
    Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2,sort_keys=True)+"\n")
    print(json.dumps(summary,sort_keys=True))

if __name__=="__main__":
    p=argparse.ArgumentParser(); p.add_argument("--signatures",nargs="+",default=r0.DEFAULT_SIGNATURES)
    p.add_argument("--deadline-seconds",type=float,default=180)
    p.add_argument("--output",default="artifacts/world-btc5m-dflow-bison-public-account-derivability-r3.json")
    main(p.parse_args())

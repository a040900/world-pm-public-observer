"""R2 account-role refinement for World BTC5m DFlow/Bison direct-CASH routes.

Read-only only. No quote/order endpoints, no simulation, no signing.
Refines positions 6/20/21/28/29 and discovers the public Bison pool by market.
"""
from __future__ import annotations
import argparse, base64, json, struct, time
from pathlib import Path
from typing import Any, Mapping

from tools.research import world_btc5m_dflow_bison_route_construction_analysis_r1 as r1
from tools.research import world_btc5m_dflow_bison_route_construction_analysis_r0 as r0

TOKEN2022=r0.TOKEN2022
BISON=r0.BISON
BISON_MAGIC=r0.BISON_MAGIC

def rpc(method,params,deadline):
    return r0.rpc(method,params,deadline)

def raw_account(key:str,deadline:float):
    v=rpc("getAccountInfo",[key,{"encoding":"base64","commitment":"confirmed"}],deadline)
    acc=v.get("value") if isinstance(v,Mapping) else None
    if not isinstance(acc,Mapping): return None
    data=acc.get("data"); raw=b""
    if isinstance(data,list) and data:
        try: raw=base64.b64decode(str(data[0]))
        except Exception: raw=b""
    return {"pubkey":key,"ownerProgram":str(acc.get("owner") or ""),"lamports":acc.get("lamports"),
            "executable":bool(acc.get("executable")),"dataLength":len(raw),"raw":raw}

def token2022(acc):
    if not isinstance(acc,Mapping) or acc.get("ownerProgram")!=TOKEN2022: return None
    raw=acc.get("raw") or b""
    if len(raw)<72:return None
    return {"mint":r0.pub(raw[0:32]),"tokenOwner":r0.pub(raw[32:64]),"amountAtoms":struct.unpack_from("<Q",raw,64)[0]}

def pubkey_chunks(acc):
    if not isinstance(acc,Mapping):return []
    raw=acc.get("raw") or b""
    out=[]
    for off in range(0,len(raw)-31,32):
        b=raw[off:off+32]
        if any(b):
            out.append({"offset":off,"pubkey":r0.pub(b),"hex":b.hex()})
    return out

def bison_pools_for_market(market:str,deadline:float):
    rows=rpc("getProgramAccounts",[BISON,{"encoding":"base64","filters":[{"dataSize":2048},{"memcmp":{"offset":48,"bytes":market}}],"commitment":"confirmed"}],deadline) or []
    out=[]
    for row in rows:
        if not isinstance(row,Mapping):continue
        acc=row.get("account"); data=acc.get("data") if isinstance(acc,Mapping) else None
        if not isinstance(data,list) or not data:continue
        try: raw=base64.b64decode(str(data[0]))
        except Exception:continue
        if len(raw)!=2048 or raw[:8]!=BISON_MAGIC:continue
        out.append({
          "pool":str(row.get("pubkey")),"market":r0.pub(raw[48:80]),"yesMint":r0.pub(raw[80:112]),"noMint":r0.pub(raw[112:144]),
          "poolYesToken":r0.pub(raw[144:176]),"poolNoToken":r0.pub(raw[176:208]),"poolCashToken":r0.pub(raw[208:240]),
          "counter240":struct.unpack_from("<Q",raw,240)[0],"yesInventory":struct.unpack_from("<Q",raw,248)[0],
          "noInventory":struct.unpack_from("<Q",raw,256)[0],"slot264":struct.unpack_from("<Q",raw,264)[0],"slot296":struct.unpack_from("<Q",raw,296)[0]
        })
    return out

def market_from_roster(roster):
    for x in roster:
        if "MARKET" in (x.get("roles") or []):return str(x["pubkey"])
        if any(str(r).startswith("PREDICT_SPLIT_MARKET") or str(r).startswith("PREDICT_MERGE_MARKET") for r in x.get("roles") or []):
            return str(x["pubkey"])
    # fixed current route template observed at position 14
    return str(roster[14]["pubkey"]) if len(roster)>14 else None

def inspect(sig:str,deadline:float):
    t=r1.analyze(sig,deadline)
    roster=t["roster"]; market=market_from_roster(roster)
    selected={}
    keys=[]
    for pos in [6,20,21,28,29]:
        if pos<len(roster):
            k=str(roster[pos]["pubkey"]); keys.append(k)
            acc=raw_account(k,deadline)
            selected[str(pos)]={"pubkey":k,"account":None if acc is None else {x:y for x,y in acc.items() if x!="raw"},
                                "token2022":token2022(acc),"pubkeyChunks32":pubkey_chunks(acc)}
    pools=bison_pools_for_market(market,deadline) if market else []
    pool_keys=set()
    for p in pools:
        pool_keys.update([p["pool"],p["market"],p["yesMint"],p["noMint"],p["poolYesToken"],p["poolNoToken"],p["poolCashToken"]])
    for pos,row in selected.items():
        row["matchesBisonPoolDerivedKey"]=row["pubkey"] in pool_keys
        tok=row.get("token2022") or {}
        row["tokenOwnerMatchesSelectedPosition6"]=bool(tok and tok.get("tokenOwner")==selected.get("6",{}).get("pubkey"))
        row["tokenOwnerMatchesPosition28"]=bool(tok and tok.get("tokenOwner")==selected.get("28",{}).get("pubkey"))
    return {"signature":sig,"market":market,"selected":selected,"bisonPools":pools,"swap":t["swap"],"recordIds":t["recordIds"]}

def compare(rows):
    a,b=rows
    out={}
    for pos in ["6","20","21","28","29"]:
        x=a["selected"].get(pos,{}); y=b["selected"].get(pos,{})
        out[pos]={"samePubkey":x.get("pubkey")==y.get("pubkey"),
                  "tokenOwnerA":(x.get("token2022") or {}).get("tokenOwner"),
                  "tokenOwnerB":(y.get("token2022") or {}).get("tokenOwner"),
                  "tokenOwnersSame":(x.get("token2022") or {}).get("tokenOwner")==(y.get("token2022") or {}).get("tokenOwner")}
    return out

def main(args):
    deadline=time.time()+args.deadline_seconds
    rows=[inspect(s,deadline) for s in args.signatures]
    comp=compare(rows)
    # Evidence-based refinements only.
    p20_static=comp["20"]["samePubkey"]
    p28_static=comp["28"]["samePubkey"]
    p29_static=comp["29"]["samePubkey"]
    p21_owner_same=comp["21"]["tokenOwnersSame"]
    summary={
      "position20StaticAcrossTrades":p20_static,
      "position28StaticAcrossTrades":p28_static,
      "position29StaticAcrossTrades":p29_static,
      "position21TokenOwnerSameAcrossTrades":p21_owner_same,
      "position6IsPosition21TokenOwnerBoth":all(bool(r["selected"]["21"].get("tokenOwnerMatchesSelectedPosition6")) for r in rows),
      "position29TokenOwnerIsPosition28Both":all(bool(r["selected"]["29"].get("tokenOwnerMatchesPosition28")) for r in rows),
      "bisonPoolCounts":[len(r["bisonPools"]) for r in rows],
      "verdict":"ACCOUNT_ROLE_REFINEMENT_COMPLETE"
    }
    result={"schemaVersion":"WORLD_BTC5M_DFLOW_BISON_ACCOUNT_ROLE_REFINEMENT_R2","mode":"PUBLIC_READ_ONLY_NO_SIMULATION",
            "researchScore":0,"startedAt":time.time(),"transactions":rows,"comparison":comp,"summary":summary,"finishedAt":time.time()}
    Path(args.output).parent.mkdir(parents=True,exist_ok=True)
    Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2,sort_keys=True)+"\n")
    print(json.dumps(summary,sort_keys=True))

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--signatures",nargs="+",default=r0.DEFAULT_SIGNATURES)
    p.add_argument("--deadline-seconds",type=float,default=180)
    p.add_argument("--output",default="artifacts/world-btc5m-dflow-bison-account-role-refinement-r2.json")
    main(p.parse_args())

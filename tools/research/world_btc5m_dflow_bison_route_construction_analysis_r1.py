"""R1 decoder for World BTC5m DFlow/Bison route-construction analysis.

Extends R0 with current DFlow Action::RecordId / RecordId2 decoding before Action::D.
Read-only chain analysis only.
"""
from __future__ import annotations
import argparse, json, struct, time
from pathlib import Path
from typing import Any, Mapping

from tools.research import world_btc5m_dflow_bison_route_construction_analysis_r0 as r0

RID = 37
RID2 = 38
D = 57
A_NAMES = {0:"B",1:"J"}

class Cursor:
    def __init__(self, raw:bytes, pos:int=0): self.raw,self.pos=raw,pos
    def take(self,n:int)->bytes:
        if self.pos+n>len(self.raw): raise ValueError(f"TRUNCATED:{self.pos}:{n}:{len(self.raw)}")
        out=self.raw[self.pos:self.pos+n]; self.pos+=n; return out
    def u8(self): return self.take(1)[0]
    def u16(self): return struct.unpack("<H",self.take(2))[0]
    def u32(self): return struct.unpack("<I",self.take(4))[0]
    def u64(self): return struct.unpack("<Q",self.take(8))[0]

def decode_swap(raw:bytes)->dict[str,Any]:
    if not raw.startswith(r0.SWAP_DISC): raise ValueError("NOT_SWAP")
    c=Cursor(raw,8); actions=[]
    for ai in range(c.u32()):
        v=c.u8()
        if v==RID:
            b=c.take(76); actions.append({"variantIndex":v,"variantName":"RecordId","idHex":b.hex(),"idLength":76}); continue
        if v==RID2:
            b=c.take(4); actions.append({"variantIndex":v,"variantName":"RecordId2","idHex":b.hex(),"idLength":4}); continue
        if v!=D: raise ValueError(f"UNSUPPORTED_ACTION:{ai}:{v}:at={c.pos-1}")
        a=[]
        for i in range(c.u32()):
            av,pad=c.u8(),c.u8()
            a.append({"index":i,"variantIndex":av,"variantName":A_NAMES.get(av,f"UNKNOWN_{av}"),"padding":pad})
        actions.append({"variantIndex":v,"variantName":"D","a":a,"aVectorLength":len(a),
                        "amount":c.u64(),"orchestratorFlags":c.u8(),"pfs":c.u16(),"dfs":c.u16()})
    out={"rawHex":raw.hex(),"rawLength":len(raw),"actions":actions,
         "quotedOutAmount":c.u64(),"slippageBps":c.u16(),"platformFeeBps":c.u16()}
    if c.pos!=len(raw): raise ValueError(f"TAIL:{c.pos}:{len(raw)}:{raw[c.pos:].hex()}")
    return out

def encode_swap(d:Mapping[str,Any])->bytes:
    out=bytearray(r0.SWAP_DISC); acts=list(d["actions"]); out+=struct.pack("<I",len(acts))
    for a in acts:
        v=int(a["variantIndex"]); out.append(v)
        if v in {RID,RID2}: out+=bytes.fromhex(str(a["idHex"])); continue
        if v!=D: raise ValueError(f"ENCODE_UNSUPPORTED:{v}")
        vec=list(a["a"]); out+=struct.pack("<I",len(vec))
        for x in vec: out+=bytes([int(x["variantIndex"]),int(x["padding"])])
        out+=struct.pack("<Q",int(a["amount"]))
        out+=bytes([int(a["orchestratorFlags"])])
        out+=struct.pack("<HH",int(a["pfs"]),int(a["dfs"]))
    out+=struct.pack("<QHH",int(d["quotedOutAmount"]),int(d["slippageBps"]),int(d["platformFeeBps"]))
    return bytes(out)

def analyze(sig:str,deadline:float)->dict[str,Any]:
    t=r0.tx(sig,deadline); pos,ix,raw=r0.find_dflow_swap(t); dec=decode_swap(raw)
    accts=[str(x) for x in (ix.get("accounts") or [])]
    infos=r0.account_infos(accts,deadline)
    roster,ctx=r0.classify_roster(ix,t,infos)
    deltas=r0.token_deltas(t); user=ctx["user"]
    ud=[x for x in deltas if x.get("owner")==user]
    cash=sum(int(x["deltaAtoms"]) for x in ud if x["mint"]==r0.CASH)
    outputs=[x for x in ud if x["mint"]!=r0.CASH and int(x["deltaAtoms"])>0]
    primary=max(outputs,key=lambda x:int(x["deltaAtoms"]),default=None)
    da=next((x for x in dec["actions"] if x["variantName"]=="D"),None)
    rids=[x for x in dec["actions"] if x["variantName"] in {"RecordId","RecordId2"}]
    rel={}
    if da: rel["actionAmountEqualsUserCashSpentAtoms"]=int(da["amount"])==max(0,-cash)
    if primary:
        observed=int(primary["deltaAtoms"])
        rel["quotedOutVsObservedOutputAtoms"]={"quotedOutAmount":int(dec["quotedOutAmount"]),
            "observedOutputAtoms":observed,"ratioQuotedToObserved":int(dec["quotedOutAmount"])/observed if observed else None}
    return {"signature":sig,"slot":t.get("slot"),"blockTime":t.get("blockTime"),
            "dflowTopLevelPosition":pos,"dflowAccountEntryCount":len(accts),"dflowUniqueAccountCount":len(set(accts)),
            "swap":dec,"swapRoundTripByteExact":encode_swap(dec)==raw,"recordIds":rids,
            "user":user,"userTokenDeltas":ud,"userCashDeltaAtoms":cash,"primaryPositiveOutput":primary,
            "relationships":rel,"routeContext":ctx,"roster":roster}

def norm(row:Mapping[str,Any])->str:
    roles=list(row.get("roles") or [])
    for p in ["DFLOW_FIXED_","BISON_POOL","MARKET","YES_MINT","NO_MINT","CASH_MINT",
              "BISON_POOL_YES_TOKEN","BISON_POOL_NO_TOKEN","BISON_POOL_CASH_TOKEN",
              "PREDICT_SPLIT_","PREDICT_MERGE_","BISON_CPI_","USER_WALLET"]:
        hit=next((x for x in roles if x.startswith(p)),None)
        if hit:return hit
    return str(row.get("sourceClass"))

def compare(a:Mapping[str,Any],b:Mapping[str,Any])->dict[str,Any]:
    sa,sb=a["swap"],b["swap"]
    da=next(x for x in sa["actions"] if x["variantName"]=="D")
    db=next(x for x in sb["actions"] if x["variantName"]=="D")
    rida=[x["idHex"] for x in a["recordIds"]]; ridb=[x["idHex"] for x in b["recordIds"]]
    fields={"actionSequenceA":[x["variantName"] for x in sa["actions"]],
            "actionSequenceB":[x["variantName"] for x in sb["actions"]],
            "recordIdsSame":rida==ridb,"recordIdsA":rida,"recordIdsB":ridb,
            "aVectorSame":[(x["variantIndex"],x["padding"]) for x in da["a"]]==[(x["variantIndex"],x["padding"]) for x in db["a"]],
            "aVectorLengthA":da["aVectorLength"],"aVectorLengthB":db["aVectorLength"],
            "amountA":da["amount"],"amountB":db["amount"],
            "orchestratorFlagsA":da["orchestratorFlags"],"orchestratorFlagsB":db["orchestratorFlags"],
            "pfsA":da["pfs"],"pfsB":db["pfs"],"dfsA":da["dfs"],"dfsB":db["dfs"],
            "quotedOutA":sa["quotedOutAmount"],"quotedOutB":sb["quotedOutAmount"],
            "slippageA":sa["slippageBps"],"slippageB":sb["slippageBps"],
            "platformFeeA":sa["platformFeeBps"],"platformFeeB":sb["platformFeeBps"]}
    accepted={"DETERMINISTIC_STATIC","USER_DERIVED","USER_DERIVED_TOKEN_ACCOUNT",
              "MARKET_DERIVED_PUBLIC_STATE","MARKET_OR_USER_DERIVED_FROM_CPI_ROLE"}
    positions=[]; unresolved=[]
    for i in range(max(len(a["roster"]),len(b["roster"]))):
        ra=a["roster"][i] if i<len(a["roster"]) else None; rb=b["roster"][i] if i<len(b["roster"]) else None
        row={"position":i,"aRole":norm(ra) if ra else None,"bRole":norm(rb) if rb else None,
             "samePubkey":bool(ra and rb and ra["pubkey"]==rb["pubkey"]),
             "aSourceClass":ra.get("sourceClass") if ra else None,"bSourceClass":rb.get("sourceClass") if rb else None}
        row["roleShapeSame"]=row["aRole"]==row["bRole"]
        row["publiclyReconstructableByCurrentClassifier"]=bool(ra and rb and row["roleShapeSame"] and
            (row["samePubkey"] or (ra["sourceClass"] in accepted and rb["sourceClass"] in accepted)))
        if not row["publiclyReconstructableByCurrentClassifier"]: unresolved.append(row)
        positions.append(row)
    return {"fieldComparison":fields,"rosterComparison":{"positionCount":len(positions),
            "publiclyReconstructablePositionCount":len(positions)-len(unresolved),
            "unresolvedPositionCount":len(unresolved),"unresolvedPositions":unresolved,"positions":positions}}

def main(args):
    deadline=time.time()+args.deadline_seconds
    trs=[analyze(s,deadline) for s in args.signatures]; comp=compare(trs[0],trs[1])
    f=comp["fieldComparison"]; unresolved=comp["rosterComparison"]["unresolvedPositionCount"]
    summary={"verdict":"PUBLIC_STATE_INSUFFICIENT_FOR_SAFE_PROSPECTIVE_ROUTE_REPRODUCTION",
             "decoderByteExactForAllTrades":all(x["swapRoundTripByteExact"] for x in trs),
             "actionAmountMatchesCashInputForAllTrades":all(bool(x["relationships"].get("actionAmountEqualsUserCashSpentAtoms")) for x in trs),
             "recordIdsSameAcrossTrades":f["recordIdsSame"],
             "aVectorSameAcrossTrades":f["aVectorSame"],
             "rosterUnresolvedPositions":unresolved,
             "firstUnresolvedDependencies":[
                 "RecordId is an opaque DFlow action with no public generation rule identified",
                 "quotedOutAmount is quote-derived execution protection and is not present in Bison on-chain pool state",
             ]}
    result={"schemaVersion":"WORLD_BTC5M_DFLOW_BISON_ROUTE_CONSTRUCTION_ANALYSIS_R1",
            "mode":"PUBLIC_READ_ONLY_OFFLINE_ROUTE_ANALYSIS","researchScore":0,
            "startedAt":time.time(),"transactions":trs,"comparison":comp,"summary":summary,
            "limitations":[
                "Byte-exact decoding/re-encoding proves the parser, not independent quote generation.",
                "Two-transaction role stability does not prove a protocol invariant.",
                "The conclusion does not prove a DFlow server is cryptographically mandatory; it identifies the first non-public or unresolved inputs for a safe prospective builder."
            ],"finishedAt":time.time()}
    Path(args.output).parent.mkdir(parents=True,exist_ok=True)
    Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2,sort_keys=True)+"\n")
    print(json.dumps(summary,sort_keys=True))

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--signatures",nargs="+",default=r0.DEFAULT_SIGNATURES)
    p.add_argument("--deadline-seconds",type=float,default=180)
    p.add_argument("--output",default="artifacts/world-btc5m-dflow-bison-route-construction-analysis-r1.json")
    main(p.parse_args())

"""DIRECT_MAKER_ACCESS_POC_R0

Read-only mainnet diagnostic. Scans recent DFlow/Janus/Bison transactions and finds
World prediction-market trades that invoke prediCt. It records transaction signers,
program invocation graph, and whether Janus/Bison/DFlow-related accounts are signers.
No credentials, signing, transaction building, or submission.
"""
from __future__ import annotations
import argparse, json, time
from pathlib import Path
from typing import Any, Mapping

from tools.research import world_polymarket_btc5m_leadlag_probe_r1 as wp

DFLOW="DF1ow4tspfHX9JwWJsAb9epbkA8hmpSEAtxXy1V27QBH"
JANUS="JanusXpm3gsW3c9ErNoUgHppL8dGLvZKB7uekkJEYFP"
BISON="2DNbzPochEcyCcWMbL4d9S3u9QqQEj5bbe6cSZFvKsbh"
PREDICT=wp.PREDICT_PROGRAM
PROGRAMS={DFLOW:"DFlow",JANUS:"JanusFI",BISON:"BisonFI",PREDICT:"prediCt"}
FILL_ORDER_DISC=bytes([232,122,115,25,199,143,136,162])
OPEN_ORDER_DISC=bytes([206,88,88,143,38,136,50,224])

ALPHABET="123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
def b58decode(s:str)->bytes:
    n=0
    for ch in s:n=n*58+ALPHABET.index(ch)
    raw=n.to_bytes((n.bit_length()+7)//8,"big") if n else b""
    return b"\x00"*(len(s)-len(s.lstrip("1")))+raw

def keys_and_signers(tx:Mapping[str,Any]):
    keys=tx["transaction"]["message"].get("accountKeys") or []
    out=[]; signers=[]
    for i,k in enumerate(keys):
        if isinstance(k,Mapping):
            pub=str(k.get("pubkey")); signer=bool(k.get("signer")); writable=bool(k.get("writable"))
        else:
            pub=str(k); signer=False; writable=False
        out.append(pub)
        if signer:signers.append(pub)
    return out,signers

def all_ix(tx:Mapping[str,Any]):
    rows=[]
    for ix in tx["transaction"]["message"].get("instructions") or []:
        if isinstance(ix,Mapping): rows.append(("top",ix))
    for g in tx.get("meta",{}).get("innerInstructions") or []:
        idx=g.get("index")
        for ix in g.get("instructions") or []:
            if isinstance(ix,Mapping): rows.append((f"inner:{idx}",ix))
    return rows

def summarize_ix(scope:str,ix:Mapping[str,Any],signers:set[str]):
    pid=str(ix.get("programId") or "")
    data=str(ix.get("data") or "")
    raw=b""
    try: raw=b58decode(data)
    except Exception: pass
    accts=[str(x) for x in (ix.get("accounts") or [])]
    kind=None
    if pid==DFLOW and raw.startswith(FILL_ORDER_DISC):kind="fill_order"
    elif pid==DFLOW and raw.startswith(OPEN_ORDER_DISC):kind="open_order"
    return {"scope":scope,"programId":pid,"program":PROGRAMS.get(pid,pid),"kind":kind,
            "dataPrefixHex":raw[:16].hex(),"accountCount":len(accts),"accounts":accts,
            "signerAccounts":[a for a in accts if a in signers]}

def main(args):
    deadline=time.time()+args.deadline_seconds
    trace={"requests":[]}
    rows=wp._rpc(args.rpc,"getSignaturesForAddress",[DFLOW,{"limit":args.signature_limit,"commitment":"confirmed"}],deadline=deadline,trace=trace) or []
    out={"schemaVersion":"DIRECT_MAKER_ACCESS_POC_R0","mode":"PUBLIC_READ_ONLY_NO_TRADE","researchScore":0,
         "startedAt":time.time(),"signatureRows":len(rows),"trades":[],"errors":[]}
    for row in rows:
        if len(out["trades"])>=args.max_trades or time.time()>=deadline:break
        if not isinstance(row,Mapping) or row.get("err") is not None:continue
        sig=str(row.get("signature") or "")
        if not sig:continue
        try:
            tx=wp._rpc(args.rpc,"getTransaction",[sig,{"encoding":"jsonParsed","maxSupportedTransactionVersion":1,"commitment":"confirmed"}],deadline=deadline,trace=trace)
        except Exception as e:
            out["errors"].append(f"{sig}:{type(e).__name__}:{e}"); continue
        if not isinstance(tx,Mapping) or tx.get("meta",{}).get("err") is not None:continue
        keys,signers=keys_and_signers(tx); signer_set=set(signers)
        ixrows=[summarize_ix(scope,ix,signer_set) for scope,ix in all_ix(tx)]
        pids={x["programId"] for x in ixrows}
        if PREDICT not in pids or not ({JANUS,BISON}&pids):continue
        trade={"signature":sig,"blockTime":row.get("blockTime"),"slot":tx.get("slot"),
               "txSignatureCount":len(tx["transaction"].get("signatures") or []),
               "signers":signers,
               "signerLabels":[PROGRAMS.get(x,x) for x in signers],
               "programs":[PROGRAMS.get(x,x) for x in sorted(pids)],
               "dflowInstructions":[x for x in ixrows if x["programId"]==DFLOW],
               "makerInstructions":[x for x in ixrows if x["programId"] in {JANUS,BISON}],
               "predictInstructions":[x for x in ixrows if x["programId"]==PREDICT],
               "janusOrBisonSignerPresent": any(s in {JANUS,BISON} for s in signers),
               "nonUserExtraSignerCount":max(0,len(signers)-1)}
        out["trades"].append(trade)
    out["finishedAt"]=time.time(); out["rpcTraceSummary"]={"requestCount":len(trace.get("requests") or []),"workingEndpoint":trace.get("workingEndpoint")}
    # Aggregate the facts that matter for self-buildability.
    out["summary"]={
      "worldTradesFound":len(out["trades"]),
      "multiSignerTransactions":sum(len(t["signers"])>1 for t in out["trades"]),
      "singleSignerTransactions":sum(len(t["signers"])==1 for t in out["trades"]),
      "fillOrderInstructions":sum(sum(x.get("kind")=="fill_order" for x in t["dflowInstructions"]) for t in out["trades"]),
      "openOrderInstructions":sum(sum(x.get("kind")=="open_order" for x in t["dflowInstructions"]) for t in out["trades"]),
      "tradesWithDflowInstruction":sum(bool(t["dflowInstructions"]) for t in out["trades"]),
      "tradesWithJanus":sum("JanusFI" in t["programs"] for t in out["trades"]),
      "tradesWithBison":sum("BisonFI" in t["programs"] for t in out["trades"]),
    }
    Path(args.output).parent.mkdir(parents=True,exist_ok=True)
    Path(args.output).write_text(json.dumps(out,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps(out["summary"],sort_keys=True))

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--rpc",default="https://solana-rpc.publicnode.com")
    p.add_argument("--signature-limit",type=int,default=800)
    p.add_argument("--max-trades",type=int,default=30)
    p.add_argument("--deadline-seconds",type=float,default=300)
    p.add_argument("--output",default="artifacts/direct-maker-access-poc-r0.json")
    main(p.parse_args())

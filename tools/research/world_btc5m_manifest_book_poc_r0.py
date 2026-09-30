"""Read-only Manifest book probe for current World BTC5m outcome tokens.

Discovers the current World BTC5m market directly from prediCt program accounts,
then searches Manifest for YES/CASH and NO/CASH books and parses their top levels.
No wallet, signing, order placement, or capital.
"""
from __future__ import annotations
import argparse, base64, json, struct, time
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping
from tools.research import world_pm_jupiter_economic_poc_r0 as jp
from tools.research import world_polymarket_btc5m_leadlag_probe_r1 as wp

MANIFEST="MNFSTqtC93rEfYHB6hF82sKdZpUDFWkViLByLd1k1Ms"
HEADER=256; NIL=0xFFFFFFFF; NODE=16

def rpc(method:str,params:list[Any],deadline:float):
    return wp._rpc(wp.DEFAULT_SOLANA_RPC,method,params,deadline=deadline)

def find_books(base:str,quote:str,deadline:float)->list[str]:
    filters=[{"memcmp":{"offset":16,"bytes":base}},{"memcmp":{"offset":48,"bytes":quote}}]
    rows=rpc("getProgramAccounts",[MANIFEST,{"encoding":"base64","dataSlice":{"offset":0,"length":0},"filters":filters,"commitment":"confirmed"}],deadline) or []
    return [str(x.get("pubkey")) for x in rows if isinstance(x,Mapping) and x.get("pubkey")]

def get_account(key:str,deadline:float)->bytes:
    r=rpc("getAccountInfo",[key,{"encoding":"base64","commitment":"confirmed"}],deadline)
    v=r.get("value") if isinstance(r,Mapping) else None
    if not isinstance(v,Mapping): raise RuntimeError("MANIFEST_ACCOUNT_MISSING")
    d=v.get("data")
    if not isinstance(d,list) or not d: raise RuntimeError("MANIFEST_DATA_MISSING")
    return base64.b64decode(str(d[0]))

def walk_offsets(data:bytes,root:int)->list[int]:
    out=[]; stack=[]; i=root
    while stack or i!=NIL:
        while i!=NIL:
            stack.append(i); i=struct.unpack_from("<I",data,HEADER+i)[0]
        i=stack.pop(); out.append(HEADER+i+NODE); i=struct.unpack_from("<I",data,HEADER+i+4)[0]
    return out

def parse_book(data:bytes)->dict[str,Any]:
    if len(data)<HEADER: raise ValueError("MANIFEST_TOO_SHORT")
    base_dec,quote_dec=data[9],data[10]
    bids_root,_,asks_root,_,_,_=struct.unpack_from("<6I",data,156)
    scale=Decimal(10)**(base_dec-quote_dec)/Decimal(10)**18
    def orders(root:int):
        arr=[]
        for off in walk_offsets(data,root):
            lo,hi,atoms,seq,_,last_valid_slot,is_bid=struct.unpack_from("<QQQQII?",data,off)
            d18=lo+(hi<<64)
            arr.append({"seq":seq,"isBid":bool(is_bid),"baseAtoms":atoms,"price":float(Decimal(d18)*scale),"lastValidSlot":last_valid_slot})
        return arr
    bids=sorted(orders(bids_root),key=lambda x:(-x["price"],x["seq"]))
    asks=sorted(orders(asks_root),key=lambda x:(x["price"],x["seq"]))
    return {"baseDecimals":base_dec,"quoteDecimals":quote_dec,"bidCount":len(bids),"askCount":len(asks),"bestBid":bids[0] if bids else None,"bestAsk":asks[0] if asks else None,"bids":bids[:10],"asks":asks[:10]}

def main(args):
    deadline=time.time()+60
    start=(int(time.time())//300)*300
    candidates=jp.describe_candidates(jp.program_markets_for_start(start,deadline),deadline)
    btc=jp.select_btc(candidates)
    out={"schemaVersion":"WORLD_BTC5M_MANIFEST_BOOK_POC_R0","mode":"PUBLIC_READ_ONLY_NO_TRADE","researchScore":0,"observedAt":time.time(),"startTs":start,"worldMarket":btc,"sides":{}}
    for side,mint in [("yes",btc["yesMint"]),("no",btc["noMint"])]:
        books=find_books(mint,btc["cashMint"],deadline)
        rows=[]
        for b in books[:10]:
            try: rows.append({"market":b,**parse_book(get_account(b,deadline))})
            except Exception as e: rows.append({"market":b,"error":f"{type(e).__name__}:{e}"})
        out["sides"][side]={"mint":mint,"manifestMarketCount":len(books),"books":rows}
    out["summary"]={
      "yesBookCount":out["sides"]["yes"]["manifestMarketCount"],
      "noBookCount":out["sides"]["no"]["manifestMarketCount"],
      "yesNonEmpty":sum(bool(x.get("bestBid") or x.get("bestAsk")) for x in out["sides"]["yes"]["books"]),
      "noNonEmpty":sum(bool(x.get("bestBid") or x.get("bestAsk")) for x in out["sides"]["no"]["books"]),
    }
    Path(args.output).parent.mkdir(parents=True,exist_ok=True)
    Path(args.output).write_text(json.dumps(out,indent=2,sort_keys=True)+"\n")
    print(json.dumps(out["summary"],sort_keys=True))

if __name__=="__main__":
    p=argparse.ArgumentParser(); p.add_argument("--output",default="artifacts/world-btc5m-manifest-book-poc-r0.json"); main(p.parse_args())

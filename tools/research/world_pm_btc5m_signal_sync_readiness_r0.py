"""WORLD↔Polymarket BTC5m signal synchronization readiness R0.

Read-only. No trade, signing, simulation or paper fill.
Tests only the Polymarket side of the synchronized capture path; World MCP
availability/identity/freshness is checked separately by the reviewer because
that connector is not available inside GitHub Actions.
"""
from __future__ import annotations
import argparse, json, time
from pathlib import Path
from typing import Any, Mapping

from tools.research import world_polymarket_btc5m_leadlag_probe_r1 as wp
from tools.research import world_pm_jupiter_economic_poc_r0 as jp
from tools.research import world_polymarket_btc5m_executable_sync_r23 as r23

def full_book(token:str)->dict[str,Any]:
    started=time.time()
    url=r23.PM_BOOK_URL+"?token_id="+token
    try:
        body=jp.http_json(url,timeout=8.0)
    except Exception as exc:
        return {"success":False,"startedAt":started,"receivedAt":time.time(),"error":f"{type(exc).__name__}:{exc}"}
    received=time.time()
    asks=r23._book_rows(r23._levels(body.get("asks") if isinstance(body,Mapping) else None),asks=True)
    bids=r23._book_rows(r23._levels(body.get("bids") if isinstance(body,Mapping) else None),asks=False)
    return {
      "success":True,"startedAt":started,"receivedAt":received,"elapsedMs":(received-started)*1000,
      "timestamp":body.get("timestamp") if isinstance(body,Mapping) else None,
      "hash":body.get("hash") if isinstance(body,Mapping) else None,
      "bestAsk":asks[0]["price"] if asks else None,
      "bestBid":bids[0]["price"] if bids else None,
      "askLevels":len(asks),"bidLevels":len(bids),
      "asks":asks,"bids":bids,
    }

def ts_seconds(value:Any)->float|None:
    try:
        v=float(value)
    except Exception:
        return None
    return v/1000.0 if v>10_000_000_000 else v

def main(args):
    local_before=time.time()
    clob_time=wp._server_time()
    local_after=time.time()
    local_mid=(local_before+local_after)/2
    start=args.start_ts if args.start_ts else (clob_time//300)*300
    if start > clob_time:
        time.sleep(max(0.0, start + 5 - time.time()))
        local_before=time.time()
        clob_time=wp._server_time()
        local_after=time.time()
        local_mid=(local_before+local_after)/2
    remaining=start+300-clob_time
    if remaining < args.min_remaining_seconds:
        result={
          "schemaVersion":"WORLD_PM_BTC5M_SIGNAL_SYNC_READINESS_R0",
          "mode":"PUBLIC_READ_ONLY_NO_TRADE",
          "startTs":start,"endTs":start+300,"clobServerTime":clob_time,
          "remainingAtStartSeconds":remaining,
          "verdict":"NO_RESULT","reason":"INSUFFICIENT_WINDOW_LIFETIME",
          "limitations":["No readiness conclusion is drawn when less than the frozen minimum lifetime remains."]
        }
        Path(args.output).parent.mkdir(parents=True,exist_ok=True)
        Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2,sort_keys=True)+"\n")
        print(json.dumps({"verdict":"NO_RESULT","reason":"INSUFFICIENT_WINDOW_LIFETIME","remaining":remaining},sort_keys=True))
        return
    market=wp.fetch_polymarket_market(start)

    snaps=[]
    for i in range(args.snapshots):
        up=full_book(market.up_token)
        down=full_book(market.down_token)
        paired_at=time.time()
        snaps.append({"index":i,"pairedAt":paired_at,"up":up,"down":down})
        if i+1<args.snapshots:
            time.sleep(args.interval_seconds)

    successes=sum(1 for s in snaps if s["up"].get("success") and s["down"].get("success"))
    nonnull=sum(1 for s in snaps if s["up"].get("bestAsk") is not None and s["down"].get("bestAsk") is not None)
    pair_skews=[abs(float(s["up"]["receivedAt"])-float(s["down"]["receivedAt"])) for s in snaps if s["up"].get("success") and s["down"].get("success")]
    source_ages=[]
    for s in snaps:
        for side in ("up","down"):
            row=s[side]
            t=ts_seconds(row.get("timestamp"))
            if t is not None:
                source_ages.append(float(row["receivedAt"])-t)

    gates={
      "serverClockSkewSeconds":abs(local_mid-clob_time),
      "serverClockSkewPass":abs(local_mid-clob_time)<=3.0,
      "pairedSuccessCount":successes,
      "pairedSuccessPass":successes>=args.snapshots-1,
      "nonnullBestAskCount":nonnull,
      "nonnullBestAskPass":nonnull>=args.snapshots-1,
      "maxPairReceiveSkewSeconds":max(pair_skews) if pair_skews else None,
      "pairReceiveSkewPass":bool(pair_skews) and max(pair_skews)<=2.0,
      "maxBookSourceAgeSeconds":max(source_ages) if source_ages else None,
      "bookSourceAgePass":bool(source_ages) and max(source_ages)<=5.0,
      "exactFiveMinuteWindowPass":market.start_ts==start,
    }
    passed=all(v for k,v in gates.items() if k.endswith("Pass"))
    result={
      "schemaVersion":"WORLD_PM_BTC5M_SIGNAL_SYNC_READINESS_R0",
      "mode":"PUBLIC_READ_ONLY_NO_TRADE",
      "startTs":start,"endTs":start+300,
      "clobServerTime":clob_time,
      "market":{"conditionId":market.condition_id,"upToken":market.up_token,"downToken":market.down_token,
                "feeSchedule":market.fee_schedule,"cryptoConfig":market.crypto_config},
      "snapshots":snaps,"gates":gates,
      "verdict":"PASS" if passed else "NO_RESULT",
      "limitations":[
        "World MCP readiness is adjudicated separately outside GitHub Actions.",
        "This checks synchronization/data capture readiness only, not alpha, fills, depth, fees, execution or settlement equivalence."
      ]
    }
    Path(args.output).parent.mkdir(parents=True,exist_ok=True)
    Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2,sort_keys=True)+"\n")
    print(json.dumps({"verdict":result["verdict"],"gates":gates},sort_keys=True))

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--start-ts",type=int,default=0)
    p.add_argument("--snapshots",type=int,default=4)
    p.add_argument("--interval-seconds",type=float,default=2.0)
    p.add_argument("--min-remaining-seconds",type=int,default=120)
    p.add_argument("--output",default="artifacts/world-pm-btc5m-signal-sync-readiness-r0.json")
    main(p.parse_args())

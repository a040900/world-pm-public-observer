"""R6 fixed 12-window indicative signal analyzer.

Inputs are compact prospective PM captures and read-only World MCP histories.
No execution, fill, depth, after-cost, or settlement-admission inference.
"""
from __future__ import annotations
import argparse,json,math
from pathlib import Path
from typing import Any

def median(a):
    if not a:return None
    s=sorted(a);n=len(s);return s[n//2] if n%2 else (s[n//2-1]+s[n//2])/2

def gaps(ts):
    s=sorted(set(ts));return [s[i]-s[i-1] for i in range(1,len(s))]

def wilson_lower(k:int,n:int,z:float=1.959963984540054)->float|None:
    if n<=0:return None
    p=k/n; d=1+z*z/n
    c=(p+z*z/(2*n))/d
    h=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/d
    return c-h

def latest_world(rows:list[dict[str,Any]], t:float, max_age:float=2.0):
    eligible=[x for x in rows if x.get("ask") is not None and x.get("ts") is not None and float(x["ts"])<=t]
    if not eligible:return None
    x=max(eligible,key=lambda z:float(z["ts"])); age=t-float(x["ts"])
    return x if 0<=age<=max_age else None

def nearest_distance(rows_yes,rows_no,t):
    vals=[abs(float(x["ts"])-t) for x in rows_yes+rows_no if x.get("ts") is not None]
    return min(vals) if vals else None

def basic_qualified(pm,world):
    snaps=[x for x in pm.get("snapshots",[]) if x.get("up",{}).get("success") and x.get("down",{}).get("success")]
    cap=[float(x["capturedAt"]) for x in snaps]
    sp=gaps(cap)
    def ws(rows):
        ts=[int(x["ts"]) for x in rows if x.get("ts") is not None];g=gaps(ts)
        return {"n":len(rows),"medianGap":median(g),"maxGap":max(g) if g else None}
    ys=ws(world.get("yes",[]));ns=ws(world.get("no",[]))
    near=[nearest_distance(world.get("yes",[]),world.get("no",[]),float(x["capturedAt"])) for x in snaps]
    align=sum(d is not None and d<=2 for d in near)/len(near) if near else 0
    gates={
      "pmPairedAtLeast120":len(snaps)>=120,
      "pmMedianSpacing":median(sp)<=2.5 if sp else False,
      "pmMaxGap":max(sp)<=8 if sp else False,
      "worldYesAtLeast210":ys["n"]>=210,
      "worldNoAtLeast210":ns["n"]>=210,
      "worldYesMedianGap":ys["medianGap"] is not None and ys["medianGap"]<=1.5,
      "worldNoMedianGap":ns["medianGap"] is not None and ns["medianGap"]<=1.5,
      "worldYesMaxGap":ys["maxGap"] is not None and ys["maxGap"]<=15,
      "worldNoMaxGap":ns["maxGap"] is not None and ns["maxGap"]<=15,
      "timestampAlignment90pct":align>=0.90,
    }
    return all(gates.values()),{"paired":len(snaps),"pmMedianSpacing":median(sp),"pmMaxGap":max(sp) if sp else None,
      "worldYes":ys,"worldNo":ns,"alignmentRate":align,"gates":gates}

def observations(pm,world):
    out=[]
    for s in pm.get("snapshots",[]):
        if not (s.get("up",{}).get("success") and s.get("down",{}).get("success")):continue
        up=s["up"];down=s["down"]
        upask=up.get("bestAsk");downask=down.get("bestAsk")
        a=[]
        if downask is not None:
            wy=latest_world(world.get("yes",[]),float(down["receivedAt"]))
            if wy is not None:a.append({"direction":"WORLD_YES+PM_DOWN","t":float(down["receivedAt"]),"worldTs":wy["ts"],"worldAsk":wy["ask"],"pmAsk":downask,"sum":float(wy["ask"])+float(downask)})
        if upask is not None:
            wn=latest_world(world.get("no",[]),float(up["receivedAt"]))
            if wn is not None:a.append({"direction":"WORLD_NO+PM_UP","t":float(up["receivedAt"]),"worldTs":wn["ts"],"worldAsk":wn["ask"],"pmAsk":upask,"sum":float(wn["ask"])+float(upask)})
        out.append({"capturedAt":s["capturedAt"],"packages":a})
    return out

def eval_window(pm,world):
    bq,bqdiag=basic_qualified(pm,world)
    rec={"startTs":pm["startTs"],"basicQualified":bq,"basicDiagnostics":bqdiag}
    if not bq:return rec
    obs=observations(pm,world)
    first=None
    for row in obs:
        trig=[x for x in row["packages"] if x["sum"]<0.99]
        if trig:
            if len(trig)>1:
                rec["triggerState"]="AMBIGUOUS_SIMULTANEOUS_DIRECTION";return rec
            first=trig[0];break
    if first is None:
        rec["triggerState"]="NO_TRIGGER";return rec
    rec["triggerState"]="TRIGGERED";rec["trigger"]=first
    follow=None
    for row in obs:
        if max((x["t"] for x in row["packages"]),default=-1)<first["t"]+2:continue
        x=next((x for x in row["packages"] if x["direction"]==first["direction"] and x["t"]>=first["t"]+2),None)
        if x is not None:follow=x;break
    rec["followup"]=follow
    if follow is not None:rec["retainedBelowOne"]=follow["sum"]<1.0
    # descriptive horizons
    diag={}
    for h in [1,2,5,10]:
        target=first["t"]+h
        cand=[]
        for row in obs:
            for x in row["packages"]:
                if x["direction"]==first["direction"] and x["t"]>=target:cand.append(x)
            if cand:break
        diag[str(h)]=cand[0] if cand else None
    rec["horizonDiagnostics"]=diag
    return rec

def main(args):
    pm=json.loads(Path(args.pm).read_text());world=json.loads(Path(args.world).read_text())
    wmap={int(x["startTs"]):x for x in world["windows"]}
    results=[]
    for p in pm["windows"]:
        w=wmap.get(int(p["startTs"]))
        results.append(eval_window(p,w) if w else {"startTs":p["startTs"],"basicQualified":False,"error":"WORLD_WINDOW_MISSING"})
    basic=sum(bool(x.get("basicQualified")) for x in results)
    trig=[x for x in results if x.get("triggerState")=="TRIGGERED"]
    unknown=[x for x in trig if x.get("followup") is None]
    evaluable=[x for x in trig if x.get("followup") is not None]
    retained=sum(bool(x.get("retainedBelowOne")) for x in evaluable)
    wl=wilson_lower(retained,len(evaluable))
    if basic<10 or len(trig)<6 or unknown:
        verdict="NO_RESULT"
    elif retained>=4 and wl is not None and wl>0.30:
        verdict="PASS"
    else:
        verdict="FAIL"
    out={"schemaVersion":"WORLD_PM_R6_SIGNAL_ANALYSIS_R0","primaryVerdict":verdict,
         "basicQualifiedWindows":basic,"triggeredWindows":len(trig),"evaluableTriggeredWindows":len(evaluable),
         "unknownRetentionWindows":len(unknown),"retainedBelowOne":retained,"wilson95Lower":wl,
         "windows":results,
         "explicitNonClaims":["No fill assumption","No depth assumption","No after-cost PnL","No execution verdict","No settlement admission"]}
    Path(args.output).write_text(json.dumps(out,ensure_ascii=False,indent=2,sort_keys=True)+"\n")
    print(json.dumps({k:out[k] for k in ["primaryVerdict","basicQualifiedWindows","triggeredWindows","evaluableTriggeredWindows","retainedBelowOne","wilson95Lower"]},sort_keys=True))

if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--pm",required=True);p.add_argument("--world",required=True)
    p.add_argument("--output",default="artifacts/world-pm-r6-signal-analysis-r0.json");main(p.parse_args())

import json
import urllib.parse
import urllib.request

BASE="https://api.limitless.exchange"

def get(path, **params):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent":"structural-radar-r0/1.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))

def flatten(rows):
    out=[]
    for row in rows:
        if isinstance(row,dict) and isinstance(row.get("markets"),list):
            for child in row["markets"]:
                if isinstance(child,dict):
                    merged=dict(child)
                    merged.setdefault("groupSlug", row.get("slug") or row.get("groupSlug"))
                    merged.setdefault("groupTitle", row.get("title"))
                    out.append(merged)
        elif isinstance(row,dict):
            out.append(row)
    return out

def main():
    result={"venue":"limitless","queries":{},"books":[],"capitalEfficiency":{}}
    seen={}
    for q in ("bitcoin","btc","bitcoin up or down","btc up or down"):
        try:
            payload=get("/markets/search", query=q, limit=50)
            rows=payload.get("markets",[]) if isinstance(payload,dict) else []
            flat=flatten(rows)
            result["queries"][q]=flat
            for row in flat:
                slug=row.get("slug")
                if slug:
                    seen[slug]=row
        except Exception as exc:
            result["queries"][q]={"error":f"{type(exc).__name__}:{exc}"}
    for slug,row in list(seen.items())[:100]:
        try:
            b=get(f"/markets/{urllib.parse.quote(slug, safe='')}/orderbook")
            bids=b.get("bids") or []
            asks=b.get("asks") or []
            result["books"].append({
                "slug":slug,
                "title":row.get("title") or row.get("question") or row.get("groupTitle"),
                "groupSlug":row.get("groupSlug"),
                "bestYesBid": bids[0] if bids else None,
                "bestYesAsk": asks[0] if asks else None,
                "rawMarket": row,
            })
        except Exception as exc:
            result["books"].append({"slug":slug,"error":f"{type(exc).__name__}:{exc}","rawMarket":row})
    # Summarize capital-lock horizons and current executable top-of-book capacity.
    horizons={"lte_1h":0,"lte_1d":0,"lte_7d":0,"gt_7d":0,"unknown":0}
    from datetime import datetime, timezone
    now=datetime.now(timezone.utc)
    for item in result["books"]:
        row=item.get("rawMarket") or {}
        raw=row.get("expirationTimestamp") or row.get("expirationDate")
        try:
            if raw is None:
                raise ValueError("missing")
            if str(raw).isdigit():
                exp=datetime.fromtimestamp(int(raw)/1000, tz=timezone.utc)
            else:
                exp=datetime.fromisoformat(str(raw).replace("Z","+00:00"))
            hours=max(0,(exp-now).total_seconds()/3600)
            if hours <= 1: horizons["lte_1h"]+=1
            elif hours <= 24: horizons["lte_1d"]+=1
            elif hours <= 168: horizons["lte_7d"]+=1
            else: horizons["gt_7d"]+=1
        except Exception:
            horizons["unknown"]+=1
    result["capitalEfficiency"]["horizons"]=horizons
    result["capitalEfficiency"]["bookCount"]=len(result["books"])
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()

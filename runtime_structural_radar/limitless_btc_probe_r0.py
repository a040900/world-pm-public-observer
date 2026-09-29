import json, requests

BASE="https://api.limitless.exchange"

def get(path, **params):
    r=requests.get(BASE+path, params=params, timeout=20, headers={"User-Agent":"structural-radar-r0/1.0"})
    r.raise_for_status()
    return r.json()

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
    result={"venue":"limitless","queries":{},"books":[]}
    seen={}
    for q in ("bitcoin","btc"):
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
            b=get(f"/markets/{slug}/orderbook")
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
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()

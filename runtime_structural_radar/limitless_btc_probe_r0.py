import asyncio, json, re, sys, subprocess

async def main():
    try:
        import ccxt.async_support as ccxt
    except Exception:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "ccxt"])
        import ccxt.async_support as ccxt

    ex = ccxt.prediction.limitless({"enableRateLimit": True})
    out={"venue":"limitless","queries":{}}
    try:
        for q in ["bitcoin", "btc"]:
            try:
                events=await ex.fetch_events({"query":q,"limit":50})
                rows=[]
                for e in events:
                    rows.append({
                        "id":e.get("id"),"title":e.get("title"),
                        "markets":[{"id":m.get("id"),"symbol":m.get("symbol"),"question":m.get("question"),
                                    "outcomes":[{"symbol":o.get("symbol"),"name":o.get("name")} for o in (m.get("outcomes") or [])]}
                                   for m in (e.get("markets") or [])]
                    })
                out["queries"][q]=rows
            except Exception as exc:
                out["queries"][q]={"error":type(exc).__name__+":"+str(exc)}
        # sample books for all discovered BTC-ish outcome symbols, capped
        books=[]
        seen=set()
        for rows in out["queries"].values():
            if not isinstance(rows,list): continue
            for e in rows:
                for m in e["markets"]:
                    for o in m["outcomes"]:
                        sym=o.get("symbol")
                        if sym and sym not in seen and len(books)<80:
                            seen.add(sym)
                            try:
                                b=await ex.fetch_order_book(sym)
                                books.append({"symbol":sym,"bid":b["bids"][0] if b["bids"] else None,"ask":b["asks"][0] if b["asks"] else None})
                            except Exception as exc:
                                books.append({"symbol":sym,"error":type(exc).__name__+":"+str(exc)})
        out["books"]=books
    finally:
        await ex.close()
    print(json.dumps(out,ensure_ascii=False,indent=2))

asyncio.run(main())

"""Bounded public frontend/quote-build diagnostic. No signing or submission.

Uses only getProgramAccounts/getMultipleAccounts for current-market discovery.
Never obtains a Turnstile token, uses a stored session, or bypasses a challenge.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from tools.research import world_pm_jupiter_economic_poc_r0 as jp

BASE = "https://world.xyz"
PROXY = "https://aggregator-api-proxy.world-xyz.workers.dev"
SIGNER = "7mwwqMKUeoWrCYefWYzczpFnNy5BMyBEfmfGV9SpzC9e"
SIGNER_TRANSACTION = "38SYKSL8XhrrDkbb1hgHCpqnA8wvHCakKMCAwYLAE5ZBRE9mSiDupxxuRSM355843wByw4WpfwmGvGqfyvdWb6sX"


def receipt(url, headers=None, data=None):
    request = urllib.request.Request(url, headers=headers or {"User-Agent": "world-pm-public-observer/frontend-contract-r0"}, data=data)
    started = time.time()
    try:
        response = urllib.request.urlopen(request, timeout=15)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        raw = response.read()
        return raw, {"url": url, "method": request.get_method(),
                     "requestHeaders": dict(request.header_items()),
                     "at": started, "elapsedMs": (time.time()-started)*1000,
                     "status": response.status, "bytes": len(raw),
                     "sha256": hashlib.sha256(raw).hexdigest(),
                     "responseHeaders": dict(response.headers.items())}


def excerpt(text, needle, before=150, after=1600):
    at = text.index(needle)
    begin = max(0, at-before)
    return {"needle": needle, "offset": at, "begin": begin,
            "text": text[begin:at+after]}


def run(output):
    output.mkdir(parents=True, exist_ok=True)
    assets_dir = output / "assets"
    assets_dir.mkdir(exist_ok=True)
    result = {"schemaVersion": "WORLD_FRONTEND_QUOTE_CONTRACT_R0",
              "startedAt": time.time(), "assets": [], "excerpts": {},
              "probes": [], "discovery": {}, "rpcReceipts": [], "NO_TRADE": True,
              "publicSigner": {"pubkey": SIGNER, "transaction": SIGNER_TRANSACTION}}
    try:
        raw, meta = receipt(BASE)
        (assets_dir / "index.html").write_bytes(raw)
        result["assets"].append(meta)
        html = raw.decode()
        assert meta["status"] == 200, meta
        entry = re.search(r'<script type="module" crossorigin src="([^"]+)"', html).group(1)
        queue = [entry]
        texts = {}
        # Trace only the trade path; match actual hashed filenames, never guess a URL.
        prefixes = ("App-", "MarketPage-", "TradingPage-", "MarketPageLayout-",
                    "ScrollRail-", "client-", "api-", "price-")
        while queue and len(texts) < 12:
            path = queue.pop(0)
            if path in texts:
                continue
            raw, meta = receipt(BASE+path)
            assert meta["status"] == 200, meta
            text = raw.decode()
            texts[path] = text
            (assets_dir / (path.rsplit("/", 1)[1]+".txt")).write_bytes(raw)
            references = sorted(set(re.findall(r'[A-Za-z0-9_-]+\.js', text)))
            meta["referencedJs"] = references
            result["assets"].append(meta)
            for name in references:
                if name.startswith(prefixes):
                    queue.append("/assets/"+name)
        client = next(t for p,t in texts.items() if p.rsplit('/',1)[1].startswith('client-'))
        rail = next(t for p,t in texts.items() if p.rsplit('/',1)[1].startswith('ScrollRail-'))
        layout = next(t for p,t in texts.items() if p.rsplit('/',1)[1].startswith('MarketPageLayout-'))
        result["excerpts"] = {
            "httpImport": excerpt(client, 'import{o as r,u as i}', 0, 100),
            "turnstileEnabled": excerpt(client, 'VITE_TURNSTILE_SITE_KEY:', 50, 500),
            "authExchange": excerpt(client, 'async function VC(', 0, 1100),
            "authCaching": excerpt(client, 'jC=`TURNSTILE_JWT`', 100, 1600),
            "httpWrapper": excerpt(client, 'var tw=[i,r]', 0, 1500),
            "orderContract": excerpt(rail, 'function Rt(e)', 0, 900),
            "responseSchema": excerpt(rail, 'var St=h(', 0, 850),
            "transactionFlow": excerpt(rail, 'Buffer.from(e.transaction', 150, 1100),
            "tradeParams": excerpt(layout, 'function Lt(e,t,n,r)', 0, 1250),
        }
        result["requestContract"] = {
            "endpoint": PROXY+"/order", "method": "GET", "body": None,
            "queryKeys": ["prioritizationFeeLamports", "dynamicComputeUnitLimit",
                          "userPublicKey", "inputMint", "outputMint", "amount", "slippageBps"],
            "frontendDefaults": {"prioritizationFeeLamports": "auto", "dynamicComputeUnitLimit": "true"},
            "authentication": "Authorization: Bearer <anonymous Turnstile-exchanged JWT>",
            "authEndpoint": PROXY+"/auth/token",
            "authMethod": "POST", "authHeaders": {"Content-Type": "application/json", "x-turnstile-token": "<Turnstile token>"},
            "authBody": {"turnstileToken": "<same Turnstile token>"},
            "predictionSpecificFlags": [], "frontendGeneratedOrderNonce": None,
            "userPublicKey": "Frontend requires connected wallet; backend necessity untested behind authentication.",
            "cookies": "No explicit credentials/include or cookie injection in quote wrapper.",
            "originReferer": "Browser-generated; backend requirement unestablished."}
        start = jp.current_start()
        deadline = time.time()+45
        original_rpc = jp.rpc
        def traced_rpc(method, params, deadline):
            assert method in {"getProgramAccounts", "getMultipleAccounts"}
            at = time.time()
            response = original_rpc(method, params, deadline)
            result["rpcReceipts"].append({"method": method, "params": params,
                                          "at": at, "result": response})
            return response
        jp.rpc = traced_rpc
        candidates = jp.describe_candidates(jp.program_markets_for_start(start, deadline), deadline)
        market = jp.select_btc(candidates)
        assert market["cashMint"] == jp.CASH_MINT
        assert market["endTs"]-market["startTs"] == 300
        assert market["startTs"] <= time.time() < market["endTs"], "DISCOVERY_WINDOW_EXPIRED"
        result["discovery"] = {"startTs": start, "market": market, "candidates": candidates,
                               "methods": ["getProgramAccounts", "getMultipleAccounts", "GET token metadata URI"],
                               "recentSignatureDiscovery": False}
        headers = {"Accept": "application/json", "Origin": BASE, "Referer": BASE+"/",
                   "User-Agent": "world-pm-public-observer/frontend-contract-r0"}
        for side, mint in (("YES",market["yesMint"]),("NO",market["noMint"])):
            assert market["startTs"] <= time.time() < market["endTs"], "PROBE_WINDOW_EXPIRED"
            params = {"prioritizationFeeLamports": "auto", "dynamicComputeUnitLimit": "true",
                      "userPublicKey": SIGNER, "inputMint": jp.CASH_MINT, "outputMint": mint,
                      "amount": "1000000", "slippageBps": "200"}
            raw, meta = receipt(PROXY+"/order?"+urllib.parse.urlencode(params), headers)
            result["probes"].append({**meta, "side": side, "parameters": params,
                                      "body": raw.decode("utf-8", "replace")})
        # One missing-token negative control. No challenge solving or fabricated token.
        raw, meta = receipt(PROXY+"/auth/token", {**headers, "Content-Type": "application/json"}, b'{}')
        result["authMissingTokenControl"] = {**meta, "requestBody": {}, "body": raw.decode("utf-8", "replace")}
        blocked = all(p["status"] == 404 and p["bytes"] == 0 for p in result["probes"])
        result["verdict"] = "WORLD_PUBLIC_FILLABLE_QUOTE_PATH_NOT_CURRENTLY_OPEN" if blocked else "NO_RESULT_CURRENT_FRONTEND_CONTRACT_UNRESOLVED"
    except Exception as error:
        result["error"] = f"{type(error).__name__}:{error}"
        result["verdict"] = "NO_RESULT_CURRENT_FRONTEND_CONTRACT_UNRESOLVED"
    result["finishedAt"] = time.time()
    target = output / "result.json"
    target.write_text(json.dumps(result, indent=2, ensure_ascii=False)+"\n", encoding="utf-8")
    print(json.dumps({"verdict": result["verdict"], "error": result.get("error"),
                      "statuses": {p["side"]:p["status"] for p in result["probes"]},
                      "result": str(target)}, indent=2))
    return result


def verify(output):
    result = json.loads((output/"result.json").read_text(encoding="utf-8"))
    texts = {}
    for row in result["assets"]:
        name = "index.html" if row["url"] == BASE else row["url"].rsplit("/",1)[1]+".txt"
        raw = (output/"assets"/name).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == row["sha256"]
        texts[name] = raw.decode()
    for row in result["excerpts"].values():
        assert any(text[row["begin"]:row["begin"]+len(row["text"])] == row["text"] for text in texts.values())
    market = result["discovery"]["market"]
    for row in result["probes"]:
        assert market["startTs"] <= row["at"] < market["endTs"]
        assert row["parameters"]["outputMint"] == market["yesMint" if row["side"] == "YES" else "noMint"]
        assert not any(k.lower() == "authorization" for k in row["requestHeaders"])
    assert {row["method"] for row in result["rpcReceipts"]} == {"getProgramAccounts", "getMultipleAccounts"}
    print("PASS: captured asset hashes, exact source excerpts, live-window probes, read-only discovery methods")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    verify(args.output) if args.verify else run(args.output)

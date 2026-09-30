"""Two read-only current BTC5m /order probes using a normal browser JWT on stdin.

No token persistence, signing, simulation, submission, or recent-signature discovery.
Run as python -m tools.research.world_authenticated_quote_probe_r0 --output <new-dir>.
"""
from __future__ import annotations
import argparse
import base64
import hashlib
import json
import sys
import time
import urllib.parse
from pathlib import Path
from tools.research import world_frontend_quote_contract_r0 as fc
from tools.research import world_pm_jupiter_economic_poc_r0 as jp


def run(output: Path, matched_control: bool = False, user_public_key: str = fc.SIGNER):
    if output.exists():
        raise RuntimeError("EVIDENCE_EXISTS_REFUSE_OVERWRITE")
    number = 0
    for char in user_public_key:
        number = number * 58 + jp.B58.index(char)
    decoded = b"\0" * (len(user_public_key) - len(user_public_key.lstrip("1"))) + number.to_bytes((number.bit_length() + 7) // 8, "big")
    if len(decoded) != 32:
        raise RuntimeError("USER_PUBLIC_KEY_INVALID_LENGTH")
    token = sys.stdin.read().strip()
    parts = token.split(".")
    if len(parts) != 3:
        raise RuntimeError("JWT_FORMAT_INVALID")
    claims = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
    started = time.time()
    if claims.get("exp", 0) <= started:
        raise RuntimeError("JWT_EXPIRED_NO_REQUEST_SENT")
    result = {"schemaVersion": "WORLD_AUTHENTICATED_QUOTE_PROBE_R0", "startedAt": started,
              "NO_TRADE": True, "tokenSource": "User normal World Chrome WebSocket URL",
              "tokenSha256": hashlib.sha256(token.encode()).hexdigest(),
              "tokenClaimsUnverified": {k: claims.get(k) for k in ("iat", "exp")},
              "claimsAreNotSignatureVerification": True, "rpcReceipts": [], "probes": [],
              "boundary": {"signing": False, "simulation": False, "submission": False,
                           "broadcast": False, "privateKey": False, "recentSignatureDiscovery": False}}
    original = jp.rpc
    def traced(method, params, deadline):
        assert method in {"getProgramAccounts", "getMultipleAccounts"}
        at = time.time()
        response = original(method, params, deadline)
        result["rpcReceipts"].append({"method": method, "params": params, "at": at, "result": response})
        return response
    jp.rpc = traced
    try:
        start = jp.current_start()
        deadline = time.time() + 45
        candidates = jp.describe_candidates(jp.program_markets_for_start(start, deadline), deadline)
        market = jp.select_btc(candidates)
        assert market["cashMint"] == jp.CASH_MINT and market["endTs"]-market["startTs"] == 300
        result["discovery"] = {"market": market, "candidates": candidates}
        headers = {"Accept": "application/json", "Origin": fc.BASE, "Referer": fc.BASE + "/",
                   "User-Agent": "world-pm-public-observer/authenticated-quote-r0",
                   "Authorization": "Bearer " + token}
        for side, mint in (("YES", market["yesMint"]), ("NO", market["noMint"])):
            assert market["startTs"] <= time.time() < market["endTs"], "CURRENT_WINDOW_EXPIRED"
            assert time.time() < claims["exp"], "JWT_EXPIRED"
            params = {"prioritizationFeeLamports": "auto", "dynamicComputeUnitLimit": "true",
                      "userPublicKey": user_public_key, "inputMint": jp.CASH_MINT, "outputMint": mint,
                      "amount": "1000000", "slippageBps": "200"}
            raw, meta = fc.receipt(fc.PROXY + "/order?" + urllib.parse.urlencode(params), headers)
            meta["requestHeaders"] = {k: ("Bearer <REDACTED>" if k.lower() == "authorization" else v)
                                      for k, v in meta["requestHeaders"].items()}
            body = raw.decode("utf-8", "replace")
            if token in body:
                raise RuntimeError("TOKEN_ECHO_REFUSE_PERSISTENCE")
            result["probes"].append({**meta, "side": side, "parameters": params, "body": body})
        if matched_control:
            assert market["startTs"] <= time.time() < market["endTs"], "CURRENT_WINDOW_EXPIRED"
            control_headers = {k:v for k,v in headers.items() if k != "Authorization"}
            params = result["probes"][0]["parameters"]
            raw, meta = fc.receipt(fc.PROXY + "/order?" + urllib.parse.urlencode(params), control_headers)
            result["sameWindowNoJwtControl"] = {**meta, "parameters": params, "body": raw.decode("utf-8", "replace")}
        result["verdict"] = "NO_RESULT_AUTHENTICATED_ORDER_RESPONSE_REQUIRES_REVIEW"
    except Exception as error:
        # Do not serialize exception text which could contain authorization material.
        result["errorType"] = type(error).__name__
        result["verdict"] = "NO_RESULT_AUTHENTICATED_QUOTE_PROBE_INCOMPLETE"
    finally:
        jp.rpc = original
    result["finishedAt"] = time.time()
    output.mkdir(parents=True)
    (output / "result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False)+"\n", encoding="utf-8")
    print(json.dumps({"verdict": result["verdict"], "statuses": {p["side"]: p["status"] for p in result["probes"]}, "errorType": result.get("errorType")}))


def verify(output: Path):
    result = json.loads((output / "result.json").read_text(encoding="utf-8"))
    market = result["discovery"]["market"]
    assert result["NO_TRADE"] and not any(result["boundary"].values())
    assert market["cashMint"] == jp.CASH_MINT
    assert market["endTs"] - market["startTs"] == 300
    assert len(result["probes"]) == 2
    assert {r["method"] for r in result["rpcReceipts"]} == {"getProgramAccounts", "getMultipleAccounts"}
    rows = result["probes"] + [result["sameWindowNoJwtControl"]]
    for row in rows:
        assert market["startTs"] <= row["at"] < market["endTs"]
        assert row["at"] < result["tokenClaimsUnverified"]["exp"]
        assert row["url"].startswith(fc.PROXY + "/order?") and row["method"] == "GET"
        body = row["body"].encode()
        assert len(body) == row["bytes"] and hashlib.sha256(body).hexdigest() == row["sha256"]
    for row in result["probes"]:
        assert row["parameters"]["outputMint"] == market["yesMint" if row["side"] == "YES" else "noMint"]
        assert [v for k,v in row["requestHeaders"].items() if k.lower() == "authorization"] == ["Bearer <REDACTED>"]
    control = result["sameWindowNoJwtControl"]
    assert control["parameters"] == result["probes"][0]["parameters"]
    assert not any(k.lower() == "authorization" for k in control["requestHeaders"])
    print("PASS: body hashes, current-window binding, redacted bearer, matched no-JWT control")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--matched-control", action="store_true")
    parser.add_argument("--user-public-key", default=fc.SIGNER)
    args = parser.parse_args()
    verify(args.output) if args.verify else run(args.output, args.matched_control, args.user_public_key)

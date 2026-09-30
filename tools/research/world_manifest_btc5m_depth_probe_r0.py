"""Bounded read-only Manifest depth probe for one current World BTC5m window."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from tools.research import world_pm_jupiter_economic_poc_r0 as jp
from tools.research import world_polymarket_btc5m_leadlag_probe_r1 as wp

MANIFEST = "MNFSTqtC93rEfYHB6hF82sKdZpUDFWkViLByLd1k1Ms"
USER_AGENT = "world-manifest-btc5m-depth-probe-r0/1.0"


def rpc_receipt(endpoint: str, method: str, params: list[Any], deadline: float) -> dict[str, Any]:
    started = time.time()
    req_body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    req = Request(endpoint, data=json.dumps(req_body, separators=(",", ":")).encode(),
                  headers={"Accept": "application/json", "Content-Type": "application/json",
                           "User-Agent": USER_AGENT}, method="POST")
    try:
        with urlopen(req, timeout=max(1.0, min(10.0, deadline - started))) as response:
            raw = response.read()
            headers = dict(response.headers.items())
            status = response.status
        payload = json.loads(raw.decode())
        return {"endpoint": endpoint, "method": method, "params": params, "startedAt": started,
                "endedAt": time.time(), "httpStatus": status, "responseHeaders": headers,
                "responseSha256": hashlib.sha256(raw).hexdigest(), "response": payload,
                "responseCanonicalSha256": hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}
    except Exception as exc:
        return {"endpoint": endpoint, "method": method, "params": params, "startedAt": started,
                "endedAt": time.time(), "error": f"{type(exc).__name__}:{exc}"}


def main() -> None:
    started = time.time()
    start = (int(started) // 300) * 300
    deadline = started + 90
    output = Path("evidence/world-manifest-btc5m-depth-20260930/result.json")
    if output.exists():
        raise RuntimeError("EVIDENCE_EXISTS_REFUSE_OVERWRITE_OR_NEW_PROBE")
    candidates = jp.describe_candidates(jp.program_markets_for_start(start, deadline), deadline)
    btc = jp.select_btc(candidates)
    out = {"schemaVersion": "WORLD_MANIFEST_BTC5M_DEPTH_PROBE_R0",
        "rpcCanonicalHashMethod": "sha256(json.dumps(receipt.response, sort_keys=True, separators=(',', ':')).encode('utf-8'))",
        "mode": "PUBLIC_READ_ONLY_NO_TRADE", "noTrade": True, "startedAt": started,
        "startTs": start, "worldMarket": btc, "candidateCount": len(candidates),
        "manifestProgram": MANIFEST, "rpcReceipts": [], "sides": {}}
    endpoint = wp.DEFAULT_SOLANA_RPC
    for side, base, quote in (("yes", btc["yesMint"], btc["cashMint"]),
                              ("no", btc["noMint"], btc["cashMint"]),
                              ("yesReverse", btc["cashMint"], btc["yesMint"]),
                              ("noReverse", btc["cashMint"], btc["noMint"])):
        params = [MANIFEST, {"encoding": "base64", "dataSlice": {"offset": 0, "length": 0},
                             "filters": [{"memcmp": {"offset": 16, "bytes": base}},
                                         {"memcmp": {"offset": 48, "bytes": quote}}],
                             "commitment": "confirmed"}]
        rec = rpc_receipt(endpoint, "getProgramAccounts", params, deadline)
        out["rpcReceipts"].append(rec)
        if rec.get("error") or rec.get("httpStatus") != 200:
            status, rows = "NO_RESULT_RPC_ERROR", []
        else:
            result = (rec.get("response") or {}).get("result")
            if not isinstance(result, list):
                status, rows = "NO_RESULT_RPC_ERROR", []
            else:
                rows = result
                status = "NO_EXISTING_BOOK" if not rows else "NO_RESULT_DEPTH_PARSER_NOT_VALIDATED"
        out["sides"][side] = {"baseMint": base, "quoteMint": quote, "bookCount": len(rows),
                              "status": status, "books": rows}
    out["finishedAt"] = time.time()
    out["offlineVerification"] = {"ownerExpected": MANIFEST, "cashMint": btc["cashMint"],
                                   "yesMint": btc["yesMint"], "noMint": btc["noMint"],
                                   "currentWindowStartTs": start, "currentWindowEndTs": start + 300}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: out["sides"][key]["status"] for key in ("yes", "no", "yesReverse", "noReverse")}))


if __name__ == "__main__":
    main()

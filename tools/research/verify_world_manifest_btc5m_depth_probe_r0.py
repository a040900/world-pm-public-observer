"""Offline integrity check for the saved Manifest depth probe evidence."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


def main(path: str) -> None:
    p = Path(path)
    doc = json.loads(p.read_text(encoding="utf-8"))
    assert doc["mode"] == "PUBLIC_READ_ONLY_NO_TRADE"
    assert doc["noTrade"] is True
    assert doc["manifestProgram"] == "MNFSTqtC93rEfYHB6hF82sKdZpUDFWkViLByLd1k1Ms"
    market = doc["worldMarket"]
    assert market["cashMint"] == doc["offlineVerification"]["cashMint"]
    assert market["yesMint"] == doc["offlineVerification"]["yesMint"]
    assert market["noMint"] == doc["offlineVerification"]["noMint"]
    assert market["startTs"] == doc["offlineVerification"]["currentWindowStartTs"]
    assert market["endTs"] == doc["offlineVerification"]["currentWindowEndTs"]
    assert len(doc["rpcReceipts"]) == 4
    assert doc["rpcCanonicalHashMethod"].startswith("sha256(json.dumps")
    expected = {
        "yes": (market["yesMint"], market["cashMint"]),
        "no": (market["noMint"], market["cashMint"]),
        "yesReverse": (market["cashMint"], market["yesMint"]),
        "noReverse": (market["cashMint"], market["noMint"]),
    }
    for receipt, side in zip(doc["rpcReceipts"], expected):
        assert receipt["method"] == "getProgramAccounts"
        assert receipt["httpStatus"] == 200 and "error" not in receipt
        assert isinstance(receipt["response"].get("result"), list)
        assert receipt["response"]["result"] == []
        assert "error" not in receipt["response"]
        assert market["startTs"] <= receipt["startedAt"] <= receipt["endedAt"]
        assert receipt["endedAt"] <= market["endTs"]
        assert receipt["endpoint"] == "https://api.mainnet-beta.solana.com"
        assert receipt["params"][0] == doc["manifestProgram"]
        filters = receipt["params"][1]["filters"]
        assert (filters[0]["memcmp"]["bytes"], filters[1]["memcmp"]["bytes"]) == expected[side]
        assert (filters[0]["memcmp"]["offset"], filters[1]["memcmp"]["offset"]) == (16, 48)
        canonical = hashlib.sha256(json.dumps(receipt["response"], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        assert receipt["responseCanonicalSha256"] == canonical
        assert receipt.get("responseSha256")
    for side in expected:
        assert doc["sides"][side]["status"] == "NO_EXISTING_BOOK"
        assert doc["sides"][side]["bookCount"] == 0
        assert doc["sides"][side]["baseMint"] == expected[side][0]
        assert doc["sides"][side]["quoteMint"] == expected[side][1]
    print(json.dumps({"ok": True, "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                      "canonicalResponseSha256": [x["responseCanonicalSha256"] for x in doc["rpcReceipts"]],
                      "window": [market["startTs"], market["endTs"]],
                      "yes": doc["sides"]["yes"]["status"], "no": doc["sides"]["no"]["status"]}))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "evidence/world-manifest-btc5m-depth-20260930/result.json")

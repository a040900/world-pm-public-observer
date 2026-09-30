"""Offline/read-only route-construction analysis for World BTC5m direct-CASH DFlow/Bison swaps.

R3-authorized scope:
- no simulateTransaction
- no signing
- no sendTransaction
- no order/quote endpoint
- no capital

It compares two known-successful direct-CASH World BTC5m transactions from different
markets, decodes DFlow Action::D, classifies the swap account roster from public/on-chain
state, and measures how much of the second route can be reconstructed by role/template
rather than by copying its addresses.
"""
from __future__ import annotations

import argparse
import base64
import json
import struct
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from tools.research import direct_maker_access_poc_r0 as dma
from tools.research import world_pm_jupiter_economic_poc_r0 as jp
from tools.research import world_polymarket_btc5m_leadlag_probe_r1 as wp

DFLOW = dma.DFLOW
BISON = dma.BISON
PREDICT = dma.PREDICT
CASH = jp.CASH_MINT
TOKEN2022 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
TOKEN = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
ATA = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
SYSTEM = "11111111111111111111111111111111"
SWAP_DISC = bytes.fromhex("f8c69e91e17587c8")
SPLIT_DISC = bytes.fromhex("7cbd1b2bd8289342")
MERGE_DISC = bytes.fromhex("948dec2fae7e456f")
BISON_MAGIC = b"PREDMKT\x00"
ACTION_D_INDEX = 57
A_NAMES = {0: "B", 1: "J"}

FIXED_DFLOW_ROLES = [
    "DFLOW_FIXED_TOKEN_PROGRAM",
    "DFLOW_FIXED_ASSOCIATED_TOKEN_PROGRAM",
    "DFLOW_FIXED_SYSTEM_PROGRAM",
    "DFLOW_FIXED_USER_TOKEN_AUTHORITY",
    "DFLOW_FIXED_EVENT_AUTHORITY",
    "DFLOW_FIXED_PROGRAM",
]

DEFAULT_SIGNATURES = [
    "24hH4oRM1qLAx7LPKkjXMkVt27kTks9mihqLiFjbCxj1NA5SoSNaib6WCMAowSZyMXoAWu3nDXPrM39RShsqGV5P",
    "fu7ULDFgcqfMPxUvtQRFTXfB6iGWKU19Q6B2yHErF9PFRT3RoP7dA1vwan9yK14Dk98t8QgQjmRMT2Pkn7EViCF",
]


@dataclass
class Cursor:
    raw: bytes
    pos: int = 0

    def take(self, n: int) -> bytes:
        if self.pos + n > len(self.raw):
            raise ValueError(f"BORSH_TRUNCATED:{self.pos}:{n}:{len(self.raw)}")
        out = self.raw[self.pos:self.pos+n]
        self.pos += n
        return out

    def u8(self) -> int:
        return self.take(1)[0]

    def u16(self) -> int:
        return struct.unpack("<H", self.take(2))[0]

    def u32(self) -> int:
        return struct.unpack("<I", self.take(4))[0]

    def u64(self) -> int:
        return struct.unpack("<Q", self.take(8))[0]


def rpc(method: str, params: list[Any], deadline: float) -> Any:
    return wp._rpc(wp.DEFAULT_SOLANA_RPC, method, params, deadline=deadline)


def tx(sig: str, deadline: float) -> Mapping[str, Any]:
    value = rpc(
        "getTransaction",
        [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 1, "commitment": "confirmed"}],
        deadline,
    )
    if not isinstance(value, Mapping):
        raise RuntimeError(f"TX_NOT_FOUND:{sig}")
    return value


def account_keys(t: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = (((t.get("transaction") or {}).get("message") or {}).get("accountKeys") or [])
    out = []
    for row in rows:
        if isinstance(row, Mapping):
            out.append({
                "pubkey": str(row.get("pubkey")),
                "signer": bool(row.get("signer")),
                "writable": bool(row.get("writable")),
                "source": row.get("source"),
            })
        else:
            out.append({"pubkey": str(row), "signer": False, "writable": False, "source": None})
    return out


def top_instructions(t: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [x for x in (((t.get("transaction") or {}).get("message") or {}).get("instructions") or []) if isinstance(x, Mapping)]


def inner_instructions(t: Mapping[str, Any]) -> list[tuple[int, Mapping[str, Any]]]:
    out = []
    for group in (t.get("meta") or {}).get("innerInstructions") or []:
        if not isinstance(group, Mapping):
            continue
        top = int(group.get("index") or 0)
        for ix in group.get("instructions") or []:
            if isinstance(ix, Mapping):
                out.append((top, ix))
    return out


def find_dflow_swap(t: Mapping[str, Any]) -> tuple[int, Mapping[str, Any], bytes]:
    found = []
    for pos, ix in enumerate(top_instructions(t)):
        if str(ix.get("programId") or "") != DFLOW:
            continue
        try:
            raw = dma.b58decode(str(ix.get("data") or ""))
        except Exception:
            continue
        if raw.startswith(SWAP_DISC):
            found.append((pos, ix, raw))
    if len(found) != 1:
        raise RuntimeError(f"DFLOW_SWAP_NOT_UNIQUE:{len(found)}")
    return found[0]


def decode_swap(raw: bytes) -> dict[str, Any]:
    if not raw.startswith(SWAP_DISC):
        raise ValueError("NOT_DFLOW_SWAP")
    c = Cursor(raw, 8)
    n_actions = c.u32()
    actions = []
    for ai in range(n_actions):
        variant = c.u8()
        if variant != ACTION_D_INDEX:
            raise ValueError(f"UNSUPPORTED_ACTION_VARIANT:{ai}:{variant}:at={c.pos-1}")
        n_a = c.u32()
        a = []
        for i in range(n_a):
            av = c.u8()
            padding = c.u8()
            a.append({"index": i, "variantIndex": av, "variantName": A_NAMES.get(av, f"UNKNOWN_{av}"), "padding": padding})
        amount = c.u64()
        flags = c.u8()
        pfs = c.u16()
        dfs = c.u16()
        actions.append({
            "variantIndex": variant,
            "variantName": "D",
            "a": a,
            "aVectorLength": n_a,
            "amount": amount,
            "orchestratorFlags": flags,
            "pfs": pfs,
            "dfs": dfs,
        })
    quoted = c.u64()
    slippage = c.u16()
    platform = c.u16()
    if c.pos != len(raw):
        raise ValueError(f"UNPARSED_SWAP_TAIL:{c.pos}:{len(raw)}:{raw[c.pos:].hex()}")
    return {
        "rawHex": raw.hex(),
        "rawLength": len(raw),
        "actions": actions,
        "quotedOutAmount": quoted,
        "slippageBps": slippage,
        "platformFeeBps": platform,
    }


def encode_swap(decoded: Mapping[str, Any]) -> bytes:
    out = bytearray(SWAP_DISC)
    actions = list(decoded["actions"])
    out += struct.pack("<I", len(actions))
    for action in actions:
        if int(action["variantIndex"]) != ACTION_D_INDEX:
            raise ValueError("ENCODER_ONLY_SUPPORTS_D")
        out.append(ACTION_D_INDEX)
        avec = list(action["a"])
        out += struct.pack("<I", len(avec))
        for row in avec:
            out.append(int(row["variantIndex"]))
            out.append(int(row["padding"]))
        out += struct.pack("<Q", int(action["amount"]))
        out.append(int(action["orchestratorFlags"]))
        out += struct.pack("<H", int(action["pfs"]))
        out += struct.pack("<H", int(action["dfs"]))
    out += struct.pack("<Q", int(decoded["quotedOutAmount"]))
    out += struct.pack("<H", int(decoded["slippageBps"]))
    out += struct.pack("<H", int(decoded["platformFeeBps"]))
    return bytes(out)


def token_deltas(t: Mapping[str, Any]) -> list[dict[str, Any]]:
    keys = account_keys(t)
    def rows(field: str) -> dict[tuple[int, str, str], int]:
        out = {}
        for row in (t.get("meta") or {}).get(field) or []:
            if not isinstance(row, Mapping):
                continue
            try:
                k = (int(row["accountIndex"]), str(row.get("mint") or ""), str(row.get("owner") or ""))
                out[k] = int(((row.get("uiTokenAmount") or {}).get("amount")))
            except Exception:
                continue
        return out
    pre, post = rows("preTokenBalances"), rows("postTokenBalances")
    out = []
    for k in sorted(set(pre) | set(post)):
        idx, mint, owner = k
        before, after = pre.get(k, 0), post.get(k, 0)
        out.append({
            "accountIndex": idx,
            "account": keys[idx]["pubkey"] if idx < len(keys) else None,
            "mint": mint,
            "owner": owner,
            "preAtoms": before,
            "postAtoms": after,
            "deltaAtoms": after-before,
        })
    return out


def account_infos(pubkeys: list[str], deadline: float) -> dict[str, dict[str, Any] | None]:
    unique = list(dict.fromkeys(pubkeys))
    value = rpc("getMultipleAccounts", [unique, {"encoding": "base64", "commitment": "confirmed"}], deadline)
    vals = value.get("value") if isinstance(value, Mapping) else None
    if not isinstance(vals, list):
        return {k: None for k in unique}
    out = {}
    for key, acc in zip(unique, vals):
        if not isinstance(acc, Mapping):
            out[key] = None
            continue
        data = acc.get("data")
        raw = b""
        if isinstance(data, list) and data:
            try:
                raw = base64.b64decode(str(data[0]))
            except Exception:
                raw = b""
        out[key] = {
            "ownerProgram": str(acc.get("owner") or ""),
            "lamports": acc.get("lamports"),
            "executable": bool(acc.get("executable")),
            "dataLength": len(raw),
            "_raw": raw,
        }
    return out


def pub(raw: bytes) -> str:
    return jp.b58encode(raw)


def bison_pool_fields(key: str, info: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(info, Mapping):
        return None
    raw = info.get("_raw")
    if info.get("ownerProgram") != BISON or not isinstance(raw, (bytes, bytearray)) or len(raw) != 2048 or bytes(raw[:8]) != BISON_MAGIC:
        return None
    return {
        "pool": key,
        "market": pub(bytes(raw[48:80])),
        "yesMint": pub(bytes(raw[80:112])),
        "noMint": pub(bytes(raw[112:144])),
        "poolYesToken": pub(bytes(raw[144:176])),
        "poolNoToken": pub(bytes(raw[176:208])),
        "poolCashToken": pub(bytes(raw[208:240])),
        "counter240": struct.unpack_from("<Q", raw, 240)[0],
        "yesInventory": struct.unpack_from("<Q", raw, 248)[0],
        "noInventory": struct.unpack_from("<Q", raw, 256)[0],
        "slot264": struct.unpack_from("<Q", raw, 264)[0],
        "slot296": struct.unpack_from("<Q", raw, 296)[0],
    }


def predict_roles(t: Mapping[str, Any]) -> dict[str, set[str]]:
    roles: dict[str, set[str]] = {}
    role_names = {
        "split": ["SIGNER_OR_MAKER", "MARKET", "CASH_MINT", "YES_MINT", "NO_MINT", "CASH_SOURCE", "MARKET_CASH_VAULT", "YES_DEST", "NO_DEST", "TOKEN2022_A", "TOKEN2022_B"],
        "merge": ["SIGNER_OR_MAKER", "MARKET", "CASH_MINT", "YES_MINT", "NO_MINT", "CASH_DEST", "MARKET_CASH_VAULT", "YES_SOURCE", "NO_SOURCE", "TOKEN2022_A", "TOKEN2022_B"],
    }
    for _, ix in inner_instructions(t):
        if str(ix.get("programId") or "") != PREDICT:
            continue
        try:
            raw = dma.b58decode(str(ix.get("data") or ""))
        except Exception:
            continue
        kind = "split" if raw.startswith(SPLIT_DISC) else "merge" if raw.startswith(MERGE_DISC) else None
        if not kind:
            continue
        for i, key in enumerate(ix.get("accounts") or []):
            role = role_names[kind][i] if i < len(role_names[kind]) else f"ACCOUNT_{i}"
            roles.setdefault(str(key), set()).add(f"PREDICT_{kind.upper()}_{role}")
    return roles


def bison_roles(t: Mapping[str, Any]) -> dict[str, set[str]]:
    roles: dict[str, set[str]] = {}
    for _, ix in inner_instructions(t):
        if str(ix.get("programId") or "") != BISON:
            continue
        for i, key in enumerate(ix.get("accounts") or []):
            roles.setdefault(str(key), set()).add(f"BISON_CPI_{i:02d}")
    return roles


def classify_roster(ix: Mapping[str, Any], t: Mapping[str, Any], infos: dict[str, dict[str, Any] | None]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    accounts = [str(x) for x in (ix.get("accounts") or [])]
    p_roles = predict_roles(t)
    b_roles = bison_roles(t)
    pools = [p for k in accounts if (p := bison_pool_fields(k, infos.get(k))) is not None]
    pool = pools[0] if len(pools) == 1 else None
    user = next((x["pubkey"] for x in account_keys(t) if x["signer"]), None)
    deltas = token_deltas(t)
    delta_by_account = {str(x.get("account")): x for x in deltas if x.get("account")}

    derived_roles: dict[str, set[str]] = {}
    if pool:
        for role, key in [
            ("BISON_POOL", pool["pool"]), ("MARKET", pool["market"]), ("YES_MINT", pool["yesMint"]),
            ("NO_MINT", pool["noMint"]), ("BISON_POOL_YES_TOKEN", pool["poolYesToken"]),
            ("BISON_POOL_NO_TOKEN", pool["poolNoToken"]), ("BISON_POOL_CASH_TOKEN", pool["poolCashToken"]),
        ]:
            derived_roles.setdefault(key, set()).add(role)

    for key, roleset in p_roles.items():
        derived_roles.setdefault(key, set()).update(roleset)
    for key, roleset in b_roles.items():
        derived_roles.setdefault(key, set()).update(roleset)

    static = {
        TOKEN: "TOKEN_PROGRAM", TOKEN2022: "TOKEN2022_PROGRAM", ATA: "ASSOCIATED_TOKEN_PROGRAM",
        SYSTEM: "SYSTEM_PROGRAM", DFLOW: "DFLOW_PROGRAM", BISON: "BISON_PROGRAM", PREDICT: "PREDICT_PROGRAM",
        CASH: "CASH_MINT",
    }

    rows = []
    for pos, key in enumerate(accounts):
        info = infos.get(key)
        owner = info.get("ownerProgram") if isinstance(info, Mapping) else None
        roles = set(derived_roles.get(key, set()))
        if key in static:
            roles.add(static[key])
        if key == user:
            roles.add("USER_WALLET")
        if pos < len(FIXED_DFLOW_ROLES):
            roles.add(FIXED_DFLOW_ROLES[pos])
        d = delta_by_account.get(key)
        if d:
            roles.add(f"TOKEN_DELTA:{d['mint']}:{d['deltaAtoms']}")

        if pos < 6 or key in static:
            source = "DETERMINISTIC_STATIC" if key != user else "USER_DERIVED"
        elif "USER_WALLET" in roles:
            source = "USER_DERIVED"
        elif any(r.startswith("TOKEN_DELTA:") for r in roles) and d and d.get("owner") == user:
            source = "USER_DERIVED_TOKEN_ACCOUNT"
        elif any(r in roles for r in {"BISON_POOL","MARKET","YES_MINT","NO_MINT","BISON_POOL_YES_TOKEN","BISON_POOL_NO_TOKEN","BISON_POOL_CASH_TOKEN","CASH_MINT"}):
            source = "MARKET_DERIVED_PUBLIC_STATE"
        elif any(r.startswith("PREDICT_SPLIT_MARKET_CASH_VAULT") or r.startswith("PREDICT_MERGE_MARKET_CASH_VAULT") for r in roles):
            source = "MARKET_DERIVED_PUBLIC_STATE"
        elif key in p_roles:
            source = "MARKET_OR_USER_DERIVED_FROM_CPI_ROLE"
        elif key in b_roles:
            source = "BISON_CPI_ROLE_OBSERVED_ONLY"
        elif owner in {DFLOW, BISON, PREDICT}:
            source = "PROGRAM_STATE_OBSERVED_ONLY"
        elif info is None:
            source = "CLOSED_OR_MISSING_UNKNOWN"
        else:
            source = "UNKNOWN_PUBLIC_ACCOUNT"

        rows.append({
            "position": pos,
            "region": "fixed" if pos < 6 else "remaining",
            "pubkey": key,
            "ownerProgram": owner,
            "dataLength": info.get("dataLength") if isinstance(info, Mapping) else None,
            "executable": info.get("executable") if isinstance(info, Mapping) else None,
            "roles": sorted(roles),
            "sourceClass": source,
        })
    return rows, {"user": user, "bisonPool": pool, "poolCountInRoster": len(pools)}


def transaction_analysis(sig: str, deadline: float) -> dict[str, Any]:
    t = tx(sig, deadline)
    pos, ix, raw = find_dflow_swap(t)
    decoded = decode_swap(raw)
    reencoded = encode_swap(decoded)
    accounts = [str(x) for x in (ix.get("accounts") or [])]
    infos = account_infos(accounts, deadline)
    roster, context = classify_roster(ix, t, infos)

    deltas = token_deltas(t)
    user = context["user"]
    user_deltas = [x for x in deltas if x.get("owner") == user]
    user_cash = sum(int(x["deltaAtoms"]) for x in user_deltas if x["mint"] == CASH)
    positive_outcomes = [x for x in user_deltas if x["mint"] != CASH and int(x["deltaAtoms"]) > 0]
    output = max(positive_outcomes, key=lambda x: int(x["deltaAtoms"]), default=None)

    action = decoded["actions"][0] if len(decoded["actions"]) == 1 else None
    field_source = {
        "actionVariant": "ROUTE_TEMPLATE_OBSERVED_ONLY",
        "aVector": "ROUTE_TEMPLATE_OBSERVED_ONLY",
        "amount": "USER_DERIVED_IF_MATCHES_INPUT",
        "orchestratorFlags": "ROUTE_TEMPLATE_OR_ROUTER_DERIVED",
        "pfs": "QUOTE_OR_ROUTER_DERIVED_UNLESS_PUBLIC_RULE_FOUND",
        "dfs": "QUOTE_OR_ROUTER_DERIVED_UNLESS_PUBLIC_RULE_FOUND",
        "quotedOutAmount": "QUOTE_DERIVED_NOT_ONCHAIN_STATE",
        "slippageBps": "USER_POLICY_OR_ROUTER_REQUEST",
        "platformFeeBps": "ROUTER_REQUEST_CONFIG",
    }
    relationships = {}
    if action:
        relationships["actionAmountEqualsUserCashSpentAtoms"] = int(action["amount"]) == max(0, -user_cash)
    if output:
        relationships["quotedOutVsObservedOutputAtoms"] = {
            "quotedOutAmount": int(decoded["quotedOutAmount"]),
            "observedOutputAtoms": int(output["deltaAtoms"]),
            "ratioQuotedToObserved": int(decoded["quotedOutAmount"]) / int(output["deltaAtoms"]) if int(output["deltaAtoms"]) else None,
        }

    clean_infos = {}
    for k, v in infos.items():
        if not isinstance(v, Mapping):
            clean_infos[k] = None
        else:
            clean_infos[k] = {kk: vv for kk, vv in v.items() if kk != "_raw"}

    return {
        "signature": sig,
        "slot": t.get("slot"),
        "blockTime": t.get("blockTime"),
        "dflowTopLevelPosition": pos,
        "dflowAccountEntryCount": len(accounts),
        "dflowUniqueAccountCount": len(set(accounts)),
        "swap": decoded,
        "swapRoundTripByteExact": reencoded == raw,
        "swapFieldSourceAssessment": field_source,
        "user": user,
        "userTokenDeltas": user_deltas,
        "userCashDeltaAtoms": user_cash,
        "primaryPositiveOutput": output,
        "relationships": relationships,
        "routeContext": context,
        "roster": roster,
        "accountInfoSummary": clean_infos,
    }


def normalized_role(row: Mapping[str, Any]) -> str:
    roles = list(row.get("roles") or [])
    priority = [
        "DFLOW_FIXED_", "BISON_POOL", "MARKET", "YES_MINT", "NO_MINT", "CASH_MINT",
        "BISON_POOL_YES_TOKEN", "BISON_POOL_NO_TOKEN", "BISON_POOL_CASH_TOKEN",
        "PREDICT_SPLIT_", "PREDICT_MERGE_", "BISON_CPI_", "USER_WALLET",
    ]
    for prefix in priority:
        hit = next((r for r in roles if r.startswith(prefix)), None)
        if hit:
            return hit
    return str(row.get("sourceClass"))


def compare(a: Mapping[str, Any], b: Mapping[str, Any]) -> dict[str, Any]:
    sa, sb = a["swap"], b["swap"]
    aa, ab = sa["actions"][0], sb["actions"][0]
    field_compare = {
        "actionVariantSame": aa["variantIndex"] == ab["variantIndex"],
        "aVectorSame": [(x["variantIndex"],x["padding"]) for x in aa["a"]] == [(x["variantIndex"],x["padding"]) for x in ab["a"]],
        "aVectorLengthA": aa["aVectorLength"],
        "aVectorLengthB": ab["aVectorLength"],
        "orchestratorFlagsSame": aa["orchestratorFlags"] == ab["orchestratorFlags"],
        "pfsSame": aa["pfs"] == ab["pfs"],
        "dfsSame": aa["dfs"] == ab["dfs"],
        "slippageBpsSame": sa["slippageBps"] == sb["slippageBps"],
        "platformFeeBpsSame": sa["platformFeeBps"] == sb["platformFeeBps"],
        "amountA": aa["amount"], "amountB": ab["amount"],
        "quotedOutA": sa["quotedOutAmount"], "quotedOutB": sb["quotedOutAmount"],
    }
    n = max(len(a["roster"]), len(b["roster"]))
    positions = []
    reproducible = 0
    blockers = []
    accepted = {
        "DETERMINISTIC_STATIC", "USER_DERIVED", "USER_DERIVED_TOKEN_ACCOUNT",
        "MARKET_DERIVED_PUBLIC_STATE", "MARKET_OR_USER_DERIVED_FROM_CPI_ROLE",
    }
    for i in range(n):
        ra = a["roster"][i] if i < len(a["roster"]) else None
        rb = b["roster"][i] if i < len(b["roster"]) else None
        row = {
            "position": i,
            "aRole": normalized_role(ra) if ra else None,
            "bRole": normalized_role(rb) if rb else None,
            "samePubkey": bool(ra and rb and ra["pubkey"] == rb["pubkey"]),
            "aSourceClass": ra.get("sourceClass") if ra else None,
            "bSourceClass": rb.get("sourceClass") if rb else None,
        }
        row["roleShapeSame"] = row["aRole"] == row["bRole"]
        row["publiclyReconstructableByCurrentClassifier"] = bool(
            ra and rb and row["roleShapeSame"] and
            (row["samePubkey"] or (ra["sourceClass"] in accepted and rb["sourceClass"] in accepted))
        )
        if row["publiclyReconstructableByCurrentClassifier"]:
            reproducible += 1
        else:
            blockers.append(row)
        positions.append(row)

    return {
        "fieldComparison": field_compare,
        "rosterComparison": {
            "positions": positions,
            "positionCount": n,
            "publiclyReconstructablePositionCount": reproducible,
            "unresolvedPositionCount": len(blockers),
            "unresolvedPositions": blockers,
        },
    }


def overall(result: dict[str, Any]) -> dict[str, Any]:
    trades = result["transactions"]
    comp = result["comparison"]
    byte_roundtrip = all(t["swapRoundTripByteExact"] for t in trades)
    amount_matches = all(bool(t["relationships"].get("actionAmountEqualsUserCashSpentAtoms")) for t in trades)
    quote_field_nonpublic = True
    roster_unresolved = int(comp["rosterComparison"]["unresolvedPositionCount"])

    if quote_field_nonpublic or roster_unresolved:
        verdict = "PUBLIC_STATE_INSUFFICIENT_FOR_SAFE_PROSPECTIVE_ROUTE_REPRODUCTION"
    else:
        verdict = "PUBLIC_STATE_ROUTE_REPRODUCTION_PLAUSIBLE"

    return {
        "verdict": verdict,
        "decoderByteExactForAllTrades": byte_roundtrip,
        "actionAmountMatchesUserCashInputForAllTrades": amount_matches,
        "rosterUnresolvedPositions": roster_unresolved,
        "irreducibleOrUnresolvedDependencies": [
            "quoted_out_amount is a quote-derived execution-protection value and is not present in Bison on-chain pool state",
            "Bison reference evidence states no live maker quote is posted on-chain",
        ] + (["one or more remaining-account positions are not yet derivable by the public-state classifier"] if roster_unresolved else []),
        "importantQualification": "This does not prove that a DFlow server is cryptographically required. It shows that a safe prospective builder cannot reproduce the economically relevant route from the currently identified public/on-chain state alone.",
    }


def main(args: argparse.Namespace) -> None:
    deadline = time.time() + args.deadline_seconds
    result: dict[str, Any] = {
        "schemaVersion": "WORLD_BTC5M_DFLOW_BISON_ROUTE_CONSTRUCTION_ANALYSIS_R0",
        "mode": "PUBLIC_READ_ONLY_OFFLINE_ROUTE_ANALYSIS_NO_SIMULATION",
        "researchScore": 0,
        "startedAt": time.time(),
        "signatures": args.signatures,
        "transactions": [],
    }
    for sig in args.signatures:
        result["transactions"].append(transaction_analysis(sig, deadline))
    if len(result["transactions"]) >= 2:
        result["comparison"] = compare(result["transactions"][0], result["transactions"][1])
    else:
        result["comparison"] = {}
    result["summary"] = overall(result) if len(result["transactions"]) >= 2 else {"verdict":"NO_RESULT_NEED_TWO_TRANSACTIONS"}
    result["limitations"] = [
        "No DFlow /order, World router /order, quote endpoint, or simulation endpoint is called.",
        "The DFlow Action::D/O/A names are intentionally opaque in the current public IDL; semantic labels beyond published field names are not invented.",
        "Cross-trade role stability over two transactions is evidence for a route template, not a protocol guarantee.",
        "The analysis separates byte-level reproducibility from economic quote generation.",
    ]
    result["finishedAt"] = time.time()
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)+"\n", encoding="utf-8")
    print(json.dumps(result["summary"], sort_keys=True))


if __name__ == "__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--signatures", nargs="+", default=DEFAULT_SIGNATURES)
    p.add_argument("--deadline-seconds", type=float, default=180)
    p.add_argument("--output", default="artifacts/world-btc5m-dflow-bison-route-construction-analysis-r0.json")
    main(p.parse_args())

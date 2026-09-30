# World BTC5m Self-Build Execution Feasibility R0

Date: 2026-09-30
Repository: a040900/world-pm-public-observer
Status: BOUNDED_READ_ONLY_DIAGNOSTIC_COMPLETE
Research score: 0

## Question

Can the World BTC5m leg be executed without relying on DFlow production /order access, by rebuilding or replacing the execution path from public/on-chain components?

## Current result

Primary finding:

SELF_BUILDABLE_NOT_PROVEN

Supporting qualifiers:
- NO_MAKER_SIGNATURE_BLOCKER_OBSERVED
- ONCHAIN_DISCOVERY_SOLVED
- JUPITER_OUTCOME_ROUTE_UNAVAILABLE_IN_TEST
- MANIFEST_EXISTING_TAKER_LIQUIDITY_ABSENT_IN_TEST
- DFLOW_ROUTE_ASSEMBLY_STILL_UNRESOLVED

This is not an economic-negative conclusion.

## 1. World BTC5m discovery

The prior recent-signature discovery path was fragile and produced false negatives.

A current on-chain alternative was verified using the prediCt program account layout:

- program: prediCtPZCttYMvm2W3PtxmMxLmT1dtN7riU6Cxh6tM
- World market account data size: 320 bytes
- current 5m slot is selected directly by the start timestamp stored at offset 258
- YES mint: offset 72
- NO mint: offset 104
- CASH mint: offset 40

Run 36668515242 found all five current 5m World markets in the slot:
BTC, ETH, SOL, XRP, HYPE.

The BTC market was uniquely identified from Token-2022 metadata and the metadata URI description.

Therefore current World BTC5m market discovery no longer needs recent Split/signature scanning.

## 2. Direct DFlow / Bison transaction evidence

Run 36662476155 scanned recent DFlow transactions with Solana transaction v1 support.

One qualifying World prediction trade was recovered:

signature:
38SYKSL8XhrrDkbb1hgHCpqnA8wvHCakKMCAwYLAE5ZBRE9mSiDupxxuRSM355843wByw4WpfwmGvGqfyvdWb6sX

Observed program path:
- DFlow
- BisonFI Predict
- prediCt

Transaction facts:
- one transaction signature
- one signer
- no Janus/Bison program account was a signer
- no additional maker wallet signature was present

The 72-account DFlow instruction discriminator:
f8c69e91e17587c8

matches DFlow IDL instruction:
swap

DFlow SwapParams contains:
- actions: Vec<Action>
- quoted_out_amount
- slippage_bps
- platform_fee_bps

The DFlow Action enum contains BisonFiSwap.
BisonFiSwapOptions contains:
- amount: u64
- orchestrator_flags: u8 wrapper

The same transaction includes an 18-account BisonFI inner instruction and prediCt CPI.

Interpretation:
there is no observed second private-key/maker-signature requirement at the transaction layer for this sample.

However, that does not prove the route is independently constructible. DFlow may still be responsible for determining:
- the Action sequence
- quoted_out_amount
- slippage protection
- remaining-account ordering
- BisonFI market/account selection
- maker-specific state
- any ephemeral or quote-derived account values

No public BisonFI IDL was identified in the evidence used here.

## 3. Jupiter keyless replacement test

Run:
36668515242

Method:
- discover the current World BTC5m market directly on-chain
- request USD/CASH-sized Jupiter Lite API quotes from CASH into the current World YES and NO outcome mints
- use CASH->Solana USDC as the control

Results over three snapshots in the same current window:
- World YES route successes: 0/3
- World NO route successes: 0/3
- Jupiter returned HTTP 400 for both outcome mints
- CASH -> Solana USDC control succeeded
- control route used AlphaQ

Therefore the observed failure is outcome-route-specific, not a general Jupiter API outage.

Conclusion:
Jupiter Lite API did not provide a usable taker replacement route for these current World BTC5m outcome mints in this test.

## 4. Manifest public-book replacement test

Run:
36668636045

The current BTC5m market was discovered on-chain without signature scanning.

Current tested market:
Gma8LyXmX3V7Zo3kiATQmULXjEhM7WFUKTyBeB5aVVng

Current YES mint:
GJSCGYHx5swda2uzLfT6QUxfvqxSaMBqP7YcNqbPXHL

Current NO mint:
Cj8vzS61ePzD1RSgt7bQzHeqeQ8fNDerMDy8LSdnygD6

Manifest searches:
- YES / CASH books: 0
- NO / CASH books: 0

Therefore there was no existing Manifest taker liquidity for the tested current BTC5m outcome tokens.

This does not invalidate Manifest as infrastructure:
- anyone may create a Manifest market for World outcome tokens
- a user can rest limit orders there
- World routing can use public books when available

But in the tested BTC5m window, Manifest was not a substitute for existing DFlow/Bison taker liquidity.

## 5. Practical interpretation

There are now three distinct paths:

### A. Rebuild the DFlow/Bison taker route

Potential upside:
- preserves existing World market-maker liquidity
- no extra maker signer was observed in the recovered trade

Remaining blocker:
- route/account construction semantics are not yet independently reproduced

Current status:
SELF_BUILDABLE_NOT_PROVEN

### B. Use an alternative public aggregator

Jupiter was tested as the most obvious keyless alternative.

Current status:
NO_ROUTE_OBSERVED_FOR_CURRENT_WORLD_BTC5M_OUTCOME_MINTS

### C. Build/use public Manifest liquidity

Technically self-buildable and permissionless.

Current status for taker arbitrage:
NO_EXISTING_BOOK_OBSERVED_IN_TESTED_WINDOW

Potential separate research direction:
self-operated maker liquidity / cross-venue hedging.

That is a different strategy from taking existing World market-maker liquidity.

## 6. Research decision

Do not treat DFlow API access as the only possible long-term architecture.

But also do not claim it can already be removed.

The next discriminating test for the existing taker route is narrowly defined:

decode/reproduce the final DFlow BisonFiSwap leg for the recovered transaction, preferably from CASH directly, and determine whether all remaining-account identities and instruction fields can be derived from:
- current World market account
- current outcome mint
- public Bison/prediCt state
- public quote semantics
- user wallet/token accounts

If a deterministic read-only transaction builder can reproduce the account list and Bison leg without server-only data:
SELF_BUILDABLE becomes plausible enough for simulation/fork validation.

If one or more required route fields depend on inaccessible DFlow/Bison off-chain state:
the existing Bison taker-liquidity path remains RFQ/router-dependent.

## 7. Important separation from economics

This work does not overturn the existing economic-first result:

EDGE_OBSERVED
+ ECONOMIC_POC_POSITIVE
+ EXECUTION_ACCESS_UNRESOLVED
+ SETTLEMENT_BASIS_RISK_UNRESOLVED

Operational-access findings must remain separate from economic-edge findings.

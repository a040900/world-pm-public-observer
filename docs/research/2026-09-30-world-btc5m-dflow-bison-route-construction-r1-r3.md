# World BTC5m DFlow/Bison Route Construction Analysis R1-R3

Date: 2026-09-30

> NON-AUTHORITATIVE VENUE-SPECIFIC EVIDENCE. Research-state authority lives in `a040900/prediction-market-relative-value/main`.

Mode: PUBLIC_READ_ONLY_OFFLINE_ROUTE_ANALYSIS
NO_TRADE: TRUE
NO_SIMULATION: TRUE

## Question

Can the existing World BTC5m Bison liquidity path be prospectively constructed from public/on-chain state without DFlow `/order` or another server-generated maker quote?

## Evidence inputs

Two known-successful direct-CASH World BTC5m trades from different fresh markets:

1. `24hH4oRM1qLAx7LPKkjXMkVt27kTks9mihqLiFjbCxj1NA5SoSNaib6WCMAowSZyMXoAWu3nDXPrM39RShsqGV5P`
2. `fu7ULDFgcqfMPxUvtQRFTXfB6iGWKU19Q6B2yHErF9PFRT3RoP7dA1vwan9yK14Dk98t8QgQjmRMT2Pkn7EViCF`

Read-only runs:
- `36681849715` — RecordId-aware swap decode and cross-trade template comparison
- `36682450943` — fee/global account-role refinement
- `36682919712` — public account derivability refinement

No quote/order endpoint or transaction simulation was used by these runs.

## DFlow payload

Both transactions decode and re-encode byte-exactly.

Both use the same Action sequence:

`RecordId -> D`

Stable `Action::D` shape across both:
- A-vector length: 1
- A variant: B
- padding: 0
- orchestrator_flags: 177
- pfs: 100
- dfs: 10
- slippage_bps: 800
- platform_fee_bps: 0

The `amount` field exactly equals the user's CASH input atoms in both transactions:
- trade A: 200038
- trade B: 200041

The route shape is therefore substantially templated.

## RecordId

The first action is `Action::RecordId`, not `Action::D`.

Current public DFlow IDL defines RecordId only as:

`id: [u8; 76]`

The two direct-CASH trades have different 76-byte values.

The first 64 bytes are opaque/high-entropy. This record does not classify them as a signature because no public verification/generation semantics were found.

The final 12 bytes contain structured integers. A u32 at offset 64 was approximately 137-138 slots ahead of the transaction slot in the two samples, and the final u32 was 120 in both. The semantics are unresolved.

Search of current public IDL mirrors/decoders found layout support but no public generation rule.

Therefore:

`RECORD_ID_PUBLIC_GENERATION_RULE_NOT_FOUND`

This is a prospective-builder blocker unless the action is optional or independently generatable. This analysis does not prove that it is cryptographically mandatory.

## quoted_out_amount

The observed values differ by trade:
- A: 326154
- B: 320971

The value is not the actual realized output:
- A observed user output: 315954
- B observed user output: 348653

DFlow's current public Trading API documentation defines the corresponding quote surface as returning expected output after fees plus minimum-output protection and route-plan/request identifiers. The server evaluates the route and returns these values.

No equivalent authoritative live maker quote was found in Bison on-chain state.

Therefore:

`PROSPECTIVE_MAKER_QUOTE_PROTECTION_NOT_DERIVED_FROM_PUBLIC_ONCHAIN_STATE`

A client can choose a desired minimum-output policy in principle, but this analysis has not established a public-state method that predicts the Bison fill price safely enough to replace the server quote.

## Account roster

Both DFlow swaps contain 30 account entries, 26 unique pubkeys.

The roster is substantially reconstructable.

### Fixed / direct-derived

Positions 0-5:
- DFlow fixed program/user/event-authority accounts

Other fixed/public roles include:
- Sysvar Instructions
- Token-2022
- CASH mint
- DFlow program
- Bison program
- prediCt program

User-derived:
- user wallet
- user's CASH token account
- user's requested outcome token account

Market-derived:
- World market ledger
- YES mint
- NO mint
- market CASH vault

### Fee accounts

Position 20 is the same CASH token account in both trades:

`8ZjzUhmxDREvwhYfoGYsfsvhLHoKy2BLA5iLCm1GZGVT`

Its token owner is:

`8psNvWTrdNTiVRNzAgsou9kETXNJm2SXZyaKuJraVRtf`

A public `getTokenAccountsByOwner(owner, CASH)` query currently returns exactly that one CASH account.

Position 21 differs between trades, but in both cases its token owner is exactly position 6.

For each observed position-6 owner, a public `getTokenAccountsByOwner(owner, CASH)` query currently returns exactly the position-21 CASH account.

Thus position 21 is publicly discoverable once position 6 is known.

Observed fee deltas retain an approximately 10:1 position20:position21 ratio:
- A: 7888 : 788 CASH atoms
- B: 9101 : 910 CASH atoms

The same Action::D instances carry `pfs=100` and `dfs=10`. This numerical correspondence is suggestive but the public IDL does not define the semantics of the obfuscated field names, so this record does not promote it to a named fee rule.

The prospective selection rule for position 6 was not recovered.

### Bison maker accounts

Position 24 owns positions 25/26/27 in historical token-balance metadata for both trades:
- position 25: maker YES token account
- position 26: maker NO token account
- position 27: maker CASH token account

Therefore 25/26/27 are publicly related to the market-specific maker authority at 24.

The prospective maker-authority selection rule at position 24 was not independently reconstructed.

### Bison PREDTRS global state

Position 28 is the same 128-byte Bison-owned state in both trades:

`7ZqA8dqjSE95wa6Nr2hSigwJ4bYLALjzjSjcSBFo7TCR`

It has magic:

`PREDTRS\0`

A current public `getProgramAccounts(BisonFI, dataSize=128)` scan found two PREDTRS accounts, including position 28.

Position 28's state directly stores position 29's pubkey at offset 48.

Position 29 is:

`FPUtHn2WnuDQaQg4N3Cexd64EoGErz2BfWd17KHC2EUg`

It is a CASH token account owned by position 28. Public `getTokenAccountsByOwner(position28, CASH)` currently returns exactly that account.

Therefore positions 28/29 are public-state derivable and are not an off-chain blocker.

## Bison quote state

Current independent World/Bison technical reference reports that Bison's 2048-byte maker pool state exposes market/mints/token accounts/inventory and counters but does not post a live maker quote on-chain; RFQ price formation remains off-chain.

The two historical markets used here no longer returned a current 2048-byte Bison pool via the bounded filter, so this analysis does not use those pool accounts as route authority.

## @World.xyz connector check

The available World.xyz/PayBox connector exposes a generic `request_swap` intent, but its contract requires a granted wallet plus live balance validation and proceeds into a signing/approval/broadcast workflow when a route is available.

It is not a read-only World prediction-market quote/build API, and it was not invoked under this NO_TRADE analysis.

Earlier connector discovery also did not expose a dedicated World BTC5m market/quote method.

Therefore it does not currently replace the missing public fillable quote surface for this research workflow.

## Result

Supported:

- `DFLOW_DIRECT_CASH_ROUTE_TEMPLATE_SUBSTANTIALLY_REVERSE_ENGINEERED`
- `DFLOW_SWAP_BYTE_DECODER_ROUNDTRIP_EXACT`
- `PUBLIC_ACCOUNT_ROSTER_MOSTLY_DERIVABLE`
- `BISON_GLOBAL_TREASURY_STATE_PUBLICLY_DERIVABLE`
- `MAKER_TOKEN_ACCOUNT_RELATIONSHIPS_PUBLICLY_OBSERVABLE`
- `RECORD_ID_PUBLIC_GENERATION_RULE_NOT_FOUND`
- `PROSPECTIVE_MAKER_QUOTE_PROTECTION_NOT_DERIVED_FROM_PUBLIC_ONCHAIN_STATE`

Not supported:

- `SELF_BUILDABLE`
- `RFQ_SERVER_CRYPTOGRAPHICALLY_REQUIRED`
- `INDEPENDENT_PROSPECTIVE_ROUTE_BUILDER_COMPLETE`
- `EXECUTABLE_EDGE`

## Practical adjudication

The account roster is no longer the main blocker.

The remaining blocker is the off-chain quote/route-material layer:
- transaction-specific RecordId material;
- expected/minimum output required for safe price protection;
- dynamic fee receiver selection;
- market-specific maker-authority selection where more than one maker/path may exist.

The public/on-chain material identified in this analysis is insufficient to reproduce the exact safe prospective route without either:
1. a fillable quote/build surface, or
2. additional evidence that the unresolved route fields are optional or can be independently generated.

Continuing into bytecode disassembly or broad maker reverse engineering would violate the economic-first bounded-engineering rule without a stronger reason.

## Recommended state for existing Bison taker liquidity

`PARK_OPERATIONAL_ACCESS_BLOCKED`

This park applies to execution access/self-build of the existing Bison liquidity path.

It does not convert the economic research state to `ECONOMIC_NEGATIVE`.

Reopen only if one of the following occurs:
- supported DFlow production quote/order access becomes available;
- World's public fillable quote proxy becomes available again;
- another public provider returns a fillable World outcome-token route;
- new public documentation/source establishes how to generate the unresolved route material;
- a separately authorized minimal mutation simulation is justified by new evidence that RecordId/fee-route fields are optional.

# World BTC5m Bounded Final-Leg Execution Validation R0/R1 Evidence

Date: 2026-09-30

> NON-AUTHORITATIVE VENUE-SPECIFIC EVIDENCE. Research-state authority lives in `a040900/prediction-market-relative-value/main`.

Mode: PUBLIC_READ_ONLY_UNSIGNED_SIMULATION_ONLY
NO_TRADE: TRUE

## Budget

Frozen aggregate budget:
- maximum fresh BTC5m windows: 2
- maximum unsigned `simulateTransaction` calls: 6
- no private keys
- no signing
- no broadcast
- no order placement
- no capital

Observed:
- windows used: 2
- simulations used: 6

The bounded budget is exhausted. No additional simulation is authorized by this evidence record.

## Run 36678004671

Window:
- 2026-09-30 06:25:00 UTC to 06:30:00 UTC
- World market: `GnxME1WnRMzgxRUuNTSaT3MTLJ98p5DFdiazeCCvaLak`

Observed:
- fresh qualifying World/DFlow/Bison/prediCt transactions: 4
- direct CASH-to-outcome candidate: 1
- simulations used: 2
- exact control success: yes
- trimmed ComputeBudget + DFlow success: yes

Selected transaction:
`24hH4oRM1qLAx7LPKkjXMkVt27kTks9mihqLiFjbCxj1NA5SoSNaib6WCMAowSZyMXoAWu3nDXPrM39RShsqGV5P`

The trimmed message retained only:
- ComputeBudget instruction 0
- ComputeBudget instruction 1
- DFlow top-level swap instruction 2

and removed the two trailing top-level prediCt instructions.

Both exact and trimmed messages simulated successfully with:
- `sigVerify=false`
- replacement recent blockhash
- original signer roles/account roster
- no valid private-key signature
- no broadcast

This establishes execution-path replay reachability for a recent same-market route payload.

It does not establish an independent route builder because the DFlow swap bytes and remaining-account roster were reused from a recently successful real transaction.

The exact and trimmed simulations produced different outcome quantities in this run. Because simulations are sequential against live changing mainnet state and do not share a frozen state snapshot, the difference must not be attributed causally to the removed top-level instructions.

## Run 36678845986

Window:
- 2026-09-30 06:35:00 UTC to 06:40:00 UTC
- World market: `2HCGH5ZzxbBZhpQBMLeHpbMyKVikwhiVLLFbCxY3CnAi`
- World NO mint: `Dft3aLuvKRK7eiqK3ufUCJgHP88R7smbMnMjxxLD4uXv`

Selected recent successful direct-CASH transaction:
`fu7ULDFgcqfMPxUvtQRFTXfB6iGWKU19Q6B2yHErF9PFRT3RoP7dA1vwan9yK14Dk98t8QgQjmRMT2Pkn7EViCF`

Original mainnet transaction user deltas:
- CASH: -0.200041
- World NO: +0.348653
- observed original effective CASH per NO share: approximately 0.573754
- signer SOL delta: -1,544,682 lamports

The large original SOL delta is not treated as a recurring network fee because the transaction may include account/rent effects. The replay simulations below, after accounts existed, observed approximately -5,442 lamports at the signer.

### Top-level prediCt instructions

The two top-level prediCt instructions after DFlow were independently identified from their current documented discriminators:

1. `8e643ffecb324361`
   - `create_user_settings_idempotent`
   - 4 accounts

2. `1ccbdc6777985d72`
   - `update_user_settings_v2`
   - 2 accounts
   - 34-byte payload after the 8-byte discriminator

They are per-user settings operations, not split/merge or payout instructions.

Source cross-check: `chainstacklabs/world-xyz-research/docs/reference.md`, current main as inspected 2026-09-30.

### Synchronized context limitation

Polymarket REST full books were captured successfully immediately before simulation:
- PM UP best ask: 0.62
- PM DOWN best ask: 0.39

DFlow quote-stream capture failed before the simulations because the diagnostic passed unsupported `ping_interval` / `ping_timeout` keywords to the synchronous websockets client:

`TypeError:create_connection() got an unexpected keyword argument 'ping_interval'`

No seventh simulation was run to repair this instrumentation issue because the frozen six-simulation aggregate budget was already exhausted.

Therefore execution-vs-stream slippage/fee inclusion remains unresolved in this bounded run.

### Four R1 simulations

All four unsigned simulations succeeded.

#### EXACT_CONTROL

Observed signer effects:
- CASH spent: 0.200041
- World NO received: 0.442231
- signer SOL delta: -5,442 lamports
- effective World price: 0.452345 CASH / NO

Using the captured PM UP full ask book and frozen PM fee schedule:
- PM cost for matching 0.442231 net UP shares: 0.281674863
- conditional package cost: 0.481715863
- conditional payout if payoff-equivalent: 0.442231
- conditional edge: -0.039484863
- conditional edge: approximately -892.86 bps

#### DFLOW_ONLY

ComputeBudget + DFlow only also succeeded.

Observed economics matched EXACT_CONTROL for this simulation snapshot:
- World NO received: 0.442231
- conditional edge: approximately -892.86 bps

This is direct evidence that the two top-level user-settings instructions are not required for this route payload to reach BisonFI/prediCt and produce the user's outcome tokens in simulation.

It does not show that DFlow swap payload construction is independent of the router/server.

#### DFLOW_PLUS_PREDICT_1

Succeeded:
- CASH spent: 0.200041
- World NO received: 0.452672
- effective World price: 0.441912
- conditional edge: approximately -788.52 bps

#### DFLOW_PLUS_PREDICT_2

Succeeded with the same measured quantities as the preceding variant:
- conditional edge: approximately -788.52 bps

The variant simulations ran sequentially against live mainnet state. Their approximately 10,441-share-atom output difference relative to EXACT/DFLOW_ONLY must not be interpreted as a causal fee or pricing effect of the settings instructions without a frozen-state replay.

## Evidence-level conclusions

Supported:
- `UNSIGNED_EXACT_CONTROL_SIMULATION_SUCCESS`
- `UNSIGNED_DFLOW_ONLY_SIMULATION_SUCCESS`
- `ROUTE_PAYLOAD_REPLAY_REACHABILITY_POSITIVE`
- `TOP_LEVEL_USER_SETTINGS_NOT_REQUIRED_FOR_REPLAY_REACHABILITY_IN_TEST`
- `ONE_FRESH_EXECUTION_SAMPLE_CONDITIONAL_PAIR_NEGATIVE`

Not supported:
- `SELF_BUILDABLE`
- `RFQ_SERVER_REQUIRED`
- `INDEPENDENT_ROUTE_BUILDER_COMPLETE`
- `EXECUTABLE_EDGE`
- any conclusion that the historical conditional edge is economically negative overall

The key remaining implementation question is no longer whether a recent DFlow/Bison route payload can execute in unsigned simulation. It can.

The remaining question is whether the DFlow swap payload, quote/output constraints, and remaining-account roster can be generated prospectively from public/on-chain state without obtaining a server-generated order/route.

## Recommended next discriminating task

Do not spend more simulation budget yet.

Perform an offline/read-only route-construction analysis over multiple successful direct-CASH transactions:

- fully decode DFlow `swap` bytes and Action vector;
- classify every remaining account as deterministic public/on-chain, user-derived, market-derived, or quote/router-derived;
- compare at least two direct-CASH trades from different BTC5m markets;
- attempt a deterministic builder that reproduces the observed instruction bytes/account ordering without calling DFlow `/order`.

Only if that builder is sufficiently reproduced should a new, separately authorized simulation budget be opened.

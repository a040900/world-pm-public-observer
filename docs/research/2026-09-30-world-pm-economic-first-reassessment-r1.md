> Cross-project adjudication authority: `a040900/prediction-market-relative-value` / `docs/decisions/2026-09-30-economic-first-research-adjudication-authority-r1.md` (authority branch `research/economic-first-authority-r1-20260930`). This observer document is venue-specific evidence and must not redefine the cross-project policy.\n\n# World.xyz <-> Polymarket BTC 5m Economic-First Reassessment R1

Date: 2026-09-30
Status: PRELIMINARY_REASSESSMENT_PENDING_SINGLE_ZERO_SCORE_LIVENESS_DIAGNOSTIC
Authority standard: docs/research/2026-09-30-economic-first-reassessment-standard-r1.md
NO_TRADE: TRUE

## Current classification

Primary: EDGE_OBSERVED

Qualifiers:
- EXECUTION_UNRESOLVED
- CAPITAL_EFFICIENCY_UNESTABLISHED
- SETTLEMENT_BASIS_RISK_UNRESOLVED

This route is not currently supported as EXECUTABLE_EDGE.
The available evidence also does not justify PROVEN_NEGATIVE.

## Why PROVEN_NEGATIVE is not supported

The historical 96-window prospective qualification (run 35934198361) recorded:
- 96 windows over 8 hours;
- 7 unique positive windows;
- combined gross nominal paper profit USD 4.111509930791898;
- gross nominal paper profit per hour USD 0.5139387413489872;
- maker: 167 candidates, 130 definite fills, 4 surviving protected-edge observations under that measurement generation;
- taker: 276 candidates, 6 paper-trade decisions and 6 complete two-leg simulations under that measurement generation.

That evidence does not establish durable executable EV, but it is positive economic/candidate evidence and cannot be reclassified as no observed edge.

Settlement evidence in that qualification had 64/64 same-direction dual resolutions and zero observed mismatches. The one-sided 95% upper bound was approximately 4.57%, above the observed strategy harmful-mismatch break-even rate of approximately 2.97%; therefore risk-adjusted positive EV was not established.

## Later measurement evidence

A7 run 36256572348 established that public Polymarket WebSocket last_trade_price.size is not authoritative price-level matched quantity:
- eventCount: 6598
- qualifiedCount: 6595
- multiPriceCount: 163
- multiPriceAggregateOnlyCount: 111
- multiPricePriceLevelOnlyCount: 0
- verdict: A7_FAIL_AGGREGATE_TAKER_SIZE_COUNTEREXAMPLE

Economic-first interpretation:
A7 invalidates the old public-WS-size maker-fill mapping. It does not test or refute the underlying World/Polymarket price discrepancy, information relation, or taker route.

The repaired maker measurement subsequently used condition-level receipt evidence instead of public WS trade size.

## Preserved positive maker evidence

Run 36331335285 contains a prospective fully hedged 5-share candidate:
- pair: WORLD_YES + PM_DOWN
- makerBid: 0.45
- confirmedFillLowerBoundShares: 5
- hedgeCoveredShares: 5
- cumulativeProtectedUnitCost: 0.98125
- protected gross edge: 0.01875/share = 187.5 bps before any remaining route-level risk treatment

The repository explicitly preserves this as prospective candidate evidence while also stating that it does not establish positive EV.

The later fixed 72-window Phase A final adjudication, under its own frozen measurement semantics, reports one fully protected survivor:
- pair: WORLD_NO + PM_UP
- makerBid: 0.95
- shares: 5
- cumulativeProtectedUnitCost: 0.996875
- gross protected edge: 0.003125/share = 31.25 bps
- gross USD at observed size: 0.015625

These measurement generations must not be pooled as if identical.

## Phase A frequency result

The fixed Phase A adjudication reports:
- 72 valid 5-minute windows;
- maker candidateCount: 10;
- maker definiteFillCount: 6;
- maker fully protected survivor: 1;
- all 10 maker candidates occurred in the first 18 formal windows;
- the final 54 formal windows had zero maker candidates;
- taker candidateCount: 281;
- taker paperTradeDecisionCount: 0;
- taker twoLegSimulationCompleteCount: 0;
- taker unknownDecisionCount: 258;
- observation availability: approximately 62.76%.

Economic-first interpretation:
- The repaired maker path is too sparse and too small to support a repeatable high-frequency executable edge.
- The taker path was not economically adjudicated to completion because execution measurement remained largely UNKNOWN.
- UNKNOWN coverage must not be interpreted as zero opportunity.

## New economic-first inspection of the accepted later Phase A batches

Direct inspection of the accepted later Phase A taker artifacts available from runs 36505337224, 36521268542, and 36535669977 shows 211 unique taker candidates in the inspected accepted batches.

All 211 had indicativeSum < 1 by candidate construction/observation.
Distribution of indicativeSum in those inspected candidates:
- minimum: 0.8251634874697921
- median: 0.9920471243651308
- below 0.98: 30
- below 0.97: 14
- below 0.95: 4

Every one of these 211 inspected candidates was classified:
- decision.status = UNKNOWN
- decision.reason = WORLD_EXACT_QUOTE_INVALID
- World exact quote HTTP status = 404

Important limitation:
indicativeSum is not an executable unit cost. These observations show economically interesting displayed/radar discrepancies, not executable profit.

However, because the exact-quote path collapsed all non-200 responses into WORLD_EXACT_QUOTE_INVALID and did not preserve the response body, the 404 set does not currently distinguish:
- genuine no-route / non-executable conditions;
- request-shape or amount errors;
- endpoint/API semantic change;
- other World exact-quote service behavior.

Therefore zero completed taker executions in this Phase A sample cannot, by itself, be treated as evidence that the displayed economic discrepancy was untradeable.

## World-specific incremental-information diagnostic

Fresh run 36501798077 completed 6/6 valid windows with 45 threshold-crossing episodes and verdict R1_NO_PROMOTION.

At all thresholds, WORLD_CEX_DISAGREE had zero episodes in this sample. Therefore the predeclared continuation gate could not be evaluated positively.

This is evidence against promoting a distinct "World contains incremental information beyond Binance+OKX when directions disagree" mechanism from this small diagnostic sample.

The frozen protocol explicitly states this result does not adjudicate current World/Polymarket relative-value economics, settlement-basis risk, or maker/taker execution.

## Economic-first conclusion

Supported:
1. World/Polymarket displayed or modeled relative-value discrepancies have occurred prospectively.
2. At least one later repaired maker candidate survived to a fully protected positive gross edge under frozen Phase A semantics, but only at 31.25 bps and 5 shares in the final sample.
3. Earlier prospective measurement generations contain larger positive candidate observations, including a preserved 187.5 bps 5-share protected candidate and the 96-window repeated-edge result.
4. A7 invalidates an execution inference, not the existence of the underlying discrepancy.
5. The final maker sample does not support a frequent/scalable primary maker route.
6. The final taker sample remains heavily execution-UNKNOWN and cannot be used as a clean economic negative.

Not supported:
- durable positive EV;
- scalable executable edge;
- reliable high-frequency maker opportunity;
- risk-adjusted return superior to the user's alternative low-risk yield benchmark;
- a World-specific incremental-information mechanism beyond CEX from R1.

## Smallest next discriminating test

Do not run another 72-window campaign.

Run one zero-research-score public-read-only liveness diagnostic against the current World exact-quote route for a current BTC 5m market, preserving:
- HTTP status;
- response body;
- request parameters;
- current DFlow indicative quote for the same mint;
- exact quote output/minOut if successful.

Decision logic:
- If DFlow shows a current quote while exact quote returns a semantic no-route response, this supports treating much of the Phase A taker gap as non-executable.
- If exact quote succeeds or the 404 is shown to be an obsolete endpoint/request-shape issue, Phase A taker zero-completion must be reclassified as instrumentation/execution-path unresolved and a small fresh economic POC is justified.
- No new long prospective sample should begin before this distinction is resolved.

## Current disposition

Primary classification: EDGE_OBSERVED + EXECUTION_UNRESOLVED

Maker:
- deprioritized as a high-frequency primary route based on the final repaired Phase A frequency/economics.

Taker:
- not killed;
- blocked on the meaning and current validity of the World exact-quote path.

World-specific incremental lead beyond CEX:
- not promoted by R1.

NO_TRADE remains binding.


## 2026-09-30 zero-score liveness findings

These diagnostics carry zero research score. They exist only to determine whether the current public execution/discovery surfaces used by Phase A still behave as assumed.

### Discovery horizon

Run 36650837024 demonstrated that the original on-chain discovery horizon of 80 recent prediCt signatures is too narrow under current transaction density.

Observed:
- 263 recent signatures in the expanded query;
- the 80th signature was still newer than the earliest slot needed;
- default80CoversEarliestNeeded = false;
- the immediately prior BTC5m market required scanOrdinal 96 to recover;
- the current BTC5m market was recovered and validated from an exact World metadata description.

Interpretation:
WORLD_CURRENT_BTC5M_MARKET_NOT_DISCOVERED can be a discovery false negative under the old 80-signature bound. This failure must not be interpreted as evidence that the market does not exist or that the economic route is absent.

### World public catalog

Run 36651034590 called:
https://markets-api-proxy.world-xyz.workers.dev/api/v1/markets

Observed:
- HTTP 401
- response body: {"error":"Unauthorized"}

A third-party technical reference had verified this catalog as open on 2026-09-11. The current 2026-09-30 observation shows that public access behavior has changed. The catalog cannot presently be treated as an unauthenticated discovery authority.

### Current executable-quote surface comparison

Run 36651423451 attempt 2 recovered a live current BTC5m World market and received healthy DFlow indicative quotes for both outcomes.

Current market:
- start: 1790728800
- market: 9Y3vPcw5iMcjEC2VPH2Ds6uQkAzEDhEhmoHWFFcdNYtq
- YES mint: 8tBWSPAA5fin515Hdc1DpN4fXA4fCs2Qxdm8wCHxkLYN
- NO mint: A23t3YJXKV9Jex6JfyZD1RjaghYkCsxrgGW8wV4t7MBP

DFlow indicative observations:
- YES ask approximately 0.67753907
- NO ask approximately 0.36554836
- both sides healthy and fresh

Read-only exact quote probes at the same live market:
- World proxy aggregator-api-proxy.world-xyz.workers.dev/order:
  - YES HTTP 404, empty body
  - NO HTTP 404, empty body
- DFlow dev dev-quote-api.dflow.net/order:
  - YES HTTP 400, {"msg":"Route not found","code":"route_not_found"}
  - NO HTTP 400, {"msg":"Route not found","code":"route_not_found"}
- DFlow production quote-api.dflow.net/order without API key:
  - YES HTTP 403, empty body
  - NO HTTP 403, empty body

DFlow current public documentation states that userPublicKey is optional for quote-only GET /order calls, so the World proxy 404 is not explained merely by omission of a wallet public key.

### Reassessment impact

The Phase A taker result paperTradeDecisionCount=0 remains valid as an observation about that runtime, but it is not clean economic negative evidence.

The current public environment demonstrates:
1. World BTC5m markets still exist and can be identified on-chain.
2. DFlow still emits live indicative quotes for the outcome mints.
3. The previously used World unauthenticated exact-quote proxy currently returns 404.
4. DFlow's dev endpoint currently reports route_not_found for the same live outcome mints.
5. DFlow production /order requires authenticated access.

Therefore the taker route is currently classified:
EDGE_OBSERVED + EXECUTION_ACCESS_BLOCKED

This does not establish executable positive EV. It also prevents the old public exact-quote failure rate from being interpreted as proof that the displayed relative-value discrepancy itself was economically false.

### Minimum next work

Do not start another long prospective campaign.

The next economically relevant branch point is:
- obtain a supported current executable quote surface for World outcome tokens, or
- accept that unauthenticated public execution access is currently unavailable and park the taker route on operational-access grounds.

Any future fresh economic POC should begin only after executable quote access is restored or independently reproduced.


## Economic-first repricing of accepted Phase A taker candidates

This section is a post-hoc prioritization diagnostic over already prospective Phase A artifacts. It does not create a new confirmatory research score and does not establish executable PnL.

### New DFlow semantic authority

Current DFlow documentation states that the quote stream:
- computes each quote from an approximately USD 10 equivalent routed trade;
- reports bid/ask rates that this routed trade would execute at;
- remains an approximation and may differ from the quote returned at actual order time.

The existing Phase A World radar used this DFlow quote stream for the World-side ask.

The Phase A candidate trigger already used:
- World DFlow ask;
- Polymarket best ask;
- Polymarket frozen taker fee adjustment;
- sub-1 combined indicative unit cost.

Therefore the DFlow leg has stronger economic meaning than a generic UI/display midpoint, while remaining insufficient for EXECUTABLE_EDGE without a supported production order quote.

### Accepted 54-window reprice diagnostic

Inputs:
- accepted Phase A batches 04 attempt 2, 05, 06, 07, 08 attempt 2, 09, 10, 11, and 12 attempt 2;
- 54 valid prospective windows;
- 211 taker candidates;
- 210 candidates with a valid post-request Polymarket response-state ask book;
- one candidate excluded from this reprice because PM transport was invalid at the response anchor.

For each analyzable candidate:
1. freeze the observed World DFlow ask from the prospective trigger;
2. use USD 10 / World ask as the approximately USD 10 World outcome-share quantity implied by the documented quote-stream construction;
3. use the Polymarket response-state full ask book recorded after the failed World exact-quote request;
4. walk Polymarket asks to acquire the same number of net outcome shares using the frozen Phase A fee schedule and fee rounding;
5. compute conditional package unit cost as:
   (USD 10 World approximate cost + PM depth-walk cost) / matched outcome shares.

Important limitation:
The World DFlow ask is frozen at trigger time while the PM response-state book is observed after the failed World /order attempt. World is not re-quoted at that later anchor. This diagnostic therefore measures whether the PM side still leaves room under the earlier approximately USD 10 World pre-trade rate; it is not synchronized two-leg executable proof.

Observed World exact-quote request latency:
- median approximately 330 ms;
- 90th percentile approximately 422 ms;
- maximum approximately 1901 ms.

Results among 210 analyzable candidates:
- PM depth could cover the approximately USD 10 World-equivalent share quantity in 210/210 using the recorded full ask books;
- positive conditional package edge after PM fee and depth at the response anchor: 173/210;
- >25 bps: 154/210;
- >50 bps: 143/210;
- >100 bps: 116/210;
- >200 bps: 65/210;
- >300 bps: 41/210;
- >500 bps: 20/210;
- >1000 bps: 2/210;
- median response-anchor conditional edge: approximately 122.8 bps;
- 90th percentile: approximately 485.3 bps;
- maximum observed diagnostic edge: approximately 2273.1 bps.

Window-level results:
- 54/54 windows had at least one positive response-anchor conditional candidate;
- 49/54 had a best candidate >=100 bps;
- 38/54 >=200 bps;
- 26/54 >=300 bps;
- 15/54 >=500 bps;
- median best candidate per window: approximately 284.7 bps;
- minimum best candidate among the 54 windows: approximately 39.4 bps;
- median positive candidate count per window: 3.

Direction counts:
- analyzable WORLD_YES + PM_DOWN candidates: 107; positive at response anchor: 94;
- analyzable WORLD_NO + PM_UP candidates: 103; positive at response anchor: 79.

Settlement observations for these 54 accepted windows:
- 44 resolved with same-direction World/Polymarket outcomes;
- 10 remained pending/unknown in the stored Phase A artifact;
- zero observed resolved direction mismatches in this 54-window subset.

This settlement observation does not prove payoff equivalence or eliminate basis risk.

### Economic-first interpretation

The prior statement that the taker route had no economic result was too pessimistic for prioritization.

A more accurate current interpretation is:
- prospective sub-1 discrepancies were frequent;
- the DFlow World quote has documented approximately USD 10 pre-trade routing semantics;
- the Polymarket side had enough recorded depth for the corresponding small-size hedge in all 210 analyzable candidates;
- a large majority retained positive conditional economics after the approximately 330 ms World exact-quote request delay when the later PM book was used;
- actual World order-time execution remains unverified because the public World proxy now returns 404 and DFlow production /order requires x-api-key authentication.

Primary classification remains:
EDGE_OBSERVED

Additional qualifiers:
- ECONOMIC_POC_POSITIVE
- EXECUTION_ACCESS_BLOCKED
- SETTLEMENT_BASIS_RISK_UNRESOLVED

The route should now be prioritized for a bounded production-quote execution POC if supported DFlow production API access becomes available. It should not be sent back through maker queue engineering or another long Phase A capture first.

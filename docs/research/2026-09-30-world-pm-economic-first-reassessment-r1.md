# World.xyz <-> Polymarket BTC 5m Economic-First Reassessment R1

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

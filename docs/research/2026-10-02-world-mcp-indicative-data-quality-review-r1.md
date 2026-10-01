# World MCP Indicative Data Quality Review R1

Date: 2026-10-02
Review baseline: `a040900/world-pm-public-observer` branch `research/economic-first-reassessment-r1-20260930` at `ad53ea6`
Mode: bounded read-only
NO_TRADE: TRUE

## Prior evidence reviewed

- `docs/research/2026-10-01-world-mcp-readonly-verification-r0.md`
- `evidence/world-mcp-readonly-verification-20261001-r0/mcp-receipts.json`
- `evidence/world-mcp-readonly-verification-20261001-r0/onchain-crosscheck.json`
- `evidence/world-mcp-readonly-verification-20261001-r0/manifest.json`

The prior receipt-level verdict is supported:

`WORLD_CONNECTOR_READONLY_MARKET_AND_INDICATIVE_ORDERBOOK_CONFIRMED_AT_RECEIPT_TIME`

The prior evidence does not establish fillable depth, built transaction availability, after-cost execution, or DFlow independence.

## Reviewer MCP availability

The reviewer's own World.xyz connector exposed the official enabled `World` plugin.

The deployed contract marked these as read-only:
- `world_get_events`
- `world_get_markets`
- `world_market_prices`
- `world_orderbook`
- `world_search`

Trading tools remained state-changing and were not invoked.

## Frozen bounded protocol

Maximum:
- one current BTC 5-minute window
- six live World orderbook reads
- approximately ten-second target spacing
- one event identity read
- one 1-second historical quote-history read for that exact window
- no long-running collector

Stop conditions:
- market end
- two consecutive read failures or identity change
- six live reads

Data-quality gates:
- live-read gate: at least five of six successful reads if sufficient window lifetime exists
- freshness/update gate: monotone source timestamp/slot and at least two distinct updates
- missingness reported per field; no forward filling
- quantity semantics must be independently characterized before any depth interpretation
- World/Polymarket pairing uses the exact same UTC 5-minute start/end window
- indicative quotes may support signal research only; they are not fills

## Tested World market

World ticker:
`WXBTC5M-26OCT011845-5`

Window:
- start: 1790880300
- end: 1790880600
- UTC: 2026-10-01 18:45:00 to 18:50:00

Market ledger:
`Gfdy9eWTJEWTfe3dnspRoZfr94j17HFegGR23DquZ3vM`

YES mint:
`4PsUcBRHS9uxbjrXMwTsGdDd6rchEDsuLjgUMh1PoEhi`

NO mint:
`7UbpoXthEBkU3DZjVRucUWJ4WZwJ6VAsc4MW2XEayfoG`

The event identity was returned by the World MCP before the market closed.

## Live orderbook-read result

The bounded live sampling started with only about 35 seconds remaining in the market.

Observed:
1. source ts 1790880565, slot 452372803
2. source ts 1790880583, slot 452372871
3. both YES and NO became null near market end

The protocol stopped rather than silently extending into a second window.

Therefore the predeclared 5/6 live-read gate was not evaluable.

Classification:

`NO_RESULT_INSUFFICIENT_LIFETIME_FOR_LIVE_FREQUENCY_GATE`

The first two successful reads do show monotone source time and slot movement across 18 seconds.

## Bounded 1-second history quality

A single read-only `world_market_prices` call was made for the exact same 300-second window with resolution=1.

### YES

- rows: 261
- unique timestamps: 261
- first ts: 1790880300
- last ts: 1790880593
- median timestamp gap: 1 second
- maximum gap: 6 seconds
- unique slots: 261
- ask missing: 0 / 261
- askQty missing: 0 / 261
- bid missing: 60 / 261 = 22.99%
- bidQty missing: 60 / 261 = 22.99%

### NO

- rows: 232
- unique timestamps: 232
- first ts: 1790880300
- last ts: 1790880593
- median timestamp gap: 1 second
- maximum gap: 13 seconds
- unique slots: 232
- ask missing: 0 / 232
- askQty missing: 0 / 232
- bid missing: 30 / 232 = 12.93%
- bidQty missing: 30 / 232 = 12.93%

Interpretation:

The MCP history surface provides high-cadence point observations suitable for measuring quote-state changes. It is not a guaranteed every-second feed and has observable gaps/missing bid states.

No missing values were filled.

## Quantity semantics

The quantity fields must not be treated as executable market depth.

Across the same history:

YES:
- 261 / 261 ask observations had `ask * askQty` within 0.01 CASH of 10
- median `ask * askQty`: 10.002516
- min: 10.002436
- max: 10.002566
- when bidQty existed, `bidQty == askQty`: 201 / 201

NO:
- 232 / 232 ask observations had `ask * askQty` within 0.01 CASH of 10
- median `ask * askQty`: 10.002518
- min: 10.002436
- max: 10.002566
- when bidQty existed, `bidQty == askQty`: 202 / 202

This is strong evidence that the returned quantity is quote-size-derived outcome quantity for an approximately 10-CASH reference request, rather than independently observed top-of-book depth.

Authority-safe classification:

`WORLD_MCP_QUANTITY_SEMANTICS_REFERENCE_QUOTE_SIZE_NOT_VERIFIED_DEPTH`

This does not prove a fill for that quantity.

## Polymarket pairing and time alignment

The repository's current Polymarket identity function constructs the exact market slug as:

`btc-updown-5m-{start_ts}`

and requires:
- one unique event
- one unique market
- outcomes exactly `[Up, Down]`
- active / accepting-orders state
- explicit Up and Down CLOB token IDs

For the tested World window, the paired Polymarket identity is therefore:

`btc-updown-5m-1790880300`

Window mapping:
- World YES -> Polymarket UP
- World NO -> Polymarket DOWN
- exact nominal window: 1790880300 to 1790880600

The current review did not obtain a contemporaneous Polymarket CLOB receipt before this window expired. Therefore identity/time-window pairing is supported by the frozen repository mapping rule, while cross-venue quote synchronization for this specific sample remains unmeasured.

Classification:

`MARKET_WINDOW_PAIRING_RULE_VALID`
`SAME_SAMPLE_CROSS_VENUE_QUOTE_ALIGNMENT_NOT_MEASURED`

## Data suitability for signal research

Supported:

`WORLD_MCP_INDICATIVE_FEED_SUITABLE_FOR_BOUNDED_SIGNAL_RESEARCH`

Why:
- exact market identity and outcome mints are available
- prior on-chain receipt cross-check matched market ledger, mints and window
- source timestamps and slots are exposed
- read-only 1-second history shows frequent quote updates
- missing states are explicit
- market direction/window can be paired deterministically with the Polymarket BTC5m series

Required handling:
- preserve raw timestamps/slot IDs
- treat missing bids as missing, not zero or forward-filled
- do not use quantity fields as CLOB depth
- keep World and PM timing/settlement basis separate

## Paper-trading execution assumptions still unknown

Not validated:
- whether bid/ask is fillable at observation time
- fillable quantity/depth
- partial-fill behavior
- maker selection
- routePlan / built transaction availability through this read-only plugin surface
- quote-to-order latency
- slippage
- World platform/router/RFQ fee inclusion
- transaction/rent/priority-fee total cost
- whether the approximately 10-CASH reference quantity is executable
- whether a paper fill should use best quote, min-out, realized output, or another execution anchor
- DFlow independence
- cross-venue atomicity / leg risk
- settlement/payoff equivalence

Therefore no paper-trade fill model should treat these MCP quote fields as executed prices.

## Verdict

Data-quality verdict:

`WORLD_MCP_INDICATIVE_DATA_QUALIFIED_FOR_BOUNDED_SIGNAL_RESEARCH`

Execution-data verdict:

`NOT_QUALIFIED_FOR_PAPER_FILL_OR_AFTER_COST_EXECUTION_RESEARCH`

No alpha verdict is produced.

No authority exists here to begin long-duration collection.

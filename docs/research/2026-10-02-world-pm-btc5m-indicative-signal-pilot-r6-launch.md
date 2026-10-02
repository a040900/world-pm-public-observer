# World ↔ Polymarket BTC5m Indicative Signal Pilot R6 Launch

Date: 2026-10-02
Mode: prospective bounded signal-only
NO_TRADE: TRUE

Authority protocol:
`a040900/prediction-market-relative-value/main@ddfb4822d41c4ee8119d909ba961ef6d86079536`

Protocol path:
`docs/decisions/2026-10-02-world-pm-btc5m-indicative-signal-pilot-r6.md`

Readiness:
- Polymarket sync readiness PASS: run `36947171225`
- World MCP same-window identity/freshness PASS for 00:45 UTC window
- earlier late-window readiness = NO_RESULT
- implementation wiring attempts are not research results

Frozen sample starts:
`[1790902200,1790902500,1790902800,1790903100,1790903400,1790903700,1790904000,1790904300,1790904600,1790904900,1790905200,1790905500]`

No replacements.

PM prospective capture:
- workflow: `WORLD_PM_BTC5M_INDICATIVE_SIGNAL_PILOT_R6`
- run: `36947708054`
- head: `0629bfacb9b204c8726e9d0c62cdff791f9e5194`
- cadence: 2 seconds
- capture region: +5 s through -5 s
- World MCP join occurs only after all frozen windows complete

Frozen evaluator:
`tools/research/world_pm_btc5m_indicative_signal_pilot_evaluate_r0.py`

Adjudication axes remain separate:
- signal: PASS / NO_RESULT / FAIL under R6 only
- execution/after-cost: NOT_TESTED_BY_DESIGN
- settlement admission: separately observed/adjudicated

No displayed quote is a fill and no hypothetical package gap is PnL.

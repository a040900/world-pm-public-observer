# World ↔ Polymarket BTC5m R6 Synchronization Readiness Result

Date: 2026-10-02
Authority: `a040900/prediction-market-relative-value/docs/decisions/2026-10-02-world-pm-btc5m-indicative-signal-pilot-authority-r6.md`
NO_TRADE: TRUE

## Verdict

`PASS`

This PASS authorizes only the fixed 12-window indicative signal pilot defined by R6. It is not execution, after-cost, settlement-admission, or alpha evidence.

## Frozen readiness window

Start: `1790905200`
End: `1790905500`

Polymarket:
- slug: `btc-updown-5m-1790905200`
- condition: `0x55afdc7b3db9711e8ced3c88267bd56f27051749e34d76c666a954777f8c952d`
- UP token: `89069460544121175048971716534335147133165617188029606852011552062089793983065`
- DOWN token: `85618056773986795323656798092472495952840283834145357565563622692713084838762`

World:
- ticker: `WXBTC5M-26OCT020140-5`
- market ledger: `cKvopFhD91dsiFfuc1bBX2UufFuemA82vH3fKYPTuCk`
- YES mint: `AwM1Hy48qEGzfEHgde7E5pdBWoR7msY8q8uYXdo94sDd`
- NO mint: `4cfTrg7BGmGCsoBQLDKw4PkFD1rTmA6yjdSu9CsHwmXS`

Both venues identify the exact same nominal UTC window.

## Polymarket capture

GitHub Actions run:
`36951852179`

- attempted paired snapshots: 150
- paired successes: 150
- median capture spacing: 2.0000004768 seconds
- maximum capture spacing: 2.0000433922 seconds
- gate: PASS

## World MCP history

Exact same ticker/window, resolution=1.

YES:
- observations: 254
- unique timestamps: 254
- unique slots: 254
- median timestamp gap: 1 second
- maximum gap: 6 seconds
- ask missing: 0
- bid missing: 43

NO:
- observations: 255
- unique timestamps: 255
- unique slots: 255
- median timestamp gap: 1 second
- maximum gap: 6 seconds
- ask missing: 0
- bid missing: 42

- World observation-count gate: PASS
- median/max gap gate: PASS

## Cross-source timestamp alignment

Using the 150 frozen PM 2-second target timestamps and the union of World YES/NO source timestamps:

- PM timestamps with a World source timestamp within ±2 seconds: 149 / 150
- rate: 99.33%
- maximum nearest distance: 4 seconds for the single unmatched edge observation
- required rate: >= 90%
- gate: PASS

No missing values were imputed.

## Readiness adjudication

All predeclared readiness gates passed.

`SAME_SAMPLE_CROSS_VENUE_QUOTE_ALIGNMENT_QUALIFIED_FOR_R6_SIGNAL_PILOT`

## Settlement observation, separately held

After close, World MCP reported the World market finalized NO and exposed rules that explicitly reference:

`Chainlink BTC/USD 60-second time-weighted average`

with a 60-second TWAP at open and close.

The paired Polymarket market identifies `btc-5m-twap-60`, `twapLookbackSeconds=60`, and a Chainlink BTC/USD TWAP resolution source.

This observation is not admitted here as settlement equivalence. Settlement remains separately adjudicated under R6.

## Pilot start

Because readiness PASSed, the R6 fixed 12-window prospective PM collector was started without changing thresholds, trigger rules, or denominators.

No replacement/reselection is permitted.

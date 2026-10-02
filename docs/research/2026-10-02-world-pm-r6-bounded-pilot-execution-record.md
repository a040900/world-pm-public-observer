# World ↔ Polymarket BTC5m R6 bounded pilot execution record

Date: 2026-10-02
Observer repository: `a040900/world-pm-public-observer`
Branch: `research/economic-first-reassessment-r1-20260930`
NO_TRADE: TRUE

## Authority actually in force

The originally referenced R4 is superseded for this task by the later R6 lineage on
`a040900/prediction-market-relative-value/main`:

- `c538ddc5f523d2c3adf85212e671e850d89cf5fb` — `2026-10-02-world-pm-btc5m-indicative-signal-pilot-authority-r6.md`
- `80d2ce469a41cdfade7e80a7e7e9bd6bf0c450ba` — R6 analysis interpretation addendum.

The earlier same-day `ddfb4822...` protocol remains in history, but the later authority and its addendum are the applicable adjudication basis.

## Session World connector contract

Discovery returned official plugin `world`, enabled, contract URI `paybox://plugins/world`.

The deployed contract contains read-only tools including:
`world_get_events`, `world_get_markets`, `world_market_prices`,
`world_orderbook`, `world_search`, and `world_filter_outcome_mints`.
Their annotations are `readOnlyHint=true`, `destructiveHint=false`.

The same contract also contains state-changing buy/change/redeem tools. None were invoked.
Exact contract evidence is stored in:
`evidence/world-pm-r6-bounded-pilot-20261002/world-plugin-contract.json`.

## Readiness

The branch already contains a prospective R6 readiness result:
`docs/research/2026-10-02-world-pm-r6-sync-readiness-result.md`.

It records PASS for window 1790905200 with 150/150 PM paired snapshots and World 1-second histories meeting the frozen count/gap/alignment gates. This is data/synchronization readiness only.

## Frozen pilot capture already consumed

A later R6 authority froze a new 12-window sample. The corresponding GitHub Actions run
`36952623833` completed and produced artifact
`world-pm-r6-fixed-12-window-pm-r0-36952623833`
(digest `sha256:19beac27dfd202c8d0435d38ab176e7510186f6f2a07115cde7772df44ca39af`).

The artifact payload hash is:
`eef4f80cdacd5b3df5602c4df909e1113f95fef77ae7b502b8c889e87c631605`.

Frozen PM starts in that artifact:
`1790905800, 1790906100, 1790906400, 1790906700, 1790907000, 1790907300, 1790907600, 1790907900, 1790908200, 1790908500, 1790908800, 1790909100`.

Observed capture outcome:
- 1790905800: market identity present; 150 snapshots.
- the remaining 11 frozen windows: `identityError=UNKNOWN`; 0 snapshots each.
- summary: `windowsFrozen=12`, `windowsRecorded=12`, `windowsWithIdentity=1`, `pairedSuccessSnapshots=150`.

This is a collection failure, not a signal/economic failure.

## Collector defect

`tools/research/world_pm_r6_fixed_12_window_pm_r0.py::capture_window` attempts identity discovery only while
`time.time() < start`.

After a successfully captured 300-second window, the sequential loop enters the next
consecutive window at or just after its start. The condition is already false, so no
identity request is attempted; the record falls through to `identityError=UNKNOWN` and
waits until that window ends. This repeats for all subsequent windows.

This exactly explains the artifact shape and means the workflow-level `success` conclusion
must not be interpreted as successful 12-window research capture.

## First frozen window World diagnostic

The first frozen window can still be used only as a data-quality diagnostic, not as a
replacement pilot sample.

World market:
- ticker: `WXBTC5M-26OCT020150-5`
- open: 1790905800
- close: 1790906100
- finalized result: YES
- YES mint: `ByWzPB2Aa8Y86bhCswHVHfECN5Vfx2MSqanuwmZff8kJ`
- NO mint: `C7sHskaPgAEo3R8oS1m9XFcUp9PvvB5bic2GGVCDReRN`

Read-only `world_market_prices`, resolution=1:
- YES: 266 rows / 266 unique timestamps / 266 unique slots; median timestamp gap 1s; max gap 11s; ask missing 0.
- NO: 232 rows / 232 unique timestamps / 232 unique slots; median timestamp gap 1s; max gap 5s; ask missing 0.

This shows useful source-time/slot cadence but does not repair the missing PM observations in the other 11 windows and does not establish executable depth.

Raw World history is stored in:
`evidence/world-pm-r6-bounded-pilot-20261002/world-window-1790905800-market-prices.json`.

## Adjudication

Primary R6 signal verdict:
`NO_RESULT_DATA_INSUFFICIENT`.

Reason:
the fixed 12-window denominator was consumed, but only one window has PM observations.
The authority forbids replacement, reselection, skipping, or extension. Therefore no new
12-window sample was started and no retrospective substitute was used.

H1 lead/lag:
`NO_RESULT_DATA_INSUFFICIENT`.
The required cross-venue sample does not exist for 11/12 windows. No sequential API arrival
ordering is treated as market lead/lag.

H2 conditional price scenario:
`NO_RESULT_DATA_INSUFFICIENT`.
The predeclared trigger hypothesis cannot be tested on the frozen denominator.

Settlement:
the first World window is finalized YES, but settlement admission is not adjudicated from
this single surviving window. Missing trigger qualification also prevents candidate-conditioned
settlement inference for the failed windows.

## Explicit non-claims

- World `bidQty/askQty` is not executable depth.
- No World quote is treated as fillable.
- No paper fill or paper PnL is produced.
- No after-cost edge is inferred.
- No execution gate is reopened.
- `PARK_OPERATIONAL_ACCESS_BLOCKED` remains unchanged.
- No signing, simulation, order placement, wallet grant, or capital action occurred.

## Minimum next evidence

A fresh pilot would require a new explicit authority because the currently frozen 12-window
sample is already consumed and cannot be replaced under R6. Before any such authorization,
the sequential identity-discovery boundary defect should be repaired and deterministically
tested. That repair alone must not silently authorize another prospective sample.

Stop here.

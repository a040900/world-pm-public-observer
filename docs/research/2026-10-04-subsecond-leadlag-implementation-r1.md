# Frozen R6 subsecond measurement implementation

Status: IMPLEMENTED_OFFLINE_VALIDATED; SECTION_2_5_BINDING_PENDING.
NO_TRADE. No prospective or historical market data were collected or analyzed
by this implementation task. All acceptance fixtures are synthetic.

Repository: `a040900/world-pm-public-observer`.
Branch: `research/subsecond-leadlag-measurement-r1`.
Base: `fc91a2300d932fd2dd51cf7d1452eff5a345e671` (main).
Existing workflows, frozen cohort collector/analyzer and historical artifacts
are unchanged. No authority files were modified. No workflow was dispatched.

## Authority and owner clarification

Read before coding, both RATIFIED_BY_OWNER in
`a040900/prediction-market-relative-value`:

- `docs/decisions/2026-10-04-subsecond-leadlag-measurement-precommit-r6.md`,
  blob `0003d90615245309c3b152fe8a2d0a3fb04bb091`.
- `docs/decisions/2026-10-04-subsecond-leadlag-measurement-binding-r2.md`,
  blob `b26a1e48ed8dfcd688ac6c978dd24f42bd37d707`.

The owner resolved three implementation questions in this session:

1. After endpoint qualification and upfront purge, use the intersection of
   all four horizons' eligible first-half rows for the 30,000 gate, inner
   comparison and first-half training. Use only selected k* in the second half.
2. World errors do not generate integrity gaps or 30s padding. World uses
   receipt/source age and all-endpoint qualification. PM gaps retain padding.
3. Purge labels crossing 4h/6h before splitting, including outer training.
   Labels beyond the 12h end are excluded. Ending exactly at an internal split
   uses receipts strictly before that boundary and does not cross it.

These choices are recorded in analysis artifacts. They do not modify authority.

## Entry points

Install `requirements-subsecond-r1.txt` in an isolated environment. Existing
repo regression tests additionally use the existing workflow dependencies:
requests 2.32.3, websockets 14.2, pycryptodome 3.23.0.

All commands below run from the repo root:

```
python tools/research/world_pm_subsecond_capture_r1.py --config bound-config.json
python tools/research/world_pm_subsecond_analyze_r1.py --input capture.jsonl --output analysis.json
python tools/research/world_pm_subsecond_offline_acceptance_r1.py --output offline-acceptance.json
```

The first command only validates config and makes zero network requests.
The second reads an existing journal offline. The third runs synthetic tests
behind network guards and writes the acceptance report/log.

The capture command's explicit `--capture` switch is for a later, approved
cohort only. It rejects unratified binding and elapsed starts. It does not
advance or replace the fixed cohort. This task did not use that switch.

## Config binding (section 2.5 is still open)

Config fields:

| Field | Required binding |
|---|---|
| schemaVersion | WORLD_PM_SUBSECOND_R6_CAPTURE_R1 |
| startTs/endTs | Exact market-aligned 12h UTC interval |
| bindingRatified | true only after actual section 2.5 binding approval |
| markets | All 144 ordered consecutive market identities: startTs/endTs/worldTicker |
| market.pmUpToken/pmDownToken | Exact outcome IDs, or PM exact-slug discovery below |
| pm.mode | websocket or rest, fixed before cohort |
| pm.websocketUrl | Public market WebSocket endpoint, for websocket mode |
| pm.booksUrl | Public POST /books batch endpoint, for REST mode |
| pm.discoveryUrl | Optional public Gamma events endpoint; exact BTC5m slug only |
| world.url | Bound public GET history endpoint; no unverified URL default |
| world.query | Bound query shape; templates ticker/start_ts/end_ts/query_start/query_end |
| world.sidePaths | JSON key paths to yes/no arrays |
| world.fields | source_time/bid/ask source field names |
| world.identityFields | Stable original history-row identity fields, e.g. ts/slotId |
| world.lookbackSeconds | Bound request overlap; repeated rows never refresh availability |

Request-shape example only, not a verified endpoint/config:

```
"query": {"ticker": "{ticker}", "start_ts": "{query_start}",
          "end_ts": "{query_end}", "resolution": 1},
"sidePaths": {"yes": ["yes"], "no": ["no"]},
"fields": {"source_time": "ts", "bid": "bid", "ask": "ask"},
"identityFields": ["ts", "slotId"]
```

Transport limitation: the current World adapter supports public GET reads.
It does not extract JWTs, perform OAuth or impersonate an MCP session. If
section 2.5 binds a different HTTP method/authenticated MCP transport, adapt
the reader within that approval before sampling; do not pretend GET is the
verified World contract. No keys, cookies, wallet or account data were accessed.

Official PM market WS documentation used for the application PING/PONG and
book/price_change contract:
https://docs.polymarket.com/market-data/realtime-data
Actual endpoint and heartbeat binding must still be documented before capture.

## Capture and evidence behavior

- Raw responses, normalized quotes, market identities, PING/PONG, integrity
  gaps, grid anchors and footer are appended to an exclusive-create JSONL file.
- World requests are serial at 1s anchors, timeout 5s, retry once after 2s;
  missed anchors are skipped. Raw receipt and post-normalization availability
  are separate. First appearance fixes each row's usable receipt forever.
- Current World state uses newest already-available source history. Late older
  history does not roll the current quote backwards. Source-time rounding stays
  UNKNOWN. Future-dated source metadata is rejected at the analysis endpoint.
- PM requires full snapshots on startup/reconnect; PONG does not restore a
  missing book or rewrite quote time. Healthy quiet books remain valid.
- PM requests/events arriving beyond a market end are not ingested. In-memory
  quote cache is released after each market; evidence remains on disk.
- Wall/monotonic pairs, clock resolution and regression/freeze errors are
  recorded. Footer carries observed clock reliability, never a hardcoded PASS.
  Absolute UTC synchronization and deployment transport delays remain part of
  pre-capture binding; these offline tests do not prove external clock accuracy.
- No order, signing, simulation, wallet or transaction methods are present.

## Analysis behavior

Strict receipt < endpoint, same-market quotes, every feature/label endpoint
qualified. Missing book/mid remains unknown, never zero. PM has no price-age
cutoff; World retains 3s receipt/5s source age. PM gap intervals expand 30s and
exclude any touching feature/label interval. Market ends are half-open.

All transforms and fits use the identical final training rows. OLS uses
`numpy.linalg.lstsq(rcond=1e-12)`, intercept included. Residual std <=1e-10 drops
the feature; dropping both reuses baseline predictions exactly. Ridge alpha
equals final n_train, implementing MSE + 1.0*L2 with unpenalized intercept.

Inner 0-4h/4-6h selects maximal paired improvement, ties smaller k. Outer fit
uses first-half common rows and validates k* only. Holdout data do not select
lag, coefficients, scaling, residual projection or feature drops.

Paired 5min and UTC 15min bootstrap uses S/N row weighting, nonempty blocks,
10,000 draws, seed 42, percentile 95% CI. 5min determines the calculation gate;
15min disagreement is disclosed. The report contains coverage/rejection
reasons, source update counts, nonzero changes, per-window correlations,
pm_update_age, MSE improvement, predicted cents/share, direction diagnostics,
and past/synchronous comparisons. Diagnostics never reselect k*.

Q1 is reported separately as unresolved at integer source-time precision;
receipt-time Q2 remains independent. GO is only this frozen research screen;
it grants no execution/paper/live admission. Data/calculation/undefined-corr
NO_RESULT has priority over GO/NO-GO.

Numerical implementation choices recorded for reproducibility: float64,
population std (ddof=0), sklearn SVD Ridge, NumPy default_rng/PCG64 and linear
percentile interpolation. None was optimized on market observations.

## B4 and reproducibility

Durable machine report/log:
`evidence/subsecond-leadlag-implementation-r1/b4-offline-acceptance.json` and
`b4-offline-acceptance.log`.

All 37 new offline tests PASS:

- Exact PM-linear World copies: both residuals dropped in all inner/outer
  fits; outer Delta=0 and primary CI=[0,0].
- Validation replacement: inner fit parameters identical after changing 4-6h;
  outer fit parameters and k* identical after changing 6-12h.
- Missing required endpoint: row removed with WORLD_MID_INVALID; complete
  unchanged price: same anchor retained, Y=0. Old healthy PM books are retained.
- Additional tests cover common-grid gates, upfront purge, unchosen holdout
  horizons, undefined correlation, block weighting, causal receipt boundaries,
  gap/heartbeat state, retry and standalone offline CLIs.

Full repository regression: 125 tests PASS (`python -m unittest discover -s
tests -v`). All `tools/research/*.py` compile. The regression initially lacked
the older modules' `websockets` dependency in the isolated environment; installing
the existing workflow pins resolved the import errors without modifying them.
Full log: `evidence/subsecond-leadlag-implementation-r1/full-regression.log`.

NumPy 2.4.6 GELSD verification:

- official release commit `b832a09cf2a169c833dd2371e7c07aa00b293242`;
- installed `_linalg.py` and official source match after LF normalization,
  SHA-256 `e47d0e2aed21361291cd6651999a59e6479ba5866abb8eabb1dbea5b0d1618ac`;
- `_linalg.py:lstsq -> _umath_linalg.lstsq -> call_gelsd -> dgelsd` for float64;
- local BLAS/LAPACK scipy-openblas 0.3.31.188.0, USE64BITINT DYNAMIC_ARCH.

Source: https://github.com/numpy/numpy/blob/b832a09cf2a169c833dd2371e7c07aa00b293242/numpy/linalg/umath_linalg.cpp

Runtime acceptance used local CPython 3.11 with pinned requirements. Re-run
offline acceptance and record versions/build evidence on the eventual capture
runner before section 2.5 is completed. These tests establish implementation
behavior, not real transport coverage or a prospective signal verdict.

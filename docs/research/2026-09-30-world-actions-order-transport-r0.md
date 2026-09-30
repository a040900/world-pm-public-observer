# World /order GitHub Actions transport control R0

Observer evidence only; authority decision unchanged. NO_TRADE: TRUE.

## Result

GitHub Actions run [36735803165](https://github.com/a040900/world-pm-public-observer/actions/runs/36735803165)
completed successfully as a diagnostic. Quote/build did not succeed.
Workflow source commit: `e923e9b01440942f8431efe6848661e2dd84fb58`.
Runner: GitHub-hosted `ubuntu-latest`, Python 3.12, four-minute job cap.

| Observation | Local Windows | GitHub Actions |
|---|---|---|
| World GET `/check-jurisdiction` | HTTP 200, `{"restricted":false,"country":"MY"}` | HTTP 200, `{"restricted":true,"country":"US"}` |
| JWT-bearing CASH → YES | 404, empty | 404, empty |
| JWT-bearing CASH → NO | 404, empty | 404, empty |
| Same-window YES without JWT | 404, empty | 404, empty |
| Output amount, route, built transaction | None | None |

Jurisdiction endpoint:
`https://markets-api-proxy.world-xyz.workers.dev/check-jurisdiction`.
These are the countries reported by World for these specific requests; they are
not independent physical-location verification. In particular, the current
local request was reported as MY, so a Taiwan egress explanation is not supported
by this receipt. The runner was reported as restricted US and cannot serve as
the assumed permitted-jurisdiction positive control.

## Bounded quote controls

The normal browser JWT supplied earlier by the user was forwarded through one
temporary repository Actions secret. Its unverified expiry claim was
2026-09-30T15:40:52Z / 23:40:52 Asia/Taipei. All requests preceded that expiry;
this does not prove the server still accepted the token. The secret was deleted
after artifact acquisition, and `gh secret list` confirmed no remaining matching
secret. No full token is saved in workflow source, artifacts, or repository files.

The runner used one on-chain discovered current World BTC5m window:
15:15–15:20 UTC / 23:15–23:20 Asia/Taipei.

```text
market: 2Jg3GZfjwetHZ8aFNRFqUYavhYfd93TMW8CDAugMktAi
YES: C6Q2YFxSGSRnKy8WGJH16n6NNgCYyV8xVCUWffbqPBgQ
NO: G2o2CcqkX97N8PXXtJmwWPWinL2uMqN6aZ6HCZqGT9j1
```

One later local control used the next current window:
15:20–15:25 UTC / 23:20–23:25 Asia/Taipei.

```text
market: 5wDgHeA1HPXMKZ7JUz8uek2XXoVc8opK5Nd98Bm9cD4B
YES: 83oxQWUrNJPmcoSK7cGZurvvX2W8Bw3Cu1w14uVU45bb
NO: J328tVk8pfUUi6QJePhvyepvFcYd3gaNAKbuqZXtXkJR
```

Both used the exact already reconstructed GET `/order` contract, no body,
Bearer JWT, amount 1,000,000 CASH atoms, slippage 200 bps, existing public signer,
and frontend defaults `prioritizationFeeLamports=auto`,
`dynamicComputeUnitLimit=true`. No DFlow API key was used. Discovery reused
prediCt getProgramAccounts, mint getMultipleAccounts, and public metadata, never
recent signatures. Each environment made two JWT-bearing calls plus one matched
no-JWT YES control; there were no quote retries or polling collectors.

The prior local 14:50–14:55 UTC batch returned authenticated YES `route_not_found`
and NO `sponsorship_unavailable`, with an empty-404 no-JWT control. This later
batch no longer reproduces that positive auth-handling difference even locally.
Do not infer that only the runner's country caused its 404, that JWT IP binding
is proven, or that unexpired claims guarantee acceptance. Rejection, revocation,
source-IP binding, jurisdiction, and other access conditions remain unisolated.
The two environments' quote windows were adjacent, not a simultaneous same-mint
experiment. World jurisdiction data alone does not establish transaction
eligibility or the internal `/order` authorization policy.

## Verification, evidence, and stop

[Evidence manifest](../../evidence/world-actions-order-transport-20260930/manifest.json)
records SHA-256 for the unmodified downloaded runner receipts, local receipts,
and run metadata. Offline verification passed for both order results: response
body hashes, in-window current mint binding, allowed discovery RPC methods,
redacted Authorization, and the matched no-JWT controls. Python compilation and
git diff checks passed. Workflow success is execution success of the diagnostic,
not `WORLD_PUBLIC_FILLABLE_QUOTE_PATH_CONFIRMED`.

Conservative verdict: `NO_RESULT_CURRENT_FRONTEND_CONTRACT_UNRESOLVED` for
successful fillable quote/build reproduction. Narrow outcome:
`RUNNER_RESTRICTED_AND_REUSED_JWT_REJECTED_ON_RUNNER_AND_LOCAL`.

The remaining prerequisite is a newly acquired normal World browser JWT with a
successful authenticated positive control in an environment World reports as
unrestricted. No further retries with this rejected token are informative. No
new remote region was provisioned, no CAPTCHA bypass or session extraction was
used, and no signing, simulation, submit/sendTransaction, broadcast, collector,
economic sampling, research protocol change, or authority adjudication occurred.

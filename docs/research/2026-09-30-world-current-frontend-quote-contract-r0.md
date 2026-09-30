# World current frontend quote/build contract — bounded diagnostic R0

Date: 2026-09-30. Observation: 13:05:17–13:05:26 UTC, 21:05:17–21:05:26 Asia/Taipei.
Status: observer evidence only; reviewer adjudication pending. NO_TRADE: TRUE.

Diagnostic verdict: `WORLD_PUBLIC_FILLABLE_QUOTE_PATH_NOT_CURRENTLY_OPEN`.

The deployed frontend still uses the same aggregator `/order` endpoint, but now
its shared HTTP client supplies an anonymous JWT acquired through Cloudflare
Turnstile. No API key is visible in this quote call. Keyless does not mean
unauthenticated. Both current BTC5m probes with complete frontend query parameters
and without that JWT returned HTTP 404 with exactly zero response bytes.

This is not an economic adjudication. The authority decision in
`a040900/prediction-market-relative-value` was not modified. Its existing
`PARK_OPERATIONAL_ACCESS_BLOCKED` remains outside this diagnostic's authority.

## Current frontend/version evidence

Source: a new GET of `https://world.xyz`, not the Chainstack repository description.
HTML identifies `/assets/index-BAcxOBUf.js`; Netlify RUM deploy ID is
`6abc4e29678c52000896311b`, deploy branch `main`, context `production`.
The retrieved trade path includes:

| Asset | Role | SHA-256 |
|---|---|---|
| `index-BAcxOBUf.js` | entry and lazy asset references | `7092705158b69d08f83faade515a285d558cc55fe37883acdd499813d6c3b5c7` |
| `api-CcCaG8Rc.js` | proxy base URL and CASH mint | `d70db74d9564dee3052d1a2892c405cf8b3eabf5abb6ab096e84bc19ba84660e` |
| `MarketPageLayout-DnRa5_bI.js` | wallet/mint/amount quote parameters | `bcb368fa6187c7beeb8a67e977ae821926584c8f3aa4a64cbf5314a4eecbe21f` |
| `ScrollRail-C_2q7fPE.js` | `/order`, response validator and signing/submission flow | `b5a2149f03569f9e73470e5918c79e6375e78b0258e1becf474b97768b57e667` |
| `client-TeZRcSG0.js` | Turnstile exchange, JWT cache and Authorization injection | `6cf31b2febf9303ce2d8d5aba0fbdda4ed55eb1c388dcbbfe439d404a3d7f5e0` |

Full downloaded bytes, HTTP headers, the bounded dependency inventory and source
excerpts with offsets are in `evidence/world-frontend-quote-contract-20260930/`.
No `price-*.js` reference appeared in the downloaded dependency inventory; the
current trade quote wrapper was located directly in `ScrollRail-C_2q7fPE.js`.
This does not assert that every other frontend chunk was exhaustively examined.

## Exact frontend request contract

Endpoint: `https://aggregator-api-proxy.world-xyz.workers.dev/order`.
Method: GET. Request body: none.

The trade hook supplies:

```text
userPublicKey=<connected wallet public key>
inputMint=<input mint>
outputMint=<YES or NO outcome mint>
amount=<integer raw token atoms as a string>
slippageBps=<wallet setting>
```

The order wrapper prepends defaults, unless explicitly overridden:

```text
prioritizationFeeLamports=auto
dynamicComputeUnitLimit=true
```

It serializes defined values using `URLSearchParams` and `String(value)`.
For a default CASH buy, input amount conversion uses six decimals.
No prediction-specific flag, order nonce, or frontend-generated order request ID
is added by this traced path. `orderToken` is a response value, not a query nonce.
The frontend does not request the trade quote until a wallet is connected.
Backend validation of `userPublicKey` could not be isolated behind the auth gate.

The shared client uses:

```text
Authorization: Bearer <anonymous JWT>
```

That token is obtained through:

```text
POST https://aggregator-api-proxy.world-xyz.workers.dev/auth/token
Content-Type: application/json
x-turnstile-token: <Cloudflare Turnstile token>

{"turnstileToken":"<same Cloudflare Turnstile token>"}
```

Current public Turnstile site key: `0x4AAAAAAEwanVzzTs5-bojG`. The widget uses
`appearance: interaction-only`, with a hidden initial surface and an interactive
modal escalation when needed. The exchange response is consumed as
`{token, expiresIn}`; `expiresIn` defaults to 3600 seconds in the client.
JWT state is stored in localStorage under `TURNSTILE_JWT` with token, expiry and
interactive fields; the cache expires one minute before the supplied lifetime.
The client also schedules background refresh for noninteractive acquisitions.

No DFlow API key or wallet signature is attached to this GET. There is no explicit
`credentials: include` setting or cookie injection in the wrapper. This establishes
the frontend contract, not universal backend acceptance without any browser state.
Origin/Referer are browser-generated. Our requests supplied both World values;
their independent necessity was not tested. Responses allow Origin `https://world.xyz`,
headers `Content-Type, Authorization, x-turnstile-token`, and methods GET/POST/OPTIONS.

No stored session, real challenge token, bearer JWT, private key or wallet was used
by this diagnostic. There was no attempt to solve or bypass Turnstile.

## Current BTC5m on-chain discovery and probes

Reused `world_pm_jupiter_economic_poc_r0.program_markets_for_start`,
`describe_candidates` and `select_btc` from observer base commit
`16780b8bad636f88210241cd835c3a89a8f5fcb8`.
Discovery used filtered prediCt `getProgramAccounts`, `getMultipleAccounts` and
the YES mint's public metadata URI. It did not use recent-signature discovery.
Raw program/mint RPC response values are retained in `result.json.rpcReceipts`.

Frozen diagnostic identity:

```text
Window: 2026-09-30 13:05:00–13:10:00 UTC / 21:05:00–21:10:00 Asia/Taipei
Market: 8zvdZniqXLaAwACcrqDuBuVZvKiskGto2eE2gG2YM6dQ
YES: Dc8bEZwqYdNQcfhgtFUtFTxXHHq1vNJtLCU3TPwNGoyA
NO: H3c6FSSRMd7BDV9BTE3WpP4Wup1rx7iGZQ6EJAVvHv9x
CASH: CASHx9KJUStyftLFWGvEVf59SGeG9sh5FfcnZMVPCASH
```

Metadata description: `BTC/USD closes at or above its open between 2026-09-30
13:05:00 UTC and 2026-09-30 13:10:00 UTC.` Both probes began during that window.

Public mainnet signer pubkey supplied solely for construction parameters:
`7mwwqMKUeoWrCYefWYzczpFnNy5BMyBEfmfGV9SpzC9e`, from existing transaction
`38SYKSL8XhrrDkbb1hgHCpqnA8wvHCakKMCAwYLAE5ZBRE9mSiDupxxuRSM355843wByw4WpfwmGvGqfyvdWb6sX`.
No balance request, wallet access or new signature was needed.

| Probe | Input atoms | Slippage | HTTP | Body | Output/route/build |
|---|---:|---:|---:|---|---|
| CASH → YES | 1,000,000 | 200 bps | 404 | 0 bytes | absent |
| CASH → NO | 1,000,000 | 200 bps | 404 | 0 bytes | absent |
| `/auth/token`, missing-token negative control | n/a | n/a | 404 | 0 bytes | absent |

Each quote included `userPublicKey`, `prioritizationFeeLamports=auto`,
`dynamicComputeUnitLimit=true`, input/output/amount/slippage, Origin and Referer.
The exact URLs, request/response headers, timestamp, body and empty-body SHA-256
are retained in `result.json`. The auth negative control sent only `{}` and no
`x-turnstile-token`; it is not a successful exchange or proof of JWT issuance.

## Response schema and built transaction

The deployed frontend validator expects:

```text
executionMode: "sync" | "async"
inAmount: string
inputMint: string
outAmount: string
outputMint: string
slippageBps: number
lastValidBlockHeight: number
transaction: string
orderToken: string
routePlan?: [{inAmount: string, inputMint: string,
              outAmount: string, outputMint: string, venue: string}]
sponsorFee?: {mint: string, amount: string}
platformFee?: {amount: string, feeBps: number, mode: string} | null
```

`priceImpactPct` is not declared by this current validator. Its presence in a
successful server response is untested; an older schema must not be assumed.
No indicative bid/ask was substituted for a fillable quote.

Source shows base64 decoding of `transaction`, versioned deserialization with
legacy fallback, wallet signing, then POST `/submit` with signed `transaction`
and the server-returned `orderToken`. Those later steps were inspected as source
only and were never invoked. They are outside this reproduction's scope.

No serialized transaction was obtained, so programs/accounts/instructions could
not be inspected and no current routePlan could be compared. In particular,
`DFlow Prediction Market Router` is not confirmed by a current returned route.
The inspected assets do not hardcode that venue label. The frontend accepts a
generic `venue: string`; retaining the aggregator URL does not prove a venue.

## Why the old request returned 404

The old probe used the correct still-current host, GET method and `/order` path,
but omitted the current anonymous Bearer authentication flow. It also omitted
the wallet public key and the two wrapper defaults. Adding those three query
fields with valid current on-chain mints still returned the same empty 404.

Crucially, the current HTTP client explicitly tests `status === 404` plus
`clone().text() === ""` after a bearer-authenticated request. It then removes
the cached JWT and obtains a replacement with trigger `rejected` before retrying.
Thus an empty 404 is deliberately recognized by the frontend as a token-rejection
signal, not sufficient evidence that `/order` was removed.

The auth flow is the concrete missing step established by source. Authentication
gating is the strongly supported explanation for the old 404 and the complete-query
404. Because no accepted JWT positive control was attempted, this diagnostic
cannot prove that missing authentication is the sole server-side cause or exclude
a simultaneous upstream outage. It does not assert successful current quote/build
operation after authentication.

## Reproduction and validation

```powershell
python -m tools.research.world_frontend_quote_contract_r0 --output evidence/world-frontend-quote-contract-20260930
python -m tools.research.world_frontend_quote_contract_r0 --output evidence/world-frontend-quote-contract-20260930 --verify
```

Run the first command into a new output directory to preserve this dated receipt.
The second command works offline: it verifies raw asset hashes, exact source
excerpt offsets, correct current-mint probe binding, in-window timestamps and
the allowed discovery method set. It passed. Python compilation and
`git diff --check` also passed.

Initial local Python default-UA HTML read returned Cloudflare HTTP 403 `error code:
1010`; the diagnostic uses its explicitly named research User-Agent and fetched
the public static assets successfully. This static-asset transport result is
separate from `/order`'s empty-body auth-gate 404. No challenge was bypassed.

The stop condition has been reached: the current frontend contract is located,
but its no-auth quote/build reproduction is unavailable. No collector, prospective
edge sample, simulation, signature, submit/sendTransaction, broadcast, Polymarket
execution, maker-bytecode investigation or settlement analysis was performed.

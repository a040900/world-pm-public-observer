# World browser JWT /order positive control — bounded diagnostic R0

Observer evidence only. NO_TRADE: TRUE. Authority reviewer adjudication pending.

The user supplied a JWT from the normal Chrome World's WebSocket URL. This token
was used only as a Bearer header on two read-only GET `/order` calls to
`https://aggregator-api-proxy.world-xyz.workers.dev`. No DFlow API key was used.
The token is not persisted in these files; only its SHA-256 and unverified
`iat`/`exp` claims are recorded. The JWT IP claim is omitted. The original
Turnstile interaction and `/auth/token` issuance response were not captured, so
this batch does not independently prove their details or verify the JWT signature.

## Current market and exact request

On-chain current-window discovery reused the existing observer helper using
prediCt `getProgramAccounts`, mint `getMultipleAccounts`, and public YES metadata.
No recent-signature discovery was used. Frozen window: 2026-09-30
14:50–14:55 UTC / 22:50–22:55 Asia/Taipei.

| Field | Value |
|---|---|
| Market | `AGQKeUXfjvAAkyjjrToPXx7MG6Nsw7Wrkpk33nXjwJX3` |
| YES | `3TGjpbSA18skp1QL39wtYMaaKAvXSKTXqhuYssVaoP5n` |
| NO | `8RH14Jrgctm8M4Qm8cNCvefsk9MBVkShJpKnp4Ghn8Wq` |
| CASH | `CASHx9KJUStyftLFWGvEVf59SGeG9sh5FfcnZMVPCASH` |

Endpoint: `GET https://aggregator-api-proxy.world-xyz.workers.dev/order`.
No body. Header `Authorization: Bearer <normal World browser JWT>`; Origin
`https://world.xyz`, Referer `https://world.xyz/`, Accept `application/json`.
Same query contract as the captured deployed frontend:

```text
prioritizationFeeLamports=auto
dynamicComputeUnitLimit=true
userPublicKey=7mwwqMKUeoWrCYefWYzczpFnNy5BMyBEfmfGV9SpzC9e
inputMint=CASHx9KJUStyftLFWGvEVf59SGeG9sh5FfcnZMVPCASH
outputMint=<current YES or NO above>
amount=1000000
slippageBps=200
```

The public signer parameter is the same existing public mainnet signer already
documented in the frontend-contract batch. No private key or wallet was accessed.

## Observed responses

| Probe | Time UTC | HTTP | Exact response body |
|---|---|---|---|
| CASH → YES, with JWT | 14:53:45 | 400 | `{"msg":"Route not found","code":"route_not_found"}` |
| CASH → NO, with JWT | 14:53:46 | 503 | `{"error":"sponsorship_unavailable"}` |
| Same YES request without JWT, same window | See receipt | 404 | Empty, 0 bytes |

This matched control strongly supports that the JWT-bearing request passed the
empty-404 rejection stage and reached route/build-related handling. We cannot
observe internal Worker auth validation. HTTP 400 says no route was returned for
this exact YES request. HTTP 503 names sponsorship availability for this exact NO
request; it does not identify the sponsor dependency or prove NO route depth.
Do not infer a remedy, change sponsor flags, or substitute a funding wallet from
these errors. No retries or additional market windows were sampled.

Neither authenticated response includes `outAmount`, `routePlan`, or a built
transaction. Consequently there is no transaction to deserialize and no current
returned venue confirming `DFlow Prediction Market Router`.

Conservative requested verdict: `NO_RESULT_CURRENT_FRONTEND_CONTRACT_UNRESOLVED`
for a successful fillable quote/build reproduction. Narrow diagnostic outcome:
`NO_RESULT_AUTHENTICATED_QUOTE_BUILD_UNAVAILABLE_OBSERVED_ERRORS`.
The endpoint and request contract are known; JWT-bearing error responses are
observed. `WORLD_PUBLIC_FILLABLE_QUOTE_PATH_CONFIRMED` is not established.
This batch also cannot justify a blanket claim that a DFlow API key is required
or that all normal authenticated World requests fail.

## Evidence, verification, and handoff

- [Result](../../evidence/world-authenticated-quote-probe-20260930/result.json)
- [Diagnostic](../../tools/research/world_authenticated_quote_probe_r0.py)
- [Prior frontend contract](2026-09-30-world-current-frontend-quote-contract-r0.md)
- [Prior local-runtime and Manifest checks](2026-09-30-world-jwt-manifest-access-validation-r0.md)

The previous browser-startup blocker is superseded: both the in-app browser and
Chrome were subsequently opened successfully. Their currently exposed browser
interfaces still lack network response/session inspection. The user-provided
normal browser token enabled this bounded HTTP positive control without reading
stored personal browser sessions through an alternate automation interface.

Offline verification:

```text
python -m py_compile tools/research/world_authenticated_quote_probe_r0.py
python -m tools.research.world_authenticated_quote_probe_r0 --output evidence/world-authenticated-quote-probe-20260930 --verify
git diff --check
```

Only a new output directory may be used for a separately authorized run. The
diagnostic consumes the JWT on stdin and refuses expired tokens. Do not persist
tokens in shell scripts, evidence, reports, or repository files. Source inspection
of token exchange remains distinct from a captured issuance response.

No signing, simulation, submission, sendTransaction, broadcast, economic sample,
collector, protocol change, or authority decision edit occurred. Stop condition
reached: the normal JWT has been tested against the exact current-window quote
surface, and the returned failures are now concrete. Report to the reviewer;
this diagnostic does not reopen execution or capital authorization.

# World normal JWT path and existing Manifest books — bounded validation R0

Date: 2026-09-30. Observer evidence only; authority adjudication remains with the
reviewer in `a040900/prediction-market-relative-value`. NO_TRADE: TRUE.

## Results

| Candidate | Observed result | Interpretation |
|---|---|---|
| Normal World Turnstile → anonymous JWT → `/order` | `NO_RESULT_NORMAL_BROWSER_RUNTIME_UNAVAILABLE` | No JWT or authenticated order response obtained. The quote/build positive control remains unresolved. |
| Existing Manifest books for current World BTC5m outcome/CASH pairs | `NO_EXISTING_BOOK` in all four exact pair orientations | No existing book or depth to use for these exact pairs in this observed window. |

These results do not establish that every keyless execution alternative is
unavailable. The normal World authenticated path remains an untested candidate;
the Manifest result is limited to the discovered market and CASH pairs below.
Neither result changes the economic or execution authority decision.

## Normal World authentication attempt

The deployed contract was established in
[the frontend contract diagnostic](2026-09-30-world-current-frontend-quote-contract-r0.md),
commit `08bea76381e7509d0a025a5db063b064c9fd40f7`:

- Normal Turnstile token acquisition on `https://world.xyz`.
- `POST https://aggregator-api-proxy.world-xyz.workers.dev/auth/token`, JSON body
  `{"turnstileToken":"<normal challenge token>"}`, with the same token in
  `x-turnstile-token` and `Content-Type: application/json`.
- The client consumes `{token, expiresIn}` and sends `Authorization: Bearer <JWT>`
  on `GET https://aggregator-api-proxy.world-xyz.workers.dev/order`.
- Order query: `prioritizationFeeLamports=auto`,
  `dynamicComputeUnitLimit=true`, `userPublicKey`, `inputMint`, `outputMint`,
  `amount` in atoms, and `slippageBps`. No request body or DFlow API key is visible
  in this frontend call.

The positive control could not start. Two browser-tool initializations failed
with `trusted Node process exited unexpectedly; kernel reset, rerun your request`.
The bundled Computer Use initialization also failed with
`windows sandbox failed: permission path cannot be represented losslessly`.
Opening the URL through the app returned `queued`; rendering was not confirmed.

No normal Turnstile token was acquired, no JWT was obtained, and no authenticated
`/order` request was made. Therefore there is no response schema observation,
built transaction, or route venue observation from an authenticated call. These
are local runtime failures, not World authentication or quote failures. No
challenge bypass, stored browser token extraction, or wallet connection was used.

Attempt record:
[result.json](../../evidence/world-authenticated-quote-check-20260930/result.json).

## Existing Manifest books

Discovery reused the observer's on-chain current-window market discovery; it did
not use recent transaction signatures. Frozen market window:
`2026-09-30T13:30:00Z` to `2026-09-30T13:35:00Z` (21:30–21:35 Asia/Taipei).

| Field | Address |
|---|---|
| World BTC5m market | `7MhFoXPPZi9CbGp78buCwoKHuuMYqtyomip3VJkZ2eEt` |
| YES | `8jsirD4PSu21Bf4gsiVmsoPYmpeaXGzRQRQqVsWJG5if` |
| NO | `J835HJeBt7jLD8MSjgd2ZMrNys34MT9SPcHyk3PmD787` |
| CASH | `CASHx9KJUStyftLFWGvEVf59SGeG9sh5FfcnZMVPCASH` |
| Manifest program | `MNFSTqtC93rEfYHB6hF82sKdZpUDFWkViLByLd1k1Ms` |

Four read-only JSON-RPC calls to `https://api.mainnet-beta.solana.com` used
`getProgramAccounts`, program owner above, `commitment=confirmed`,
`encoding=base64`, `dataSlice={offset:0,length:0}`, and exact mint `memcmp`
filters at offsets 16 (base mint) and 48 (quote mint).

| Base mint | Quote mint | Query time UTC | HTTP/RPC result |
|---|---|---|---|
| YES | CASH | 13:30:23 | 200; `result=[]` |
| NO | CASH | 13:30:24 | 200; `result=[]` |
| CASH | YES | 13:33:33 | 200; `result=[]` |
| CASH | NO | 13:33:33 | 200; `result=[]` |

There were zero matching accounts in all four successful calls, no RPC errors,
and no retries. Therefore no book or order depth was available to inspect. No
book was created and no Manifest swap/build was attempted. This is not a claim
about other collateral, other venues, other windows, or economic expectancy.

The mint offsets follow the public Manifest adapter in
[Chainstack's `manifest.py`](https://github.com/chainstacklabs/world-xyz-limit-orders/blob/main/mmkit/venue/manifest.py).
No Manifest bytecode or maker protocol research was performed.

Evidence:
[result.json](../../evidence/world-manifest-btc5m-depth-20260930/result.json).
Receipts preserve exact request params, timestamps, HTTP headers, parsed RPC
responses, and response-byte SHA-256 recorded at acquisition. Original response
bytes were not persisted, so their byte hashes cannot be independently recomputed
from this file. Derived canonical JSON hashes are verified offline and do not
replace that limitation.

Diagnostic:
[world_manifest_btc5m_depth_probe_r0.py](../../tools/research/world_manifest_btc5m_depth_probe_r0.py).
The fresh diagnostic is bounded to one current window and four exact book
queries. Nonempty books require a separately validated depth parser; this batch
does not infer fillability from account existence.

Offline validation (no network):

```text
python -m py_compile tools/research/world_manifest_btc5m_depth_probe_r0.py tools/research/verify_world_manifest_btc5m_depth_probe_r0.py
python tools/research/verify_world_manifest_btc5m_depth_probe_r0.py evidence/world-manifest-btc5m-depth-20260930/result.json
git diff --check
```

## Boundary and next unresolved test

No API key, session credential, private key, signature, simulation,
`sendTransaction`, broadcast, book creation, prospective edge sample, or collector
was used. The authority repository and research protocol were not modified.

The remaining narrow test is a normal successful browser Turnstile/JWT acquisition
followed by the already reconstructed read-only `/order` request. Until that
positive control exists, the shortest keyless quote/build candidate is unresolved.

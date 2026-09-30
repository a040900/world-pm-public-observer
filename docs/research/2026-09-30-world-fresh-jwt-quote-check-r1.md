# World fresh browser JWT quote/build check R1

Observer evidence only. NO_TRADE: TRUE. Authority decision unchanged.

A new normal browser JWT supplied by the user was tested locally on Windows,
using the same reconstructed frontend GET `/order` contract, existing public
signer parameter, 1,000,000 CASH atoms, and 200 bps slippage. Only the token's
hash and unverified issue/expiry times are retained; no full JWT or IP claim is
saved. No GitHub secret or runner was used in this batch.

One on-chain discovered current BTC5m window: 2026-09-30 15:45–15:50 UTC /
23:45–23:50 Asia/Taipei. Discovery used the existing prediCt/mint/metadata helper,
not recent signatures.

```text
market: Hd58Pq1pxQM6f6bCw8eZ9DnG3i52yaiHdhwd3fMHriJh
YES: JnpBprvUEiKk7ouiqnkbPnszU4mGdvxEmon3LWDM5cG
NO: GQzoT9JfeLs9GAnV1ebpuo9Jy9huxQ8eWMiKoESvKRmp
CASH: CASHx9KJUStyftLFWGvEVf59SGeG9sh5FfcnZMVPCASH
```

| Request | Time UTC | HTTP | Body |
|---|---|---|---|
| CASH → YES with fresh JWT | 15:46:59 | 503 | `{"error":"sponsorship_unavailable"}` |
| CASH → NO with fresh JWT | 15:47:01 | 503 | `{"error":"sponsorship_unavailable"}` |
| Same YES without JWT | 15:47:02 | 404 | Empty |

A subsequent local World `/check-jurisdiction` request returned HTTP 200,
`{"restricted":false,"country":"MY"}`. This is World's classification of that
request, not proof of physical location or transaction eligibility.

The fresh token restores the differential previously lost with the old JWT:
JWT-bearing requests reach a concrete sponsorship error, while the matched
unauthenticated request produces the client's empty-404 token-rejection shape.
This strongly supports passing that rejection stage but does not independently
observe internal JWT validation or token issuance. Why the old unexpired token
was rejected remains unresolved.

The current frontend `ScrollRail-C_LU1hSf.js` maps this error to
`Gasless trading is temporarily unavailable`. That wording does not prove a
global outage or identify the underlying sponsor dependency; this diagnostic
uses a public existing signer, not an authenticated owner's wallet integration.
No sponsor flag, alternate payer, or private wallet was guessed or tested.

Neither response has an output amount, routePlan, or built transaction. No
transaction deserialization or current DFlow venue confirmation is possible.
Success verdict `WORLD_PUBLIC_FILLABLE_QUOTE_PATH_CONFIRMED` remains unestablished;
the conservative requested verdict remains
`NO_RESULT_CURRENT_FRONTEND_CONTRACT_UNRESOLVED` for successful reproduction.
Narrow result: fresh-JWT auth handling difference restored; both sides blocked
by observed sponsorship error.

[Evidence and hashes](../../evidence/world-fresh-jwt-quote-check-20260930-r1/manifest.json).
Offline response hashes, current-window binding, matched no-JWT control,
redaction, allowed RPC methods, and git diff checks passed. No retries, signing,
simulation, sendTransaction, broadcast, collector, economic sampling, or protocol
and authority changes occurred. This one-window diagnostic is complete; no
repeat quote sampling is authorized by this result.

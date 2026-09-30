# Owner-public-key quote diagnostic R0 — result

Observer evidence only. NO_TRADE: TRUE. Authority decision unchanged.

The owner supplied a fresh browser JWT and authorized quote/build probes with
their specified public key. Local Windows testing used the existing frontend
contract and 1,000,000 CASH atoms / 200 bps slippage. No owner's account balance
was read and no SOL was transferred. The owner's public key is redacted from
repository evidence; the local unredacted receipt hash is retained for provenance.
No full JWT, JWT IP claim, or Turnstile token is saved. The supplied Turnstile
token was not exchanged because a fresh JWT was already available.

One on-chain discovered BTC5m window: 2026-09-30 18:05–18:10 UTC /
2026-10-01 02:05–02:10 Asia/Taipei. Market:
`6Nnf3vWkaEgeRAp97au9mmNMwYBzDM4fDfj9ji3GZnN9`.
YES: `7vXGv2xewjpZe2LwgdxAbJXNW46zRBtbKtxbq8iHSXju`.
NO: `6xZmZzC7YWAAfSSpaK3pJCgzyAbwsTxBf2gU3EXR7sqP`.
CASH: `CASHx9KJUStyftLFWGvEVf59SGeG9sh5FfcnZMVPCASH`.

| Probe | Time UTC | HTTP | Body |
|---|---|---|---|
| CASH → YES with owner public key and JWT | 18:07:59 | 503 | `{"error":"sponsorship_unavailable"}` |
| CASH → NO with owner public key and JWT | 18:08:01 | 503 | `{"error":"sponsorship_unavailable"}` |
| Same YES without JWT | 18:08:03 | 404 | Empty |

Using the owner's public key did not resolve the observed sponsorship error.
This does not isolate its cause or prove the owner cannot trade normally. No
transaction was returned, so neither fee payer nor a self-funded fallback could
be inspected. No output amount or routePlan was returned. An SOL funding remedy
remains unproven; this result does not authorize funding or trading.

[Evidence manifest](../../evidence/world-owner-quote-check-20261001-r0/manifest.json).
Offline verification of the original local receipt passed: body hashes,
current-window/mint binding, allowed discovery RPC methods, redacted Bearer,
and matched no-JWT control. Public evidence redacts the owner's address from
request params and URL; its derived hash is recorded separately. JSON parsing,
full-JWT scan, and git diff checks passed. This supersedes the pending handoff.

Requested success verdict remains unestablished. Conservative verdict:
`NO_RESULT_CURRENT_FRONTEND_CONTRACT_UNRESOLVED` for successful fillable
quote/build reproduction. No signing, simulation, submission, broadcast,
collector, protocol change, or authority adjudication occurred. Stop after this
bounded diagnostic; the next missing evidence is a confirmed supported self-pay
or functioning sponsorship build path, not another identical JWT retry.

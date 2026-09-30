# Owner-public-key quote diagnostic preparation R0

Observer handoff only. No authority or protocol change. NO_TRADE: TRUE.

The owner authorized a read-only quote/build check with an explicitly supplied
Solana public address. The address was validated offline as a 32-byte base58
public key. The diagnostic now accepts `--user-public-key` rather than binding
every test to the historical public signer. The supplied address is omitted from
this repository handoff; no owner's wallet account or balance was read.

No request with the owner's address was sent. At 2026-09-30T17:55:42Z /
2026-10-01 01:55:42 Asia/Taipei, the latest supplied JWT's unverified expiry claim
was already past: 2026-09-30T16:46:02Z / 2026-10-01 00:46:02 Asia/Taipei.
The diagnostic refuses expired tokens before network requests. Testing this
address remains pending a fresh normally acquired JWT; expired-token rejection
must not be interpreted as sponsorship failure.

Python compilation, prior fresh-JWT evidence verification, offline public-key
format validation, and git diff check passed. Planned bound: one current on-chain
BTC5m discovery, two `/order` requests with the owner-selected public key, and one
matched no-JWT control. No private keys, wallet connection, transfer, signature,
simulation, submit/sendTransaction, broadcast, or collector is authorized here.

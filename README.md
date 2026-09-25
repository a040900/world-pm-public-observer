# World ↔ Polymarket Public Observer

Read-only prospective BTC/USD 5-minute settlement observer.

## Boundary

- Public data only.
- No credentials, private keys, wallet seed, signing, transaction submission, order placement, or capital.
- No private repository checkout.
- No maker/taker admission thresholds or trading strategy logic.
- Artifacts are observation evidence only.

## Evidence

World market identity comes from the public World.xyz market catalog and is matched to the exact BTC/USD 5-minute rule text. World settlement is inferred only from public Solana redeem/burn settlement actions touching the matched market and outcome mint. Polymarket settlement comes from the public Gamma event final outcome.

The workflow emits reviewer-recalculable JSON plus SHA-256 hashes.

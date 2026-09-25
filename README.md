# World ↔ Polymarket Public Observer

Public read-only execution repository for prospective World.xyz ↔ Polymarket BTC/USD 5-minute observation.

## Authority

This repository is **not** the research authority.

The authoritative project is:

- `a040900/prediction-market-relative-value`

This public repository exists only to provide a public GitHub Actions runtime and reviewer-recalculable artifacts. Research state, qualification statistics, decisions, conclusions, and protocol changes belong in `prediction-market-relative-value`.

Artifacts from this repository must be imported or referenced by the authoritative repository before they may change research state.

## Boundary

- Public data only.
- No credentials, private keys, wallet seed, signing, transaction submission, order placement, or capital.
- No private repository checkout.
- No maker/taker admission thresholds or trading strategy logic.
- Artifacts are observation evidence only.
- `NO_TRADE` is permanent for this repository.

## Prospective collection

The collector is designed to discover and freeze World market identity while the BTC 5-minute window is current or recent, then use that frozen identity for later settlement observation.

Frozen identity evidence includes:

- window `startTs` / `endTs`
- exact World BTC/USD 5-minute description
- World market account
- yesMint
- noMint
- discovery source and timestamps

Settlement evidence is collected separately from public World/Solana and Polymarket sources.

Historical known-good cases are regression tests only. They are not prospective samples and must not be mixed into qualification statistics.

## Evidence handling

Each Actions run emits JSON evidence plus SHA-256 hashes. A successful Actions run proves execution and artifact production; it does not by itself promote evidence into the research authority.

Any research-relevant update must be recorded in `a040900/prediction-market-relative-value`.

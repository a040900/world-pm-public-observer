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

## Fresh qualification runtime

The request-triggered batch selects its first start after checkout, installation and QA using the public Polymarket server clock: ceil((server time + 120 seconds) / 300) * 300. An explicit requested start is a lower bound, and stale requests advance by this time-only rule. All six consecutive windows are fixed before observation. There is no within-batch replacement or selection using prices, candidates or discovery availability.

The launcher starts maker and taker in separate Python processes on one runner so they share the exact schedule and a persistent, file-locked World identity cache. This replaces split-runner deployment; timing measurements and all frozen measurement guards still apply. Cache success is atomic and included in artifacts. It is reusable by both processes and on replay; identities for a different startTs are never reused. A crashed writer times out other readers rather than silently bypassing validation. GitHub evidence can preserve the cache for subsequent exact-window reads; it is not a perpetual cache of undiscovered future markets.

Discovery checks the exact World description and the prediCt Split instruction discriminator and account layout, without requiring debug logs. It scans up to 80 recent signatures, does not refetch already inspected transactions within an attempt, caches metadata by mint, and polls for newly available signatures at three-second intervals. RPC calls use at most four attempts, 1/2/4-second backoff and an eight-second request timeout, all limited by the caller deadline. HTTP 429/5xx, timeout/reset and transient RPC errors can fall back from api.mainnet-beta.solana.com to solana-rpc.publicnode.com. A successful fallback remains preferred for that discovery. The batch uses a 60-second discovery deadline including cache-lock wait. No API key is used.

A qualification-valid window requires both market identities, nonzero radar polls, at least one simultaneous fresh/healthy observation of both venues and both outcomes, normal loop completion, and no discovery/runtime error. This is an execution-validity gate, not an assertion of full-window data coverage or positive edge. Frozen candidate-level UNKNOWN states and all fill/inventory/hedge records are preserved. These counts are reported separately from raw candidate counts; invalid windows cannot support zero-candidate edge conclusions.

Each role and the paired batch report requestedWindowCount, validQualificationWindowCount, invalidExecutionWindowCount, missingExecutionWindowCount and invalidExecutionReasons. Any absent or invalid window yields INCOMPLETE_QUALIFICATION_BATCH, qualificationValid=false and zero Phase A eligible windows. The launcher exits nonzero after retaining evidence. Only six valid paired windows can make a batch eligible for authority import; the public runtime cannot itself promote research state.

Historical runs 36309301112, 36309585620 and 36318577787 remain execution history and contribute zero fresh Phase A windows. Reproducible observations, hashes and the measurement-scope comparison are under evidence/runtime-repair-20260927/. The original measurement freeze remains fcd316ad0cfe0cadeb035716894103ea1ee73063.

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

## Draft prestart and observation-exposure repair

This branch is not Phase A admitted. The public frontend catalog returned HTTP 401/403 in the bounded checks. No reliable public pre-T0 identity source has yet been established. Allowing an exactly verified Split from a fixed prestart lookback removes a logical search restriction; it does not demonstrate that such an instruction will exist before T0.

Preparation for each predetermined window starts up to 120 seconds before T0. It fetches the unchanged PM identity, validated World identity and mint decimals and starts the same feeds. Preparation for the next window can overlap observation of the current window; candidate processing stays serial and never begins before T0. Failed or late preparation retains an execution-invalid record. It does not replace the slot or import retrospective data.

The independent observation sampler records wall-clock exposure for both pairs, including startup and tail gaps. Its freshness leases are bounded by the existing World two-second radar age and PM 10+5-second heartbeat budget. A fresh explicit no_route response is a known lack of radar quote. Other errors, absent responses or unavailable books are unknown. An unchanged PM book with a live subscribed heartbeat can be observed even while it fails the separate, unchanged candidate-entry source TTL. Quote presence is not an observation coverage percentage.

Coverage output preserves requested seconds, observed seconds, unknown intervals and known quote/no-route seconds. The draft whole-window gate requires complete exposure and prestart readiness; it is intentionally not an approved policy for tolerating small unknown gaps. The owner was asked whether to retain the complete five-minute estimand or use an explicitly observed-time estimand. That decision remains pending. Neither this gate nor the existence of a passing unit test grants Phase A admission.

Diagnostic requests always contribute zero Phase A windows in both role reports and batch aggregation. Existing candidate, fill, inventory and hedge evidence is preserved. Frozen maker/taker calculation functions, condition tape, admission thresholds, fees and quote semantics remain unchanged.

PHASE_A_READINESS_BOUNDED_CHECK runs deterministic checks on branch pushes. Explicit workflow_dispatch additionally runs one fixed future-window readiness diagnostic. The first draft commit triggered the initial diagnostic once; subsequent test/documentation pushes do not repeat it. It requests no exact hedge quote and submits no transaction. Its artifact always has phaseAEligibleWindowCount=0; a failed identity lookup is a diagnostic NO_RESULT, not zero-candidate edge evidence.

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

Discovery checks the exact World description and the prediCt Split instruction discriminator and account layout, without requiring debug logs. It scans up to 80 recent signatures, does not refetch already inspected transactions within an attempt, caches metadata by mint, and polls for newly available signatures at three-second intervals. RPC calls use at most four attempts, 1/2/4-second backoff and an eight-second request timeout, all limited by the caller deadline. HTTP 429/5xx, timeout/reset and transient RPC errors can fall back from api.mainnet-beta.solana.com to solana-rpc.publicnode.com. A successful fallback remains preferred for that discovery. The preparation budget and its prestart/poststart bounds are described below; cache-lock wait is included in the discovery deadline. No API key is used.

A qualification-valid window requires both market identities, actual measurement-loop entry and completion, trustworthy coverage accounting, and positive known exposure in at least one pair. Partial observation is allowed. Discovery or feed startup after T0 is an unknown interval, not an entire-window rejection. Execution crashes, broken coverage accounting and no effective observation remain execution-invalid. Frozen candidate-level UNKNOWN states and all fill/inventory/hedge records are preserved.

Each role and the paired batch report requestedWindowCount, validQualificationWindowCount, invalidExecutionWindowCount, missingExecutionWindowCount and invalidExecutionReasons. Any absent or invalid window yields INCOMPLETE_QUALIFICATION_BATCH, qualificationValid=false and zero Phase A eligible windows. The launcher exits nonzero after retaining evidence. Only six valid paired windows can make a batch eligible for authority import; the public runtime cannot itself promote research state.

Historical runs 36309301112, 36309585620 and 36318577787 remain execution history and contribute zero fresh Phase A windows. Reproducible observations, hashes and the measurement-scope comparison are under evidence/runtime-repair-20260927/. The original measurement freeze remains fcd316ad0cfe0cadeb035716894103ea1ee73063.

## Dual-denominator observation policy

The owner-approved Phase A policy preserves two denominators. Strategy frequencies use `observedSeconds`; operational frequencies use full `scheduledSeconds`. Every row also reports `unknownSeconds` and `observationAvailability = observedSeconds / scheduledSeconds`. A 282-second observed / 18-second unknown slot remains valid with 282 strategy seconds and 300 operational seconds. No minimum availability percentage or zero-gap gate is imposed. A valid window count is a slot count, never a claim of 300 observed seconds.

Each direction has its own `pairDenominators`. Top-level window/role observed time is the intersection where both pairs are known; top-level strategy candidate counts use radar-trigger timestamps inside that same intersection. Single-direction evidence remains eligible in its corresponding pair denominator even when the other direction is unknown. Operational counts include all recorded candidates over scheduled time. Candidates outside a given known interval remain in raw candidate evidence and are explicitly counted outside that strategy denominator. A zero observed denominator produces a null frequency, never zero. Maker and taker frequencies remain separate; the paired batch intersects calendar exposure rather than adding both observers' time.

A fresh `KNOWN_QUOTE` or explicit `KNOWN_NO_ROUTE` with live PM authority counts as observed. A known absence of a World route is not a data failure. Unknown World responses, stale/disconnected data, unavailable identity or unready feeds do not count as strategy time. Existing World two-second freshness and PM 10+5-second heartbeat budgets bound the observation sampler. An unchanged PM book can remain observed while failing the separate, unchanged candidate-entry source TTL. Coverage is clipped at actual measurement-loop entry, so prewarming does not credit time when the engine was not measuring. Raw feed exposure and candidate-level evidence remain intact.

Preparation begins up to 120 seconds before each fixed slot. Verified cached identity can warm immediately. With the default 60-second budget, uncached discovery scans from approximately T0-60 until T0+60, sharing one scan/cache with bounded requests; later-starting preparation is still capped at window end. Mint reads have their own bounded deadline within the same window. Pre-T0 identity availability remains unestablished and is a non-blocking runtime limitation. Successful late discovery measures current data only; it does not replay missing startup observations or replace slots. Candidate processing remains serial and never starts before T0.

Coverage sampler failures, clock/accounting errors and execution crashes exclude the slot from strategy denominators without erasing its raw evidence. Missing/invalid slots remain in the scheduled operational denominator. Diagnostic requests always contribute zero Phase A windows. Six valid paired slots make the batch eligible for authority import; they may contain different observed durations. Incomplete batches retain their valid partial evidence and failure reasons, but cannot claim six Phase A windows.

The frozen maker/taker economic calculations, condition tape, candidate admission, fees, queue rules, fill classification, inventory bounds, hedge sizing, protected economics and UNKNOWN execution-evidence semantics remain unchanged. Run 36331335285's fully hedged 5/5-share candidate with cumulativeProtectedUnitCost 0.98125 remains prospective candidate evidence; denominator policy does not retrospectively re-adjudicate it or establish positive EV.

`PHASE_A_READINESS_BOUNDED_CHECK` runs deterministic checks on branch pushes. Explicit dispatch additionally runs a public prestart-efficiency diagnostic. Its pre-T0 success is not a Phase A admission gate, and it always contributes zero Phase A windows. Initial diagnostic 36406967691 and all prior failed/incomplete runs remain preserved. The initial NO_RESULT tested the old T0-120 to T0-60 search; it is neither proof that prestart identity is impossible nor negative edge evidence.

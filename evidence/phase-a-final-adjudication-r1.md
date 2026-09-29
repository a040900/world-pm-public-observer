# World.xyz ↔ Polymarket BTC 5m Phase A Final Adjudication R1

**Verdict: `PARK_PRIMARY_ROUTE_NO_EXECUTION_QUALIFIED_EDGE_PHASE_A`**

Phase A reached the frozen stopping point of **72 valid 5-minute windows**. Six additional valid windows collected after the stopping point are excluded from the primary adjudication.

## Primary results

| Metric | Maker | Taker |
|---|---:|---:|
| Scheduled time | 21,600 s | 21,600 s |
| Observed time | 13,556.204 s | 13,555.934 s |
| Unknown time | 8,043.796 s | 8,044.066 s |
| Observation availability | 62.76% | 62.76% |
| Candidate count | 10 | 281 |
| Strategy candidate count | 8 | 274 |
| Definite fills | 6 | n/a |
| Fully protected survivor | 1 | 0 |
| Complete two-leg simulation | n/a | 0 |
| Paper trade decision | n/a | 0 |

## Maker result

The only fully hedged protected-edge survivor was `WORLD_NO+PM_UP`, maker bid 0.95, 5 shares, with `cumulativeProtectedUnitCost=0.996875`. The frozen model therefore records gross protected edge of **0.003125/share = 31.25 bps**, or **USD 0.015625** at the observed 5-share size.

This is valid positive candidate-level evidence under the frozen measurement semantics. It is not evidence of durable positive EV or scalable economics.

All **10 maker candidates occurred in the first 18 formal windows**. The final **54 formal windows had zero maker candidates**. The sample therefore does not support the intended repeatable high-frequency maker opportunity.

## Taker result

Across the formal 72-window sample the taker observer recorded **281 candidates**, but:

- `paperTradeDecisionCount = 0`
- `twoLegSimulationCompleteCount = 0`
- `secondLegPositiveCompleteCount = 0`
- `noTradeDecisionCount = 23`
- `unknownDecisionCount = 258`

No execution-qualified two-leg taker path was demonstrated.

## Coverage limitation

Observation availability was about **62.76%**. The remaining 37.24% is UNKNOWN, not zero opportunity. This prevents a strong claim that no edge exists during the unobserved periods.

The operational conclusion is still negative for the primary research goal: the observed sample does not demonstrate a sufficiently frequent, executable, economically meaningful edge to justify continued primary engineering investment.

## Economic interpretation

The single protected maker survivor is too small and too sparse to establish a return profile, let alone one shown to exceed the Bitfinex lending benchmark. Historical World/Polymarket settlement-payoff mismatch risk also remains unresolved; Phase A does not remove that risk.

## Disposition

- **Primary engineering budget:** stop.
- **Live trading:** not authorized and not supported by Phase A.
- **Paper-trading promotion:** no.
- **Existing measurement/runtime:** preserve.
- **Background opportunistic monitoring:** optional.
- **Further work on this route:** require a genuinely new mechanism or materially stronger independent evidence. Do not continue by tuning thresholds against this Phase A sample.

`NO_TRADE = TRUE` remains binding.

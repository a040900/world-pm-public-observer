# Economic-First Reassessment Standard R1

Date: 2026-09-30
Scope: World.xyz <-> Polymarket BTC 5m and future prediction-market alpha candidates
Authority intent: research classification and prioritization. Existing evidence remains immutable.

## Goal
Prioritize proof or falsification of economically meaningful edge before additional engineering completeness.

First question: Does enough money appear to exist here to justify reducing execution uncertainty?

Do not require production-grade execution proof before recording an observed economic edge. Do not promote an observed edge to executable edge without execution evidence.

## Required classifications
- PROVEN_NEGATIVE: adequate evidence for the tested mechanism/sample shows relevant after-cost edge is non-positive or fails its frozen economic criterion.
- NO_RESULT: evidence is incomplete, invalid, underpowered, or otherwise insufficient to decide.
- EDGE_OBSERVED: an admissible economic signal/price discrepancy is observed, but executable after-cost PnL is unresolved.
- EXECUTABLE_EDGE: evidence supports executable, after-cost positive edge under tested size, latency/fill assumptions, and market conditions.

Optional qualifiers: MATHEMATICAL_ARB, CAPITAL_INEFFICIENT, LIQUIDITY_LIMITED, ORACLE_BASIS_RISK, EXECUTION_UNRESOLVED. Qualifiers do not replace the primary classification.

## Evidence layers
1. Signal/economic observation: is a meaningful discrepancy or predictive relationship visible?
2. Executability: can relevant legs fill at assumed prices and sizes?
3. Net economics: after fees, spread, slippage, latency/fill uncertainty, and hedge failure risk, is PnL positive?
4. Capital efficiency: executable capacity, expected lock time, release mechanism, repeat frequency.

Failure at a later layer must not erase evidence established at an earlier layer.

## Economic-first gating
Before material new engineering, estimate or bound:
- gross edge;
- executable or visible capacity;
- fees and obvious transaction costs;
- capital lock/release time;
- repeat frequency;
- uncertainty from unresolved execution assumptions.

If plausible edge is smaller than execution uncertainty and there is no credible path to a materially larger edge, deprioritize further precision work.

If a large edge remains after conservative coarse costs, promote to execution POC before adversarial hardening.

## Research sequence
DISCOVERY -> ECONOMIC POC -> EXECUTION POC -> ADVERSARIAL VALIDATION -> CAPITAL

Engineering, provenance, governance, and perfect replayability are subordinate until they can materially change economic classification, execution safety, or the decision to allocate capital.

## World.xyz <-> Polymarket BTC 5m mandatory reassessment
Reassess the full existing World.xyz <-> Polymarket BTC 5m evidence under this standard.

Requirements:
- Preserve all prior runs, A7 evidence, counterexamples, decisions, and invalidations.
- Do not reinterpret NO_RESULT as PROVEN_NEGATIVE.
- Do not treat A7 public trade-size mapping counterexample as proof that the underlying World information relationship is absent.
- Separate information-lead evidence from maker queue/fill evidence.
- Identify the strongest admissible observed edge before execution assumptions.
- Quantify magnitude and frequency where existing evidence permits.
- Separately quantify uncertainty introduced by PM fill/queue assumptions.
- Classify current state as PROVEN_NEGATIVE, NO_RESULT, EDGE_OBSERVED, or EXECUTABLE_EDGE.
- State the single smallest new test most likely to change that classification.
- Do not start a long prospective campaign merely to improve engineering confidence if existing evidence can answer the economic question.
- Do not repair non-material provenance/governance defects unless they could change classification or invalidate evidence.

## Capital-efficiency reporting
For positive candidates report at minimum:
- after-cost edge;
- executable capacity;
- expected capital lock time;
- early-release/merge/hedge mechanism if any;
- repeat frequency.

Prioritization heuristic:
after-cost edge * executable capacity / expected capital lock time

This is a prioritization heuristic, not proof of expected return.

## Stop / promote rules
Promote when a materially positive edge survives coarse conservative costs and has plausible executable capacity.

Deprioritize when the best credible edge is dominated by execution uncertainty, costs, or capital lock time and no inexpensive discriminating test remains.

Record NO_RESULT when evidence cannot decide. Do not convert uncertainty into a negative conclusion.

## Anti-overengineering rule
Repair immediately only defects that can materially alter:
- existence or magnitude of edge;
- fill/execution probability;
- after-cost PnL;
- settlement/payoff equivalence;
- capital safety;
- evidence admissibility for the economic decision.

Record other defects for later work.

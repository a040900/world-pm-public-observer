from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class Leg:
    market_id: str
    outcome: str
    ask: float
    size: float


@dataclass(frozen=True)
class PackageResult:
    relation: str
    guaranteed_payout: float
    acquisition_cost: float
    fee_cost: float
    reserve_cost: float
    net_edge: float
    capacity: float


def _validate_leg(leg: Leg) -> None:
    if leg.outcome not in {"YES", "NO"}:
        raise ValueError("OUTCOME_INVALID")
    if not (0.0 < leg.ask < 1.0):
        raise ValueError("ASK_INVALID")
    if leg.size <= 0:
        raise ValueError("SIZE_INVALID")


def package_economics(
    relation: str,
    legs: Iterable[Leg],
    *,
    guaranteed_payout: float,
    fee_cost: float = 0.0,
    reserve_cost: float = 0.0,
) -> PackageResult:
    rows = tuple(legs)
    if not rows:
        raise ValueError("LEGS_REQUIRED")
    for row in rows:
        _validate_leg(row)
    if guaranteed_payout <= 0:
        raise ValueError("GUARANTEED_PAYOUT_INVALID")
    if fee_cost < 0 or reserve_cost < 0:
        raise ValueError("COST_INVALID")
    acquisition = sum(row.ask for row in rows)
    capacity = min(row.size for row in rows)
    edge = guaranteed_payout - acquisition - fee_cost - reserve_cost
    return PackageResult(
        relation=relation,
        guaranteed_payout=guaranteed_payout,
        acquisition_cost=acquisition,
        fee_cost=fee_cost,
        reserve_cost=reserve_cost,
        net_edge=edge,
        capacity=capacity,
    )


def implication_package(
    antecedent_market: str,
    antecedent_no_ask: float,
    antecedent_no_size: float,
    consequent_market: str,
    consequent_yes_ask: float,
    consequent_yes_size: float,
    *,
    fee_cost: float = 0.0,
    reserve_cost: float = 0.0,
) -> PackageResult:
    """For A => B, NO(A) + YES(B) guarantees >= 1 unit of payout."""
    return package_economics(
        "IMPLICATION",
        (
            Leg(antecedent_market, "NO", antecedent_no_ask, antecedent_no_size),
            Leg(consequent_market, "YES", consequent_yes_ask, consequent_yes_size),
        ),
        guaranteed_payout=1.0,
        fee_cost=fee_cost,
        reserve_cost=reserve_cost,
    )


def mutually_exclusive_pair(
    market_a: str,
    no_a_ask: float,
    no_a_size: float,
    market_b: str,
    no_b_ask: float,
    no_b_size: float,
    *,
    fee_cost: float = 0.0,
    reserve_cost: float = 0.0,
) -> PackageResult:
    """For mutually exclusive A,B, NO(A)+NO(B) guarantees >= 1."""
    return package_economics(
        "MUTUALLY_EXCLUSIVE",
        (
            Leg(market_a, "NO", no_a_ask, no_a_size),
            Leg(market_b, "NO", no_b_ask, no_b_size),
        ),
        guaranteed_payout=1.0,
        fee_cost=fee_cost,
        reserve_cost=reserve_cost,
    )


def collectively_exhaustive_yes(
    markets: Iterable[tuple[str, float, float]],
    *,
    fee_cost: float = 0.0,
    reserve_cost: float = 0.0,
) -> PackageResult:
    """If at least one listed event must occur, buying all YES guarantees >= 1."""
    legs = tuple(Leg(m, "YES", ask, size) for m, ask, size in markets)
    return package_economics(
        "COLLECTIVELY_EXHAUSTIVE",
        legs,
        guaranteed_payout=1.0,
        fee_cost=fee_cost,
        reserve_cost=reserve_cost,
    )

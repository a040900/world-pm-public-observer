"""Compatibility slice migrated from polymarket-quant-sandbox.

Only the fee-rounding API required by the active R2.4 runtime is carried forward.
Historical source blob: 5a060a323a13a1c5e8b1ee8721388894291ebdce.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from typing import Any

FEE_ENDPOINTS = ("FEE_LOWER", "FEE_UPPER")


def taker_fee_outcomes_per_share(price: float, fee_schedule: dict[str, Any]) -> dict[str, Any]:
    rate = float(fee_schedule.get("rate", 0.0))
    exponent = float(fee_schedule.get("exponent", 1.0))
    if not 0.0 <= price <= 1.0 or rate < 0.0 or exponent <= 0.0:
        raise ValueError("FEE_INPUT_INVALID")
    raw = Decimal(str(rate)) * (Decimal(str(price)) * (Decimal("1") - Decimal(str(price)))) ** Decimal(str(exponent))
    quantum = Decimal("0.00001")
    lower_adjacent = raw.quantize(quantum, rounding=ROUND_FLOOR)
    upper_adjacent = raw.quantize(quantum, rounding=ROUND_CEILING)
    remainder = raw - lower_adjacent
    half_tie = lower_adjacent != upper_adjacent and remainder == quantum / 2
    if half_tie:
        lower, upper = lower_adjacent, upper_adjacent
    else:
        rounded = lower_adjacent if remainder < quantum / 2 else upper_adjacent
        lower = upper = rounded
    return {
        "rawDecimal": str(raw),
        "halfTie": half_tie,
        "FEE_LOWER": float(lower),
        "FEE_UPPER": float(upper),
    }


def taker_fee_per_share(price: float, fee_schedule: dict[str, Any], *, endpoint: str = "FEE_UPPER") -> float:
    if endpoint not in FEE_ENDPOINTS:
        raise ValueError("FEE_ROUNDING_ENDPOINT_INVALID")
    return float(taker_fee_outcomes_per_share(price, fee_schedule)[endpoint])

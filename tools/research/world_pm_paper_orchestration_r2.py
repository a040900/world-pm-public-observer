"""Shared two-leg paper orchestration with injectable clock and venue plans."""
from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable, Protocol

from tools.research import world_pm_paper_execution_core_r1 as core


class Clock(Protocol):
    def now(self) -> float: ...
    async def sleep(self, seconds: float) -> None: ...


class SystemClock:
    def now(self) -> float:
        return time.time()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(max(0.0, seconds))


PlanFactory = Callable[[float], Awaitable[list[dict[str, Any]]]]


def _fill_from_step(step: dict[str, Any]) -> dict[str, Any]:
    return core.make_fill_event(
        event_id=str(step["eventId"]),
        trade_id=str(step["tradeId"]),
        order_id=str(step["orderId"]),
        fill_id=str(step["fillId"]),
        venue=str(step["venue"]),
        gross_shares=float(step["grossShares"]),
        net_shares=float(step["netShares"]),
        cash_out_usd=float(step["cashOutUsd"]),
        fee_usd_equivalent=float(step.get("feeUsdEquivalent") or 0.0),
        evidence_object=dict(step["evidenceObject"]),
        simulation_rule=str(step["simulationRule"]),
    )


async def apply_plan(*, ledger: core.EventLedger, steps: list[dict[str, Any]], clock: Clock) -> list[str]:
    results: list[str] = []
    for step in steps:
        delay = float(step.get("delaySeconds") or 0.0)
        if delay > 0:
            await clock.sleep(delay)
        event = _fill_from_step(step) if step["type"] == "FILL" else {k: v for k, v in step.items() if k != "delaySeconds"}
        results.append(ledger.append(event))
    return results


async def execute_two_leg_flow(
    *,
    ledger: core.EventLedger,
    spec: core.ExecutionSpec,
    trade_id: str,
    world_order_id: str,
    world_target_net_shares: float,
    world_plan: list[dict[str, Any]],
    pm_order_id: str,
    pm_plan_factory: PlanFactory,
    clock: Clock | None = None,
) -> dict[str, Any]:
    clock = clock or SystemClock()
    ledger.append(core.event(
        f"{world_order_id}:intent", "ORDER_INTENT", tradeId=trade_id,
        orderId=world_order_id, venue="WORLD", purpose="ENTRY",
        targetNetShares=world_target_net_shares,
    ))

    risk_opened = False
    world_results: list[str] = []

    def sync_deadline_state() -> bool:
        ledger.recover_overdue_deadlines(now=clock.now())
        return bool(
            ledger.state["trades"][trade_id]["risk"].get("deadlineBreached")
        )

    def open_risk_if_needed() -> None:
        nonlocal risk_opened
        if risk_opened:
            return
        trade = ledger.state["trades"][trade_id]
        if core.exposure_shares(trade) <= 1e-9:
            return
        opened_at = clock.now()
        deadline_at = opened_at + spec.max_unhedged_hold_ms / 1000.0
        ledger.append(core.event(
            f"{trade_id}:risk-opened", "UNHEDGED_RISK_OPENED",
            tradeId=trade_id, openedAt=opened_at, deadlineAt=deadline_at,
        ))
        risk_opened = True

    async def wait_for_deadline_if_unresolved() -> None:
        while risk_opened:
            risk = ledger.state["trades"][trade_id]["risk"]
            if risk.get("resolutionRequired") is not True or sync_deadline_state():
                return
            # Sleep completion is not proof that the absolute deadline elapsed.
            # The ledger owns the independent watcher, including after restart.
            await clock.sleep(max(.001, risk["deadlineAt"] - clock.now()))

    try:
        for step in world_plan:
            delay = float(step.get("delaySeconds") or 0.0)
            if delay > 0:
                await clock.sleep(delay)
                sync_deadline_state()
            event = (
                _fill_from_step(step)
                if step["type"] == "FILL"
                else {k: v for k, v in step.items() if k != "delaySeconds"}
            )
            world_results.append(ledger.append(event))
            open_risk_if_needed()
            sync_deadline_state()
    except BaseException:
        await wait_for_deadline_if_unresolved()
        raise

    world_order = ledger.state["orders"][world_order_id]
    if world_order.get("reconciliationRequired") is True or core._order_unresolved(world_order):
        await wait_for_deadline_if_unresolved()
        return {
            "status": "WORLD_FILL_RECONCILIATION_PENDING",
            "worldResults": world_results,
            "pmStarted": False,
            "deadlineBreached": sync_deadline_state() if risk_opened else False,
            "risk": dict(ledger.state["trades"][trade_id]["risk"]),
        }

    hedge_target = core.hedge_target_shares(ledger.state, trade_id)
    if hedge_target <= 1e-9:
        if risk_opened:
            sync_deadline_state()
        return {
            "status": "WORLD_NO_NET_FILL",
            "worldResults": world_results,
            "pmStarted": False,
            "deadlineBreached": (
                bool(ledger.state["trades"][trade_id]["risk"].get("deadlineBreached"))
                if risk_opened else False
            ),
        }

    ledger.append(core.event(
        f"{pm_order_id}:intent", "ORDER_INTENT", tradeId=trade_id,
        orderId=pm_order_id, venue="POLYMARKET", purpose="HEDGE",
        targetNetShares=hedge_target,
    ))

    async def run_pm() -> list[str]:
        return await apply_plan(
            ledger=ledger,
            steps=await pm_plan_factory(hedge_target),
            clock=clock,
        )

    pm_task = asyncio.create_task(run_pm())
    if not risk_opened:
        open_risk_if_needed()
    assert risk_opened

    pm_results: list[str] = []
    pm_error: str | None = None
    try:
        pm_results = await pm_task
    except BaseException as exc:
        pm_error = f"{type(exc).__name__}:{exc}"

    trade = ledger.state["trades"][trade_id]
    unresolved = any(
        core._order_unresolved(ledger.state["orders"][oid])
        for oid in trade.get("openOrders", [])
    )

    if core.exposure_shares(trade) <= 1e-9 and not unresolved:
        deadline_breached = sync_deadline_state()
        ledger.append(core.event(
            f"{trade_id}:risk-resolved", "RISK_RESOLUTION_CONFIRMED",
            tradeId=trade_id, resolvedAt=clock.now(),
            action=(
                "HEDGE_RECONCILED_AFTER_DEADLINE"
                if deadline_breached
                else "HEDGE_RECONCILED"
            ),
        ))
    else:
        await wait_for_deadline_if_unresolved()

    deadline_breached = sync_deadline_state()
    result = {
        "status": (
            "RISK_REMAINS_UNRESOLVED"
            if ledger.state["trades"][trade_id]["risk"]["resolutionRequired"]
            else "TWO_LEG_RECONCILED"
        ),
        "worldResults": world_results,
        "pmResults": pm_results,
        "pmStarted": True,
        "deadlineBreached": deadline_breached,
        "hedgeTargetNetShares": hedge_target,
        "risk": dict(ledger.state["trades"][trade_id]["risk"]),
    }
    if pm_error is not None:
        result["pmError"] = pm_error
    return result


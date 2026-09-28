"""Bounded public preparation and independent observation coverage. NO_TRADE."""
from __future__ import annotations
import asyncio
import time
from dataclasses import dataclass, field
from tools.research import world_polymarket_btc5m_leadlag_probe_r1 as wp
from tools.research import world_pm_qualification_runtime_r1 as q

PREPARE_SECONDS = 120


@dataclass
class PreparedWindow:
    start: int
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    tasks: list = field(default_factory=list)
    row: dict = field(default_factory=dict)
    pm: object = None
    world: object = None
    feed: object = None
    radar: object = None
    decimals: tuple = ()
    coverage: object = None
    closed: bool = False
    coverage_error: str | None = None

    async def close(self):
        if self.closed:
            return
        self.closed = True
        if self.coverage is not None:
            try:
                self.row["observationCoverage"] = self.coverage.finish(time.time())
            except Exception as exc:
                self.coverage_error = f"{type(exc).__name__}:{exc}"
        if self.coverage_error:
            self.row.setdefault("observationCoverage", {})["accountingError"] = self.coverage_error
        self.stop.set()
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)


def token_decimals(mint, rpc_url, deadline):
    payload = wp._rpc(rpc_url, "getAccountInfo", [mint, {"encoding": "jsonParsed", "commitment": "confirmed"}], deadline=deadline)
    info = payload["value"]["data"]["parsed"]["info"]
    value = info.get("decimals")
    if type(value) is not int or not 0 <= value <= 18:
        raise ValueError("TOKEN_DECIMALS_INVALID")
    return value


def record_poll(p, now, world):
    # Also sample at the actual radar decision, so candidate timestamps align with
    # exposure. This records availability only; it never admits a candidate.
    p.coverage.observe(now, world, {
        "yes": p.feed.snapshot(p.pm.down_token), "no": p.feed.snapshot(p.pm.up_token)})


async def _monitor(p):
    try:
        while not p.stop.is_set():
            now = time.time()
            record_poll(p, now, p.radar.snapshot(now))
            if now >= p.start + 300:
                return
            await asyncio.sleep(q.POLL_SECONDS)
    except Exception as exc:
        p.coverage_error = f"{type(exc).__name__}:{exc}"


async def prepare(start, args, feed_factory, radar_factory):
    """Prepare all windows ahead; the caller still processes candidates serially."""
    await asyncio.sleep(max(0.0, start - PREPARE_SECONDS - time.time()))
    p = PreparedWindow(start)
    p.row = {"startTs": start, "endTs": start+300, "preparationStartedAt": time.time()}
    try:
        if time.time() >= start + 300:
            raise TimeoutError("WINDOW_ALREADY_ENDED")
        p.pm = await asyncio.to_thread(wp.fetch_polymarket_market, start)
        p.row["pmMarket"] = {"conditionId": p.pm.condition_id, "upToken": p.pm.up_token,
                             "downToken": p.pm.down_token}
        p.feed = feed_factory([p.pm.up_token, p.pm.down_token])
        p.tasks.append(asyncio.create_task(p.feed.run(p.stop)))
        # Keep the prestart attempt adjacent to T0, but allow the same bounded
        # scan to continue after T0. Missing startup time remains UNKNOWN; no
        # historical quote or candidate is replayed to fill it.
        if wp._cached_world_market(start) is None:
            await asyncio.sleep(max(0.0, start - args.world_discovery_timeout_seconds - time.time()))
        deadline = min(float(start+300), max(float(start), time.time()) + args.world_discovery_timeout_seconds)
        p.world = await asyncio.to_thread(wp.discover_world_market, start, rpc_url=args.rpc_url, deadline=deadline)
        p.row["worldDiscoveryTrace"] = wp.DISCOVERY_TRACES.get(start, {})
        p.decimals = tuple(await asyncio.gather(*[
            asyncio.to_thread(token_decimals, mint, args.rpc_url,
                                  min(float(start+300), time.time()+args.world_discovery_timeout_seconds))
            for mint in (p.world.yes_mint, p.world.no_mint)]))
        p.row["worldMarket"] = {"market": p.world.market, "yesMint": p.world.yes_mint,
                                "noMint": p.world.no_mint, "yesDecimals": p.decimals[0],
                                "noDecimals": p.decimals[1]}
        p.radar = radar_factory(p.world.yes_mint, p.world.no_mint)
        p.tasks.append(asyncio.create_task(p.radar.run(p.stop)))
        p.coverage = q.ObservationCoverage(start, start+300)
        p.tasks.append(asyncio.create_task(_monitor(p)))
        # Start the measurement loop even when feeds have not become known yet.
        # Its independent exposure trace excludes all unready/startup seconds.
        p.row["preparationCompletedAt"] = time.time()
    except asyncio.CancelledError:
        await p.close()
        raise
    except Exception as exc:
        p.row["executionError"] = f"PREPARATION:{type(exc).__name__}:{exc}"
        p.row["worldDiscoveryTrace"] = wp.DISCOVERY_TRACES.get(start, {})
        await p.close()
    return p


async def close_preparations(tasks):
    for task in tasks.values():
        if not task.done():
            task.cancel()
    results = await asyncio.gather(*tasks.values(), return_exceptions=True)
    for result in results:
        if isinstance(result, PreparedWindow):
            await result.close()

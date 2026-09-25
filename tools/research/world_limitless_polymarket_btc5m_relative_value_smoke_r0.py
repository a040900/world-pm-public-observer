"""Compatibility slice for the World DFlow radar used by frozen R2.4.

Historical source blob: ba4a5e3d60e403ab5ccf0c96ffe550d9faffdd2a.
Limitless discovery code is intentionally not carried into this active R2.4 slice.
Public read-only; NO_TRADE.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import websockets

from tools.research import world_polymarket_btc5m_leadlag_probe_r1 as wp

DFLOW_DEV_WS = "wss://dev-quote-api.dflow.net/quote-stream"
DEFAULT_RPC = "https://mellisa-rsahjb-fast-mainnet.helius-rpc.com"


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _discover_world_market(start_ts: int, rpc_url: str, deadline: float) -> wp.WorldMarket:
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            return wp.discover_world_market(start_ts, rpc_url=rpc_url)
        except Exception as exc:
            last_error = exc
            time.sleep(1.0)
    raise RuntimeError(f"WORLD_DISCOVERY_TIMEOUT:{type(last_error).__name__ if last_error else 'UNKNOWN'}:{last_error}")


class DflowQuotes:
    def __init__(self, yes_mint: str, no_mint: str) -> None:
        self.pairs = {
            f"{yes_mint}/{wp.CASH_MINT}": {"outcome": "yes", "baseMint": yes_mint, "quoteMint": wp.CASH_MINT},
            f"{no_mint}/{wp.CASH_MINT}": {"outcome": "no", "baseMint": no_mint, "quoteMint": wp.CASH_MINT},
        }
        self.latest: dict[str, dict[str, Any]] = {}
        self.connection_error: str | None = None
        self.connected = False
        self.frame_count = 0

    async def run(self, stop: asyncio.Event) -> None:
        try:
            async with websockets.connect(DFLOW_DEV_WS, open_timeout=10.0, close_timeout=5.0, ping_interval=20.0, ping_timeout=10.0) as ws:
                self.connected = True
                for item in self.pairs.values():
                    await ws.send(json.dumps({"op": "subscribe", "base_mint": item["baseMint"], "quote_mint": item["quoteMint"]}, separators=(",", ":")))
                while not stop.is_set():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                    except asyncio.TimeoutError:
                        continue
                    self.frame_count += 1
                    frame = json.loads(raw)
                    for update in frame.get("updates", []) or []:
                        key = f"{update.get('sb')}/{update.get('sq')}"
                        if key not in self.pairs:
                            continue
                        self.latest[self.pairs[key]["outcome"]] = {
                            "receivedAt": time.time(),
                            "sourceTimestamp": update.get("ts", frame.get("ts")),
                            "slot": frame.get("u"),
                            "error": update.get("e"),
                            "bid": _to_float(update.get("b")),
                            "ask": _to_float(update.get("a")),
                            "bidSizeQuote": _to_float(update.get("B")),
                            "askSizeQuote": _to_float(update.get("A")),
                        }
        except Exception as exc:
            self.connection_error = f"{type(exc).__name__}:{exc}"
        finally:
            self.connected = False

    def snapshot(self, observed_at: float) -> dict[str, Any]:
        out: dict[str, Any] = {"connected": self.connected, "connectionError": self.connection_error, "frameCount": self.frame_count}
        for side in ("yes", "no"):
            item = dict(self.latest.get(side) or {})
            received = _to_float(item.get("receivedAt"))
            item["quoteAgeSeconds"] = None if received is None else max(0.0, observed_at - received)
            out[side] = item
        return out

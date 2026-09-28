"""One future-window public readiness diagnostic; never Phase A evidence."""
from __future__ import annotations
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import time
from types import SimpleNamespace
from tools.research import world_polymarket_btc5m_leadlag_probe_r1 as wp
from tools.research import world_pm_qualification_runtime_r1 as q
from tools.research import world_pm_window_preparation_r1 as prep
from tools.research import world_pm_pm_maker_first_shadow_r1 as maker


async def check():
    server = await asyncio.to_thread(wp._json_request_transient_retry, wp.CLOB_TIME_URL)
    schedule = q.select_schedule(float(server), buffer_seconds=120)
    start = schedule["firstWindowStartTs"]
    result = {"mode": "NO_TRADE_PRESTART_READINESS_DIAGNOSTIC", "noTrade": True,
              "phaseAEligibleWindowCount": 0, "schedule": schedule,
              "githubSha": os.environ.get("GITHUB_SHA"), "githubRunId": os.environ.get("GITHUB_RUN_ID")}
    args = SimpleNamespace(rpc_url=wp.DEFAULT_SOLANA_RPC, world_discovery_timeout_seconds=60)
    p = await prep.prepare(start, args, maker.TradeAwareBook, maker.r2s.GapRadar)
    try:
        result["preparation"] = p.row
        if p.row.get("executionError"):
            result["status"] = "NO_RESULT_PRESTART_IDENTITY_OR_FEEDS_UNAVAILABLE"
        else:
            await asyncio.sleep(max(0, start-time.time()))
            observed = time.time()
            states = {side: q.observation_state(p.radar.snapshot(observed), p.feed.snapshot(token), side, observed)[0]
                      for side, token in (("yes", p.pm.down_token), ("no", p.pm.up_token))}
            result["boundaryObservedAt"] = observed
            result["boundaryStates"] = states
            result["status"] = ("PRESTART_READINESS_OBSERVED" if observed <= start+q.POLL_SECONDS
                                and all(s.startswith("KNOWN_") for s in states.values())
                                else "NO_RESULT_BOUNDARY_NOT_OBSERVABLE")
    finally:
        await p.close()
    result["completedAt"] = time.time()
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=Path("artifacts/prestart-readiness.json"))
    args = parser.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = asyncio.run(check())
    except Exception as exc:
        result = {"status": "NO_RESULT_DIAGNOSTIC_EXECUTION_FAILURE", "error": f"{type(exc).__name__}:{exc}",
                  "noTrade": True, "phaseAEligibleWindowCount": 0}
    args.out.write_text(json.dumps(result, indent=2)+"\n", encoding="utf-8")
    args.out.with_suffix(".sha256").write_text(hashlib.sha256(args.out.read_bytes()).hexdigest()+"\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0 if result["status"] == "PRESTART_READINESS_OBSERVED" else 2


if __name__ == "__main__":
    raise SystemExit(main())

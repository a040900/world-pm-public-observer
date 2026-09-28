"""One prospective schedule, two observer processes, shared discovery cache. NO_TRADE."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from tools.research import world_polymarket_btc5m_leadlag_probe_r1 as wp
from tools.research import world_pm_qualification_runtime_r1 as q


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--request', type=Path, default=Path('.github/run-requests/maker-taker-r1.json'))
    parser.add_argument('--out-dir', type=Path, default=Path('artifacts'))
    args = parser.parse_args()
    request = json.loads(args.request.read_text(encoding='utf-8'))
    windows = int(request['windows'])
    if not 1 <= windows <= 6:
        raise ValueError('WINDOW_COUNT_OUT_OF_RANGE')
    root = args.out_dir.resolve(); root.mkdir(parents=True, exist_ok=True)
    cache = root / 'world_btc5m_identity_cache.json'
    if cache.exists():
        raise ValueError('FRESH_BATCH_OUTPUT_DIRECTORY_REQUIRED')
    shutil.copyfile('world_btc5m_identity_cache.json', cache)
    diagnostic = (request.get('coverageCalibration', {}).get('phaseAEligible') is False
                  or 'DIAGNOSTIC' in str(request.get('mode', '')))
    env = dict(os.environ, WORLD_IDENTITY_CACHE=str(cache), PYTHONUNBUFFERED='1',
               WORLD_PM_PHASE_A_ELIGIBLE='0' if diagnostic else '1')
    batch = {'noTrade': True, 'request': request, 'requestedWindowCount': windows,
             'githubSha': os.environ.get('GITHUB_SHA'), 'githubRunId': os.environ.get('GITHUB_RUN_ID'),
             'qualificationValid': False, 'qualificationStatus': 'INCOMPLETE_QUALIFICATION_BATCH',
             'phaseAEligibleWindowCount': 0, 'topology': 'SEPARATE_PROCESSES_SHARED_RUNNER_AND_IDENTITY_CACHE',
             'measurementFreezeSha': 'fcd316ad0cfe0cadeb035716894103ea1ee73063'}
    batch.update(q.qualify_batch([], windows, phase_a_eligible=not diagnostic))
    processes = {}; handles = []
    try:
        # This is after checkout, QA and dependency installation; no market data is consulted.
        local_before = time.time()
        server_now = wp._json_request_transient_retry(wp.CLOB_TIME_URL, timeout=5.0, attempts=4)
        local_after = time.time()
        schedule = q.select_schedule(float(server_now) + (local_after - local_before),
                                     request.get('firstWindowStartTs'), buffer_seconds=120)
        schedule.update(localRequestAt=local_before, localReceivedAt=local_after, serverTimeSource=wp.CLOB_TIME_URL)
        batch['schedule'] = schedule
        first = str(schedule['firstWindowStartTs'])
        commands = {
            'maker': [sys.executable, '-m', 'tools.research.world_pm_pm_maker_first_shadow_r1', '--start-ts', first],
            'taker': [sys.executable, '-m', 'tools.research.world_pm_two_leg_paper_bot_r1', '--first-window-start-ts', first,
                      '--ledger', str(root / 'taker-ledger.jsonl'), '--checkpoint', str(root / 'taker-checkpoint.json')]}
        (root / 'batch.json').write_text(json.dumps(batch, indent=2) + '\n', encoding='utf-8')
        for role, cmd in commands.items():
            handle = (root / f'{role}-console.txt').open('w', encoding='utf-8'); handles.append(handle)
            cmd += ['--windows', str(windows), '--rpc-url', wp.DEFAULT_SOLANA_RPC,
                    '--world-discovery-timeout-seconds', '60', '--out', str(root / f'{role}.json')]
            processes[role] = subprocess.Popen(cmd, env=env, stdout=handle, stderr=subprocess.STDOUT)
        deadline = schedule['firstWindowStartTs'] + windows * 300 + 600
        exits = {}
        for role, process in processes.items():
            exits[role] = process.wait(timeout=max(1, deadline - time.time()))
        batch['processExitCodes'] = exits
        reports = {role: json.loads((root / f'{role}.json').read_text(encoding='utf-8')) for role in commands}
        expected = [int(first) + i * 300 for i in range(windows)]
        batch.update(q.qualify_paired_batch(reports, expected, phase_a_eligible=not diagnostic))
        if any(exits.values()):
            batch.update(qualificationValid=False, qualificationStatus='INCOMPLETE_QUALIFICATION_BATCH', phaseAEligibleWindowCount=0)
    except Exception as exc:
        batch['executionError'] = f'{type(exc).__name__}:{exc}'
    finally:
        for process in processes.values():
            if process.poll() is None:
                process.terminate(); process.wait(timeout=10)
        for handle in handles:
            handle.close()
        batch['completedAt'] = time.time()
        (root / 'batch.json').write_text(json.dumps(batch, indent=2) + '\n', encoding='utf-8')
        (root / 'artifacts.sha256').write_text(''.join(
            f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n'
            for p in sorted(root.iterdir()) if p.is_file() and p.name != 'artifacts.sha256'), encoding='utf-8')
    print(json.dumps(batch, sort_keys=True), flush=True)
    return 0 if batch['qualificationValid'] else 2


if __name__ == '__main__':
    raise SystemExit(main())

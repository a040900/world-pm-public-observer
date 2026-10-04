# Cloud handover and offline acceptance R1

CLOUD_HANDOVER_OFFLINE_ACCEPTED. SECTION_2_5_BINDING_PENDING. NO_TRADE.

Actual cloud Linux x86_64 execution, isolated CPython 3.11.9, authorized GitHub
reads and a real checkout were verified. Starting branch HEAD was
85e1f0d54ae4139e2d5b16e99b05f4b704ad39e9. Both specified authority blob versions
were read with the authorized connector and were unchanged. No private authority
checkout or private document contents were copied into this repository.

Actual reruns: full repository 125/125; new synthetic acceptance 37/37; all 52
research Python files compiled successfully. No implementation repair was needed.
Only this new evidence directory is added; existing artifacts remain unchanged.

B4 results:

- Linear copies: both residuals dropped in every inner/outer fit; maximum residual
  std 7.353595402179449e-16; Delta exactly 0; primary bootstrap CI [0, 0].
- Leakage: inner training parameters unchanged under 4–6h replacements; outer
  parameters and selected lag unchanged under 6–12h replacements.
- Missing versus zero: missing endpoint row excluded as WORLD_MID_INVALID;
  complete unchanged-price row retained with target 0. Executed assertion is
  recorded in b4-offline-acceptance.log, not inferred from a historical PASS.

Runtime: Python 3.11.9, NumPy 2.4.6, SciPy 1.17.1, scikit-learn 1.9.1.
NumPy BLAS/LAPACK scipy-openblas 0.3.31.188.0 (64-bit integers). Actual loaded
libraries, package pins, build configuration and source hashes are recorded in
cloud-runtime-manifest.json and installed-dependencies.txt.

Cloud GELSD re-verification used official version source, byte equality of the
installed Python wrapper after LF normalization, installed ELF symbol mapping
and dynamic linker binding to scipy_dgelsd_64_. See numpy-gelsd-verification.log.
This was independently executed here. Static solver metadata in the acceptance
entry point is supplemented by this runner-specific evidence.

The audit hook blocked Python socket connect/sendto/address lookup throughout
both test commands, including spawned Python CLI processes. No blocked events
were observed. OS network namespace creation was unavailable; this is a Python
transport guard rather than a system-wide network isolation claim.

Reproduce from the repository root on a new cloud runner (venv outside repo):

```bash
uv python install 3.11.9
uv venv --python 3.11.9 ../subsecond-venv
uv pip install --python ../subsecond-venv/bin/python -r requirements-subsecond-r1.txt requests==2.32.3 websockets==14.2 pycryptodome==3.23.0
../subsecond-venv/bin/python -m unittest discover -s tests -v
../subsecond-venv/bin/python -m py_compile tools/research/*.py
../subsecond-venv/bin/python tools/research/world_pm_subsecond_offline_acceptance_r1.py --output <new-exclusive-output-path.json>
```

For guarded reproduction, place offline-network-guard.py in a private directory
as sitecustomize.py, change its blocked-events log path to that runner's private
workspace, and set PYTHONPATH to that directory for both test commands. Do not
reuse this evidence output path. Reverify versions and GELSD on a new runner.
The recorded environment and installed dependency snapshot allow recreation;
this transient VM/venv is not guaranteed to survive future sessions.

Section 2.5 remains open: exact World endpoint/request/auth and formal UTC
start/end are not bound. World adapter currently supports public GET only,
without MCP/OAuth/JWT acquisition; prospective transport was not verified.
PM endpoint/heartbeat binding and UTC clock synchronization also remain required
before sampling. No market API/WS probe, capture flag, live sample, workflow
dispatch, order, wallet, signing, simulation, transaction or funds operation was
performed. This is implementation acceptance, not a signal verdict.

# R6 collector boundary repair R0

Scope: engineering repair only, authorized by the owner in this session. Base observer commit: `e109b244dd0172e492d8b6a2162bae49179c4ca7`.

The consumed sample/run `36952623833` remains `NO_RESULT_DATA_INSUFFICIENT`. No historical artifact, frozen starts, signal threshold, analyzer, or authority decision was changed. NO_TRADE remains TRUE. No fresh sampling was performed.

## Problem and change

The sequential PM collector discovered identity only before the frozen window start. Finishing the preceding window at/after the next start skipped discovery completely. In the original code, the new boundary tests reproduced a single identified window followed by eleven missing windows, and exact-start entry returned UNKNOWN instead of making an identity request.

`capture_window` now discovers only the exact requested slug, with retries bounded by that same window's end. A late identity receives its actual timestamp; a reply after the end is rejected. Expired windows and failed discovery preserve explicit failure reasons and stay in the fixed denominator.

The sampling loop skips already elapsed cadence targets after slow requests. It retains actual capture/start/receive times and original schedule indices; it never writes current books as observations of missed past targets. Book requests use the remaining window budget as a socket timeout, avoid new requests after the end, and mark replies arriving at/after the end unsuccessful while retaining returned data for diagnostics. Socket timeout is not an absolute HTTP wall-clock deadline: a slow response can still overrun, and the receive-time guard excludes it from successful observations.

The JSON summary reports PM capture counts plus `PM_CAPTURE_RECORDED_PENDING_QUALIFICATION` or `NO_RESULT_PM_CAPTURE_INCOMPLETE`. The former checks identity and minimum paired counts only; it is not synchronization qualification, a signal verdict, executable quote evidence, or settlement admission. Actual source timestamps/full books continue to be preserved for the analyzer.

## Preventing unintended new samples

The old push-triggered workflow would launch a new 12-window capture when this collector repair was pushed. The workflow now runs only Python compilation and deterministic unittest validation. Its live capture/artifact steps were removed, with no manual dispatch entry added. No other workflow was changed. A future cohort needs new authority and an explicitly reviewed launch mechanism.

## Validation

Before repair, the first nine offline regressions failed: seven failures and two errors. After repair, thirteen offline tests pass using a fake clock, fake HTTP, and a forbidden real `urlopen` boundary:

- exact-start discovery and all twelve consecutive frozen windows;
- late entry preserves actual times and skips old targets;
- slow responses skip targets without a catch-up burst;
- overrun book replies are unsuccessful and the second leg is not requested after the end;
- expired windows make no identity/book request;
- identity failure preserves its cause with bounded retries;
- late identity replies are rejected;
- invalid cadence is rejected before network operations;
- a missing second window remains in the twelve-window denominator and produces incomplete status;
- book timeout retains failure/timing evidence;
- push workflow invokes offline tests, with no live collector/dispatch command.
- the actual `get_json` transport is stopped by the forbidden `urlopen` sentinel.

Commands:

```
python -m unittest discover -s tests -p test_world_pm_r6_fixed_12_window_pm_r0.py -v
python -m py_compile tools/research/world_pm_r6_fixed_12_window_pm_r0.py tests/test_world_pm_r6_fixed_12_window_pm_r0.py
git diff --check
```

Verification receipt: `evidence/world-pm-r6-collector-repair-20261002/verification.json`.

TypeSafe's claim/source verification method was applied to the repair claims. No TypeSafe/Jev API call, dependency, or integration was introduced: these timing rules and checks are deterministic code.

## Handoff

Luna independently reviewed the diff and confirmed the boundary fixes, fixed denominator, and offline workflow. A review objection to the 120-count summary was checked against the actual R6 authority/addendum: 120 is the frozen minimum, whereas 150 was the observed readiness result. It was therefore retained; this summary still does not apply the other qualification gates.

Collector repair is available for independent authority review. This batch does not prove runtime network quality or authorize another cohort. Source timestamp freshness remains a separate qualification concern, not proved by receive-time guards. The CLI still selects a fresh set of starts on each independent invocation; it does not resume an existing cohort or prevent an operator from relaunching. A future authorized launch must explicitly bind and preserve its frozen cohort identity, and must not treat rerunning this CLI as resuming R6. Retain the old NO_RESULT evidence; submit repair/testing evidence to the primary authority reviewer before any newly frozen pilot.

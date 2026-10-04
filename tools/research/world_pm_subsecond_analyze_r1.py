"""Offline frozen R6 analysis. No network, sampling or trading operations."""
from __future__ import annotations

import bisect
import argparse
import hashlib
import json
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import numpy as np

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.research import world_pm_subsecond_model_r1 as numerical

PM_LAGS = (1, 2, 4, 8, 16, 32, 64)
MINIMUM_GRIDS = 30_000
ANALYSIS_SCHEMA = "WORLD_PM_SUBSECOND_R6_ANALYSIS_R1"
AUTHORITY = {
    "repository": "a040900/prediction-market-relative-value",
    "spec": {"path": "docs/decisions/2026-10-04-subsecond-leadlag-measurement-precommit-r6.md",
             "blob": "0003d90615245309c3b152fe8a2d0a3fb04bb091"},
    "binding": {"path": "docs/decisions/2026-10-04-subsecond-leadlag-measurement-binding-r2.md",
                "blob": "b26a1e48ed8dfcd688ac6c978dd24f42bd37d707"},
    "ownerClarification": {
        "firstHalfCoverage": "four-horizon intersection; same inner comparison rows",
        "secondHalfCoverage": "selected horizon only",
        "worldIntegrityGaps": False,
        "labelPurge": "upfront across 4h/6h; exclude labels beyond capture end"}}


class MissingEndpoint(ValueError):
    pass


class QuoteIndex:
    """Strict receipt as-of index; a late historical row never rolls back W."""
    def __init__(self, quotes):
        self.series = {}
        for quote in quotes:
            key = (quote["market_start"], quote["venue"], quote["side"])
            self.series.setdefault(key, []).append(quote)
        for key, rows in list(self.series.items()):
            rows.sort(key=lambda r: (r["receive_time"], r["sequence"]))
            latest, states = None, []
            for row in rows:
                if key[1] != "world" or latest is None or row["source_time"] >= latest["source_time"]:
                    latest = row
                states.append(latest)
            self.series[key] = ([r["receive_time"] for r in rows], states)

    def at(self, start, venue, side, t):
        series = self.series.get((start, venue, side))
        if series is None:
            raise MissingEndpoint(f"{venue.upper()}_ENDPOINT_MISSING")
        times, states = series
        i = bisect.bisect_left(times, t) - 1
        if i < 0:
            raise MissingEndpoint(f"{venue.upper()}_ENDPOINT_MISSING")
        quote = states[i]
        if quote.get("mid") is None or not np.isfinite(quote["mid"]):
            raise MissingEndpoint(f"{venue.upper()}_MID_INVALID")
        if venue == "world":
            source = quote.get("source_time")
            if source is None or not np.isfinite(source):
                raise MissingEndpoint("WORLD_SOURCE_TIME_MISSING")
            if source > t:
                raise MissingEndpoint("WORLD_SOURCE_TIME_AHEAD_OF_ENDPOINT")
            if t - quote["receive_time"] > 3 or t - source > 5:
                raise MissingEndpoint("WORLD_ENDPOINT_AGE_EXCEEDED")
        return quote


def expanded_gaps(events, end_ts):
    """Read recorded integrity gaps; do not infer gaps from silent prices."""
    active, result = {}, {}
    for event in events:
        if event.get("venue") != "pm":
            continue  # Owner: World uses age/endpoint qualification, no gap padding.
        key = (event.get("market_start"), event.get("venue"))
        if event["type"] == "gap_open":
            active[key] = event["gap_start"]
        elif event["type"] == "gap_close":
            start = event["gap_start"]
            result.setdefault(key[0], []).append((start - 30, event["gap_end"] + 30))
            active.pop(key, None)
    for (market, _venue), began in active.items():
        result.setdefault(market, []).append((began - 30, min(end_ts, market + 300) + 30))
    return result


@dataclass
class Rows:
    timestamps: np.ndarray
    market_starts: np.ndarray
    pm: np.ndarray
    world: np.ndarray
    target: np.ndarray
    pm_update_age: np.ndarray
    rejected: dict

    def subset(self, mask):
        return Rows(self.timestamps[mask], self.market_starts[mask], self.pm[mask],
                    self.world[mask], self.target[mask], self.pm_update_age[mask], self.rejected)


def build_rows(quotes, gaps, markets, horizon, *, index=None):
    """Construct each k's qualified grid before any fit or split.

    Every endpoint has its own receipt/age qualification. Missing future data
    is rejected, while a healthy unchanged PM book produces a valid zero Y.
    """
    if horizon not in numerical.HORIZONS:
        raise ValueError("NON_FROZEN_HORIZON")
    index = QuoteIndex(quotes) if index is None else index
    accepted, rejected = [], {}
    for market in markets:
        start, end = market["startTs"], market["endTs"]
        reasons = Counter()
        for step in range(600):
            t = start + 0.5 * step
            try:
                if t < start + 32:
                    raise MissingEndpoint("INSUFFICIENT_32S_LOOKBACK")
                if t + horizon >= end:
                    raise MissingEndpoint("TARGET_OUTSIDE_SAME_MARKET")
                if any(t - 32 <= b and t + horizon >= a for a, b in gaps.get(start, [])):
                    raise MissingEndpoint("EXPANDED_INTEGRITY_GAP_INTERSECTION")
                p = index.at(start, "pm", "up", t)
                w = index.at(start, "world", "yes", t)
                previous_pm = [index.at(start, "pm", "up", t - 0.5 * lag) for lag in PM_LAGS]
                previous_world = [index.at(start, "world", "yes", t - lag) for lag in (8, 32)]
                future = index.at(start, "pm", "up", t + horizon)
                fp = [p["mid"] - q["mid"] for q in previous_pm]
                fw = [w["mid"] - q["mid"] for q in previous_world]
                y = future["mid"] - p["mid"]
                if not np.isfinite([*fp, *fw, y]).all():
                    raise MissingEndpoint("NONFINITE_FEATURE_OR_LABEL")
                accepted.append((t, start, fp, fw, y, t - p["receive_time"]))
            except MissingEndpoint as exc:
                reasons[str(exc)] += 1
        rejected[str(start)] = dict(reasons)
    n = len(accepted)
    return Rows(
        np.asarray([r[0] for r in accepted], dtype=float),
        np.asarray([r[1] for r in accepted], dtype=np.int64),
        np.asarray([r[2] for r in accepted], dtype=float).reshape(n, 7),
        np.asarray([r[3] for r in accepted], dtype=float).reshape(n, 2),
        np.asarray([r[4] for r in accepted], dtype=float),
        np.asarray([r[5] for r in accepted], dtype=float), rejected)


def fit_fold(training, validation):
    """All transforms and both Ridge fits share the final training row set."""
    pair = numerical.FittedPair.fit(training.pm, training.world, training.target,
                                    training.timestamps.tolist())
    d, score = numerical.paired_loss(pair, validation.pm, validation.world, validation.target)
    return pair, d, score


def select_horizon(inner_folds):
    """Input is already qualified, split and purged; only inner data enter."""
    if set(inner_folds) != set(numerical.HORIZONS):
        raise numerical.AnalysisInvalid("ALL_FOUR_INNER_FOLDS_REQUIRED")
    fits, scores = {}, {}
    for k in numerical.HORIZONS:
        training, validation = inner_folds[k]
        pair, _d, score = fit_fold(training, validation)
        fits[k], scores[k] = pair, score
    chosen = max(numerical.HORIZONS, key=lambda k: (scores[k]["delta"], -k))
    return chosen, fits, scores


def evaluate_selected(training, validation):
    """Calculation gate only; caller must pass cohort qualification first."""
    pair, d, score = fit_fold(training, validation)
    rho = numerical.correlation(validation.world[:, 0], validation.target)
    primary = numerical.bootstrap(d, validation.timestamps, validation.market_starts, 300)
    sensitivity = numerical.bootstrap(d, validation.timestamps, validation.market_starts, 900)
    low, high = primary["confidenceInterval"]
    passed = score["delta"] > 0 and (low > 0 or high < 0) and rho > 0.40
    return {"score": score, "corr": rho, "bootstrap5min": primary,
            "bootstrap15min": sensitivity, "conditionalCalculationVerdict": "GO" if passed else "NO-GO",
            "parameters": pair.parameters(), "runtime": numerical.runtime_versions()}


def purge(rows, horizon, start, end):
    """Upfront purge before all splits, including the outer training set."""
    ts = rows.timestamps
    mask = (ts >= start) & (ts + horizon < end)
    for boundary in (start + 4 * 3600, start + 6 * 3600):
        # Labels ending exactly at the split use only receipts strictly before
        # that boundary and therefore do not cross it.
        mask &= ~((ts < boundary) & (ts + horizon > boundary))
    return rows.subset(mask)


def age_summary(values):
    a = np.asarray(values, dtype=float)
    return {"count": len(a), "min": float(a.min()), "median": float(np.median(a)),
            "p95": float(np.percentile(a, 95)), "max": float(a.max())} if len(a) else {"count": 0}


def row_summary(rows, markets):
    return {"effectiveGrids": len(rows.timestamps),
            "nonzeroTargetChanges": int(np.count_nonzero(rows.target)),
            "nonzeroWorld8sChanges": int(np.count_nonzero(rows.world[:, 0])),
            "pmUpdateAge": age_summary(rows.pm_update_age),
            "windows": [{"startTs": m["startTs"],
                         "effectiveGrids": int(np.count_nonzero(rows.market_starts == m["startTs"])),
                         "nonzeroTargetChanges": int(np.count_nonzero(rows.target[rows.market_starts == m["startTs"]])),
                         "rejectedReasons": rows.rejected.get(str(m["startTs"]), {})} for m in markets]}


def analyze_rows(rows_by_k, start, end, *, receive_times_reliable, capture_complete, markets):
    """Qualification -> numerical validity -> decision, in the frozen order."""
    report = {"schemaVersion": ANALYSIS_SCHEMA, "authority": AUTHORITY,
              "scope": "Frozen model/features/8s scalar/lag policy/observation conditions only; NO_TRADE",
              "startTs": start, "endTs": end, "runtime": numerical.runtime_versions(),
              "q1": {"verdict": "NO_RESULT_TIME_RESOLUTION_INSUFFICIENT",
                     "reason": "Q1 event timing is not inferred from integer historical source timestamps"}}
    def no_result(reason, data=True):
        report.update(verdict="NO_RESULT_DATA_INSUFFICIENT" if data else "NO_RESULT_CALCULATION_FAILED",
                      reason=reason)
        report["q2"] = {"verdict": report["verdict"], "reason": reason}
        return report
    if not receive_times_reliable or not capture_complete:
        return no_result("PROSPECTIVE_RECEIVE_TIME_UNRELIABLE_OR_CAPTURE_INCOMPLETE")
    if end != start + 12 * 3600 or set(rows_by_k) != set(numerical.HORIZONS):
        return no_result("EXACT_12H_FOUR_HORIZON_INPUT_REQUIRED")
    final = {k: purge(rows, k, start, end) for k, rows in rows_by_k.items()}
    boundary4, boundary6 = start + 4 * 3600, start + 6 * 3600
    first = {k: r.subset(r.timestamps < boundary6) for k, r in final.items()}
    common = first[numerical.HORIZONS[0]].timestamps
    for k in numerical.HORIZONS[1:]:
        common = np.intersect1d(common, first[k].timestamps, assume_unique=True)
    first = {k: r.subset(np.isin(r.timestamps, common)) for k, r in first.items()}
    report["qualification"] = {"firstHalfCommonGrids": len(common), "minimumPerHalf": MINIMUM_GRIDS,
                               "qualifiedRowsByHorizon": {str(k): row_summary(r, markets) for k, r in final.items()}}
    if len(common) < MINIMUM_GRIDS:
        return no_result("FIRST_HALF_FOUR_HORIZON_INTERSECTION_BELOW_30000")
    try:
        folds = {k: (r.subset(r.timestamps < boundary4),
                     r.subset(r.timestamps >= boundary4)) for k, r in first.items()}
        selected, fits, scores = select_horizon(folds)
        report["selectedHorizonSeconds"] = selected
        report["inner"] = {str(k): {"score": scores[k], "parameters": fits[k].parameters()}
                           for k in numerical.HORIZONS}
        outer_test = final[selected].subset(final[selected].timestamps >= boundary6)
        report["qualification"]["secondHalfSelectedGrids"] = len(outer_test.timestamps)
        if len(outer_test.timestamps) < MINIMUM_GRIDS:
            return no_result("SECOND_HALF_SELECTED_HORIZON_BELOW_30000")
        result = evaluate_selected(first[selected], outer_test)
        report["outer"] = result
        ci5 = result["bootstrap5min"]["confidenceInterval"]
        ci15 = result["bootstrap15min"]["confidenceInterval"]
        excludes = lambda ci: ci[0] > 0 or ci[1] < 0
        report["significanceDependsOnBlockLength"] = excludes(ci5) and not excludes(ci15)
        report["verdict"] = result["conditionalCalculationVerdict"]
        report["q2"] = {"verdict": report["verdict"], "selectedHorizonSeconds": selected}
        # Other holdout horizons remain descriptive: never refit or reselect.
        report["descriptiveOtherHorizonCorrelations"] = {}
        for k, rows in final.items():
            tail = rows.subset(rows.timestamps >= boundary6)
            try:
                value = numerical.correlation(tail.world[:, 0], tail.target)
                report["descriptiveOtherHorizonCorrelations"][str(k)] = value
            except numerical.AnalysisInvalid:
                report["descriptiveOtherHorizonCorrelations"][str(k)] = None
        report["directionAccuracy"] = "diagnostic only; ties recorded separately"
        return report
    except (numerical.AnalysisInvalid, ValueError, np.linalg.LinAlgError, FloatingPointError) as exc:
        return no_result(str(exc), data="CORRELATION" in str(exc))


def read_journal(path):
    """Keep raw capture intact; retain only normalized evidence for analysis."""
    header, footer, quotes, gaps, identities = None, None, [], [], {}
    digest = hashlib.sha256()
    raw_counts = Counter()
    expected_sequence = 0
    with Path(path).open("rb") as source:
        for raw in source:
            digest.update(raw)
            event = json.loads(raw)
            if event["sequence"] != expected_sequence:
                raise ValueError("JOURNAL_SEQUENCE_BROKEN")
            expected_sequence += 1
            kind = event["type"]
            if footer is not None:
                raise ValueError("EVENT_AFTER_CAPTURE_FOOTER")
            if kind == "header":
                if header is not None or event["sequence"] != 0:
                    raise ValueError("CAPTURE_HEADER_INVALID")
                header = event
            elif kind == "footer":
                footer = event
            elif kind == "quote":
                if not np.isfinite(event["receive_time"]):
                    raise ValueError("RECEIVE_TIME_INVALID")
                quotes.append(event)
            elif kind in ("gap_open", "gap_close"):
                gaps.append(event)
            elif kind == "identity":
                identities[event["market_start"]] = event["market"]
            elif kind == "raw":
                raw_counts[event["venue"]] += 1
    if header is None or header.get("schemaVersion") != "WORLD_PM_SUBSECOND_R6_CAPTURE_R1":
        raise ValueError("CAPTURE_SCHEMA_INVALID")
    return header, footer, quotes, gaps, identities, digest.hexdigest(), dict(raw_counts)


def analyze_journal(path):
    header, footer, quotes, gaps, identities, digest, raw_counts = read_journal(path)
    config = header["configuration"]
    markets, start, end = config["markets"], header["startTs"], header["endTs"]
    from tools.research.world_pm_subsecond_capture_r1 import validate_config
    validate_config(config)
    if config["startTs"] != start or config["endTs"] != end:
        raise ValueError("CAPTURE_CONFIG_TIME_MISMATCH")
    bound = {m["startTs"]: {**m, **identities.get(m["startTs"], {})} for m in markets}
    for q in quotes:
        m = bound.get(q["market_start"])
        if m is None or not m["startTs"] <= q["receive_time"]:
            raise ValueError("QUOTE_MARKET_IDENTITY_INVALID")
        if q["venue"] == "pm":
            token = m.get("pmUpToken" if q["side"] == "up" else "pmDownToken")
            if token is None or str(q.get("asset_id")) != str(token):
                raise ValueError("PM_QUOTE_TOKEN_IDENTITY_MISMATCH")
        elif q["venue"] == "world":
            if q.get("ticker") != m["worldTicker"]:
                raise ValueError("WORLD_QUOTE_TICKER_IDENTITY_MISMATCH")
        else:
            raise ValueError("UNKNOWN_QUOTE_VENUE")
    pm_started = {e["market_start"] for e in gaps if e["type"] == "gap_open" and e["venue"] == "pm"}
    integrity_complete = pm_started == set(bound)
    expanded = expanded_gaps(gaps, end)
    index = QuoteIndex(quotes)
    rows = {k: build_rows(quotes, expanded, markets, k, index=index) for k in numerical.HORIZONS}
    clock = (footer or {}).get("clockReliability", {})
    reliable = clock.get("reliable") is True and not clock.get("errors")
    report = analyze_rows(rows, start, end, receive_times_reliable=reliable,
                          capture_complete=(footer or {}).get("completed") is True and integrity_complete,
                          markets=markets)
    report.update(inputSha256=digest, rawResponseCounts=raw_counts, clockReliability=clock)
    report["sourceDiagnostics"] = source_diagnostics(quotes)
    if "outer" in report:
        report["descriptiveDiagnostics"] = descriptive_diagnostics(index, rows[report["selectedHorizonSeconds"]], start)
    return report


def descriptive_diagnostics(index, rows, start):
    """Past/negative/synchronous comparisons never select lag or change verdict."""
    tail = rows.subset(rows.timestamps >= start + 6 * 3600)
    values = {"synchronous30s": ([], []), **{str(-k): ([], []) for k in numerical.HORIZONS}}
    for t, market in zip(tail.timestamps, tail.market_starts):
        try:
            p, w = index.at(market, "pm", "up", t), index.at(market, "world", "yes", t)
            w8 = index.at(market, "world", "yes", t - 8)
            for k in numerical.HORIZONS:
                previous = index.at(market, "pm", "up", t - k)
                x, y = values[str(-k)]
                x.append(w["mid"] - w8["mid"])
                y.append(previous["mid"] - p["mid"])
            p30, w30 = index.at(market, "pm", "up", t - 30), index.at(market, "world", "yes", t - 30)
            x, y = values["synchronous30s"]
            x.append(w["mid"] - w30["mid"])
            y.append(p["mid"] - p30["mid"])
        except MissingEndpoint:
            continue
    result = {"negativeLagFormula": "corr(W(t)-W(t-8), P(t+k)-P(t)), k<0",
              "synchronousFormula": "corr(W(t)-W(t-30), P(t)-P(t-30))",
              "sameSelectedGridBeforeAdditionalDiagnosticEndpointChecks": True}
    for key, (x, y) in values.items():
        try:
            rho = numerical.correlation(x, y)
        except numerical.AnalysisInvalid:
            rho = None
        result[key] = {"corr": rho, "effectivePairs": len(x)}
    result["selectedHorizonWindowCorrelations"] = []
    for market in np.unique(tail.market_starts):
        mask = tail.market_starts == market
        try:
            rho = numerical.correlation(tail.world[mask, 0], tail.target[mask])
        except numerical.AnalysisInvalid:
            rho = None
        result["selectedHorizonWindowCorrelations"].append(
            {"startTs": int(market), "corr": rho, "effectivePairs": int(mask.sum())})
    return result


def source_diagnostics(quotes):
    groups = {}
    for q in quotes:
        key = f'{q["market_start"]}/{q["venue"]}/{q["side"]}'
        groups.setdefault(key, []).append(q)
    result = {}
    for key, events in groups.items():
        ordered = sorted(events, key=lambda q: (q["receive_time"], q["sequence"]))
        valid_mid = [e["mid"] for e in ordered if e.get("mid") is not None]
        sources = {q.get("source_time") for q in ordered if q.get("source_time") is not None}
        result[key] = {"independentSourceTimestamps": len(sources), "receivedQuoteRows": len(ordered),
                       "independentSourceUpdates": len({e.get("row_identity", (e.get("source_time"), e["sequence"]))
                                                        for e in ordered}),
                       "nonzeroMidChanges": int(np.count_nonzero(np.diff(valid_mid))) if valid_mid else 0,
                       "sourceTimestampIntegersOnly": all(float(s).is_integer() for s in sources),
                       "responseLatency": age_summary([e["response_latency"] for e in ordered
                                                       if e.get("response_latency") is not None])}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="Prospective raw JSONL journal, never a downloaded history backfill")
    parser.add_argument("--output", required=True, help="Exclusive-create offline analysis report")
    args = parser.parse_args()
    try:
        report = analyze_journal(args.input)
    except (ValueError, KeyError, TypeError, OverflowError) as exc:
        report = {"schemaVersion": ANALYSIS_SCHEMA, "authority": AUTHORITY,
                  "verdict": "NO_RESULT_DATA_INSUFFICIENT", "reason": str(exc)}
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as sink:
        json.dump(report, sink, indent=2, allow_nan=False)
        sink.write("\n")
    print(json.dumps({"verdict": report["verdict"], "output": str(path)}))


if __name__ == "__main__":
    main()

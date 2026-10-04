"""Frozen R6 numerical model and paired inference. Offline, NO_TRADE.

Authority lives in prediction-market-relative-value; this module cannot grant
capture or trading permission. All fit inputs must already be qualified/purged.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

import numpy as np
import sklearn
from sklearn.linear_model import Ridge

HORIZONS = (0.5, 1.0, 1.5, 2.0)
RESIDUAL_TOLERANCE = 1e-10
OLS_RCOND = 1e-12
BOOTSTRAP_REPLICATES = 10_000
BOOTSTRAP_SEED = 42


class AnalysisInvalid(ValueError):
    """A calculation failure, never economic NO-GO."""


def finite_matrix(value, columns):
    a = np.asarray(value, dtype=np.float64)
    if a.ndim != 2 or a.shape[1] != columns or not np.isfinite(a).all():
        raise AnalysisInvalid("INVALID_FEATURE_MATRIX")
    return a


@dataclass
class ConstantModel:
    intercept_: float

    @property
    def coef_(self):
        return np.empty(0, dtype=np.float64)

    def predict(self, x):
        return np.full(len(x), self.intercept_, dtype=np.float64)


def ridge_fit(x, y):
    # The intercept-only model is the same unpenalized-intercept objective.
    if x.shape[1] == 0:
        return ConstantModel(float(np.mean(y)))
    return Ridge(alpha=float(len(y)), fit_intercept=True, solver="svd").fit(x, y)


@dataclass
class FittedPair:
    pm_mean: np.ndarray
    pm_std: np.ndarray
    pm_keep: np.ndarray
    world_mean: np.ndarray
    world_std: np.ndarray
    world_keep: np.ndarray
    projection: np.ndarray
    residual_std: np.ndarray
    residual_keep: np.ndarray
    base: object
    full: object
    n_train: int
    training_rows_sha256: str

    @classmethod
    def fit(cls, pm, world, target, row_ids):
        pm = finite_matrix(pm, 7)
        world = finite_matrix(world, 2)
        y = np.asarray(target, dtype=np.float64)
        if not len(y) or y.shape != (len(pm),) or len(world) != len(pm):
            raise AnalysisInvalid("INVALID_TRAINING_ROWS")
        if len(row_ids) != len(y) or not np.isfinite(y).all():
            raise AnalysisInvalid("INVALID_TRAINING_TARGET")
        p_mean, p_std = pm.mean(axis=0), pm.std(axis=0, ddof=0)
        w_mean, w_std = world.mean(axis=0), world.std(axis=0, ddof=0)
        p_keep, w_keep = p_std > 0, w_std > 0
        zp = (pm[:, p_keep] - p_mean[p_keep]) / p_std[p_keep]
        zw = (world[:, w_keep] - w_mean[w_keep]) / w_std[w_keep]
        design = np.column_stack((zp, np.ones(len(y))))
        # rcond is the public NumPy API; no unsupported driver argument.
        projection = np.linalg.lstsq(design, zw, rcond=OLS_RCOND)[0]
        residual = zw - design @ projection
        residual_std = residual.std(axis=0, ddof=0)
        residual_keep = residual_std > RESIDUAL_TOLERANCE
        if not p_keep.any() and not residual_keep.any():
            raise AnalysisInvalid("ALL_TRAIN_FEATURES_CONSTANT_OR_REDUNDANT")
        zr = residual[:, residual_keep] / residual_std[residual_keep]
        base = ridge_fit(zp, y)
        # Reuse predictions exactly when both World residuals are dropped.
        full = ridge_fit(np.column_stack((zp, zr)), y) if residual_keep.any() else base
        digest = hashlib.sha256(json.dumps(list(row_ids), separators=(",", ":")).encode()).hexdigest()
        return cls(p_mean, p_std, p_keep, w_mean, w_std, w_keep, projection,
                   residual_std, residual_keep, base, full, len(y), digest)

    def predict(self, pm, world):
        pm = finite_matrix(pm, 7)
        world = finite_matrix(world, 2)
        if len(pm) != len(world):
            raise AnalysisInvalid("PREDICTION_ROWS_DIFFER")
        zp = (pm[:, self.pm_keep] - self.pm_mean[self.pm_keep]) / self.pm_std[self.pm_keep]
        b = self.base.predict(zp)
        if not np.isfinite(b).all():
            raise AnalysisInvalid("NONFINITE_BASELINE_PREDICTION")
        if self.full is self.base:
            return b, b.copy()
        zw = (world[:, self.world_keep] - self.world_mean[self.world_keep]) / self.world_std[self.world_keep]
        residual = zw - np.column_stack((zp, np.ones(len(pm)))) @ self.projection
        zr = residual[:, self.residual_keep] / self.residual_std[self.residual_keep]
        f = self.full.predict(np.column_stack((zp, zr)))
        if not np.isfinite(b).all() or not np.isfinite(f).all():
            raise AnalysisInvalid("NONFINITE_PREDICTION")
        return b, f

    def parameters(self):
        return {
            "pmMean": self.pm_mean.tolist(), "pmStd": self.pm_std.tolist(),
            "pmKeep": self.pm_keep.tolist(), "worldMean": self.world_mean.tolist(),
            "worldStd": self.world_std.tolist(), "worldKeep": self.world_keep.tolist(),
            "projection": self.projection.tolist(), "residualStd": self.residual_std.tolist(),
            "residualKeep": self.residual_keep.tolist(), "nTrain": self.n_train,
            "trainingRowsSha256": self.training_rows_sha256,
            "ridgeAlpha": float(self.n_train), "baseCoef": self.base.coef_.tolist(),
            "baseIntercept": float(self.base.intercept_), "fullCoef": self.full.coef_.tolist(),
            "fullIntercept": float(self.full.intercept_), "reuseBaseline": self.full is self.base,
        }


def paired_loss(pair, pm, world, target):
    b, f = pair.predict(pm, world)
    y = np.asarray(target, dtype=np.float64)
    if y.shape != b.shape or not np.isfinite(y).all() or not len(y):
        raise AnalysisInvalid("INVALID_VALIDATION_TARGET")
    d = (y - b) ** 2 - (y - f) ** 2
    if not np.isfinite(d).all():
        raise AnalysisInvalid("NONFINITE_PAIRED_LOSS")
    return d, {"delta": float(d.mean()), "mseBaseline": float(np.mean((y - b) ** 2)),
               "mseFull": float(np.mean((y - f) ** 2)),
               "meanPredictedCentsPerShare": float(np.mean(f) * 100),
               "meanAbsolutePredictedCentsPerShare": float(np.mean(np.abs(f)) * 100),
               "meanAbsoluteIncrementalPredictedCentsPerShare": float(np.mean(np.abs(f - b)) * 100),
               "directionAccuracyBaseline": float(np.mean(np.sign(b) == np.sign(y))),
               "directionAccuracyFull": float(np.mean(np.sign(f) == np.sign(y))),
               "zeroLabelCount": int(np.count_nonzero(y == 0)),
               "directionAccuracyConvention": "sign agreement, including zero/zero; not execution value"}


def correlation(x, y):
    x, y = np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64)
    if len(x) < 2 or x.shape != y.shape or not np.isfinite(x).all() or not np.isfinite(y).all():
        raise AnalysisInvalid("INVALID_CORRELATION_ROWS")
    if x.std(ddof=0) == 0 or y.std(ddof=0) == 0:
        raise AnalysisInvalid("CORRELATION_UNDEFINED_ZERO_VARIANCE")
    rho = float(np.corrcoef(x, y)[0, 1])
    if not np.isfinite(rho):
        raise AnalysisInvalid("CORRELATION_NONFINITE")
    return rho


def bootstrap(d, timestamps, market_starts, seconds):
    """Independent paired blocks, row-weighted, UTC grouping, no gap compression."""
    d = np.asarray(d, dtype=np.float64)
    ts = np.asarray(timestamps, dtype=np.float64)
    starts = np.asarray(market_starts, dtype=np.int64)
    if seconds not in (300, 900) or not len(d) or d.shape != ts.shape or d.shape != starts.shape:
        raise AnalysisInvalid("BOOTSTRAP_INPUT_INVALID")
    if not np.isfinite(d).all() or not np.isfinite(ts).all():
        raise AnalysisInvalid("BOOTSTRAP_NONFINITE")
    blocks = starts if seconds == 300 else (ts // 900).astype(np.int64) * 900
    keys, inverse = np.unique(blocks, return_inverse=True)
    sums = np.bincount(inverse, weights=d, minlength=len(keys))
    counts = np.bincount(inverse, minlength=len(keys))
    # np.unique only sees actual rows, so empty blocks never enter the pool.
    keep = counts > 0
    keys, sums, counts = keys[keep], sums[keep], counts[keep]
    if not len(keys):
        raise AnalysisInvalid("BOOTSTRAP_EMPTY_POOL")
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    draws = np.empty(BOOTSTRAP_REPLICATES, dtype=np.float64)
    done = 0
    while done < BOOTSTRAP_REPLICATES:
        batch = min(256, BOOTSTRAP_REPLICATES - done)
        idx = rng.integers(0, len(keys), size=(batch, len(keys)))
        denominator = counts[idx].sum(axis=1)
        valid = denominator > 0
        values = sums[idx][valid].sum(axis=1) / denominator[valid]
        draws[done:done + len(values)] = values
        done += len(values)
    ci = np.percentile(draws, [2.5, 97.5], method="linear")
    if not np.isfinite(ci).all():
        raise AnalysisInvalid("BOOTSTRAP_CI_INVALID")
    return {"blockSeconds": seconds, "replicates": BOOTSTRAP_REPLICATES,
            "seed": BOOTSTRAP_SEED, "rng": "numpy.default_rng/PCG64",
            "percentileMethod": "linear", "confidenceInterval": ci.tolist(),
            "blocks": [{"startTs": int(k), "S": float(s), "N": int(n)}
                       for k, s, n in zip(keys, sums, counts)]}


def runtime_versions():
    return {"numpy": np.__version__, "scikitLearn": sklearn.__version__,
            "dtype": "float64", "standardDeviationDDoF": 0,
            "olsRcond": OLS_RCOND, "residualTolerance": RESIDUAL_TOLERANCE,
            "ridgeObjective": "MSE + 1.0 * squared_l2(coef); intercept unpenalized",
            "ridgeSolver": "svd", "numpyGelsdVerification": {
                "verifiedVersion": "2.4.6", "installedVersionMatches": np.__version__ == "2.4.6",
                "releaseCommit": "b832a09cf2a169c833dd2371e7c07aa00b293242",
                "pythonSourceSha256Lf": "e47d0e2aed21361291cd6651999a59e6479ba5866abb8eabb1dbea5b0d1618ac",
                "kernel": "numpy/linalg/umath_linalg.cpp:lstsq -> call_gelsd -> dgelsd (float64)",
                "verificationScope": "official release source + installed Python wrapper; reverify after version changes"}}

"""
Causal, sample-by-sample filters that could run on a microcontroller.

Every filter exposes `update(x) -> y` (one sample in, one out, O(1) or
O(window) memory) so the same code models an edge device processing a live
stream. `apply(filter, signal)` runs one over a whole array for experiments.

    MovingAverage   N-tap FIR boxcar. Group delay (N-1)/2 samples.
    EMA             1st-order IIR low-pass y = a*x + (1-a)*y.
                    -3 dB cutoff ~ a*fs/(2*pi) for small a.
    MedianFilter    Non-linear; removes impulsive spikes that linear
                    filters only smear out.
    KalmanFilter    Constant-velocity (level + slope) Kalman filter with
                    innovation gating: samples whose innovation exceeds
                    `gate` standard deviations are treated as outliers and
                    skipped, so EMI spikes do not corrupt the state.
"""
from __future__ import annotations

from collections import deque

import numpy as np


class MovingAverage:
    def __init__(self, n: int = 8):
        self.buf = deque(maxlen=n)
        self.acc = 0.0

    def update(self, x: float) -> float:
        if len(self.buf) == self.buf.maxlen:
            self.acc -= self.buf[0]
        self.buf.append(x)
        self.acc += x
        return self.acc / len(self.buf)


class EMA:
    def __init__(self, alpha: float = 0.1):
        if not 0 < alpha <= 1:
            raise ValueError("alpha must be in (0, 1]")
        self.a = alpha
        self.y = None

    def update(self, x: float) -> float:
        self.y = x if self.y is None else self.a * x + (1 - self.a) * self.y
        return self.y


class MedianFilter:
    def __init__(self, n: int = 5):
        self.buf = deque(maxlen=n)

    def update(self, x: float) -> float:
        self.buf.append(x)
        s = sorted(self.buf)
        m = len(s) // 2
        return s[m] if len(s) % 2 else 0.5 * (s[m - 1] + s[m])


class KalmanFilter:
    """
    State  z = [level, slope]^T,  z[n+1] = F z[n] + process noise
    Meas.  y[n] = H z[n] + v[n],   v ~ N(0, r)

    q   process-noise spectral density (how fast the true level may change)
    r   measurement-noise variance (sensor noise_std ** 2)
    gate  outlier gate in innovation standard deviations (None = off)
    """

    # Written with explicit scalar algebra (no matrix library) so it maps
    # directly onto C for a microcontroller; F = [[1, dt], [0, 1]], H = [1, 0].

    def __init__(self, q: float = 0.05, r: float = 1600.0,
                 dt: float = 1.0, gate: float | None = 4.0):
        self.dt, self.R, self.gate = dt, r, gate
        # discrete white-noise-acceleration process noise
        self.q11, self.q12, self.q22 = q * dt**3 / 3, q * dt**2 / 2, q * dt
        self.x = self.v = None            # level, slope
        self.p11, self.p12, self.p22 = r, 0.0, 1.0
        self.rejected = 0

    def update(self, y: float) -> float:
        if self.x is None:
            self.x, self.v = y, 0.0
            return y
        dt = self.dt
        # predict: z = F z,  P = F P F^T + Q
        x = self.x + dt * self.v
        v = self.v
        p11 = self.p11 + 2 * dt * self.p12 + dt * dt * self.p22 + self.q11
        p12 = self.p12 + dt * self.p22 + self.q12
        p22 = self.p22 + self.q22
        # innovation
        innov = y - x
        S = p11 + self.R
        if self.gate is not None and innov * innov > self.gate**2 * S:
            self.rejected += 1                 # outlier: predict-only step
            self.x, self.v, self.p11, self.p12, self.p22 = x, v, p11, p12, p22
            return x
        k1, k2 = p11 / S, p12 / S
        self.x, self.v = x + k1 * innov, v + k2 * innov
        self.p11, self.p12, self.p22 = (1 - k1) * p11, (1 - k1) * p12, p22 - k2 * p12
        return self.x

    @property
    def slope(self) -> float:
        return 0.0 if self.v is None else self.v


class Cascade:
    """Chain filters, e.g. Cascade(MedianFilter(5), EMA(0.1))."""

    def __init__(self, *stages):
        self.stages = stages

    def update(self, x: float) -> float:
        for s in self.stages:
            x = s.update(x)
        return x


def apply(filt, signal) -> np.ndarray:
    return np.array([filt.update(float(v)) for v in signal])


def rmse(estimate, truth) -> float:
    return float(np.sqrt(np.mean((np.asarray(estimate) - np.asarray(truth)) ** 2)))

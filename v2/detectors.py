"""
Streaming fire detectors. Each has `update(sample: dict) -> bool` where
`sample` maps channel name -> measured value, and returns True on the
sample where it raises an alarm.

    RawThreshold       v1 baseline: alarm when raw smoke > threshold
                       (v1 queried `smoke > 450`).
    FilteredThreshold  same rule on a denoised signal.
    CusumDetector      one-sided CUSUM change detector (Page, 1954) on a
                       spike-filtered channel, against a slowly adapting
                       baseline so sensor drift is not mistaken for a fire.
    FusedCusum         alarm only when the smoke AND CO CUSUMs both fire
                       within `window` samples. Steam / cooking raise smoke
                       but not CO, so fusion rejects them.

CUSUM recap. With residual e[n] = x[n] - mu0 and drift allowance k,
    g[n] = max(0, g[n-1] + e[n] - k),   alarm when g[n] > h.
For a mean shift of size delta in Gaussian noise the optimal k is delta/2;
h trades detection delay against false-alarm rate (larger h = fewer false
alarms, longer delay). After an alarm g is reset so alarms can repeat.
"""
from __future__ import annotations

from . import dsp


class RawThreshold:
    def __init__(self, threshold: float = 450.0, channel: str = "smoke"):
        self.th, self.ch = threshold, channel
        self._prev = False

    def update(self, s: dict) -> bool:
        above = s[self.ch] > self.th
        fire = above and not self._prev     # rising edge only
        self._prev = above
        return fire


class FilteredThreshold:
    def __init__(self, threshold: float = 450.0, channel: str = "smoke",
                 filt=None):
        self.th, self.ch = threshold, channel
        self.filt = filt or dsp.KalmanFilter()
        self._prev = False
        self.y = None

    def update(self, s: dict) -> bool:
        self.y = self.filt.update(s[self.ch])
        above = self.y > self.th
        fire = above and not self._prev
        self._prev = above
        return fire


class CusumDetector:
    def __init__(self, channel: str = "smoke", k: float = 50.0,
                 h: float = 500.0, baseline_alpha: float = 0.002,
                 prefilter=None, warmup: int = 30):
        self.ch, self.k, self.h = channel, k, h
        self.pre = prefilter if prefilter is not None else dsp.MedianFilter(5)
        self.base = dsp.EMA(baseline_alpha)
        self.warmup = warmup
        self.g = 0.0
        self.n = 0

    def update(self, s: dict) -> bool:
        x = self.pre.update(s[self.ch])
        self.n += 1
        if self.n <= self.warmup:            # learn the baseline first
            # fast EMA during warm-up so mu0 converges quickly
            mu0 = self.base.y if self.base.y is not None else x
            self.base.y = mu0 + (x - mu0) / self.n
            return False
        mu0 = self.base.y
        self.g = max(0.0, self.g + (x - mu0) - self.k)
        if self.g == 0.0:                    # only adapt when "in control"
            self.base.update(x)
        if self.g > self.h:
            self.g = 0.0
            return True
        return False


class FusedCusum:
    def __init__(self, smoke: CusumDetector | None = None,
                 co: CusumDetector | None = None, window: int = 60):
        self.smoke = smoke or CusumDetector("smoke")
        self.co = co or CusumDetector("carbon-monoxide", k=1.0, h=12.0)
        self.window = window
        self._last_smoke = self._last_co = -10**9
        self._last_alarm = -10**9
        self.n = 0

    def update(self, s: dict) -> bool:
        self.n += 1
        if self.smoke.update(s):
            self._last_smoke = self.n
        if self.co.update(s):
            self._last_co = self.n
        both = (self.n - self._last_smoke <= self.window and
                self.n - self._last_co <= self.window)
        if both and self.n - self._last_alarm > self.window:
            self._last_alarm = self.n
            return True
        return False


def run(detector, measured: dict) -> list:
    """Return the sample indices at which `detector` raised an alarm."""
    n = len(next(iter(measured.values())))
    keys = list(measured)
    alarms = []
    for i in range(n):
        if detector.update({c: float(measured[c][i]) for c in keys}):
            alarms.append(i)
    return alarms

"""
Physics-informed model of a multi-gas smoke detector node.

v1 generated each reading with random.uniform(0, 800), i.e. independent
samples with no time structure, no noise model and no "ground truth" to
compare against. This module replaces that with a sampled signal model:

    x[n] = s[n] + d[n] + w[n] + i[n]        (per channel)
    y[n] = Q_adc( x[n] )

    s[n]  true physical signal: background + fire / nuisance events
    d[n]  slow sensor drift (random walk, e.g. heater ageing / temperature)
    w[n]  white Gaussian measurement noise
    i[n]  impulsive spikes (EMI / supply glitches), Bernoulli-arrival
    Q_adc 12-bit ADC quantisation and clipping to the sensor's full scale

Events
    fire     smoke and CO rise together as first-order step responses,
             temperature follows with a longer time constant.
    nuisance cooking / shower steam: a smoke + humidity bump that decays,
             with no CO rise. This is the classic false-alarm source and
             is what multi-sensor fusion is meant to reject.

Because the true signal s[n] is kept, every downstream filter and detector
can be scored against ground truth.
"""
from __future__ import annotations

import datetime as _dt
import json
from dataclasses import dataclass, field

import numpy as np

CHANNELS = ("smoke", "carbon-monoxide", "temperature", "humidity")


@dataclass
class ChannelSpec:
    baseline: float          # resting value of the true signal
    full_scale: tuple        # (min, max) of the sensor / ADC range
    noise_std: float         # white-noise standard deviation
    drift_std: float         # random-walk step std per sample
    spike_prob: float = 0.0  # probability of an impulse per sample
    spike_amp: float = 0.0   # impulse amplitude (sign is random)
    adc_bits: int = 12


DEFAULT_SPECS = {
    # units: smoke ppm, CO ppm, temperature degF, humidity fraction (0..1)
    "smoke": ChannelSpec(150.0, (0.0, 1000.0), 40.0, 0.15, 0.003, 350.0),
    "carbon-monoxide": ChannelSpec(1.5, (0.0, 50.0), 0.4, 0.002, 0.002, 4.0),
    "temperature": ChannelSpec(72.0, (32.0, 212.0), 0.3, 0.003),
    "humidity": ChannelSpec(0.40, (0.0, 1.0), 0.01, 0.0001),
}


@dataclass
class Event:
    kind: str        # "fire" or "nuisance"
    start: int       # sample index
    amplitude: float  # peak smoke rise (ppm)
    tau: float       # rise time constant (samples)
    duration: int = 0  # nuisance only: samples before decay starts


@dataclass
class Trace:
    """A simulated recording. All arrays have shape (n_samples,)."""
    fs: float
    true: dict          # channel -> clean physical signal s[n]
    measured: dict      # channel -> ADC output y[n]
    events: list = field(default_factory=list)

    @property
    def n(self) -> int:
        return len(next(iter(self.true.values())))


def _step_response(n, start, amp, tau):
    t = np.arange(n) - start
    out = np.zeros(n)
    m = t >= 0
    out[m] = amp * (1.0 - np.exp(-t[m] / tau))
    return out


def _bump(n, start, amp, tau, duration):
    """Rise with time constant tau, hold for `duration`, then decay."""
    rise = _step_response(n, start, amp, tau)
    t_dec = np.arange(n) - (start + duration)
    decay = np.ones(n)
    m = t_dec >= 0
    decay[m] = np.exp(-t_dec[m] / (2.0 * tau))
    return rise * decay


def quantize(x, lo, hi, bits):
    """Uniform mid-tread ADC: clip to [lo, hi], round to 2**bits levels."""
    levels = 2 ** bits - 1
    code = np.round((np.clip(x, lo, hi) - lo) / (hi - lo) * levels)
    return lo + code / levels * (hi - lo)


def simulate(
    n_samples: int = 1800,
    fs: float = 1.0,
    events: list | None = None,
    specs: dict | None = None,
    noise_scale: float = 1.0,
    seed: int | None = None,
) -> Trace:
    """Generate one recording of `n_samples` at `fs` Hz."""
    rng = np.random.default_rng(seed)
    specs = specs or DEFAULT_SPECS
    events = events or []
    n = n_samples

    true = {c: np.full(n, specs[c].baseline) for c in CHANNELS}
    for ev in events:
        if ev.kind == "fire":
            true["smoke"] += _step_response(n, ev.start, ev.amplitude, ev.tau)
            # CO tracks smoke (incomplete combustion), ~1 ppm CO per 25 ppm smoke
            true["carbon-monoxide"] += _step_response(
                n, ev.start, ev.amplitude / 25.0, ev.tau)
            true["temperature"] += _step_response(
                n, ev.start, ev.amplitude / 20.0, 3.0 * ev.tau)
        elif ev.kind == "nuisance":
            true["smoke"] += _bump(n, ev.start, ev.amplitude, ev.tau, ev.duration)
            true["humidity"] += _bump(n, ev.start, 0.35, ev.tau, ev.duration)
        else:
            raise ValueError(f"unknown event kind {ev.kind!r}")

    measured = {}
    for c in CHANNELS:
        sp = specs[c]
        drift = np.cumsum(rng.normal(0.0, sp.drift_std, n))
        white = rng.normal(0.0, sp.noise_std * noise_scale, n)
        spikes = np.zeros(n)
        if sp.spike_prob > 0:
            hit = rng.random(n) < sp.spike_prob
            spikes[hit] = sp.spike_amp * rng.choice([-1.0, 1.0], hit.sum())
        x = true[c] + drift + white + spikes
        measured[c] = quantize(x, *sp.full_scale, sp.adc_bits)

    return Trace(fs=fs, true=true, measured=measured, events=list(events))


def random_scenario(rng: np.random.Generator, n_samples: int,
                    p_fire: float = 0.5, p_nuisance: float = 0.5) -> list:
    """Draw a random set of events for one Monte Carlo trial."""
    evs = []
    if rng.random() < p_nuisance:
        evs.append(Event("nuisance", int(rng.integers(100, n_samples // 2)),
                         amplitude=float(rng.uniform(150, 350)),
                         tau=float(rng.uniform(10, 30)),
                         duration=int(rng.integers(30, 120))))
    if rng.random() < p_fire:
        evs.append(Event("fire", int(rng.integers(n_samples // 2, n_samples - 400)),
                         amplitude=float(rng.uniform(300, 600)),
                         tau=float(rng.uniform(40, 120))))
    return evs


def to_json_messages(trace: Trace, device_id: str = "0-ZZZ123456780A",
                     t0: _dt.datetime | None = None):
    """Yield one v1-schema JSON message per sample (for the Kafka path)."""
    t0 = t0 or _dt.datetime.now(_dt.timezone.utc)
    for k in range(trace.n):
        ts = t0 + _dt.timedelta(seconds=k / trace.fs)
        yield json.dumps({
            "guid": device_id,
            "destination": "0-AAA12345678",
            "eventTime": ts.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "payload": {
                "format": "urn:example:sensor:Smoke",
                "data": {c: round(float(trace.measured[c][k]), 5) for c in CHANNELS},
            },
        })

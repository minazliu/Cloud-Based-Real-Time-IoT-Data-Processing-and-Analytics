"""Run from repo root:  python -m pytest -q"""
import numpy as np

from v2 import detectors as D
from v2 import dsp, edge
from v2 import sensor_model as sm


def test_adc_quantization_levels_and_clipping():
    x = np.array([-5.0, 0.0, 500.0, 1000.0, 2000.0])
    y = sm.quantize(x, 0.0, 1000.0, 12)
    assert y[0] == 0.0 and y[-1] == 1000.0
    step = 1000.0 / 4095
    assert abs(y[2] - 500.0) <= step / 2 + 1e-9


def test_moving_average_matches_numpy():
    x = np.arange(20, dtype=float)
    y = dsp.apply(dsp.MovingAverage(4), x)
    assert np.allclose(y[3:], np.convolve(x, np.ones(4) / 4, "valid"))


def test_ema_step_response_time_constant():
    a = 0.1
    y = dsp.apply(dsp.EMA(a), np.r_[0.0, np.ones(50)])
    # after k samples a unit step reaches 1 - (1-a)^k
    assert np.isclose(y[10], 1 - (1 - a) ** 10)


def test_median_filter_removes_single_spike():
    x = np.full(20, 100.0)
    x[10] = 900.0
    assert dsp.apply(dsp.MedianFilter(5), x).max() == 100.0


def test_kalman_tracks_ramp_and_rejects_spike():
    rng = np.random.default_rng(0)
    truth = 100 + 2.0 * np.arange(300)
    y = truth + rng.normal(0, 10, 300)
    y[150] += 500                                   # EMI spike
    kf = dsp.KalmanFilter(q=0.01, r=100.0)
    est = dsp.apply(kf, y)
    assert kf.rejected >= 1
    assert dsp.rmse(est[50:], truth[50:]) < 6.0     # well below noise std 10
    assert abs(kf.slope - 2.0) < 0.2


def test_filters_reduce_error():
    tr = sm.simulate(1200, seed=1)
    raw = dsp.rmse(tr.measured["smoke"], tr.true["smoke"])
    filt = dsp.rmse(dsp.apply(dsp.KalmanFilter(), tr.measured["smoke"]),
                    tr.true["smoke"])
    assert filt < 0.5 * raw


def test_fused_cusum_ignores_steam_detects_fire():
    steam = sm.simulate(1500, events=[sm.Event("nuisance", 300, 300, 20, 90)], seed=2)
    fire = sm.simulate(1500, events=[sm.Event("fire", 800, 450, 60)], seed=3)
    assert D.run(D.FusedCusum(D.CusumDetector(h=200)), steam.measured) == []
    alarms = D.run(D.FusedCusum(D.CusumDetector(h=200)), fire.measured)
    assert alarms and 800 <= alarms[0] <= 800 + 120


def test_send_on_delta_and_reconstruction():
    v = np.r_[np.zeros(10), np.full(10, 50.0)]
    idx = edge.send_on_delta(v, deadband=20, heartbeat=100)
    assert list(idx) == [0, 10]
    assert np.array_equal(edge.zoh_reconstruct(v, idx, len(v)), v)


def test_json_schema_matches_v1():
    import json
    m = json.loads(next(sm.to_json_messages(sm.simulate(1, seed=0))))
    assert set(m) == {"guid", "destination", "eventTime", "payload"}
    assert set(m["payload"]["data"]) == {"smoke", "humidity", "temperature",
                                         "carbon-monoxide"}

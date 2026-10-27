"""
Edge latency: wall-clock time for the on-device pipeline to handle one
sample: Kalman filter on smoke + fused smoke/CO CUSUM decision.

    python -m v2.latency.edge_bench               # 20 000 samples

Measured in Python on a laptop CPU, which is an *upper bound on code
overhead* but not a microcontroller number (a C port on an MCU would be
slower per op but has no interpreter overhead). Reported as such.
"""
import argparse
import time

from .. import detectors as D
from .. import dsp
from .. import sensor_model as sm
from . import save


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", type=int, default=20000)
    a = ap.parse_args()

    evs = [sm.Event("nuisance", a.samples // 4, 250, 20, 60),
           sm.Event("fire", a.samples // 2, 450, 80)]
    tr = sm.simulate(a.samples, events=evs, seed=0)
    rows = [{c: float(tr.measured[c][i]) for c in sm.CHANNELS}
            for i in range(a.samples)]

    kf = dsp.KalmanFilter()
    det = D.FusedCusum(D.CusumDetector(h=200))
    for r in rows[:500]:                      # warm-up (caches, allocator)
        kf.update(r["smoke"]); det.update(r)

    kf = dsp.KalmanFilter()
    det = D.FusedCusum(D.CusumDetector(h=200))
    lat = []
    clock = time.perf_counter_ns
    for r in rows:
        t0 = clock()
        kf.update(r["smoke"])
        det.update(r)
        lat.append((clock() - t0) / 1e6)      # ns -> ms
    save("edge", lat, {"what": "Kalman + fused CUSUM, per sample",
                       "samples": a.samples})


if __name__ == "__main__":
    main()

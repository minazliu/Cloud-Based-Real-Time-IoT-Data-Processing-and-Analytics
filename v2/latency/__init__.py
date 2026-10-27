"""
Edge vs cloud decision-latency measurements.

    edge      time for the on-device pipeline (Kalman + fused CUSUM) to
              process one sample                          -> edge_bench.py
    network   real round trip laptop -> public MQTT broker -> laptop,
              QoS 1, on Wi-Fi and on a phone hotspot      -> mqtt_rtt.py
    cloud     time from a reading landing in the cloud to Spark Structured
              Streaming flagging it                       -> spark_latency.py

    cloud decision latency = network round trip + Spark processing
    (full round trip: sensor -> cloud decides -> alarm back to the device)

combine.py merges the saved measurements into a table and chart.
"""
import json
import os
import platform

import numpy as np

OUT = os.environ.get("LATENCY_OUT") or os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "results", "latency"))


def summarize(ms):
    a = np.asarray(ms, float)
    return {"n": int(a.size), "median_ms": float(np.median(a)),
            "p95_ms": float(np.percentile(a, 95)),
            "p99_ms": float(np.percentile(a, 99)),
            "mean_ms": float(a.mean()), "min_ms": float(a.min()),
            "max_ms": float(a.max())}


def save(name, samples_ms, meta):
    os.makedirs(OUT, exist_ok=True)
    rec = {"meta": meta | {"machine": platform.platform(),
                           "python": platform.python_version()},
           "summary": summarize(samples_ms),
           "samples_ms": [round(float(x), 4) for x in samples_ms]}
    path = os.path.join(OUT, f"{name}.json")
    with open(path, "w") as f:
        json.dump(rec, f, indent=1)
    s = rec["summary"]
    print(f"[{name}] n={s['n']}  median={s['median_ms']:.3f} ms  "
          f"p95={s['p95_ms']:.3f} ms  p99={s['p99_ms']:.3f} ms  -> {path}")
    return rec

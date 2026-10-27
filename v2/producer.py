"""
Python 3 replacement for v1's iotsimulator.py.

Generates sensor-model traces (several devices, with fire / steam events)
and emits one v1-schema JSON message per sample, either

  * to Kafka           --bootstrap localhost:9092 --topic iotmsgs
                       (needs `pip install kafka-python`)
  * to a directory     --out-dir data/   (JSON-lines files; lets the Spark
                       job run with no Kafka at all, using its file source)
  * to stdout          default

Examples
  python -m v2.producer --devices 3 --seconds 600 --out-dir data/
  python -m v2.producer --bootstrap localhost:9092 --topic iotmsgs --rate 10
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import numpy as np

from . import sensor_model as sm


def device_streams(n_devices, seconds, seed):
    rng = np.random.default_rng(seed)
    for d in range(n_devices):
        evs = sm.random_scenario(rng, seconds) if seconds >= 900 else []
        tr = sm.simulate(seconds, events=evs, seed=int(rng.integers(1 << 31)))
        dev = f"0-ZZZ12345678{d}{chr(65 + d % 26)}"
        kinds = ", ".join(f"{e.kind}@{e.start}s" for e in evs) or "none"
        print(f"[producer] {dev}: events = {kinds}", file=sys.stderr)
        yield list(sm.to_json_messages(tr, device_id=dev))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--devices", type=int, default=3)
    ap.add_argument("--seconds", type=int, default=1800)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--bootstrap")
    ap.add_argument("--topic", default="iotmsgs")
    ap.add_argument("--out-dir")
    ap.add_argument("--rate", type=float, default=0,
                    help="samples per second per device (0 = as fast as possible)")
    a = ap.parse_args()

    streams = list(device_streams(a.devices, a.seconds, a.seed))
    # interleave devices sample-by-sample, as they would arrive in real time
    msgs = [m for tick in zip(*streams) for m in tick]

    if a.bootstrap:
        from kafka import KafkaProducer
        prod = KafkaProducer(bootstrap_servers=a.bootstrap)
        for i, m in enumerate(msgs):
            prod.send(a.topic, m.encode())
            if a.rate and (i + 1) % a.devices == 0:
                time.sleep(1.0 / a.rate)
        prod.flush()
    elif a.out_dir:
        os.makedirs(a.out_dir, exist_ok=True)
        chunk = 60 * a.devices                    # one file per simulated minute
        for j in range(0, len(msgs), chunk):
            with open(os.path.join(a.out_dir, f"part-{j // chunk:05d}.json"), "w") as f:
                f.write("\n".join(msgs[j:j + chunk]) + "\n")
    else:
        for m in msgs:
            print(m)
    print(f"[producer] {len(msgs)} messages", file=sys.stderr)


if __name__ == "__main__":
    main()

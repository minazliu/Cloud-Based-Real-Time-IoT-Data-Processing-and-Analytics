"""
Cloud processing latency: time from a reading landing in the cloud to Spark
Structured Streaming evaluating the alert rule on it.

A writer thread drops one reading per second into a watched directory
(atomic rename, stamped with `written_at`). The streaming query parses it
with the v2 schema and applies the per-reading fused rule
(smoke > 450 AND CO > 5). In foreachBatch we record now - written_at.
No windowing, so this measures Spark's micro-batch overhead, not a window
length. The first --warmup readings (JVM / query start-up) are discarded.

    python -m v2.latency.spark_latency            # ~70 s
"""
import argparse
import json
import os
import shutil
import tempfile
import threading
import time

from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import DoubleType, StructField, StructType

from .. import sensor_model as sm
from ..stream_job import SCHEMA
from . import save


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=60, help="readings to measure")
    ap.add_argument("--warmup", type=int, default=10)
    ap.add_argument("--interval", type=float, default=1.0)
    a = ap.parse_args()
    total = a.n + a.warmup

    work = tempfile.mkdtemp(prefix="spark-lat-")
    inbox, staging = os.path.join(work, "in"), os.path.join(work, "stage")
    os.makedirs(inbox), os.makedirs(staging)

    spark = (SparkSession.builder.appName("spark-latency")
             .master("local[2]")
             .config("spark.sql.shuffle.partitions", "1")
             .config("spark.ui.enabled", "false")
             .getOrCreate())
    spark.sparkContext.setLogLevel("ERROR")

    schema = StructType(SCHEMA.fields + [StructField("written_at", DoubleType())])
    df = (spark.readStream.schema(schema).json(inbox)
          .select("guid", "written_at",
                  ((F.col("payload.data.smoke") > 450) &
                   (F.col("payload.data.`carbon-monoxide`") > 5)).alias("alert")))

    lat = {}

    def on_batch(batch, batch_id):
        for r in batch.collect():           # alert evaluated + on the driver
            lat[r["guid"]] = (time.time() - r["written_at"]) * 1000.0

    q = (df.writeStream.foreachBatch(on_batch)
         .option("checkpointLocation", os.path.join(work, "ckpt"))
         .start())

    msgs = list(sm.to_json_messages(sm.simulate(total, seed=2)))

    def writer():
        for i, body in enumerate(msgs):
            m = json.loads(body)
            m["guid"] = f"r{i:05d}"
            src = os.path.join(staging, f"{i:05d}.json")
            with open(src, "w") as f:
                m["written_at"] = time.time()
                f.write(json.dumps(m) + "\n")
            os.rename(src, os.path.join(inbox, f"{i:05d}.json"))
            time.sleep(a.interval)

    print(f"writing {total} readings ({a.warmup} warm-up), 1 per {a.interval}s ...")
    t = threading.Thread(target=writer)
    t.start()
    t.join()
    deadline = time.time() + 30
    while len(lat) < total and time.time() < deadline:
        time.sleep(0.2)
    q.stop()
    spark.stop()
    shutil.rmtree(work, ignore_errors=True)

    keep = [lat[k] for k in sorted(lat) if int(k[1:]) >= a.warmup]
    print(f"measured {len(keep)}/{a.n} (missing: {a.n - len(keep)})")
    save("spark", keep, {"what": "file landed -> Spark micro-batch evaluated alert",
                         "source": "file", "trigger": "default (ASAP)",
                         "warmup_discarded": a.warmup})


if __name__ == "__main__":
    main()

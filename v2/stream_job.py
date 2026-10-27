"""
Spark Structured Streaming port of v1's kafka-direct-iot-sql.py.

What changed vs v1
  * DStreams / KafkaUtils (removed in Spark 3)  ->  Structured Streaming
  * per-batch string concat + regex + re-parallelize  ->  from_json with an
    explicit schema (parsing runs on executors, scales with partitions)
  * driver-side accumulator for "max smoke"  ->  event-time windowed
    aggregation with a watermark for late data
  * bare `except: pass`  ->  errors surface
  * single-sensor threshold  ->  fused smoke + CO alert rule per window

Run with Kafka (see docker-compose.yml):
  spark-submit --packages org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.3 \
      v2/stream_job.py --source kafka --bootstrap localhost:9092 --topic iotmsgs

Run with no Kafka (file source, e.g. output of `python -m v2.producer --out-dir data/`):
  python -m v2.stream_job --source files --path data/ --once
"""
from __future__ import annotations

import argparse

from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import (DoubleType, StringType, StructField,
                               StructType)

SCHEMA = StructType([
    StructField("guid", StringType()),
    StructField("destination", StringType()),
    StructField("eventTime", StringType()),
    StructField("payload", StructType([
        StructField("format", StringType()),
        StructField("data", StructType([
            StructField("smoke", DoubleType()),
            StructField("carbon-monoxide", DoubleType()),
            StructField("temperature", DoubleType()),
            StructField("humidity", DoubleType()),
        ])),
    ])),
])

SMOKE_ALERT = 450.0   # ppm, window mean
CO_ALERT = 5.0        # ppm, window mean; steam raises smoke but not CO


def build(spark, args):
    if args.source == "kafka":
        raw = (spark.readStream.format("kafka")
               .option("kafka.bootstrap.servers", args.bootstrap)
               .option("subscribe", args.topic)
               .option("startingOffsets", "latest")
               .load()
               .select(F.col("value").cast("string").alias("json")))
        parsed = raw.select(F.from_json("json", SCHEMA).alias("m")).select("m.*")
    else:
        parsed = (spark.readStream.schema(SCHEMA)
                  .option("maxFilesPerTrigger", 5).json(args.path))

    readings = parsed.select(
        F.col("guid").alias("device"),
        F.to_timestamp("eventTime").alias("ts"),
        F.col("payload.data.smoke").alias("smoke"),
        F.col("payload.data.`carbon-monoxide`").alias("co"),
        F.col("payload.data.temperature").alias("temp"),
        F.col("payload.data.humidity").alias("humidity"),
    )

    windows = (readings
               .withWatermark("ts", "30 seconds")
               .groupBy(F.window("ts", args.window), "device")
               .agg(F.count("*").alias("n"),
                    F.round(F.avg("smoke"), 1).alias("smoke_avg"),
                    F.max("smoke").alias("smoke_max"),
                    F.round(F.stddev("smoke"), 1).alias("smoke_std"),
                    F.round(F.avg("co"), 2).alias("co_avg"),
                    F.round(F.avg("humidity"), 3).alias("hum_avg"))
               .withColumn("alert",
                           (F.col("smoke_avg") > SMOKE_ALERT) &
                           (F.col("co_avg") > CO_ALERT))
               .select(F.col("window.start").alias("start"), "device", "n",
                       "smoke_avg", "smoke_max", "smoke_std", "co_avg",
                       "hum_avg", "alert"))
    return windows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["kafka", "files"], default="kafka")
    ap.add_argument("--bootstrap", default="localhost:9092")
    ap.add_argument("--topic", default="iotmsgs")
    ap.add_argument("--path", default="data/")
    ap.add_argument("--window", default="1 minute")
    ap.add_argument("--once", action="store_true",
                    help="process what is available, then stop (file source)")
    args = ap.parse_args()

    spark = (SparkSession.builder.appName("iot-structured-streaming")
             .config("spark.sql.shuffle.partitions", "4")
             .getOrCreate())
    spark.sparkContext.setLogLevel("WARN")

    q = (build(spark, args).writeStream
         .outputMode("append")          # emit each window once it is final
         .format("console")
         .option("truncate", False)
         .option("numRows", 50))
    if args.once:
        q = q.trigger(availableNow=True)
    q.start().awaitTermination()


if __name__ == "__main__":
    main()

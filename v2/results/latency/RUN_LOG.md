# Latency measurement run log

**Date:** 2026-10-26, ~19:58–20:05 PDT
**Operator:** Mina
**Location:** home

## Environment

| item | value |
|---|---|
| Machine | MacBook, Apple silicon (`macOS-26.7.1-arm64`) |
| Python | 3.11.11 (Anaconda) |
| Java | OpenJDK 17.0.18 (Zulu, via conda-forge) |
| PySpark | 3.5.3, `local[2]` |
| paho-mqtt | 2.1.0 |
| MQTT broker | `broker.emqx.io:1883`, TCP, no TLS, QoS 1 |
| Networks | home Wi-Fi; phone hotspot (cellular) |

## Broker selection

The planned broker `test.mosquitto.org:1883` timed out. A TCP connectivity
check from the same network showed it was the only broker that failed:

```
FAIL  test.mosquitto.org:1883  (TimeoutError)
OK    broker.hivemq.com:1883
OK    broker.emqx.io:1883
OK    broker.emqx.io:8883
OK    broker.emqx.io:8084
OK    broker.hivemq.com:8884
```

`broker.emqx.io:1883` was chosen and used for **both** networks so the
Wi-Fi vs cellular comparison uses the same broker.

## Commands and output

```
$ python -m v2.latency.mqtt_rtt --label wifi --broker broker.emqx.io
connecting to broker.emqx.io:1883 (tcp) ...
sending 500 msgs at 5.0/s, QoS 1
lost: 0/500
[mqtt_wifi] n=500  median=35.784 ms  p95=232.568 ms  p99=584.712 ms

# switched laptop to phone hotspot
$ python -m v2.latency.mqtt_rtt --label hotspot --broker broker.emqx.io
connecting to broker.emqx.io:1883 (tcp) ...
sending 500 msgs at 5.0/s, QoS 1
lost: 0/500
[mqtt_hotspot] n=500  median=296.079 ms  p95=644.472 ms  p99=938.236 ms

$ python -m v2.latency.edge_bench
[edge] n=20000  median=0.001 ms  p95=0.003 ms  p99=0.003 ms

$ python -m v2.latency.spark_latency
writing 70 readings (10 warm-up), 1 per 1.0s ...
measured 60/60 (missing: 0)
[spark] n=60  median=387.925 ms  p95=420.036 ms  p99=438.535 ms

$ python -m v2.latency.combine
-> latency.md, latency.png
```

## Raw data

Every individual timing plus metadata is saved in this folder:
`edge.json`, `mqtt_wifi.json`, `mqtt_hotspot.json`, `spark.json`.
`latency.md` and `latency.png` are generated from them by `combine`.

## Notes and limitations

- **Spark ran locally on the laptop**, not on a cloud server, so the cloud figure is an **estimate**: "Cloud
  decision" = real measured network round trip + locally measured Spark
  processing, combined by random pairing (assumes the two are independent).
- Edge timing is Python on a laptop CPU, not a microcontroller.
- The public broker is shared, so network times include its load. Wi-Fi
  was steady (IQR 34–40 ms) with occasional spikes (7% > 200 ms).
- Single session; times vary by time of day and network conditions.

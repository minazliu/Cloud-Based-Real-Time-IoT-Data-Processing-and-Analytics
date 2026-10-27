# Edge vs cloud decision latency

Measured on: macOS-26.7.1-arm64-arm-64bit

| path | median | p95 | p99 | n | note |
|---|---|---|---|---|---|
| Edge (on-device Kalman + fused CUSUM) | 1.3 µs | 3.0 µs | 3.1 µs | 20000 |  |
| Network round trip, hotspot (MQTT QoS 1) | 296.1 ms | 644.5 ms | 938.2 ms | 500 | lost 0/500 |
| Network round trip, wifi (MQTT QoS 1) | 35.8 ms | 232.6 ms | 584.7 ms | 500 | lost 0/500 |
| Spark processing (file → alert evaluated) | 387.9 ms | 420.0 ms | 438.5 ms | 60 |  |
| **Estimated cloud decision, hotspot** (network + Spark, full round trip) | 677.0 ms | 1033.1 ms | 1333.3 ms | 20000 |  |
| **Estimated cloud decision, wifi** (network + Spark, full round trip) | 428.9 ms | 620.7 ms | 971.6 ms | 20000 |  |

- **hotspot:** estimated cloud decision median 677.0 ms vs edge 1.3 µs (≈507,864× faster on the edge); cloud p95 1033.1 ms.

- **wifi:** estimated cloud decision median 428.9 ms vs edge 1.3 µs (≈321,730× faster on the edge); cloud p95 620.7 ms.

Edge timing is Python on a laptop CPU, not a microcontroller. Network and Spark were measured separately and combined by random pairing (assumes independence). The public broker is shared, so network times include its load.

![latency](latency.png)

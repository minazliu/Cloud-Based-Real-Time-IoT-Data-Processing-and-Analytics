"""
Merge saved latency measurements into a table + chart.

    python -m v2.latency.combine

edge decision   = edge per-sample compute (alarm sounds locally)
cloud decision  = MQTT round trip (network) + Spark processing, for each
                  network label found (wifi, hotspot, ...). The two parts
                  were measured separately, so the total distribution is
                  estimated by summing randomly paired samples (assumes the
                  two delays are independent).
"""
import glob
import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from . import OUT, summarize


def load(name):
    p = os.path.join(OUT, f"{name}.json")
    if not os.path.exists(p):
        return None
    with open(p) as f:
        return json.load(f)


def main():
    edge, spark = load("edge"), load("spark")
    nets = {os.path.basename(p)[5:-5]: json.load(open(p))
            for p in sorted(glob.glob(os.path.join(OUT, "mqtt_*.json")))}
    if edge is None:
        raise SystemExit("run v2.latency.edge_bench first")
    rng = np.random.default_rng(0)

    rows = [("Edge (on-device Kalman + fused CUSUM)", edge["summary"], "")]
    for label, net in nets.items():
        rows.append((f"Network round trip, {label} (MQTT QoS {net['meta']['qos']})",
                     net["summary"], f"lost {net['meta']['lost']}/{net['meta']['sent']}"))
    if spark:
        rows.append(("Spark processing (file → alert evaluated)", spark["summary"], ""))
    totals = {}
    for label, net in nets.items():
        r = np.array(net["samples_ms"])
        s = np.array(spark["samples_ms"]) if spark else np.zeros(1)
        tot = rng.choice(r, 20000) + rng.choice(s, 20000)
        totals[label] = summarize(tot)
        name = "network + Spark" if spark else "network only"
        rows.append((f"**Cloud decision, {label}** ({name}, full round trip)",
                     totals[label], ""))

    def fmt(v):
        return f"{v * 1000:.1f} µs" if v < 0.1 else f"{v:.1f} ms"

    e = edge["summary"]["median_ms"]
    L = ["# Edge vs cloud decision latency\n",
         f"Measured on: {edge['meta']['machine']}\n",
         "| path | median | p95 | p99 | n | note |",
         "|---|---|---|---|---|---|"]
    for name, s, note in rows:
        L.append(f"| {name} | {fmt(s['median_ms'])} | {fmt(s['p95_ms'])} | "
                 f"{fmt(s['p99_ms'])} | {s['n']} | {note} |")
    for label, t in totals.items():
        L.append(f"\n- **{label}:** cloud decision median {fmt(t['median_ms'])} vs "
                 f"edge {fmt(e)} (≈{t['median_ms'] / e:,.0f}× faster on the edge); "
                 f"cloud p95 {fmt(t['p95_ms'])}.")
    L.append("\nEdge timing is Python on a laptop CPU, not a microcontroller. "
             "Network and Spark were measured separately and combined by random "
             "pairing (assumes independence). The public broker is shared, so "
             "network times include its load.\n")
    L.append("![latency](latency.png)\n")
    with open(os.path.join(OUT, "latency.md"), "w") as f:
        f.write("\n".join(L))

    labels = ["edge"] + [f"cloud\n({k})" for k in totals]
    med = [e] + [t["median_ms"] for t in totals.values()]
    p95 = [edge["summary"]["p95_ms"]] + [t["p95_ms"] for t in totals.values()]
    fig, ax = plt.subplots(figsize=(5.5, 4))
    x = np.arange(len(labels))
    ax.bar(x - 0.18, med, 0.36, label="median")
    ax.bar(x + 0.18, p95, 0.36, label="p95")
    for xi, v in zip(x, med):
        ax.text(xi - 0.18, v * 1.15, f"{v:.3g}", ha="center", fontsize=8)
    ax.set_yscale("log")
    ax.set_xticks(x, labels)
    ax.set_ylabel("decision latency (ms, log)")
    ax.set_title("Edge vs cloud alarm decision latency")
    ax.legend()
    ax.grid(alpha=.3, axis="y", which="both")
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "latency.png"), dpi=150)
    print("\n".join(L[:len(rows) + 4]))
    print(f"\n-> {os.path.join(OUT, 'latency.md')}")


if __name__ == "__main__":
    main()

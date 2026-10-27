"""
Network latency: real round trip laptop -> public MQTT broker -> laptop.

The same process publishes v1-format sensor readings and subscribes to the
topic, so send and receive times come from one clock (no clock-sync error).
Each message carries a sequence number; RTT = receive time - send time.

    python -m v2.latency.mqtt_rtt --label wifi
    python -m v2.latency.mqtt_rtt --label hotspot      # after switching networks

Defaults: test.mosquitto.org:1883, QoS 1, 500 messages at 5 Hz.
Lost messages (no echo within --timeout s) are counted and reported.
"""
import argparse
import json
import threading
import time
import uuid

import paho.mqtt.client as mqtt

from .. import sensor_model as sm
from . import save


def make_client(transport="tcp"):
    try:                                     # paho-mqtt >= 2.0
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, transport=transport)
    except AttributeError:                   # paho-mqtt 1.x
        return mqtt.Client(transport=transport)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True, help="e.g. wifi, hotspot")
    ap.add_argument("--broker", default="test.mosquitto.org")
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--qos", type=int, default=1, choices=[0, 1, 2])
    ap.add_argument("--n", type=int, default=500)
    ap.add_argument("--rate", type=float, default=5.0, help="messages/s")
    ap.add_argument("--timeout", type=float, default=10.0)
    ap.add_argument("--transport", choices=["tcp", "websockets"], default="tcp",
                    help="websockets helps on networks that block port 1883")
    ap.add_argument("--ws-path", default="/mqtt")
    ap.add_argument("--tls", action="store_true", help="encrypted connection")
    a = ap.parse_args()

    topic = f"iot-latency-test/{uuid.uuid4().hex[:12]}"   # private-ish topic
    msgs = list(sm.to_json_messages(sm.simulate(a.n, seed=1)))
    sent, rtt = {}, {}
    lock = threading.Lock()
    subscribed = threading.Event()

    def on_connect(c, userdata, flags, rc, *props):
        c.subscribe(topic, qos=a.qos)

    def on_subscribe(c, *args):
        subscribed.set()

    def on_message(c, userdata, m):
        t = time.perf_counter()
        seq = json.loads(m.payload)["seq"]
        with lock:
            if seq in sent and seq not in rtt:
                rtt[seq] = (t - sent[seq]) * 1000.0

    c = make_client(a.transport)
    if a.transport == "websockets":
        c.ws_set_options(path=a.ws_path)
    if a.tls:
        c.tls_set()                          # system CA certificates
    c.on_connect, c.on_subscribe, c.on_message = on_connect, on_subscribe, on_message
    print(f"connecting to {a.broker}:{a.port} ({a.transport}"
          f"{', TLS' if a.tls else ''}) ...")
    try:
        c.connect(a.broker, a.port, keepalive=30)
    except OSError as e:
        raise SystemExit(f"cannot reach {a.broker}:{a.port} ({e}). This network may "
                         "block the port; try another broker/port (see README).")
    c.loop_start()
    if not subscribed.wait(15):
        raise SystemExit("could not subscribe: broker unreachable?")

    print(f"sending {a.n} msgs at {a.rate}/s, QoS {a.qos}, topic {topic}")
    period = 1.0 / a.rate
    nxt = time.perf_counter()
    for i, body in enumerate(msgs):
        payload = json.dumps({"seq": i, "reading": json.loads(body)})
        with lock:
            sent[i] = time.perf_counter()
        c.publish(topic, payload, qos=a.qos)
        nxt += period
        time.sleep(max(0.0, nxt - time.perf_counter()))
        if (i + 1) % 100 == 0:
            print(f"  {i + 1}/{a.n} sent, {len(rtt)} echoed")

    deadline = time.time() + a.timeout
    while len(rtt) < a.n and time.time() < deadline:
        time.sleep(0.1)
    c.loop_stop()
    c.disconnect()

    lost = a.n - len(rtt)
    print(f"lost: {lost}/{a.n}")
    save(f"mqtt_{a.label}", [rtt[k] for k in sorted(rtt)],
         {"what": "MQTT publish -> broker -> subscribe round trip",
          "label": a.label, "broker": f"{a.broker}:{a.port}", "qos": a.qos,
          "transport": a.transport, "tls": a.tls,
          "rate_hz": a.rate, "sent": a.n, "lost": lost,
          "payload_bytes": len(payload)})


if __name__ == "__main__":
    main()

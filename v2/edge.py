"""
Edge-side data reduction: filter on the device, transmit only what matters.

v1 streamed every raw sample to the cloud. Here the node runs a denoising
filter and uses *send-on-delta* (a.k.a. deadband / level-crossing) sampling:
a message is sent only when the filtered value moves more than `deadband`
from the last transmitted value, plus a heartbeat every `heartbeat` samples
so the cloud can tell "no change" from "node offline". Alarms are always
sent immediately.

The cloud reconstructs the signal with a zero-order hold (last value
received), so we can measure the bandwidth / fidelity trade-off directly:
bytes on the wire vs RMSE of the reconstruction against ground truth.
"""
from __future__ import annotations

import json

import numpy as np

from . import dsp, sensor_model

# Size of one v1-format JSON message, measured from the actual serialiser
RAW_MSG_BYTES = len(next(sensor_model.to_json_messages(
    sensor_model.simulate(1, seed=0))))
# Compact edge message: {"id":..,"t":..,"smoke":..,"co":..}
EDGE_MSG_BYTES = len(json.dumps(
    {"id": "0-ZZZ123456780A", "t": 1793000000.0, "smoke": 123.4, "co": 1.23}))


def send_on_delta(values, deadband: float, heartbeat: int = 60):
    """Return indices of samples the node would transmit."""
    sent = [0]
    last_v, last_i = values[0], 0
    for i in range(1, len(values)):
        if abs(values[i] - last_v) >= deadband or i - last_i >= heartbeat:
            sent.append(i)
            last_v, last_i = values[i], i
    return np.array(sent)


def zoh_reconstruct(values, sent_idx, n):
    """Cloud-side zero-order-hold reconstruction from transmitted samples."""
    out = np.empty(n)
    for j, i in enumerate(sent_idx):
        end = sent_idx[j + 1] if j + 1 < len(sent_idx) else n
        out[i:end] = values[i]
    return out


def evaluate_edge(measured, truth, deadband, heartbeat=60, filt=None):
    """
    Returns dict with messages sent, bytes, reduction vs raw streaming and
    reconstruction RMSE vs ground truth. `filt=None` -> no filtering
    (send-on-delta applied to the raw signal).
    """
    n = len(measured)
    vals = dsp.apply(filt, measured) if filt is not None else np.asarray(measured, float)
    idx = send_on_delta(vals, deadband, heartbeat)
    recon = zoh_reconstruct(vals, idx, n)
    raw_bytes = n * RAW_MSG_BYTES
    edge_bytes = len(idx) * EDGE_MSG_BYTES
    return {
        "messages": int(len(idx)),
        "bytes": int(edge_bytes),
        "reduction": 1.0 - edge_bytes / raw_bytes,
        "rmse": dsp.rmse(recon, truth),
    }

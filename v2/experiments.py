"""
Monte Carlo evaluation. Run from the repo root:

    python -m v2.experiments            # full run  (~ a few minutes)
    python -m v2.experiments --quick    # smoke test (~ seconds)

Writes figures and results.md to v2/results/.

Experiments
  E1  Filter accuracy: RMSE vs ground truth across noise levels.
  E2  Detection: false alarms per hour vs mean detection delay, sweeping
      each detector's threshold (an operating-characteristic curve).
  E3  Edge data reduction: bytes sent vs reconstruction RMSE, sweeping the
      send-on-delta deadband, with and without on-device filtering.
"""
from __future__ import annotations

import argparse
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from . import detectors as D
from . import dsp, edge
from . import sensor_model as sm

OUT = os.path.join(os.path.dirname(__file__), "results")
N_SAMPLES = 1800          # 30 min at 1 Hz per trial
MAX_DELAY = 300           # a fire not detected within 5 min counts as missed


def filters(sigma=40.0):
    """sigma = sensor noise std; the Kalman filter uses it as its measurement
    variance (in practice measured once during calibration)."""
    return {
        "Moving avg (N=8)": lambda: dsp.MovingAverage(8),
        "EMA (a=0.1)": lambda: dsp.EMA(0.1),
        "Median5 + EMA": lambda: dsp.Cascade(dsp.MedianFilter(5), dsp.EMA(0.1)),
        "Kalman (gated, calibrated R)": lambda: dsp.KalmanFilter(r=sigma**2),
    }


def make_trials(n_trials, seed):
    rng = np.random.default_rng(seed)
    return [sm.simulate(N_SAMPLES, events=sm.random_scenario(rng, N_SAMPLES),
                        seed=int(rng.integers(1 << 31)))
            for _ in range(n_trials)]


# --------------------------------------------------------------------- E1
def e1_filter_rmse(n_trials, seed):
    scales = [0.5, 1.0, 1.5, 2.0, 3.0]
    res = {"Raw (no filter)": []} | {k: [] for k in filters()}
    rng = np.random.default_rng(seed)
    for sc in scales:
        acc = {k: [] for k in res}
        sigma = sc * sm.DEFAULT_SPECS["smoke"].noise_std
        for _ in range(n_trials):
            tr = sm.simulate(N_SAMPLES, events=sm.random_scenario(rng, N_SAMPLES),
                             noise_scale=sc, seed=int(rng.integers(1 << 31)))
            t, m = tr.true["smoke"], tr.measured["smoke"]
            acc["Raw (no filter)"].append(dsp.rmse(m, t))
            for name, mk in filters(sigma).items():
                acc[name].append(dsp.rmse(dsp.apply(mk(), m), t))
        for k in res:
            res[k].append(float(np.mean(acc[k])))
    sigmas = [sc * sm.DEFAULT_SPECS["smoke"].noise_std for sc in scales]

    fig, ax = plt.subplots(figsize=(6, 4))
    for k, v in res.items():
        ax.plot(sigmas, v, "o-", label=k)
    ax.set_xlabel("sensor white-noise std (ppm)")
    ax.set_ylabel("RMSE vs true smoke (ppm)")
    ax.set_title("E1  Filter accuracy vs noise level")
    ax.grid(alpha=.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "e1_filter_rmse.png"), dpi=150)
    plt.close(fig)
    return sigmas, res


# --------------------------------------------------------------------- E2
def score(alarms_per_trial, trials):
    """False alarms / hour and detection delays across trials."""
    fa, quiet_s, delays, n_fire, n_det = 0, 0.0, [], 0, 0
    for alarms, tr in zip(alarms_per_trial, trials):
        fire = next((e for e in tr.events if e.kind == "fire"), None)
        t_fire = fire.start if fire else tr.n
        fa += sum(1 for a in alarms if a < t_fire)
        quiet_s += t_fire / tr.fs
        if fire:
            n_fire += 1
            hits = [a for a in alarms if t_fire <= a <= t_fire + MAX_DELAY]
            if hits:
                n_det += 1
                delays.append((hits[0] - t_fire) / tr.fs)
    return {
        "fa_per_hr": fa / (quiet_s / 3600.0),
        "p_detect": n_det / max(n_fire, 1),
        "mean_delay": float(np.mean(delays)) if delays else float("nan"),
        "median_delay": float(np.median(delays)) if delays else float("nan"),
    }


DETECTOR_SWEEPS = {
    # name: (factory(param), param values, default param)
    "Raw threshold (v1)": (lambda p: D.RawThreshold(p),
                           [350, 400, 450, 500, 550, 600, 650], 450),
    "Kalman + threshold": (lambda p: D.FilteredThreshold(p),
                           [250, 300, 350, 400, 450, 500], 450),
    "CUSUM (smoke only)": (lambda p: D.CusumDetector(h=p),
                           [100, 200, 400, 800, 1600, 3200], 800),
    "Fused CUSUM (smoke+CO)": (lambda p: D.FusedCusum(D.CusumDetector(h=p)),
                               [50, 100, 200, 400, 800, 1600], 200),
}


def e2_detection(trials):
    curves, defaults = {}, {}
    for name, (mk, params, default) in DETECTOR_SWEEPS.items():
        pts = []
        for p in params:
            s = score([D.run(mk(p), tr.measured) for tr in trials], trials)
            s["param"] = p
            pts.append(s)
            if p == default:
                defaults[name] = s
        curves[name] = pts

    fig, ax = plt.subplots(figsize=(6.4, 4.2))
    for name, pts in curves.items():
        x = [max(p["fa_per_hr"], 0.01) for p in pts]
        y = [p["mean_delay"] for p in pts]
        ax.plot(x, y, "o-", label=name)
        d = defaults[name]
        ax.plot(max(d["fa_per_hr"], 0.01), d["mean_delay"], "k*", ms=11)
    ax.set_xscale("log")
    ax.set_xlabel("false alarms per hour (log; 0 plotted at 0.01)")
    ax.set_ylabel("mean detection delay (s)")
    ax.set_title("E2  Delay vs false-alarm trade-off  (* = default setting)")
    ax.grid(alpha=.3, which="both")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "e2_detection_tradeoff.png"), dpi=150)
    plt.close(fig)
    return curves, defaults


# --------------------------------------------------------------------- E3
def e3_edge(trials):
    deadbands = [5, 10, 20, 40, 80, 160]
    out = {"Raw + send-on-delta": [], "Kalman + send-on-delta": []}
    for db in deadbands:
        for label, mk in [("Raw + send-on-delta", lambda: None),
                          ("Kalman + send-on-delta", dsp.KalmanFilter)]:
            rs = [edge.evaluate_edge(tr.measured["smoke"], tr.true["smoke"], db,
                                     filt=mk()) for tr in trials]
            out[label].append({
                "deadband": db,
                "reduction": float(np.mean([r["reduction"] for r in rs])),
                "msgs_per_hr": float(np.mean([r["messages"] for r in rs])) * 2,
                "rmse": float(np.mean([r["rmse"] for r in rs])),
            })
    raw_rmse = float(np.mean([dsp.rmse(tr.measured["smoke"], tr.true["smoke"])
                              for tr in trials]))

    fig, ax = plt.subplots(figsize=(6, 4))
    for label, pts in out.items():
        ax.plot([100 * p["reduction"] for p in pts], [p["rmse"] for p in pts],
                "o-", label=label)
        for p in pts:
            ax.annotate(f'{p["deadband"]}', (100 * p["reduction"], p["rmse"]),
                        fontsize=7, xytext=(3, 3), textcoords="offset points")
    ax.axhline(raw_rmse, ls="--", c="gray", label="v1: stream every raw sample")
    ax.set_xlabel("bandwidth saved vs v1 raw JSON stream (%)")
    ax.set_ylabel("cloud-side RMSE vs true smoke (ppm)")
    ax.set_title("E3  Edge data reduction (labels = deadband, ppm)")
    ax.grid(alpha=.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "e3_edge_tradeoff.png"), dpi=150)
    plt.close(fig)
    return out, raw_rmse


# ------------------------------------------------------------- example
def example_trace(seed=7):
    evs = [sm.Event("nuisance", 400, 260, 20, 80), sm.Event("fire", 1100, 450, 80)]
    tr = sm.simulate(N_SAMPLES, events=evs, seed=seed)
    k = dsp.apply(dsp.KalmanFilter(), tr.measured["smoke"])
    a_raw = D.run(D.RawThreshold(450), tr.measured)
    a_fused = D.run(D.FusedCusum(D.CusumDetector(h=200)), tr.measured)

    fig, axs = plt.subplots(2, 1, figsize=(8, 5.2), sharex=True,
                            gridspec_kw={"height_ratios": [3, 1.3]})
    ax = axs[0]
    ax.plot(tr.measured["smoke"], lw=.5, c="0.6", label="measured (ADC)")
    ax.plot(tr.true["smoke"], c="k", lw=1.2, label="true signal")
    ax.plot(k, c="C0", lw=1.2, label="Kalman estimate")
    ax.axhline(450, ls=":", c="C3", lw=1)
    for a in a_raw:
        ax.axvline(a, c="C3", alpha=.35, lw=.8)
    for a in a_fused:
        ax.axvline(a, c="C2", lw=2)
    ax.axvspan(400, 400 + 80 + 60, color="C1", alpha=.08)
    ax.text(410, 900, "steam (nuisance)", fontsize=8, color="C1")
    ax.text(1110, 900, "fire", fontsize=8, color="C3")
    ax.set_ylabel("smoke (ppm)")
    ax.set_ylim(0, 1000)
    ax.legend(loc="upper left", fontsize=7, bbox_to_anchor=(0, .9))
    ax.set_title("One trace: red lines = v1 threshold alarms, green = fused CUSUM")
    axs[1].plot(tr.measured["carbon-monoxide"], lw=.5, c="0.6")
    axs[1].plot(tr.true["carbon-monoxide"], c="k", lw=1.2)
    axs[1].set_ylabel("CO (ppm)")
    axs[1].set_xlabel("time (s)")
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "example_trace.png"), dpi=150)
    plt.close(fig)
    return a_raw, a_fused


# ------------------------------------------------------------- report
def write_report(e1, e2, e3, n_trials, ex):
    sigmas, rm = e1
    curves, defaults = e2
    edge_pts, raw_rmse = e3
    L = []
    L.append(f"# v2 results\n\nMonte Carlo over **{n_trials} simulated 30-min "
             f"recordings** (1 Hz, 12-bit ADC); each has a fire with p=0.5 and a "
             f"steam/cooking nuisance event with p=0.5. Generated by "
             f"`python -m v2.experiments`.\n")
    L.append("## E1  Filter accuracy (RMSE vs ground truth, ppm)\n")
    L.append("| filter | " + " | ".join(f"σ={s:.0f}" for s in sigmas) + " |")
    L.append("|---|" + "---|" * len(sigmas))
    for k, v in rm.items():
        L.append(f"| {k} | " + " | ".join(f"{x:.1f}" for x in v) + " |")
    i1 = sigmas.index(sm.DEFAULT_SPECS["smoke"].noise_std)
    best = min((k for k in rm if k != "Raw (no filter)"), key=lambda k: rm[k][i1])
    L.append(f"\nAt the default noise (σ={sigmas[i1]:.0f} ppm) the best filter is "
             f"**{best}**: {rm['Raw (no filter)'][i1]:.1f} → {rm[best][i1]:.1f} ppm "
             f"({100 * (1 - rm[best][i1] / rm['Raw (no filter)'][i1]):.0f}% lower error).\n")
    L.append("![E1](e1_filter_rmse.png)\n")

    L.append("## E2  Detection at default settings\n")
    L.append("| detector | false alarms / hr | P(detect ≤ 5 min) | mean delay (s) | median delay (s) |")
    L.append("|---|---|---|---|---|")
    for k, d in defaults.items():
        L.append(f"| {k} | {d['fa_per_hr']:.2f} | {d['p_detect']:.2f} | "
                 f"{d['mean_delay']:.0f} | {d['median_delay']:.0f} |")
    L.append("\nFull sweep (each row = one threshold setting):\n")
    L.append("| detector | param | FA/hr | P(detect) | mean delay (s) |")
    L.append("|---|---|---|---|---|")
    for k, pts in curves.items():
        for p in pts:
            L.append(f"| {k} | {p['param']} | {p['fa_per_hr']:.2f} | "
                     f"{p['p_detect']:.2f} | {p['mean_delay']:.0f} |")
    L.append("\n![E2](e2_detection_tradeoff.png)\n")

    L.append("## E3  Edge data reduction (smoke channel)\n")
    L.append(f"v1 streams every raw sample: {edge.RAW_MSG_BYTES} B/msg, 3600 msg/hr, "
             f"cloud-side RMSE {raw_rmse:.1f} ppm. Edge messages use a compact "
             f"{edge.EDGE_MSG_BYTES} B format, which alone accounts for "
             f"{100 * (1 - edge.EDGE_MSG_BYTES / edge.RAW_MSG_BYTES):.0f}% savings; "
             f"the *messages/hr* column isolates the effect of filtering + "
             f"send-on-delta.\n")
    L.append("| pipeline | deadband (ppm) | msgs / hr | bandwidth saved | RMSE (ppm) |")
    L.append("|---|---|---|---|---|")
    for k, pts in edge_pts.items():
        for p in pts:
            L.append(f"| {k} | {p['deadband']} | {p['msgs_per_hr']:.0f} | "
                     f"{100 * p['reduction']:.1f}% | {p['rmse']:.1f} |")
    L.append("\n![E3](e3_edge_tradeoff.png)\n")
    L.append("## Example trace\n")
    L.append(f"v1 threshold alarms: {len(ex[0])} (first at t={ex[0][0] if ex[0] else '-'} s); "
             f"fused CUSUM alarms: {len(ex[1])} (first at t={ex[1][0] if ex[1] else '-'} s). "
             "Steam event at t=400 s, fire at t=1100 s.\n")
    L.append("![trace](example_trace.png)\n")
    with open(os.path.join(OUT, "results.md"), "w") as f:
        f.write("\n".join(L))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=200)
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--seed", type=int, default=2026)
    a = ap.parse_args()
    n = 10 if a.quick else a.trials
    global OUT
    if a.quick:                       # never overwrite the committed results
        OUT = os.path.join(OUT, "quick")
    os.makedirs(OUT, exist_ok=True)

    trials = make_trials(n, a.seed)
    print(f"[E1] filter RMSE ({n // 4 or 1} trials x 5 noise levels)")
    e1 = e1_filter_rmse(max(n // 4, 2), a.seed + 1)
    print(f"[E2] detection sweep ({n} trials)")
    e2 = e2_detection(trials)
    print(f"[E3] edge data reduction ({n} trials)")
    e3 = e3_edge(trials)
    ex = example_trace()
    write_report(e1, e2, e3, n, ex)
    print(f"done -> {OUT}/results.md")


if __name__ == "__main__":
    main()

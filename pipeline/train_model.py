#!/usr/bin/env python3
"""
Train & select the Aeris ICU-occupancy forecaster.

Builds a weekly panel per Bundesland from live public data (2020->today),
engineers features (features.py), then trains three model families, backtests
them on a held-out final year against the persistence baseline, and saves the
most accurate one to pipeline/model.pkl for build_data.py to use.

Run:  python3 pipeline/train_model.py
"""

import io
import json
import math
import pickle
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime

import numpy as np
import pandas as pd
from sklearn.linear_model import ElasticNetCV
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.neural_network import MLPRegressor
from sklearn.pipeline import make_pipeline
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, sys.path[0])
from features import make_features, FEATURE_COLS, STATE_IDS

RAW = "https://raw.githubusercontent.com/robert-koch-institut"
DIVI = f"{RAW}/Intensivkapazitaeten_und_COVID-19-Intensivbettenbelegung_in_Deutschland/main/Intensivregister_Bundeslaender_Kapazitaeten.csv"
ARE = f"{RAW}/ARE-Konsultationsinzidenz/main/ARE-Konsultationsinzidenz.tsv"
SARI = f"{RAW}/SARI-Hospitalisierungsinzidenz/main/SARI-Hospitalisierungsinzidenz.tsv"
HORIZONS = [1, 2, 3]
TEST_WEEKS = 52  # final year held out

STATE_NAME = {
    "01": "Schleswig-Holstein", "02": "Hamburg", "03": "Niedersachsen",
    "04": "Bremen", "05": "Nordrhein-Westfalen", "06": "Hessen",
    "07": "Rheinland-Pfalz", "08": "Baden-Württemberg", "09": "Bayern",
    "10": "Saarland", "11": "Berlin", "12": "Brandenburg",
    "13": "Mecklenburg-Vorpommern", "14": "Sachsen", "15": "Sachsen-Anhalt",
    "16": "Thüringen",
}


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "aeris-train/1.0"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return r.read().decode("utf-8", "replace")


def week_key(kw):
    """'2026-W28' -> (2026, 28) integer sortable, plus a monotonic index."""
    y, w = kw.split("-W")
    return int(y) * 100 + int(w)


# --------------------------------------------------------------------------- #
# Build the weekly panel
# --------------------------------------------------------------------------- #
def build_panel():
    print("· loading DIVI / ARE / SARI …", file=sys.stderr)
    # --- DIVI daily -> weekly occupancy per state ---
    lines = fetch(DIVI).splitlines()
    hdr = {c: i for i, c in enumerate(lines[0].split(","))}
    recs = []
    for ln in lines[1:]:
        f = ln.split(",")
        sid = f[hdr["bundesland_id"]]
        if sid not in STATE_NAME:
            continue
        try:
            d = datetime.strptime(f[hdr["datum"]], "%Y-%m-%d").date()
            bel = float(f[hdr["intensivbetten_belegt"]])
            fre = float(f[hdr["intensivbetten_frei"]])
        except (ValueError, KeyError):
            continue
        if bel + fre <= 0:
            continue
        iso = d.isocalendar()
        recs.append((sid, f"{iso[0]}-W{iso[1]:02d}", 100 * bel / (bel + fre)))
    divi = pd.DataFrame(recs, columns=["state", "week", "occ"])
    divi = divi.groupby(["state", "week"], as_index=False)["occ"].mean()

    # --- ARE per state weekly (00+) ---
    lines = fetch(ARE).splitlines()
    hdr = {c: i for i, c in enumerate(lines[0].split("\t"))}
    name2id = {v: k for k, v in STATE_NAME.items()}
    arecs = []
    for ln in lines[1:]:
        f = ln.split("\t")
        if f[hdr["Altersgruppe"]] != "00+":
            continue
        sid = name2id.get(f[hdr["Bundesland"]])
        if not sid:
            continue
        try:
            arecs.append((sid, f[hdr["Kalenderwoche"]], float(f[hdr["ARE_Konsultationsinzidenz"]])))
        except ValueError:
            continue
    are = pd.DataFrame(arecs, columns=["state", "week", "are"])

    # --- SARI national weekly (00+, Gesamt) ---
    lines = fetch(SARI).splitlines()
    hdr = {c: i for i, c in enumerate(lines[0].split("\t"))}
    srecs = []
    for ln in lines[1:]:
        f = ln.split("\t")
        if f[hdr["Altersgruppe"]] != "00+" or f[hdr["SARI"]] != "Gesamt":
            continue
        try:
            srecs.append((f[hdr["Kalenderwoche"]], float(f[hdr["Hospitalisierungsinzidenz"]])))
        except ValueError:
            continue
    sari = pd.DataFrame(srecs, columns=["week", "sari"])

    # --- merge, sort, forward-fill exogenous within state ---
    df = divi.merge(are, on=["state", "week"], how="left").merge(sari, on="week", how="left")
    df["wk"] = df["week"].map(week_key)
    df = df.sort_values(["state", "wk"]).reset_index(drop=True)
    df["are"] = df.groupby("state")["are"].ffill()
    df["sari"] = df["sari"].ffill()
    return df


def assemble_examples(df):
    """Turn the panel into (X, y, wk, state) rows across all horizons."""
    rows, ys, wks, sts = [], [], [], []
    for sid, g in df.groupby("state"):
        g = g.sort_values("wk").reset_index(drop=True)
        occ = g["occ"].tolist()
        are = g["are"].tolist()
        sari = g["sari"].tolist()
        wk = g["wk"].tolist()
        for t in range(4, len(g)):
            for h in HORIZONS:
                if t + h >= len(g):
                    continue
                tgt_woy = wk[t + h] % 100
                feat = make_features(occ[: t + 1], are[: t + 1], sari[: t + 1], tgt_woy, h, sid)
                if feat is None or occ[t + h] != occ[t + h]:
                    continue
                rows.append(feat)
                ys.append(occ[t + h])
                wks.append(wk[t + h])       # target week (for the time split)
                sts.append(sid)
    X = pd.DataFrame(rows)[FEATURE_COLS]
    return X, np.array(ys), np.array(wks), np.array(sts)


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
def models():
    return {
        "ElasticNet": make_pipeline(
            SimpleImputer(strategy="median"), StandardScaler(),
            ElasticNetCV(l1_ratio=[.2, .5, .8], alphas=np.logspace(-3, 1, 20),
                         cv=4, max_iter=8000, random_state=0)),
        "HistGradientBoosting": HistGradientBoostingRegressor(
            max_depth=4, learning_rate=0.06, max_iter=500,
            l2_regularization=1.0, random_state=0),
        "MLP": make_pipeline(
            SimpleImputer(strategy="median"), StandardScaler(),
            MLPRegressor(hidden_layer_sizes=(64, 32), alpha=1e-3,
                         learning_rate_init=3e-3, max_iter=1200,
                         early_stopping=True, random_state=0)),
    }


def evaluate():
    df = build_panel()
    X, y, wks, sts = assemble_examples(df)
    cutoff = np.sort(np.unique(wks))[-TEST_WEEKS]
    tr, te = wks < cutoff, wks >= cutoff
    print(f"· {len(y)} examples · train {tr.sum()} / test {te.sum()} "
          f"(last {TEST_WEEKS} wks, cutoff {cutoff}) · {X.shape[1]} features",
          file=sys.stderr)

    # persistence baseline: predict occ_t for every horizon
    persist = X["occ_t"].values
    base_mae = np.abs(persist[te] - y[te]).mean()

    results, fitted = {}, {}
    for name, mdl in models().items():
        mdl.fit(X[tr], y[tr])
        pred = mdl.predict(X[te])
        mae = np.abs(pred - y[te]).mean()
        rmse = math.sqrt(((pred - y[te]) ** 2).mean())
        skill = 1 - mae / base_mae
        # per-horizon MAE
        per_h = {int(h): round(float(np.abs(mdl.predict(X[te & (X["h"] == h)])
                  - y[te & (X["h"] == h)]).mean()), 2) for h in HORIZONS}
        results[name] = {"mae": round(mae, 2), "rmse": round(rmse, 2),
                         "skill_vs_persistence": round(skill, 3), "mae_by_h": per_h}
        fitted[name] = mdl
        print(f"  {name:22} MAE {mae:5.2f}  RMSE {rmse:5.2f}  "
              f"skill {skill:+.1%}  by-h {per_h}", file=sys.stderr)

    print(f"  {'Persistence (baseline)':22} MAE {base_mae:5.2f}", file=sys.stderr)

    best = min(results, key=lambda k: results[k]["mae"])
    print(f"\n✓ winner: {best} (MAE {results[best]['mae']}, "
          f"skill {results[best]['skill_vs_persistence']:+.1%})", file=sys.stderr)

    # residual quantiles per horizon (empirical prediction intervals),
    # computed on the TEST predictions of the winner
    best_mdl = fitted[best]
    resid_q = {}
    for h in HORIZONS:
        mask = te & (X["h"] == h)
        r = best_mdl.predict(X[mask]) - y[mask]
        resid_q[int(h)] = {
            "p05": float(np.percentile(r, 5)), "p50": float(np.percentile(r, 50)),
            "p95": float(np.percentile(r, 95)), "sd": float(np.std(r)),
        }

    # retrain winner on ALL data for live use
    final = models()[best]
    final.fit(X, y)

    with open(f"{sys.path[0]}/model.pkl", "wb") as fh:
        pickle.dump({"model": final, "name": best, "resid_q": resid_q,
                     "feature_cols": FEATURE_COLS}, fh)
    with open(f"{sys.path[0]}/model_metrics.json", "w") as fh:
        json.dump({"winner": best, "baseline_mae": round(base_mae, 2),
                   "results": results, "test_weeks": TEST_WEEKS,
                   "n_examples": int(len(y))}, fh, indent=1)
    print(f"✓ saved model.pkl + model_metrics.json", file=sys.stderr)
    return results, best


if __name__ == "__main__":
    evaluate()

"""
Shared feature engineering for the Aeris ICU-occupancy forecaster.

Both training (train_model.py) and live inference (build_data.py) build feature
rows through make_features(), so the model always sees identical inputs. One
model handles all horizons — the horizon h is itself a feature.
"""

import math

# fixed Bundesland order for the state one-hot
STATE_IDS = ["01", "02", "03", "04", "05", "06", "07", "08",
             "09", "10", "11", "12", "13", "14", "15", "16"]

# canonical feature column order (must stay stable across train + inference)
FEATURE_COLS = (
    ["h", "occ_t", "occ_lag1", "occ_lag2", "occ_lag3",
     "occ_roll4_mean", "occ_slope4", "occ_chg1",
     "are_t", "are_chg", "sari_t", "sari_chg",
     "woy_sin", "woy_cos", "woy_sin2", "woy_cos2"]
    + [f"st_{s}" for s in STATE_IDS]
)


def _safe(seq, i, default=float("nan")):
    """seq[-i] with graceful fallback for short histories."""
    return seq[-i] if len(seq) >= i else default


def make_features(occ, are, sari, target_woy, h, state_id):
    """Build one feature dict.

    occ / are / sari : chronological weekly lists ending at the ORIGIN week t.
                       occ must have >= 4 usable points.
    target_woy       : ISO week-of-year of the target week (t + h).
    h                : horizon in weeks (1..3).
    state_id         : two-digit Bundesland id.
    Returns None if history is too short.
    """
    if len([x for x in occ[-4:] if x == x]) < 4:  # need 4 real lags
        return None

    occ_t = occ[-1]
    lag1, lag2, lag3 = _safe(occ, 2), _safe(occ, 3), _safe(occ, 4)
    roll4 = sum(occ[-4:]) / 4.0
    slope4 = (occ[-1] - occ[-4]) / 3.0
    chg1 = occ_t - lag1

    are_t = _safe(are, 1)
    are_chg = are_t - _safe(are, 2) if are_t == are_t else float("nan")
    sari_t = _safe(sari, 1)
    sari_chg = sari_t - _safe(sari, 2) if sari_t == sari_t else float("nan")

    ang = 2 * math.pi * (target_woy / 52.0)
    row = {
        "h": h, "occ_t": occ_t, "occ_lag1": lag1, "occ_lag2": lag2, "occ_lag3": lag3,
        "occ_roll4_mean": roll4, "occ_slope4": slope4, "occ_chg1": chg1,
        "are_t": are_t, "are_chg": are_chg, "sari_t": sari_t, "sari_chg": sari_chg,
        "woy_sin": math.sin(ang), "woy_cos": math.cos(ang),
        "woy_sin2": math.sin(2 * ang), "woy_cos2": math.cos(2 * ang),
    }
    for s in STATE_IDS:
        row[f"st_{s}"] = 1.0 if s == state_id else 0.0
    return row

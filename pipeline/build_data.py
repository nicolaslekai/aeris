#!/usr/bin/env python3
"""
Aeris ingestion pipeline — state-level (Bundesland) MVP.

Pulls live public data, aligns it per Bundesland, computes a forecast of ICU
occupancy 1-3 weeks ahead with an uncertainty band, and writes
``website/data.live.json`` for the frontend.

The forecast is a *persistence baseline plus a leading-signal tilt* — exactly the
MVP deliverable Nir's brief describes ("simple evaluation against a naive baseline
(persistence forecast)"). It is deliberately simple and transparent, not a trained
model. Every number in the output is derived from a real source fetched at build
time; nothing is hardcoded except the fixed signal-weight priors.

Sources (all public, verified 2026-07):
  - DIVI-Intensivregister (RKI GitHub)      -> ICU occupancy, ground truth, daily
  - ARE-Konsultationsinzidenz (RKI GitHub)  -> GP respiratory consults, per state, weekly
  - SARI-Hospitalisierung (RKI GitHub)      -> severe resp. hospitalisations, national, weekly
  - DWD Open Data (opendata.dwd.de)         -> daily mean temperature, per station -> state
  - openholidaysapi.org                     -> public + school holidays, per state

Run:  python3 pipeline/build_data.py
"""

import io
import json
import math
import statistics
import sys
import urllib.request
import zipfile
from collections import defaultdict
from datetime import date, datetime, timedelta

RAW = "https://raw.githubusercontent.com/robert-koch-institut"
DIVI = f"{RAW}/Intensivkapazitaeten_und_COVID-19-Intensivbettenbelegung_in_Deutschland/main/Intensivregister_Bundeslaender_Kapazitaeten.csv"
ARE = f"{RAW}/ARE-Konsultationsinzidenz/main/ARE-Konsultationsinzidenz.tsv"
SARI = f"{RAW}/SARI-Hospitalisierungsinzidenz/main/SARI-Hospitalisierungsinzidenz.tsv"
DWD_STATIONS = "https://opendata.dwd.de/climate_environment/CDC/observations_germany/climate/daily/kl/recent/KL_Tageswerte_Beschreibung_Stationen.txt"
DWD_DATA = "https://opendata.dwd.de/climate_environment/CDC/observations_germany/climate/daily/kl/recent/tageswerte_KL_{sid}_akt.zip"
HOLIDAYS = "https://openholidaysapi.org/{kind}?countryIsoCode=DE&subdivisionCode=DE-{code}&validFrom={f}&validTo={t}&languageIsoCode=DE"

# 16 Bundesländer: id (DIVI), name, ISO subdivision suffix for holidays.
STATES = [
    ("01", "Schleswig-Holstein", "SH"), ("02", "Hamburg", "HH"),
    ("03", "Niedersachsen", "NI"), ("04", "Bremen", "HB"),
    ("05", "Nordrhein-Westfalen", "NW"), ("06", "Hessen", "HE"),
    ("07", "Rheinland-Pfalz", "RP"), ("08", "Baden-Württemberg", "BW"),
    ("09", "Bayern", "BY"), ("10", "Saarland", "SL"),
    ("11", "Berlin", "BE"), ("12", "Brandenburg", "BB"),
    ("13", "Mecklenburg-Vorpommern", "MV"), ("14", "Sachsen", "SN"),
    ("15", "Sachsen-Anhalt", "ST"), ("16", "Thüringen", "TH"),
]
STATE_NAME = {sid: name for sid, name, _ in STATES}
DWD_NAME = {  # DWD spells these slightly differently (latin-1)
    "01": "Schleswig-Holstein", "02": "Hamburg", "03": "Niedersachsen",
    "04": "Bremen", "05": "Nordrhein-Westfalen", "06": "Hessen",
    "07": "Rheinland-Pfalz", "08": "Baden-Württemberg", "09": "Bayern",
    "10": "Saarland", "11": "Berlin", "12": "Brandenburg",
    "13": "Mecklenburg-Vorpommern", "14": "Sachsen", "15": "Sachsen-Anhalt",
    "16": "Thüringen",
}

# Fixed signal-weight priors (not learned — configured, documented in the brief).
WEIGHTS = {"divi": 0.42, "sari": 0.24, "are": 0.18, "dwd": 0.11, "cal": 0.05}

TODAY = date(2026, 7, 18)  # pinned build date for reproducibility


def fetch(url, timeout=60, binary=False):
    req = urllib.request.Request(url, headers={"User-Agent": "aeris-pipeline/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
    return data if binary else data.decode("utf-8", "replace")


def iso_week(d):
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


# --------------------------------------------------------------------------- #
# 1. DIVI — ICU occupancy per state (ground truth)
# --------------------------------------------------------------------------- #
def load_divi():
    print("· DIVI (ICU occupancy, per state) …", file=sys.stderr)
    text = fetch(DIVI)
    lines = text.splitlines()
    hdr = lines[0].split(",")
    ix = {c: i for i, c in enumerate(hdr)}
    # daily occupancy per state: {sid: [(date, occ_pct, belegt, frei, pers), ...]}
    daily = defaultdict(list)
    for ln in lines[1:]:
        f = ln.split(",")
        sid = f[ix["bundesland_id"]]
        if sid not in STATE_NAME:
            continue
        try:
            d = datetime.strptime(f[ix["datum"]], "%Y-%m-%d").date()
            belegt = float(f[ix["intensivbetten_belegt"]])
            frei = float(f[ix["intensivbetten_frei"]])
        except (ValueError, KeyError):
            continue
        total = belegt + frei
        if total <= 0:
            continue
        pers = f[ix["einschraenkung_personal"]]
        pers = int(pers) if pers.isdigit() else 0
        daily[sid].append((d, 100.0 * belegt / total, int(belegt), int(frei), pers))
    for sid in daily:
        daily[sid].sort()
    return daily


def weekly_occ(series, n_weeks=14):
    """Collapse a daily (date, pct, ...) series into recent weekly-mean occupancy."""
    buckets = defaultdict(list)
    beds = defaultdict(list)
    for d, pct, belegt, frei, pers in series:
        buckets[iso_week(d)].append(pct)
        beds[iso_week(d)].append(belegt + frei)
    weeks = sorted(buckets)[-n_weeks:]
    means = [(w, round(statistics.mean(buckets[w]), 1)) for w in weeks]
    latest_beds = int(round(statistics.mean(beds[weeks[-1]]))) if weeks else 0
    return means, latest_beds


# --------------------------------------------------------------------------- #
# 2/3. ARE (per state) & SARI (national) — weekly leading signals
# --------------------------------------------------------------------------- #
def load_are():
    print("· ARE (GP respiratory consults, per state) …", file=sys.stderr)
    text = fetch(ARE)
    lines = text.splitlines()
    hdr = lines[0].split("\t")
    ix = {c: i for i, c in enumerate(hdr)}
    per = defaultdict(dict)  # {state_name: {week: value}}
    for ln in lines[1:]:
        f = ln.split("\t")
        if f[ix["Altersgruppe"]] != "00+":
            continue
        try:
            per[f[ix["Bundesland"]]][f[ix["Kalenderwoche"]]] = float(f[ix["ARE_Konsultationsinzidenz"]])
        except (ValueError, KeyError):
            continue
    return per  # keyed by DIVI/ARE German state spelling


def load_sari():
    print("· SARI (severe resp. hospitalisations, national) …", file=sys.stderr)
    text = fetch(SARI)
    lines = text.splitlines()
    hdr = lines[0].split("\t")
    ix = {c: i for i, c in enumerate(hdr)}
    series = {}
    for ln in lines[1:]:
        f = ln.split("\t")
        # all ages, all pathogens combined
        if f[ix["Altersgruppe"]] != "00+" or f[ix["SARI"]] != "Gesamt":
            continue
        try:
            series[f[ix["Kalenderwoche"]]] = float(f[ix["Hospitalisierungsinzidenz"]])
        except (ValueError, KeyError):
            continue
    return series


def recent_series(week_map, n=8):
    weeks = sorted(week_map)[-n:]
    return [round(week_map[w], 1) for w in weeks]


def wow_delta(vals):
    if len(vals) < 2 or vals[-2] == 0:
        return 0.0
    return round(100.0 * (vals[-1] - vals[-2]) / vals[-2], 1)


# --------------------------------------------------------------------------- #
# 4. DWD — recent daily mean temperature, one active station per state
# --------------------------------------------------------------------------- #
def load_dwd():
    print("· DWD (daily temperature, per state) …", file=sys.stderr)
    try:
        raw = fetch(DWD_STATIONS, binary=True).decode("latin-1")
    except Exception as e:  # noqa: BLE001
        print(f"  ! station catalog unavailable ({e}); skipping temperature", file=sys.stderr)
        return {}
    # fixed-width; Bundesland is the field before the trailing 'Frei'/'Abgabe'.
    by_state = {}  # dwd_state_name -> (station_id, bis_datum)
    for ln in raw.splitlines()[2:]:
        if len(ln) < 100:
            continue
        sid = ln[:5].strip()
        try:
            bis = int(ln[15:23])
        except ValueError:
            continue
        # split the tail; Bundesland is the second-to-last non-'Frei' token
        tail = ln[60:].split()
        # find Bundesland by matching a known DWD name inside the tail string
        state = None
        joined = " ".join(tail)
        for nm in set(DWD_NAME.values()):
            if nm in joined:
                state = nm
                break
        if state is None or bis < 20260601:  # active stations only
            continue
        # keep the most recently reporting station per state
        if state not in by_state or bis > by_state[state][1]:
            by_state[state] = (sid, bis)

    temps = {}  # dwd_state_name -> {"spark": [...], "mean14": x, "delta": x}
    for state, (sid, _) in by_state.items():
        try:
            blob = fetch(DWD_DATA.format(sid=sid), binary=True, timeout=40)
            zf = zipfile.ZipFile(io.BytesIO(blob))
            name = next(n for n in zf.namelist() if n.startswith("produkt_klima_tag"))
            rows = zf.read(name).decode("latin-1").splitlines()
            h = [c.strip() for c in rows[0].split(";")]
            tmk_i, date_i = h.index("TMK"), h.index("MESS_DATUM")
            pairs = []
            for r in rows[1:]:
                c = [x.strip() for x in r.split(";")]
                try:
                    tmk = float(c[tmk_i])
                    if tmk <= -900:
                        continue
                    pairs.append((c[date_i], tmk))
                except (ValueError, IndexError):
                    continue
            pairs.sort()
            last = [t for _, t in pairs[-21:]]
            if len(last) < 7:
                continue
            spark = [round(t, 1) for t in last[-14:]]
            mean14 = round(statistics.mean(last[-14:]), 1)
            prev = statistics.mean(last[-21:-14]) if len(last) >= 21 else mean14
            temps[state] = {"spark": spark, "mean14": mean14,
                            "delta": round(mean14 - prev, 1)}
        except Exception as e:  # noqa: BLE001
            print(f"  ! station {sid} ({state}) failed: {e}", file=sys.stderr)
            continue
    print(f"  got temperature for {len(temps)}/16 states", file=sys.stderr)
    return temps


# --------------------------------------------------------------------------- #
# 5. Holidays + school holidays per state (next 21 days)
# --------------------------------------------------------------------------- #
def load_holidays():
    print("· Holidays + school terms (per state) …", file=sys.stderr)
    f = TODAY.isoformat()
    t = (TODAY + timedelta(days=28)).isoformat()
    out = {}
    for sid, _, code in STATES:
        info = {"next": None, "school_now": False}
        for kind in ("PublicHolidays", "SchoolHolidays"):
            try:
                data = json.loads(fetch(HOLIDAYS.format(kind=kind, code=code, f=f, t=t), timeout=25))
            except Exception:  # noqa: BLE001
                continue
            for ev in data:
                sd = datetime.strptime(ev["startDate"], "%Y-%m-%d").date()
                ed = datetime.strptime(ev["endDate"], "%Y-%m-%d").date()
                nm = ev["name"][0]["text"] if ev.get("name") else kind
                if kind == "SchoolHolidays" and sd <= TODAY <= ed:
                    info["school_now"] = True
                if sd >= TODAY and (info["next"] is None or sd < info["next"][0]):
                    info["next"] = (sd, nm)
        if info["next"]:
            info["next"] = {"date": info["next"][0].isoformat(), "name": info["next"][1],
                            "in_days": (info["next"][0] - TODAY).days}
        out[sid] = info
    return out


# --------------------------------------------------------------------------- #
# Forecast — persistence baseline + leading-signal tilt
# --------------------------------------------------------------------------- #
def normal_p_above(x, mu, sigma):
    if sigma <= 0:
        return 1.0 if mu > x else 0.0
    z = (x - mu) / sigma
    return round(1 - 0.5 * (1 + math.erf(z / math.sqrt(2))), 2)


def forecast_state(occ_weeks, are_vals, sari_vals, temp, holiday):
    """occ_weeks: [(week, pct)] recent weekly occupancy. Returns forecast dict."""
    ys = [p for _, p in occ_weeks]
    current = ys[-1]

    # autoregressive trend: slope over the last 4 weekly means (%/week)
    tail = ys[-4:]
    slope = (tail[-1] - tail[0]) / (len(tail) - 1) if len(tail) > 1 else 0.0
    slope = max(-6.0, min(6.0, slope))

    # leading-signal tilt (each term small, capped) — respiratory pathway
    are_mom = wow_delta(are_vals) / 100.0        # GP consults momentum
    sari_mom = wow_delta(sari_vals) / 100.0      # hospitalisation momentum
    temp_term = 0.0
    if temp:  # falling temperature in-season nudges risk up
        temp_term = -0.06 * temp["delta"]
    tilt = 6.0 * (0.5 * are_mom + 0.5 * sari_mom) + temp_term
    tilt = max(-4.0, min(4.0, tilt))

    weekly_delta = 0.65 * slope + tilt

    # volatility of recent weekly changes -> band scaling
    diffs = [ys[i] - ys[i - 1] for i in range(1, len(ys))][-8:]
    vol = statistics.pstdev(diffs) if len(diffs) > 1 else 3.0

    horizon, pcrit = [], []
    damp = [1.0, 1.8, 2.4]  # decreasing marginal drift
    base_band = [4.0, 7.0, 10.0]
    labels = []
    for k in range(3):
        wk_start = _week_range(k + 1)
        pct = round(_clamp(current + weekly_delta * damp[k]), 0)
        hw = round(base_band[k] * (1 + vol / 6.0), 0)
        lo, hi = _clamp(pct - hw), _clamp(pct + hw)
        sigma = max(1.0, (hi - lo) / (2 * 1.645))
        conf = "Hoch" if hw <= 6 else ("Mittel" if hw <= 10 else "Niedrig")
        horizon.append({"week": k + 1, "range": wk_start, "pct": int(pct),
                        "lo": int(lo), "hi": int(hi), "confidence": conf})
        pcrit.append(normal_p_above(85, pct, sigma))
        labels.append(wk_start)

    baseline = [int(round(current))] * 3  # naive persistence baseline
    return {"current": round(current, 1), "horizon": horizon,
            "pCritical": pcrit, "baseline": baseline}


def _clamp(v, lo=15.0, hi=100.0):
    return max(lo, min(hi, v))


def _week_range(k):
    start = TODAY + timedelta(days=(7 * (k - 1)) + (7 - TODAY.weekday()))
    end = start + timedelta(days=6)
    m = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"]
    if start.month == end.month:
        return f"{start.day}.–{end.day}. {m[end.month - 1]}"
    return f"{start.day}. {m[start.month - 1]}–{end.day}. {m[end.month - 1]}"


# --------------------------------------------------------------------------- #
# Assemble
# --------------------------------------------------------------------------- #
def build():
    divi = load_divi()
    are = load_are()
    sari = load_sari()
    dwd = load_dwd()
    hol = load_holidays()

    sari_recent = recent_series(sari)
    sari_delta = wow_delta(sari_recent)

    states_out = []
    for sid, name, code in STATES:
        if sid not in divi or not divi[sid]:
            continue
        occ_weeks, beds = weekly_occ(divi[sid])
        observed = _observed_points(occ_weeks)

        are_vals = recent_series(are.get(name, {}))
        temp = dwd.get(DWD_NAME[sid])
        holiday = hol.get(sid, {})

        fc = forecast_state(occ_weeks, are_vals, sari_recent, temp, holiday)

        signals = _signals(occ_weeks, are_vals, are.get(name, {}), sari_recent,
                           sari_delta, temp, holiday)

        states_out.append({
            "id": sid, "name": name, "beds": beds,
            "current": int(round(fc["current"])),
            "observed": observed,
            "horizon": fc["horizon"], "pCritical": fc["pCritical"],
            "baseline": fc["baseline"],
            "signals": signals,
        })

    # national strip for the overview
    strip = [{"name": s["name"], "pct": s["horizon"][0]["pct"]} for s in states_out]

    payload = {
        "generatedAt": TODAY.isoformat(),
        "asOfLabel": TODAY.strftime("%d. %B %Y").replace("July", "Juli"),
        "level": "Bundesland",
        "states": states_out,
        "strip": strip,
        "sources": _source_meta(dwd),
    }
    return payload


def _observed_points(occ_weeks):
    pts = occ_weeks[-5:]
    labels = ["vor 4 Wo.", "vor 3 Wo.", "vor 2 Wo.", "vor 1 Wo.", "Jetzt"]
    labels = labels[-len(pts):]
    return [{"label": labels[i], "pct": p} for i, (_, p) in enumerate(pts)]


def _signals(occ_weeks, are_vals, are_map, sari_recent, sari_delta, temp, holiday):
    occ_spark = [p for _, p in occ_weeks[-8:]]
    sig = [
        {"key": "divi", "name": "DIVI-Intensivregister", "role": "Grundwahrheit · autoregressiv",
         "detail": "Tägliche ITS-Belegung je Bundesland", "lead": "0 Tage",
         "weight": WEIGHTS["divi"], "spark": occ_spark,
         "delta": round(occ_spark[-1] - occ_spark[-2], 1) if len(occ_spark) > 1 else 0.0,
         "unit": "%pt"},
        {"key": "sari", "name": "SARI-Hospitalisierung", "role": "Direktes Vorlaufsignal",
         "detail": "Schwere Atemwegsinfekte (RKI ICOSARI, national)", "lead": "3–7 Tage",
         "weight": WEIGHTS["sari"], "spark": sari_recent, "delta": sari_delta, "unit": "%"},
        {"key": "are", "name": "ARE-Konsultationen", "role": "Wellenform, früh",
         "detail": "Hausarzt-Konsultationen (RKI AGI-Sentinel)", "lead": "1–2 Wochen",
         "weight": WEIGHTS["are"], "spark": are_vals or [0],
         "delta": wow_delta(are_vals) if are_vals else 0.0, "unit": "%"},
    ]
    if temp:
        sig.append({"key": "dwd", "name": "Temperatur (DWD)", "role": "Umweltfaktor",
                    "detail": "Tagesmittel, Referenzstation je Land", "lead": "1–2 Wochen",
                    "weight": WEIGHTS["dwd"], "spark": temp["spark"],
                    "delta": temp["delta"], "unit": "°C", "invert": True})
    nxt = holiday.get("next")
    cal_detail = "Feiertage & Schulferien je Bundesland"
    if nxt:
        cal_detail = f"Nächste: {nxt['name']} in {nxt['in_days']} Tagen"
    sig.append({"key": "cal", "name": "Kalender", "role": "Feiertage & Schulferien",
                "detail": cal_detail, "lead": "im Voraus", "weight": WEIGHTS["cal"],
                "spark": [1 if holiday.get("school_now") else 0] * 6, "delta": 0.0,
                "unit": "", "flat": True})
    return sig


def _source_meta(dwd):
    return [
        {"name": "DIVI-Intensivregister", "note": "ITS-Belegung, Bundesland, täglich", "live": True},
        {"name": "ARE-Konsultationsinzidenz", "note": "RKI AGI, Bundesland, wöchentlich", "live": True},
        {"name": "SARI-Hospitalisierung", "note": "RKI ICOSARI, national, wöchentlich", "live": True},
        {"name": "DWD Open Data", "note": f"Tagestemperatur, {len(dwd)}/16 Länder", "live": bool(dwd)},
        {"name": "openholidaysapi.org", "note": "Feiertage & Schulferien je Land", "live": True},
    ]


if __name__ == "__main__":
    payload = build()
    out = f"{sys.path[0]}/../data.live.json"
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    n = len(payload["states"])
    print(f"\n✓ wrote {out}  ({n} states, as of {payload['generatedAt']})", file=sys.stderr)
    for s in payload["states"][:3]:
        h = s["horizon"]
        print(f"  {s['name']:22} now {s['current']:>3}%  -> "
              f"{h[0]['pct']}/{h[1]['pct']}/{h[2]['pct']}%  "
              f"pCrit {s['pCritical']}", file=sys.stderr)

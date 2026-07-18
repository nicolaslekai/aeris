# Aeris — Health System Foresight

Frontend prototype for a **regional ICU (Intensiv) occupancy forecast** for the
German health system. Forecasts bed occupancy **1–3 weeks ahead** per *Landkreis*
(county) from publicly available signals, so hospitals and health authorities can
plan staffing and elective surgeries ahead of a capacity crunch instead of
reacting to it.

> **Status: static prototype.** The forecast values in [`data.js`](data.js) are
> illustrative (a Munich worked example). There is **no live data fetch, model, or
> pipeline yet** — this is the design/UX front end. The data model does, however,
> mirror the real DIVI-Intensivregister CSV schema, so wiring it to live data is
> the next step.

## Run it

Charts are hand-built SVG rendered by `app.js`, so it needs to be served over HTTP
(opening `index.html` from `file://` works in a normal browser too):

```bash
python3 -m http.server 8777
# then open http://localhost:8777
```

## Structure

| File | Purpose |
|---|---|
| `index.html` | Page markup — hero, live dashboard, signals, methodology, data sources |
| `styles.css` | Light theme. One petrol brand accent; traffic-light colors reserved for risk status |
| `app.js`     | Renders the trend chart, gauge cards, and signal sparklines (vanilla JS, no deps) |
| `data.js`    | Sample forecast payload; shape mirrors the real DIVI Landkreise CSV |

## Data sources (verified public, target for live integration)

| Signal | Source | Cadence |
|---|---|---|
| ICU occupancy (ground truth) | [DIVI-Intensivregister](https://github.com/robert-koch-institut/Intensivkapazitaeten_und_COVID-19-Intensivbettenbelegung_in_Deutschland) — `Intensivregister_Landkreise_Kapazitaeten.csv` | Daily, per Landkreis |
| SARI hospitalizations | [RKI SARI-Hospitalisierungsinzidenz](https://github.com/robert-koch-institut/SARI-Hospitalisierungsinzidenz) (ICOSARI) | Weekly |
| ARE / flu GP consultations | [RKI ARE-Konsultationsinzidenz](https://github.com/robert-koch-institut/ARE-Konsultationsinzidenz) | Weekly |
| Self-reported symptoms | [RKI GrippeWeb](https://github.com/robert-koch-institut/GrippeWeb_Daten_des_Wochenberichts) | Weekly (v1.1) |
| Weather (temperature) | [DWD Open Data / CDC](https://opendata.dwd.de/climate_environment/CDC/observations_germany/climate/) | Daily, per station |
| Wastewater viral load | Umweltbundesamt AMELAG | Deferred to v1.1 |

## Roadmap

- [ ] Fetch real current DIVI occupancy on load
- [ ] Ingestion pipeline aligning all sources to Landkreis level
- [ ] Forecast model (ICU utilization 1–2 weeks ahead) with uncertainty vs. a persistence baseline
- [ ] Region map / multi-region switching driven by real data

---

Illustrative forecast values. All referenced data sources are publicly available.

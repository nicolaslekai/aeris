/* ------------------------------------------------------------------
   Aeris — sample forecast payload
   Shape mirrors the real ground-truth source (DIVI-Intensivregister,
   "Intensivregister_Landkreise_Kapazitaeten.csv"):
     datum, landkreis_id, landkreis_name, intensivbetten_frei,
     intensivbetten_belegt, ...
   plus the model's forward forecast with uncertainty bands.
   Numbers here are illustrative but anchored to real Munich capacity
   (≈430 operable ICU beds in SK München / 09162).
------------------------------------------------------------------ */

const REGIONS = [
  { id: "09162", name: "München (Stadtkreis)", state: "Bayern", beds: 430 },
  { id: "11000", name: "Berlin", state: "Berlin", beds: 1180 },
  { id: "02000", name: "Hamburg", state: "Hamburg", beds: 720 },
  { id: "05315", name: "Köln (Stadtkreis)", state: "Nordrhein-Westfalen", beds: 610 },
  { id: "06412", name: "Frankfurt am Main", state: "Hessen", beds: 540 },
];

/* Thresholds (occupancy %) shared by every visual. */
const BANDS = {
  normal:   { max: 70, label: "Normal",   color: "var(--normal)" },
  elevated: { max: 85, label: "Erhöht",   color: "var(--elevated)" },
  critical: { max: 100, label: "Kritisch", color: "var(--critical)" },
};

function statusFor(pct) {
  if (pct < BANDS.normal.max) return "normal";      // < 70
  if (pct <= BANDS.elevated.max) return "elevated";  // 70–85 inclusive
  return "critical";                                 // > 85
}

/* The München payload from the mock: 4 weeks observed -> 3 weeks forecast. */
const FORECAST = {
  region: "09162",
  asOf: "2026-03-21",
  current: 72,
  // Observed history (weekly mean occupancy %)
  observed: [
    { label: "vor 4 Wo.", pct: 62 },
    { label: "vor 3 Wo.", pct: 65 },
    { label: "vor 2 Wo.", pct: 68 },
    { label: "vor 1 Wo.", pct: 70 },
    { label: "Jetzt",     pct: 72 },
  ],
  // Model output, 1–3 weeks ahead
  horizon: [
    { week: 1, range: "24.–30. Mär", pct: 78, lo: 73, hi: 83, confidence: "Hoch" },
    { week: 2, range: "31. Mär–6. Apr", pct: 85, lo: 76, hi: 93, confidence: "Mittel" },
    { week: 3, range: "7.–13. Apr", pct: 88, lo: 74, hi: 97, confidence: "Niedrig" },
  ],
  // Probability of breaching critical (85%) capacity per horizon
  pCritical: [0.06, 0.51, 0.66],
};

/* Leading indicators currently feeding the model for this region.
   delta = week-over-week change; trend drives the sparkline direction. */
const SIGNALS = [
  {
    key: "divi",
    name: "DIVI-Intensivregister",
    role: "Grundwahrheit · autoregressiv",
    detail: "Tägliche ITS-Belegung je Landkreis",
    lead: "0 Tage",
    weight: 0.42,
    spark: [61, 62, 63, 65, 66, 68, 70, 72],
    delta: +2.0,
  },
  {
    key: "sari",
    name: "SARI-Hospitalisierung",
    role: "Direktes Vorlaufsignal",
    detail: "Schwere Atemwegsinfekte, Sentinelkliniken (RKI ICOSARI)",
    lead: "3–7 Tage",
    weight: 0.24,
    spark: [8, 9, 9, 11, 12, 14, 16, 19],
    delta: +3.1,
  },
  {
    key: "ari",
    name: "ARE-Konsultationen",
    role: "Wellenform, früh",
    detail: "Hausarzt-Konsultationen (RKI AGI-Sentinel)",
    lead: "1–2 Wochen",
    weight: 0.18,
    spark: [820, 910, 1010, 1180, 1360, 1520, 1680, 1840],
    delta: +9.5,
  },
  {
    key: "dwd",
    name: "Temperatur (DWD)",
    role: "Umweltfaktor",
    detail: "Tagesmittel, Stationen → Landkreis",
    lead: "1–2 Wochen",
    weight: 0.11,
    spark: [9, 7, 6, 4, 3, 2, 4, 3],
    delta: -1.0,
    invert: true, // falling temp = rising risk
  },
  {
    key: "cal",
    name: "Kalender",
    role: "Feiertage & Schulferien",
    detail: "Bekannte Nachfrageverschiebungen je Bundesland",
    lead: "im Voraus",
    weight: 0.05,
    spark: [0, 0, 1, 0, 0, 1, 1, 0],
    delta: 0,
    flat: true,
  },
];

/* Regional strip for the mini overview map/list. */
const REGION_STRIP = [
  { name: "München", pct: 78, status: "elevated" },
  { name: "Berlin", pct: 64, status: "normal" },
  { name: "Hamburg", pct: 69, status: "normal" },
  { name: "Köln", pct: 81, status: "elevated" },
  { name: "Frankfurt", pct: 88, status: "critical" },
  { name: "Stuttgart", pct: 72, status: "elevated" },
  { name: "Dresden", pct: 66, status: "normal" },
  { name: "Leipzig", pct: 58, status: "normal" },
];

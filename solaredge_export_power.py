#!/usr/bin/env python3
"""
SolarEdge → Home Assistant Power Statistics Exporter
======================================================
Ruft historische Leistungsdaten (Watt) von der SolarEdge Monitoring API ab
und erzeugt CSV-Dateien im mean/min/max-Format für die HACS-Integration
"homeassistant-statistics" (klausj1/homeassistant-statistics).

Die SE API liefert Leistungswerte in 15-Minuten-Intervallen (Watt).
Pro Stunde werden daraus mean, min und max berechnet.

Netz-Sensor Modi (GRID_MODE):
  "combined_standard"  → ein Sensor, positiv=Bezug, negativ=Einspeisung
  "combined_inverted"  → ein Sensor, positiv=Einspeisung, negativ=Bezug
  "split"              → zwei getrennte Sensoren für FeedIn und Purchased

Verwendung:
  pip install requests python-dateutil
  python solaredge_export_power.py
  python solaredge_export_power.py --api KEY --start "01.04.2021 00:00" --end "27.05.2026 08:45" --dryrun false
"""

import argparse
import requests
import csv
import os
from collections import defaultdict
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta

# ==============================================================================
# KONFIGURATION — hier anpassen
# ==============================================================================

SITE_ID   = "DEINE_SITE_ID"          # SolarEdge Site-ID
API_KEY   = "DEIN_API_KEY"           # API-Key aus dem SE Monitoring Portal

# Zeitraum — minutengenau
START_DATETIME = datetime(2021, 4, 1, 0, 0)
END_DATETIME   = datetime(2024, 12, 31, 23, 59)

# Timezone deiner HA-Instanz
TIMEZONE = "Europe/Berlin"

# Ausgabeverzeichnis
OUTPUT_DIR = "."

# Vorschau-Modus: True = nur Vorschau, keine CSV
DRY_RUN = True

# ------------------------------------------------------------------------------
# Netz-Sensor Modus:
#   "combined_standard" → ein Sensor, positiv=Bezug,      negativ=Einspeisung
#   "combined_inverted" → ein Sensor, positiv=Einspeisung, negativ=Bezug
#   "split"             → zwei getrennte Sensoren
# ------------------------------------------------------------------------------
GRID_MODE = "combined_inverted"

# Statistic-IDs der Leistungssensoren in HA
STATISTIC_IDS = {
    "Production": "sensor.solaredge_i1_ac_power",              # PV Leistung (W)

    # combined_standard / combined_inverted → nur GRID_COMBINED wird verwendet
    "grid_combined": "sensor.solaredge_i1_m1_ac_power",        # Netz kombiniert

    # split → FEEDIN und PURCHASED werden separat verwendet
    "grid_feedin":   "sensor.solaredge_i1_m1_ac_power_feedin",
    "grid_purchased":"sensor.solaredge_i1_m1_ac_power_purchased",
}

# ==============================================================================
# INTERNE KONSTANTEN
# ==============================================================================

API_BASE  = "https://monitoringapi.solaredge.com"
MAX_RANGE = relativedelta(months=1)

# ==============================================================================


def parse_args():
    parser = argparse.ArgumentParser(
        description="SolarEdge → Home Assistant Power Statistics Exporter",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--api",     "--API",     metavar="KEY",  help="SolarEdge API-Key")
    parser.add_argument("--site",    "--Site",    metavar="ID",   help="SolarEdge Site-ID")
    parser.add_argument("--start",   "--Start",   metavar="'DD.MM.YYYY HH:MM'", help="Startzeitpunkt")
    parser.add_argument("--end",     "--End",     metavar="'DD.MM.YYYY HH:MM'", help="Endzeitpunkt")
    parser.add_argument("--dryrun",  "--DryRun",  "--Dryrun", metavar="BOOL",   help="true/false")
    parser.add_argument("--gridmode","--GridMode", metavar="MODE",
                        choices=["combined_standard", "combined_inverted", "split"],
                        help="Netz-Sensor Modus: combined_standard, combined_inverted, split")
    return parser.parse_args()


def datetime_chunks(start, end, max_delta):
    current = start
    while current <= end:
        chunk_end = min(current + max_delta - timedelta(seconds=1), end)
        yield current, chunk_end
        current = chunk_end + timedelta(seconds=1)


def fetch_power_details(site_id, api_key, start, end, meters):
    url = f"{API_BASE}/site/{site_id}/powerDetails"
    params = {
        "api_key":   api_key,
        "startTime": start.strftime("%Y-%m-%d %H:%M:%S"),
        "endTime":   end.strftime("%Y-%m-%d %H:%M:%S"),
        "meters":    ",".join(meters),
    }
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def parse_power_details(json_data):
    """Aggregiert 15-min Werte zu stündlichen mean/min/max."""
    result = {}
    pd = json_data.get("powerDetails", {})

    for meter in pd.get("meters", []):
        meter_type = meter.get("type")
        buckets = defaultdict(list)

        for entry in meter.get("values", []):
            date_str = entry.get("date")
            value    = entry.get("value")
            if date_str and value is not None:
                dt       = datetime.strptime(date_str[:16], "%Y-%m-%d %H:%M")
                hour_str = dt.replace(minute=0).strftime("%Y-%m-%d %H:%M")
                buckets[hour_str].append(float(value))

        hourly = {}
        for hour_str, values in buckets.items():
            if values:
                hourly[hour_str] = (
                    round(sum(values) / len(values), 2),
                    round(min(values), 2),
                    round(max(values), 2),
                )
        result[meter_type] = hourly

    return result


def fetch_all_data(site_id, api_key, start_dt, end_dt, api_meters):
    """Holt alle Daten in monatlichen Chunks."""
    all_data = {m: {} for m in api_meters}
    chunks   = list(datetime_chunks(start_dt, end_dt, MAX_RANGE))

    print(f"Abruf in {len(chunks)} Chunk(s) (je max. 1 Monat, 15-min Auflösung)...")

    for i, (chunk_start, chunk_end) in enumerate(chunks, 1):
        print(f"  [{i}/{len(chunks)}] {chunk_start.strftime('%Y-%m-%d %H:%M')} → "
              f"{chunk_end.strftime('%Y-%m-%d %H:%M')} ...", end=" ")
        try:
            json_data = fetch_power_details(site_id, api_key, chunk_start, chunk_end, api_meters)
            parsed    = parse_power_details(json_data)
            for meter_type, hourly in parsed.items():
                if meter_type in all_data:
                    all_data[meter_type].update(hourly)
            print(f"OK ({sum(len(v) for v in parsed.values())} Stunden-Buckets)")
        except requests.HTTPError as e:
            print(f"FEHLER: {e}")
        except Exception as e:
            print(f"FEHLER: {e}")

    return all_data


def apply_grid_mode(feedin_data, purchased_data, grid_mode):
    """
    Kombiniert oder splittet FeedIn/Purchased-Daten je nach GRID_MODE.

    Rückgabe: Dict { statistic_key → hourly_data }
      statistic_key ist einer von: "grid_combined", "grid_feedin", "grid_purchased"

    Vorzeichenlogik der SE API:
      FeedIn   → immer positive Werte
      Purchased→ immer positive Werte
    """
    if grid_mode == "split":
        # Zwei separate Sensoren — Werte bleiben positiv
        return {
            "grid_feedin":    feedin_data,
            "grid_purchased": purchased_data,
        }

    # Kombinierter Sensor: alle Stunden zusammenführen
    all_hours = set(feedin_data.keys()) | set(purchased_data.keys())
    combined  = {}

    for hour in all_hours:
        fi = feedin_data.get(hour)    # (mean, min, max) oder None
        pu = purchased_data.get(hour) # (mean, min, max) oder None

        if grid_mode == "combined_inverted":
            # positiv = Einspeisung, negativ = Bezug
            fi_mean  = ( fi[0] if fi else 0.0)
            pu_mean  = (-pu[0] if pu else 0.0)
            fi_max   = ( fi[2] if fi else 0.0)
            pu_min   = (-pu[2] if pu else 0.0)  # größter Bezug → negativstes min
        else:
            # combined_standard: positiv = Bezug, negativ = Einspeisung
            fi_mean  = (-fi[0] if fi else 0.0)
            pu_mean  = ( pu[0] if pu else 0.0)
            fi_max   = (-fi[2] if fi else 0.0)
            pu_min   = ( pu[2] if pu else 0.0)

        mean = round(fi_mean + pu_mean, 2)
        # min/max: niedrigster bzw. höchster Wert über die Stunde
        mn   = round(min(fi_max, pu_min), 2)
        mx   = round(max(fi_max, pu_min), 2)

        combined[hour] = (mean, mn, mx)

    return {"grid_combined": combined}


def preview_data(label, statistic_id, hourly, n=5):
    if not hourly:
        print(f"  Keine Daten.")
        return
    sorted_hours = sorted(hourly.items())
    non_zero     = [(h, v) for h, v in sorted_hours if v[0] != 0]
    peak_max     = max(abs(v[2]) for _, v in sorted_hours) if sorted_hours else 0

    print(f"  Statistic-ID   : {statistic_id}")
    print(f"  Stunden-Buckets: {len(sorted_hours):,} ({len(non_zero):,} davon ≠ 0 W)")
    print(f"  Peak (abs. max): {peak_max:,.0f} W")
    print(f"  Erster Wert    : {sorted_hours[0][0]}")
    print(f"  Letzter Wert   : {sorted_hours[-1][0]}")
    print(f"  Erste {n} Stunden ≠ 0:")
    shown = 0
    for hour_str, (mean, mn, mx) in sorted_hours:
        if mean != 0:
            print(f"    {hour_str}  mean={mean:8.1f} W  min={mn:8.1f} W  max={mx:8.1f} W")
            shown += 1
            if shown >= n:
                break
    print(f"  Letzte {n} Stunden ≠ 0:")
    shown = 0
    for hour_str, (mean, mn, mx) in reversed(sorted_hours):
        if mean != 0:
            print(f"    {hour_str}  mean={mean:8.1f} W  min={mn:8.1f} W  max={mx:8.1f} W")
            shown += 1
            if shown >= n:
                break


def write_csv(label, statistic_id, hourly, output_dir):
    if not hourly:
        print(f"  {label}: Keine Daten, überspringe.")
        return None

    filename = os.path.join(output_dir, f"se_power_{label}_ha_statistics.csv")
    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter=",")
        writer.writerow(["statistic_id", "start", "unit", "mean", "min", "max"])
        for hour_str, (mean, mn, mx) in sorted(hourly.items()):
            writer.writerow([statistic_id, hour_str, "W", mean, mn, mx])

    non_zero = sum(1 for v in hourly.values() if v[0] != 0)
    print(f"  → {filename}")
    print(f"     {len(hourly):,} Stunden ({non_zero:,} ≠ 0 W)")
    return filename


def main():
    global SITE_ID, API_KEY, START_DATETIME, END_DATETIME, DRY_RUN, GRID_MODE

    args = parse_args()
    if args.api:      API_KEY        = args.api
    if args.site:     SITE_ID        = args.site
    if args.gridmode: GRID_MODE      = args.gridmode
    if args.dryrun:   DRY_RUN        = args.dryrun.lower() not in ("false", "0", "no", "nein")
    if args.start:
        try:    START_DATETIME = datetime.strptime(args.start, "%d.%m.%Y %H:%M")
        except ValueError:
            print(f"FEHLER: Ungültiges Start-Format '{args.start}'"); return
    if args.end:
        try:    END_DATETIME = datetime.strptime(args.end, "%d.%m.%Y %H:%M")
        except ValueError:
            print(f"FEHLER: Ungültiges End-Format '{args.end}'"); return

    print("=" * 60)
    print("SolarEdge → Home Assistant Power Statistics Exporter")
    print("=" * 60)
    print(f"Site ID   : {SITE_ID}")
    print(f"Zeitraum  : {START_DATETIME.strftime('%Y-%m-%d %H:%M')} → {END_DATETIME.strftime('%Y-%m-%d %H:%M')}")
    print(f"Grid-Modus: {GRID_MODE}")
    print(f"Modus     : {'🔍 DRY RUN (nur Vorschau)' if DRY_RUN else '💾 SCHREIBEN'}")
    print()

    if API_KEY == "DEIN_API_KEY":
        print("FEHLER: Bitte API_KEY eintragen oder --api KEY übergeben!")
        return

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Immer Production + FeedIn + Purchased von der API holen
    api_meters = ["Production", "FeedIn", "Purchased"]
    all_data   = fetch_all_data(SITE_ID, API_KEY, START_DATETIME, END_DATETIME, api_meters)

    # Grid-Daten je nach Modus aufbereiten
    grid_outputs = apply_grid_mode(
        all_data.get("FeedIn", {}),
        all_data.get("Purchased", {}),
        GRID_MODE,
    )

    # Alle Outputs zusammenstellen: Production + Grid
    outputs = {"production": all_data.get("Production", {})}
    outputs.update(grid_outputs)

    # Label → Statistic-ID Mapping
    stat_map = {
        "production":    STATISTIC_IDS["Production"],
        "grid_combined": STATISTIC_IDS["grid_combined"],
        "grid_feedin":   STATISTIC_IDS["grid_feedin"],
        "grid_purchased":STATISTIC_IDS["grid_purchased"],
    }

    if DRY_RUN:
        print()
        print("=== DATENVORSCHAU (kein Schreiben) ===")
        for label, hourly in outputs.items():
            stat_id = stat_map.get(label, "???")
            print()
            print(f"── {label} {'─' * (40 - len(label))}")
            preview_data(label, stat_id, hourly)
        print()
        print(f"→ Grid-Modus '{GRID_MODE}' erklärt:")
        if GRID_MODE == "combined_inverted":
            print("  Einspeisung = positiv, Bezug = negativ → ein Sensor")
        elif GRID_MODE == "combined_standard":
            print("  Bezug = positiv, Einspeisung = negativ → ein Sensor")
        else:
            print("  Zwei getrennte Sensoren für FeedIn und Purchased")
        print()
        print("→ Setze DRY_RUN = False (oder --dryrun false) um CSVs zu erzeugen.")
    else:
        print()
        print("Schreibe CSV-Dateien...")
        written_files = []
        for label, hourly in outputs.items():
            stat_id = stat_map.get(label)
            if not stat_id:
                print(f"  {label}: Keine statistic_id konfiguriert, überspringe.")
                continue
            result = write_csv(label, stat_id, hourly, OUTPUT_DIR)
            if result:
                written_files.append(result)

        print()
        print("=" * 60)
        print("FERTIG! Import-Parameter für HA:")
        for f in written_files:
            print(f"\n  filename: {os.path.basename(f)}")
            print(f"  delimiter: \",\"")
            print(f"  decimal: \".\"")
            print(f"  datetime_format: \"%Y-%m-%d %H:%M\"")
            print(f"  timezone_identifier: \"{TIMEZONE}\"")
    print()
    print("=" * 60)


if __name__ == "__main__":
    main()

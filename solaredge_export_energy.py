#!/usr/bin/env python3
"""
SolarEdge → Home Assistant Statistics Importer
================================================
Ruft historische Energiedaten von der SolarEdge Monitoring API ab und
erzeugt CSV-Dateien, die mit der HACS-Integration "homeassistant-statistics"
(klausj1/homeassistant-statistics) direkt importiert werden können.

Unterstützte Meter-Typen:
  - Production  → PV-Produktion
  - FeedIn      → Einspeisung ins Netz
  - Purchased   → Bezug vom Netz
  - Consumption → Hausverbrauch

Verwendung:
  pip install requests
  python solaredge_to_ha_statistics.py

Ausgabe: Eine CSV-Datei pro Meter-Typ im Verzeichnis OUTPUT_DIR
"""

import argparse
import requests
import csv
import os
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta

# ==============================================================================
# KONFIGURATION — hier anpassen
# ==============================================================================

SITE_ID   = "DEINE_SITE_ID"          # SolarEdge Site-ID
API_KEY   = "DEIN_API_KEY"           # API-Key aus dem SE Monitoring Portal

# Zeitraum der historischen Daten — minutengenau konfigurierbar
# Startpunkt: Inbetriebnahme der Anlage oder gewünschter Beginn
START_DATETIME = datetime(2021, 4, 1, 0, 0)

# Endpunkt: exakt bis kurz VOR dem ersten vorhandenen HA-Datenpunkt
# Beispiel: HA hat ab 27.05.2025 08:47 Daten → Ende auf 08:45 setzen
END_DATETIME   = datetime(2024, 12, 31, 23, 59)

# Zeitauflösung: "QUARTER_OF_AN_HOUR", "HOUR" oder "DAY"
# QUARTER_OF_AN_HOUR → max. 1 Monat pro API-Call (~50 Calls für 4 Jahre, gut im Limit)
# HOUR
# DAY               → max. 1 Jahr pro API-Call (schneller, aber weniger granular)
TIME_UNIT  = "QUARTER_OF_AN_HOUR"

# Timezone deiner HA-Instanz (für den CSV-Import)
TIMEZONE   = "Europe/Berlin"

# Ausgabeverzeichnis für die CSV-Dateien
OUTPUT_DIR = "."

# Vorschau-Modus: True = nur Datenvorschau, keine CSV-Dateien werden geschrieben
DRY_RUN = True

# Welche Meter abrufen? Kommentiere nicht benötigte aus.
METERS = [
    "Production",   # PV-Produktion  → Solar Panel Output in HA Energy Dashboard
    "FeedIn",       # Einspeisung    → Return to grid
    "Purchased",    # Netzbezug      → Grid consumption
    "Consumption",  # Hausverbrauch  → Echter Messwert vom SE-Meter
]

# Statistic-IDs aus deiner HA-Instanz (Einstellungen → Entwicklerwerkzeuge → Statistiken)
# Passe diese an deine tatsächlichen Entity-IDs an!
STATISTIC_IDS = {
    "Production":  "sensor.your_pv_production_sensor",          # PV Produktion (kWh)
    "FeedIn":      "sensor.your_pv_export_sensor",              # Einspeisung   (kWh)
    "Purchased":   "sensor.your_pv_import_sensor",              # Netzbezug     (kWh)
    "Consumption": "sensor.solaredge_m1_ac_energy_wh_consumed",  # Hausverbrauch
}

# ==============================================================================
# INTERNE KONSTANTEN — normalerweise nicht ändern
# ==============================================================================

API_BASE = "https://monitoringapi.solaredge.com"

# Maximale Zeitspanne pro API-Call je nach Auflösung
MAX_RANGE = {
    "DAY":               relativedelta(years=1),
    "HOUR":              relativedelta(months=1),
    "QUARTER_OF_AN_HOUR": relativedelta(months=1),
    "WEEK":              relativedelta(years=5),
    "MONTH":             relativedelta(years=5),
}

# ==============================================================================


def fetch_energy_details(site_id, api_key, start, end, time_unit, meters):
    """Ruft energyDetails von der SolarEdge API ab."""
    url = f"{API_BASE}/site/{site_id}/energyDetails"
    params = {
        "api_key":   api_key,
        "timeUnit":  time_unit,
        "startTime": start.strftime("%Y-%m-%d %H:%M:%S"),
        "endTime":   end.strftime("%Y-%m-%d %H:%M:%S"),
        "meters":    ",".join(meters),
    }
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def date_chunks(start, end, max_delta):
    """Teilt einen Datetime-Bereich in API-konforme Chunks auf."""
    current = start
    while current <= end:
        chunk_end = min(current + max_delta - timedelta(seconds=1), end)
        yield current, chunk_end
        current = chunk_end + timedelta(seconds=1)


def parse_energy_details(json_data):
    """Parst die API-Antwort und gibt Dict meter_type → [(date_str, wh)] zurück."""
    result = {}
    ed = json_data.get("energyDetails", {})
    unit = ed.get("unit", "Wh")

    for meter in ed.get("meters", []):
        meter_type = meter.get("type")
        values = []
        for entry in meter.get("values", []):
            date_str = entry.get("date")
            value    = entry.get("value")  # kann None sein (kein Ertrag)
            if date_str and value is not None:
                # Einheit normalisieren → immer in Wh
                if unit == "kWh":
                    value = value * 1000
                elif unit == "MWh":
                    value = value * 1_000_000
                values.append((date_str, float(value)))
        result[meter_type] = values
    return result


def fetch_all_data(site_id, api_key, start_dt, end_dt, time_unit, meters):
    """Holt alle Daten für den gesamten Zeitraum in Chunks."""
    all_data = {m: [] for m in meters}
    max_delta = MAX_RANGE.get(time_unit, relativedelta(years=1))

    chunks = list(date_chunks(start_dt, end_dt, max_delta))
    print(f"Abruf in {len(chunks)} Chunk(s) mit Auflösung '{time_unit}'...")

    for i, (chunk_start, chunk_end) in enumerate(chunks, 1):
        print(f"  [{i}/{len(chunks)}] {chunk_start.strftime('%Y-%m-%d %H:%M')} → {chunk_end.strftime('%Y-%m-%d %H:%M')} ...", end=" ")
        try:
            json_data = fetch_energy_details(
                site_id, api_key, chunk_start, chunk_end, time_unit, meters
            )
            parsed = parse_energy_details(json_data)
            for meter_type, values in parsed.items():
                if meter_type in all_data:
                    all_data[meter_type].extend(values)
            print(f"OK ({sum(len(v) for v in parsed.values())} Datenpunkte)")
        except requests.HTTPError as e:
            print(f"FEHLER: {e}")
        except Exception as e:
            print(f"FEHLER: {e}")

    return all_data


def preview_data(meter_type, values, statistic_id, n=5):
    """Zeigt eine Vorschau der abgerufenen Daten ohne CSV zu schreiben."""
    if not values:
        print(f"  Keine Daten für {meter_type}.")
        return

    sorted_values = sorted(values, key=lambda x: x[0])
    total_kwh     = round(sum(wh for _, wh in sorted_values) / 1000, 2)
    non_zero      = [(d, wh) for d, wh in sorted_values if wh > 0]

    print(f"  Statistic-ID : {statistic_id}")
    print(f"  Datenpunkte  : {len(sorted_values):,} ({len(non_zero):,} davon > 0)")
    print(f"  Gesamt       : {total_kwh:,.2f} kWh")
    print(f"  Erster Wert  : {sorted_values[0][0][:16]}")
    print(f"  Letzter Wert : {sorted_values[-1][0][:16]}")

    # Erste n Einträge mit Wert > 0
    print(f"  Erste {n} Einträge mit Ertrag > 0:")
    shown = 0
    for date_str, wh in sorted_values:
        if wh > 0:
            print(f"    {date_str[:16]}  →  {wh/1000:.4f} kWh")
            shown += 1
            if shown >= n:
                break
    if shown == 0:
        print("    (keine Einträge mit Wert > 0 gefunden)")

    # Letzte n Einträge mit Wert > 0
    print(f"  Letzte {n} Einträge mit Ertrag > 0:")
    shown = 0
    for date_str, wh in reversed(sorted_values):
        if wh > 0:
            print(f"    {date_str[:16]}  →  {wh/1000:.4f} kWh")
            shown += 1
            if shown >= n:
                break
    if shown == 0:
        print("    (keine Einträge mit Wert > 0 gefunden)")


def write_csv(meter_type, values, statistic_id, timezone, output_dir):
    """
    Schreibt eine CSV-Datei im Format für homeassistant-statistics.

    Format (delta-Import — empfohlen für historische Daten vor Sensor-Existenz):
      statistic_id, start, unit, delta

    delta = Energiemenge für diesen Zeitraum in kWh (nicht kumulativ!)
    Die Integration berechnet sum/state selbst und verbindet nahtlos mit
    vorhandenen Daten.
    """
    if not values:
        print(f"  Keine Daten für {meter_type}, überspringe.")
        return

    filename = os.path.join(output_dir, f"se_{meter_type.lower()}_ha_statistics.csv")

    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter=",")
        writer.writerow(["statistic_id", "start", "unit", "delta"])

        for date_str, wh in sorted(values, key=lambda x: x[0]):
            kwh = round(wh / 1000, 4)
            # Datum aus API ist lokal (Site-Timezone), Format: "2021-04-01 00:00:00"
            # → direkt als datetime-String übernehmen, HA-Integration parst per TZ
            dt_str = date_str[:16]  # "2021-04-01 00:00"
            writer.writerow([statistic_id, dt_str, "kWh", kwh])

    count = len(values)
    total_kwh = round(sum(wh for _, wh in values) / 1000, 2)
    print(f"  → {filename}")
    print(f"     {count} Datenpunkte, Gesamt: {total_kwh} kWh")
    return filename


def parse_args():
    """Parst optionale CLI-Argumente. Skript-Konfiguration dient als Default."""
    parser = argparse.ArgumentParser(
        description="SolarEdge → Home Assistant Statistics Importer",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "--api", "--API",
        metavar="KEY",
        help="SolarEdge API-Key (überschreibt API_KEY im Skript)",
    )
    parser.add_argument(
        "--site", "--Site",
        metavar="ID",
        help="SolarEdge Site-ID (überschreibt SITE_ID im Skript)",
    )
    parser.add_argument(
        "--start", "--Start",
        metavar="'DD.MM.YYYY HH:MM'",
        help="Startzeitpunkt, z.B. '01.04.2021 00:00'",
    )
    parser.add_argument(
        "--end", "--End",
        metavar="'DD.MM.YYYY HH:MM'",
        help="Endzeitpunkt, z.B. '27.05.2026 08:45'",
    )
    parser.add_argument(
        "--timeunit", "--TimeUnit",
        metavar="UNIT",
        choices=["QUARTER_OF_AN_HOUR", "HOUR", "DAY", "WEEK", "MONTH"],
        help="Zeitauflösung (QUARTER_OF_AN_HOUR, HOUR, DAY, ...)",
    )
    parser.add_argument(
        "--dryrun", "--DryRun", "--Dryrun",
        metavar="BOOL",
        help="Vorschau-Modus: true/false (überschreibt DRY_RUN im Skript)",
    )
    return parser.parse_args()


def main():
    global SITE_ID, API_KEY, START_DATETIME, END_DATETIME, TIME_UNIT, DRY_RUN

    args = parse_args()

    # CLI-Argumente überschreiben Skript-Konfiguration
    if args.api:
        API_KEY = args.api
    if args.site:
        SITE_ID = args.site
    if args.start:
        try:
            START_DATETIME = datetime.strptime(args.start, "%d.%m.%Y %H:%M")
        except ValueError:
            print(f"FEHLER: Ungültiges Start-Format '{args.start}'. Erwartet: DD.MM.YYYY HH:MM")
            return
    if args.end:
        try:
            END_DATETIME = datetime.strptime(args.end, "%d.%m.%Y %H:%M")
        except ValueError:
            print(f"FEHLER: Ungültiges End-Format '{args.end}'. Erwartet: DD.MM.YYYY HH:MM")
            return
    if args.timeunit:
        TIME_UNIT = args.timeunit
    if args.dryrun is not None:
        DRY_RUN = args.dryrun.lower() not in ("false", "0", "no", "nein")

    print("=" * 60)
    print("SolarEdge → Home Assistant Statistics Importer")
    print("=" * 60)
    print(f"Site ID  : {SITE_ID}")
    print(f"Zeitraum : {START_DATETIME.strftime('%Y-%m-%d %H:%M')} → {END_DATETIME.strftime('%Y-%m-%d %H:%M')}")
    print(f"Auflösung: {TIME_UNIT}")
    print(f"Meter    : {', '.join(METERS)}")
    print(f"Modus    : {'🔍 DRY RUN (nur Vorschau, keine CSV)' if DRY_RUN else '💾 SCHREIBEN (CSV wird erstellt)'}")
    print()

    if API_KEY == "DEIN_API_KEY":
        print("FEHLER: Bitte API_KEY in der Konfiguration eintragen!")
        return

    # Prüfe ob alle Statistic-IDs konfiguriert sind
    missing = [m for m in METERS if m not in STATISTIC_IDS]
    if missing:
        print(f"WARNUNG: Fehlende STATISTIC_IDs für: {', '.join(missing)}")
        print("         Bitte in der Konfiguration ergänzen.")
        print()

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Daten abrufen
    all_data = fetch_all_data(
        SITE_ID, API_KEY, START_DATETIME, END_DATETIME, TIME_UNIT, METERS
    )

    # Vorschau oder CSV schreiben
    if DRY_RUN:
        print("=== DATENVORSCHAU (kein Schreiben) ===")
        for meter_type in METERS:
            values  = all_data.get(meter_type, [])
            stat_id = STATISTIC_IDS.get(meter_type, "???")
            print()
            print(f"── {meter_type} {'─' * (40 - len(meter_type))}")
            preview_data(meter_type, values, stat_id)
        print()
        print("→ Setze DRY_RUN = False um die CSV-Dateien zu erzeugen.")
    else:
        print()
        print("Schreibe CSV-Dateien...")
        written_files = []
        for meter_type in METERS:
            values  = all_data.get(meter_type, [])
            stat_id = STATISTIC_IDS.get(meter_type)
            if not stat_id:
                print(f"  {meter_type}: Keine statistic_id konfiguriert, überspringe.")
                continue
            result = write_csv(meter_type, values, stat_id, TIMEZONE, OUTPUT_DIR)
            if result:
                written_files.append(result)

        print()
        print("=" * 60)
        print("FERTIG!")
        print()
        print("Nächste Schritte:")
        print("1. CSV-Dateien in das HA-Konfigurationsverzeichnis kopieren:")
        print("   z.B. /config/  (oder ein Unterverzeichnis)")
        print()
        print("2. In HA unter Entwicklerwerkzeuge → Aktionen aufrufen:")
        print("   Aktion: import_statistics.import_from_file")
        print()
        for f in written_files:
            fname = os.path.basename(f)
            print(f"   filename: {fname}")
            print(f"   delimiter: \",\"")
            print(f"   decimal: \".\"")
            print(f"   datetime_format: \"%Y-%m-%d %H:%M\"")
            print(f"   timezone_identifier: \"{TIMEZONE}\"")
            print()
        print("3. Nach dem Import: Energy Dashboard → Statistiken prüfen")
    print("=" * 60)


if __name__ == "__main__":
    main()

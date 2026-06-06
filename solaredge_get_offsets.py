#!/usr/bin/env python3
"""
SolarEdge Meter-Offset Abfrage
================================
Ruft den kumulativen Zählerstand der SE-Meter zu einem bestimmten Zeitpunkt ab.
Nützlich um den Startwert (Offset) vor dem ersten importierten Datenpunkt zu
ermitteln — z.B. bei Meter-Nachrüstung oder HA-Neuinstallation.

Verwendung:
  pip install requests
  python solaredge_get_offsets.py
  python solaredge_get_offsets.py --api KEY --until "21.09.2021 14:00"

Ausgabe: Kumulativer Zählerstand pro Meter bis zum angegebenen Zeitpunkt
"""

import argparse
import requests
from datetime import datetime

# ==============================================================================
# KONFIGURATION — hier anpassen oder per CLI überschreiben
# ==============================================================================

SITE_ID   = "DEINE_SITE_ID"          # SolarEdge Site-ID
API_KEY   = "DEIN_API_KEY"           # API-Key aus dem SE Monitoring Portal

# Zeitpunkt BIS zu dem der kumulative Zählerstand ermittelt werden soll
# → typisch: kurz VOR dem ersten importierten Datenpunkt
# → z.B. Meter-Installation war am 21.09.2021 15:00 → "21.09.2021 14:00"
UNTIL_DATETIME = datetime(2021, 9, 21, 14, 0)

# Startdatum der Anlage (Inbetriebnahme)
# → wird als Beginn der kumulativen Summierung verwendet
SINCE_DATETIME = datetime(2021, 4, 1, 0, 0)

# Welche Meter abfragen?
METERS = ["Production", "FeedIn", "Purchased"]

# ==============================================================================

API_BASE = "https://monitoringapi.solaredge.com"


def parse_args():
    parser = argparse.ArgumentParser(
        description="SolarEdge Meter-Offset Abfrage",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--api", "--API", metavar="KEY",
                        help="SolarEdge API-Key")
    parser.add_argument("--site", "--Site", metavar="ID",
                        help="SolarEdge Site-ID")
    parser.add_argument("--since", "--Since", metavar="'DD.MM.YYYY HH:MM'",
                        help="Beginn der Summierung (Inbetriebnahme), z.B. '01.04.2021 00:00'")
    parser.add_argument("--until", "--Until", metavar="'DD.MM.YYYY HH:MM'",
                        help="Bis zu diesem Zeitpunkt summieren, z.B. '21.09.2021 14:00'")
    return parser.parse_args()


def fetch_energy_details(site_id, api_key, start, end, meters):
    """Ruft stündliche Energiedaten für einen Zeitraum ab (max. 1 Monat)."""
    url = f"{API_BASE}/site/{site_id}/energyDetails"
    params = {
        "api_key":   api_key,
        "timeUnit":  "HOUR",
        "startTime": start.strftime("%Y-%m-%d %H:%M:%S"),
        "endTime":   end.strftime("%Y-%m-%d %H:%M:%S"),
        "meters":    ",".join(meters),
    }
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def main():
    global SITE_ID, API_KEY, SINCE_DATETIME, UNTIL_DATETIME

    args = parse_args()
    if args.api:
        API_KEY = args.api
    if args.site:
        SITE_ID = args.site
    if args.since:
        try:
            SINCE_DATETIME = datetime.strptime(args.since, "%d.%m.%Y %H:%M")
        except ValueError:
            print(f"FEHLER: Ungültiges Since-Format '{args.since}'. Erwartet: DD.MM.YYYY HH:MM")
            return
    if args.until:
        try:
            UNTIL_DATETIME = datetime.strptime(args.until, "%d.%m.%Y %H:%M")
        except ValueError:
            print(f"FEHLER: Ungültiges Until-Format '{args.until}'. Erwartet: DD.MM.YYYY HH:MM")
            return

    if API_KEY == "DEIN_API_KEY":
        print("FEHLER: Bitte API_KEY eintragen oder --api KEY übergeben!")
        return

    print("=" * 60)
    print("SolarEdge Meter-Offset Abfrage")
    print("=" * 60)
    print(f"Site ID  : {SITE_ID}")
    print(f"Seit     : {SINCE_DATETIME.strftime('%Y-%m-%d %H:%M')}")
    print(f"Bis      : {UNTIL_DATETIME.strftime('%Y-%m-%d %H:%M')}")
    print(f"Meter    : {', '.join(METERS)}")
    print()

    # Zeitraum in Monats-Chunks aufteilen (API-Limit: max. 1 Monat bei HOUR)
    from dateutil.relativedelta import relativedelta
    from datetime import timedelta

    totals = {m: 0.0 for m in METERS}
    unit   = "Wh"

    current = SINCE_DATETIME
    chunks  = []
    while current <= UNTIL_DATETIME:
        chunk_end = min(current + relativedelta(months=1) - timedelta(seconds=1), UNTIL_DATETIME)
        chunks.append((current, chunk_end))
        current = chunk_end + timedelta(seconds=1)

    print(f"Abruf in {len(chunks)} Chunk(s)...")

    for i, (chunk_start, chunk_end) in enumerate(chunks, 1):
        print(f"  [{i}/{len(chunks)}] {chunk_start.strftime('%Y-%m-%d %H:%M')} → {chunk_end.strftime('%Y-%m-%d %H:%M')} ...", end=" ")
        try:
            data = fetch_energy_details(SITE_ID, API_KEY, chunk_start, chunk_end, METERS)
            ed   = data.get("energyDetails", {})
            unit = ed.get("unit", "Wh")

            chunk_totals = {m: 0.0 for m in METERS}
            for meter in ed.get("meters", []):
                mt = meter.get("type")
                if mt in totals:
                    s = sum(e.get("value") or 0.0 for e in meter.get("values", []))
                    totals[mt]       += s
                    chunk_totals[mt]  = s

            summary = ", ".join(f"{m}: {v:.1f}" for m, v in chunk_totals.items() if v > 0)
            print(f"OK ({summary or '0'} {unit})")

        except requests.HTTPError as e:
            print(f"FEHLER: {e}")
        except Exception as e:
            print(f"FEHLER: {e}")

    # Einheit normalisieren → kWh
    def to_kwh(val):
        if unit == "Wh":
            return val / 1000
        elif unit == "MWh":
            return val * 1000
        return val

    print()
    print("=" * 60)
    print(f"KUMULATIVER ZÄHLERSTAND bis {UNTIL_DATETIME.strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)
    for meter, total in totals.items():
        kwh = to_kwh(total)
        print(f"  {meter:<12}: {kwh:>12,.2f} kWh")

    print()
    print("Diese Werte sind die Offsets für den CSV-Import.")
    print("Der erste importierte Delta-Wert wird relativ zu diesen")
    print("Zählerständen interpretiert — so entsteht kein Spike.")
    print()
    print("Tipp: Im homeassistant-statistics Delta-Import werden Offsets")
    print("automatisch korrekt gesetzt wenn vorhandene Statistiken existieren.")
    print("Für neue Sensoren ohne Vorgeschichte muss der erste Datenpunkt")
    print("in der CSV ggf. angepasst werden.")
    print("=" * 60)


if __name__ == "__main__":
    main()

# SolarEdge → Home Assistant Statistics Toolkit
 
A set of Python scripts to fetch historical energy and power data from the **SolarEdge Monitoring API** and export it as CSV files ready to import into **Home Assistant's long-term statistics database** via the [homeassistant-statistics](https://github.com/klausj1/homeassistant-statistics) HACS integration.
 
## Use Case
 
If you reinstalled Home Assistant or started using HA after your SolarEdge system was already in operation, your Energy Dashboard will be missing historical data. These scripts fill that gap by pulling data directly from the SolarEdge cloud and generating import-ready CSV files.
 
## Prerequisites
 
- Python 3.8+
- A SolarEdge account with API access enabled
- Your **Site ID** and **API Key** (found in the SolarEdge Monitoring Portal under *Admin → Site Access → API Access*)
- The [homeassistant-statistics](https://github.com/klausj1/homeassistant-statistics) HACS integration installed in Home Assistant
### Install dependencies
 
```bash
pip install requests python-dateutil
```
 
---
 
## Scripts Overview
 
| Script | Purpose | Output Format |
|---|---|---|
| `solaredge_export_energy.py` | Fetch historical **energy** data (kWh) | `delta` per hour |
| `solaredge_export_power.py` | Fetch historical **power** data (W) | `mean / min / max` per hour |
| `solaredge_get_offsets.py` | Query cumulative meter readings at a point in time | Console output only |
 
---
 
## `solaredge_export_energy.py` — Energy Export
 
Fetches energy data (Production, FeedIn, Purchased, Consumption) from the SolarEdge API and writes one CSV file per meter type in the delta format required by `homeassistant-statistics`.
 
### Configuration
 
Edit the configuration block at the top of the script:
 
| Parameter | Default | Description |
|---|---|---|
| `SITE_ID` | `"2183551"` | Your SolarEdge Site ID |
| `API_KEY` | `"DEIN_API_KEY"` | Your SolarEdge API Key |
| `START_DATETIME` | `datetime(2021, 4, 1, 0, 0)` | Start of historical data (system commissioning date) |
| `END_DATETIME` | `datetime(2024, 12, 31, 23, 59)` | End of historical data — set this to just **before** your first existing HA data point |
| `TIME_UNIT` | `"HOUR"` | API resolution: `QUARTER_OF_AN_HOUR`, `HOUR`, `DAY` — **must be `HOUR` or coarser** for HA import (HA requires full-hour timestamps) |
| `TIMEZONE` | `"Europe/Berlin"` | Timezone of your HA instance |
| `OUTPUT_DIR` | `"."` | Directory to write CSV files to |
| `DRY_RUN` | `True` | Preview mode — no files written when `True` |
| `METERS` | see below | Which meter types to export |
| `STATISTIC_IDS` | see below | HA entity IDs to map each meter to |
 
#### Meters
 
```python
METERS = [
    "Production",   # PV production energy
    "FeedIn",       # Energy exported to grid
    "Purchased",    # Energy imported from grid
    "Consumption",  # Total household consumption (requires SE meter)
]
```
 
#### Statistic IDs
 
Map each meter type to the corresponding HA sensor entity ID. Find your IDs under *Settings → Developer Tools → Statistics* in Home Assistant.
 
```python
STATISTIC_IDS = {
    "Production":  "sensor.solaredge_i1_ac_energy_wh",
    "FeedIn":      "sensor.solaredge_m1_ac_energy_wh_exported",
    "Purchased":   "sensor.solaredge_m1_ac_energy_wh_imported",
    "Consumption": "sensor.solaredge_m1_ac_energy_wh_consumed",
}
```
 
> **Note:** The statistic entity must already exist in HA (i.e. the sensor must have recorded at least some data) before importing.
 
### API Limits
 
| Resolution | Max range per API call |
|---|---|
| `QUARTER_OF_AN_HOUR` | 1 month |
| `HOUR` | 1 month |
| `DAY` | 1 year |
| `WEEK` / `MONTH` | 5 years |
 
The script automatically splits the requested time range into compliant chunks.
 
### Command Line Usage
 
All configuration values can be overridden via CLI arguments:
 
```bash
python solaredge_to_ha_statistics.py \
  --api YOUR_API_KEY \
  --site YOUR_SITE_ID \
  --start "01.04.2021 00:00" \
  --end "27.05.2026 08:45" \
  --timeunit HOUR \
  --dryrun false
```
 
| Argument | Description |
|---|---|
| `--api` / `--API` | SolarEdge API Key |
| `--site` / `--Site` | SolarEdge Site ID |
| `--start` / `--Start` | Start datetime `DD.MM.YYYY HH:MM` |
| `--end` / `--End` | End datetime `DD.MM.YYYY HH:MM` |
| `--timeunit` / `--TimeUnit` | API resolution (`QUARTER_OF_AN_HOUR`, `HOUR`, `DAY`, ...) |
| `--dryrun` / `--DryRun` | `true` / `false` / `yes` / `no` / `1` / `0` |
 
### Output
 
One CSV file per meter type, e.g.:
 
```
se_production_ha_statistics.csv
se_feedin_ha_statistics.csv
se_purchased_ha_statistics.csv
se_consumption_ha_statistics.csv
```
 
CSV format (delta):
```
statistic_id,start,unit,delta
sensor.solaredge_i1_ac_energy_wh,2021-04-07 13:00,kWh,5.996
sensor.solaredge_i1_ac_energy_wh,2021-04-07 14:00,kWh,5.593
...
```
 
### Importing into Home Assistant
 
1. Copy the CSV file(s) to your HA configuration directory (e.g. `/config/`)
2. In HA go to *Developer Tools → Actions* and call:
```yaml
action: import_statistics.import_from_file
data:
  filename: se_production_ha_statistics.csv
  delimiter: ","
  decimal: "."
  datetime_format: "%Y-%m-%d %H:%M"
  timezone_identifier: "Europe/Berlin"
```
 
Repeat for each CSV file.
 
### Known Issue: Statistics Spike After Import
 
If the target sensor already has statistics in HA before the import, the first imported value may cause a spike in the Energy Dashboard. This happens because HA uses the existing cumulative `sum` value as the base for the delta calculation.
 
**Fix:** Go to *Settings → Developer Tools → Statistics*, find the affected sensor, click *Adjust statistics*, locate the entry with the wrong value (usually the very first timestamp of your import), and set it to `0`.
 
Use `solaredge_get_offsets.py` to verify the expected cumulative values before and after the meter installation date.
 
---
 
## `solaredge_export_power.py` — Power Export
 
Fetches 15-minute power readings (Watt) from the SolarEdge API, aggregates them into hourly `mean / min / max` values, and writes CSV files compatible with `homeassistant-statistics`.
 
This fills the historical **Stromquellen** (Power Sources) graph in the Home Assistant Energy Dashboard.
 
### Grid Sensor Modes
 
SolarEdge uses a single meter for both grid feed-in and grid consumption, distinguished by sign. Home Assistant supports three configurations:
 
| `GRID_MODE` | Feed-in | Consumption | HA Dashboard setting |
|---|---|---|---|
| `combined_inverted` | positive | negative | *Invertiert* |
| `combined_standard` | negative | positive | *Standard* |
| `split` | separate sensor | separate sensor | *Zwei Sensoren* |
 
### Configuration
 
| Parameter | Default | Description |
|---|---|---|
| `SITE_ID` | `"2183551"` | Your SolarEdge Site ID |
| `API_KEY` | `"DEIN_API_KEY"` | Your SolarEdge API Key |
| `START_DATETIME` | `datetime(2021, 4, 1, 0, 0)` | Start of historical data |
| `END_DATETIME` | `datetime(2024, 12, 31, 23, 59)` | End of historical data |
| `TIMEZONE` | `"Europe/Berlin"` | Timezone of your HA instance |
| `OUTPUT_DIR` | `"."` | Directory to write CSV files to |
| `DRY_RUN` | `True` | Preview mode |
| `GRID_MODE` | `"combined_inverted"` | Grid sensor mode (see above) |
| `STATISTIC_IDS` | see below | HA entity IDs for each output |
 
#### Statistic IDs
 
```python
STATISTIC_IDS = {
    "Production":    "sensor.solaredge_i1_ac_power",          # PV power (W)
    "grid_combined": "sensor.solaredge_i1_m1_ac_power",       # combined grid sensor
    "grid_feedin":   "sensor.solaredge_i1_m1_ac_power_feedin",    # split mode only
    "grid_purchased":"sensor.solaredge_i1_m1_ac_power_purchased",  # split mode only
}
```
 
### Command Line Usage
 
```bash
python solaredge_export_power.py \
  --api YOUR_API_KEY \
  --start "01.04.2021 00:00" \
  --end "27.05.2026 08:45" \
  --gridmode combined_inverted \
  --dryrun false
```
 
| Argument | Description |
|---|---|
| `--api` / `--API` | SolarEdge API Key |
| `--site` / `--Site` | SolarEdge Site ID |
| `--start` / `--Start` | Start datetime `DD.MM.YYYY HH:MM` |
| `--end` / `--End` | End datetime `DD.MM.YYYY HH:MM` |
| `--gridmode` / `--GridMode` | `combined_standard`, `combined_inverted`, `split` |
| `--dryrun` / `--DryRun` | `true` / `false` |
 
### Output
 
```
se_power_production_ha_statistics.csv
se_power_grid_combined_ha_statistics.csv   # combined_standard or combined_inverted
se_power_grid_feedin_ha_statistics.csv     # split mode only
se_power_grid_purchased_ha_statistics.csv  # split mode only
```
 
CSV format (mean/min/max):
```
statistic_id,start,unit,mean,min,max
sensor.solaredge_i1_ac_power,2021-04-07 13:00,W,5817.2,3539.0,7627.3
sensor.solaredge_i1_ac_power,2021-04-07 14:00,W,5131.9,3954.3,5946.3
...
```
 
---
 
## `solaredge_get_offsets.py` — Meter Offset Query
 
Queries the cumulative meter readings for all meters up to a specified point in time. Useful to verify expected meter readings before/after a meter installation or HA reinstall, and to diagnose spikes in the Energy Dashboard after import.
 
### Configuration
 
| Parameter | Default | Description |
|---|---|---|
| `SITE_ID` | `"2183551"` | Your SolarEdge Site ID |
| `API_KEY` | `"DEIN_API_KEY"` | Your SolarEdge API Key |
| `SINCE_DATETIME` | `datetime(2021, 4, 1, 0, 0)` | System commissioning date (start of summation) |
| `UNTIL_DATETIME` | `datetime(2021, 9, 21, 14, 0)` | Point in time to query cumulative readings for |
| `METERS` | `["Production", "FeedIn", "Purchased"]` | Meters to query |
 
### Command Line Usage
 
```bash
python solaredge_get_offsets.py \
  --api YOUR_API_KEY \
  --since "01.04.2021 00:00" \
  --until "21.09.2021 14:00"
```
 
| Argument | Description |
|---|---|
| `--api` / `--API` | SolarEdge API Key |
| `--site` / `--Site` | SolarEdge Site ID |
| `--since` / `--Since` | Start of summation `DD.MM.YYYY HH:MM` |
| `--until` / `--Until` | End of summation `DD.MM.YYYY HH:MM` |
 
### Example Output
 
```
============================================================
CUMULATIVE METER READINGS until 2021-09-21 14:00
============================================================
  Production  :      8,111.22 kWh
  FeedIn      :          0.00 kWh
  Purchased   :          0.00 kWh
```
 
---
 
## Typical Workflow
 
```
1. Check your HA Energy Dashboard sensor IDs
   Settings → Developer Tools → Statistics → search "solaredge"
 
2. (Optional) Check cumulative meter readings at your meter installation date
   python solaredge_get_offsets.py --api KEY --since "01.04.2021 00:00" --until "21.09.2021 14:00"
 
3. Run energy export in dry-run mode to preview data
   python solaredge_to_ha_statistics.py --api KEY --start "01.04.2021 00:00" --end "27.05.2026 08:45" --dryrun true
 
4. Export energy CSVs
   python solaredge_to_ha_statistics.py --api KEY --start "01.04.2021 00:00" --end "27.05.2026 08:45" --dryrun false
 
5. Copy CSVs to /config/ on your HA instance
 
6. Import via Developer Tools → Actions → import_statistics.import_from_file
 
7. Check Energy Dashboard — fix any spikes via Developer Tools → Statistics → Adjust statistics
 
8. (Optional) Run power export for the Stromquellen graph
   python solaredge_export_power.py --api KEY --start "01.04.2021 00:00" --end "27.05.2026 08:45" --gridmode combined_inverted --dryrun false
```
 
---
 
## Notes
 
- The SolarEdge API is limited to **300 requests per day** per API key. For a 4-year import at hourly resolution this requires ~50 requests per script run, well within the limit.
- The `homeassistant-statistics` integration requires timestamps to be **full hours** — quarter-hour resolution from the API must be aggregated before import (the energy script uses `HOUR` resolution by default for this reason).
- Energy CSV files use the **delta** format — each value represents the energy produced/consumed in that one-hour period, not a cumulative total.
- Power CSV files use the **mean/min/max** format — values are aggregated from 15-minute API readings into hourly statistics.
- The `Consumption` meter is only available if a physical SolarEdge revenue-grade meter is installed. Without it, Home Assistant calculates consumption automatically from Production + Purchased − FeedIn.
---
 
## License
 
MIT

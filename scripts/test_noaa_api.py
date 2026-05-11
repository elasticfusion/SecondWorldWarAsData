#!/usr/bin/env python3
"""Test NOAA CDO API — check key validity and 1944 Normandy data availability."""

import sys
import yaml
import requests

config = yaml.safe_load(open("config.yaml"))
token = config.get("api", {}).get("noaa_api_token", "")
if not token:
    print("Set api.noaa_api_token in config.yaml")
    sys.exit(1)

BASE = "https://www.ncei.noaa.gov/cdo-web/api/v2"
HEADERS = {"token": token}

# 1. Verify key works
print("=== 1. Verify API key ===")
resp = requests.get(
    f"{BASE}/datasets", headers=HEADERS, params={"limit": "3"}, timeout=30
)
print(f"Status: {resp.status_code}")
if resp.status_code != 200:
    print(f"Error: {resp.text[:200]}")
    sys.exit(1)
datasets = resp.json().get("results", [])
print(f"Datasets returned: {len(datasets)}")
for d in datasets:
    print(f"  {d['id']}: {d['name']} ({d['mindate']} to {d['maxdate']})")

# 2. Check GHCND dataset covers 1944
print("\n=== 2. GHCND dataset info ===")
resp = requests.get(f"{BASE}/datasets/GHCND", headers=HEADERS, timeout=30)
d = resp.json()
print(f"  Name: {d.get('name')}")
print(f"  Range: {d.get('mindate')} to {d.get('maxdate')}")

# 3. Find stations near Normandy (49.2, -0.9) with 1944 data
print("\n=== 3. Stations near Normandy (June 1944) ===")
resp = requests.get(
    f"{BASE}/stations",
    headers=HEADERS,
    params={
        "datasetid": "GHCND",
        "extent": "48.5,-1.5,49.5,0.5",
        "startdate": "1944-06-01",
        "enddate": "1944-06-30",
        "limit": "10",
    },
    timeout=30,
)
if resp.status_code == 200:
    stations = resp.json().get("results", [])
    print(f"  Stations found: {len(stations)}")
    for s in stations:
        print(
            f"  {s['id']}: {s['name']} ({s.get('latitude')}, {s.get('longitude')}) coverage={s.get('datacoverage')}"
        )
else:
    print(f"  Error: {resp.status_code} {resp.text[:200]}")

# 4. Try fetching D-Day weather from first station found
if resp.status_code == 200 and stations:
    station_id = stations[0]["id"]
    print(f"\n=== 4. D-Day weather from {station_id} ===")
    resp = requests.get(
        f"{BASE}/data",
        headers=HEADERS,
        params={
            "datasetid": "GHCND",
            "stationid": station_id,
            "startdate": "1944-06-06",
            "enddate": "1944-06-06",
            "units": "metric",
            "limit": "25",
        },
        timeout=30,
    )
    if resp.status_code == 200:
        data = resp.json().get("results", [])
        print(f"  Observations: {len(data)}")
        for obs in data:
            print(f"  {obs['datatype']}: {obs['value']} ({obs.get('attributes', '')})")
    else:
        print(f"  Error: {resp.status_code} {resp.text[:200]}")

#!/usr/bin/env python3
"""Dump Garmin API response structures for endpoint discovery."""

import json
import sys
from garminconnect import Garmin

TOKEN_STORE = "/Users/willemwijnans/.garminconnect"
DATE = "2026-05-03"

client = Garmin()
client.login(tokenstore=TOKEN_STORE)
print(f"Logged in. Dumping endpoints for {DATE}\n")

endpoints = [
    ("get_stats", lambda: client.get_stats(DATE)),
    ("get_max_metrics", lambda: client.get_max_metrics(DATE)),
    ("get_spo2_data", lambda: client.get_spo2_data(DATE)),
    ("get_respiration_data", lambda: client.get_respiration_data(DATE)),
    ("get_body_battery", lambda: client.get_body_battery(DATE)),
    ("get_body_composition", lambda: client.get_body_composition(DATE, DATE)),
    ("get_hrv_data", lambda: client.get_hrv_data(DATE)),
]

for name, call in endpoints:
    print(f"{'='*60}")
    print(f"  {name}({DATE})")
    print(f"{'='*60}")
    try:
        data = call()
        if isinstance(data, dict):
            print(json.dumps(data, indent=2, default=str))
        elif isinstance(data, list):
            print(f"List with {len(data)} items")
            if data:
                first = data[0]
                if isinstance(first, dict):
                    print(f"First item keys: {list(first.keys())}")
                    print(json.dumps(first, indent=2, default=str))
                else:
                    print(f"First item: {first}")
        else:
            print(f"Type: {type(data).__name__}, Value: {data}")
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}")
    print()

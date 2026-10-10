import os
import json
import shutil
from datetime import datetime

# Adjust path for x_engine imports
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from x_engine.s3_fetcher import normalize_market_json
from x_engine.template_compositor import render_random_template

VENDOR_FILE = r"D:\Downloads\economic calendar vendor data.txt"
ARTIFACTS_DIR = r"C:\Users\Lenovo\.gemini\antigravity-ide\brain\75cb6422-75cb-4889-99c9-52460d42e95e"
MEDIA_DIR = os.path.join(os.path.dirname(__file__), "media")
os.makedirs(MEDIA_DIR, exist_ok=True)
os.makedirs(ARTIFACTS_DIR, exist_ok=True)

with open(VENDOR_FILE, "r", encoding="utf-8") as f:
    raw = f.read()

idx = raw.find("{")
data = json.loads(raw[idx:])
all_events = data.get("data", {}).get("events", [])
print(f"Total raw events loaded: {len(all_events)}")

# Filter to get interesting events across different dates/times/countries
# Let's inspect unique events by name
seen = set()
unique_events = []
for ev in all_events:
    name = ev.get("event_name")
    if name and name not in seen:
        seen.add(name)
        unique_events.append(ev)

print(f"Total unique events: {len(unique_events)}")

# Define 3 distinct 5-event sets from the vendor data
# Set A: Labor, Autos, Central Bank, Retail, Jobless
set_a_names = [
    "Employment Change",
    "New Car Registrations YoY",
    "Fed Williams Speech",
    "Retail Sales MoM Prel",
    "Initial Jobless Claims"
]

# Set B: Housing, Fed, Consumer Confidence, Distribution, Sentiment
set_b_names = [
    "New Home Sales",
    "Fed Paulson Speech",
    "GfK Consumer Confidence",
    "CBI Distributive Trades",
    "Michigan Consumer Sentiment Final"
]

# Set C: Energy, Manufacturing PMI, Labor, Fed, Durable Goods
set_c_names = [
    "EIA Crude Oil Stocks Change",
    "S&P Global Manufacturing PMI Flash",
    "Unemployment Rate",
    "Fed Barkin Speech",
    "Durable Goods Orders Ex Transp MoM"
]

def find_events(name_list):
    selected = []
    for name in name_list:
        match = next((e for e in all_events if e.get("event_name") == name), None)
        if match:
            selected.append(match)
        else:
            print(f"Warning: {name} not found directly, searching substring...")
            match = next((e for e in all_events if name.lower() in (e.get("event_name") or "").lower()), None)
            if match:
                selected.append(match)
    return selected

events_a = find_events(set_a_names)
events_b = find_events(set_b_names)
events_c = find_events(set_c_names)

print(f"Set A count: {len(events_a)}")
print(f"Set B count: {len(events_b)}")
print(f"Set C count: {len(events_c)}")

# Fallback if any is missing from all_events
if len(events_a) < 5:
    events_a = unique_events[0:5]
if len(events_b) < 5:
    events_b = unique_events[5:10]
if len(events_c) < 5:
    events_c = unique_events[10:15]

sets = [
    ("Set A - Torn Paper Notepad (Circled Calendar Date)", events_a, "template_2_paper_notepad.jpg", "setA_layout_2_notepad.jpg"),
    ("Set B - Calendar Chart (Circled Notepad Date & 3 Phone Charts)", events_b, "template_4_calendar_chart.jpg", "setB_layout_4_chart.jpg"),
    ("Set C - Split Events & Mobile Phone Mockup", events_c, "template_5_split_events_phone.jpg", "setC_layout_5_split.jpg"),
    ("Set D - Three Mobile Phones 2-Column Events", events_c, "template_1_three_phones.jpg", "setD_layout_1_phones.jpg")
]

results = []
for title, evs, template, filename in sets:
    payload = {
        "title": title,
        "date": evs[0].get("event_date", "2026-09-24"),
        "data": {"events": evs}
    }
    normalized = normalize_market_json(payload)
    out_media_path = os.path.join(MEDIA_DIR, filename)
    res = render_random_template(normalized, out_media_path, template_name=template)
    
    # Copy to artifacts directory for inline display
    out_artifact_path = os.path.join(ARTIFACTS_DIR, filename)
    shutil.copyfile(out_media_path, out_artifact_path)
    
    results.append({
        "set_title": title,
        "template": template,
        "media_path": out_media_path,
        "artifact_path": out_artifact_path,
        "events": [
            f"{e.get('time')} | {e.get('country')} ({e.get('currency')}) - {e.get('event')} [{e.get('impact')}]"
            for e in normalized.get("events", [])
        ]
    })

print("\n--- RESULTS ---")
print(json.dumps(results, indent=2))

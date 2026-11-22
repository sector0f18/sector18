
import pandas as pd
from datetime import datetime
import time
import os
import pytz
import requests 

# ==========================================
# CONFIGURATION 
# ==========================================
# Fetches the key securely from GitHub Secrets (Do NOT hardcode your key here)
API_KEY = os.environ.get("TOMTOM_API_KEY")
if not API_KEY:
    raise ValueError("Error: TOMTOM_API_KEY environment variable is missing.")

MASTER_CSV = "Your_Traffic_Data.csv" # Ensure this matches your uploaded CSV name

# Timezone setup
ist = pytz.timezone('Asia/Kolkata')
ORIGIN_NAME = "Origin Location Name"
ORIGIN_COORD = "LATITUDE,LONGITUDE" # Replace with your actual origin coordinates

LOCATIONS = [
    {"name": "Location 1", "lat": 28.5672, "lon": 77.3219},
    {"name": "Location 2", "lat": 28.5847, "lon": 77.2917}
]

ICON_CATEGORY_MAP = {
    0: "Unknown", 1: "Accident", 2: "Fog", 3: "Dangerous Conditions",
    4: "Rain", 5: "Ice", 6: "Jam", 7: "Lane Closed", 8: "Road Closed",
    9: "Road Works", 10: "Wind", 11: "Flooding", 14: "Broken Down Vehicle"
}
MAGNITUDE_MAP = {
    0: "Unknown", 1: "Minor", 2: "Moderate", 3: "Major", 4: "Undefined/Closed"
}

# ==========================================
# SINGLE RUN EXECUTION (Triggered by GitHub)
# ==========================================
print(f"Starting single-run Traffic Extraction for {len(LOCATIONS)} locations...")

current_time = datetime.now(ist).strftime("%Y-%m-%d %H:%M:%S")
batch_records = []

print(f"--- Running data collection at {current_time} ---")

for loc in LOCATIONS:
    lat, lon = loc["lat"], loc["lon"]
    record = {
        "Timestamp": current_time,
        "Location": loc["name"],
        "Latitude": lat,
        "Longitude": lon,
        "Origin_Reference": ORIGIN_NAME
    }

    # 1. TOMTOM TRAFFIC FLOW SEGMENT API
    flow_url = f"https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/10/json?key={API_KEY}&point={lat},{lon}&unit=KMPH&openLr=false"
    try:
        flow_resp = requests.get(flow_url, timeout=10).json()
        flow = flow_resp.get("flowSegmentData", {})
        curr_speed = flow.get("currentSpeed")
        free_speed = flow.get("freeFlowSpeed")
        curr_tt = flow.get("currentTravelTime", 0)
        free_tt = flow.get("freeFlowTravelTime", 0)
        coords_list = flow.get("coordinates", {}).get("coordinate", [])
        
        record.update({
            "Flow_Functional_Road_Class": flow.get("frc"),
            "Flow_Current_Speed_kmh": curr_speed,
            "Flow_Free_Flow_Speed_kmh": free_speed,
            "Flow_Speed_Ratio": round(curr_speed / free_speed, 3) if curr_speed and free_speed else None,
            "Flow_Current_Travel_Time_sec": curr_tt,
            "Flow_Free_Flow_Travel_Time_sec": free_tt,
            "Flow_Segment_Delay_sec": curr_tt - free_tt,
            "Flow_Confidence_Score": flow.get("confidence"),
            "Flow_Road_Closure": flow.get("roadClosure"),
            "Flow_Segment_Geometry_Points": len(coords_list)
        })
    except Exception as e:
        print(f"[Flow Error] {loc['name']}: {e}")

    # 2. TOMTOM ROUTING API
    route_url = f"https://api.tomtom.com/routing/1/calculateRoute/{ORIGIN_COORD}:{lat},{lon}/json?key={API_KEY}&computeTravelTimeFor=all&routeType=fastest&traffic=true"
    try:
        route_resp = requests.get(route_url, timeout=10).json()
        if "routes" in route_resp and len(route_resp["routes"]) > 0:
            summary = route_resp["routes"][0].get("summary", {})
            record.update({
                "Route_Distance_km": round(summary.get("lengthInMeters", 0) / 1000, 3),
                "Route_Live_Travel_Time_sec": summary.get("travelTimeInSeconds"),
                "Route_No_Traffic_Time_sec": summary.get("noTrafficTravelTimeInSeconds"),
                "Route_Historic_Traffic_Time_sec": summary.get("historicTrafficTravelTimeInSeconds"),
                "Route_Live_Incidents_Time_sec": summary.get("liveTrafficIncidentsTravelTimeInSeconds"),
                "Route_Traffic_Delay_sec": summary.get("trafficDelayInSeconds"),
                "Route_Congested_Distance_m": summary.get("trafficLengthInMeters"),
                "Route_Departure_Time": summary.get("departureTime"),
                "Route_Arrival_Time": summary.get("arrivalTime")
            })
    except Exception as e:
        print(f"[Routing Error] {loc['name']}: {e}")

    # 3. TOMTOM INCIDENT DETAILS v5 API
    bbox = f"{lon - 0.007},{lat - 0.007},{lon + 0.007},{lat + 0.007}"
    fields = "{incidents{type,properties{id,iconCategory,magnitudeOfDelay,events{description,code},startTime,endTime,from,to,length,delay,roadNumbers,timeValidity,probabilityOfOccurrence,numberOfReports}}}"
    incident_url = f"https://api.tomtom.com/traffic/services/5/incidentDetails?key={API_KEY}&bbox={bbox}&fields={fields}&language=en-GB&t=1111"
    
    try:
        inc_resp = requests.get(incident_url, timeout=10).json()
        incidents = inc_resp.get("incidents", [])
        categories, magnitudes, descriptions = [], [], []
        from_streets, to_streets, roads = [], [], []
        total_inc_delay = 0
        total_inc_length = 0
        
        for inc in incidents:
            props = inc.get("properties", {})
            cat_code = props.get("iconCategory", 0)
            mag_code = props.get("magnitudeOfDelay", 0)
            categories.append(ICON_CATEGORY_MAP.get(cat_code, str(cat_code)))
            magnitudes.append(MAGNITUDE_MAP.get(mag_code, str(mag_code)))
            
            if props.get("from"): from_streets.append(props["from"])
            if props.get("to"): to_streets.append(props["to"])
            if props.get("roadNumbers"): roads.extend(props["roadNumbers"])
            total_inc_delay += props.get("delay") or 0
            total_inc_length += props.get("length") or 0
            
            for ev in props.get("events", []):
                if ev.get("description"):
                    descriptions.append(ev["description"])
                    
        record.update({
            "Incident_Count": len(incidents),
            "Incident_Categories": " | ".join(categories) if categories else "None",
            "Incident_Magnitudes": " | ".join(magnitudes) if magnitudes else "None",
            "Incident_Descriptions": " | ".join(set(descriptions)) if descriptions else "None",
            "Incident_Total_Delay_sec": total_inc_delay,
            "Incident_Total_Length_m": total_inc_length,
            "Incident_From_Locations": " | ".join(from_streets) if from_streets else "None",
            "Incident_To_Locations": " | ".join(to_streets) if to_streets else "None",
            "Incident_Road_Numbers": ", ".join(set(roads)) if roads else "None"
        })
    except Exception as e:
        print(f"[Incident Error] {loc['name']}: {e}")

    batch_records.append(record)
    time.sleep(0.5) # Short sleep to prevent hitting per-second API limits
    
if batch_records:
    df_batch = pd.DataFrame(batch_records)
    df_batch.to_csv(MASTER_CSV, mode='a', header=not os.path.exists(MASTER_CSV), index=False)
    print(f"Extraction complete. {len(batch_records)} rows appended to {MASTER_CSV}.")

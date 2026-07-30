from flask import Flask, render_template, jsonify, request
import pandas as pd
import requests
import numpy as np
import time
import json
from shapely.geometry import Point, Polygon, MultiPolygon

app = Flask(__name__)

# --- CACHED GLOBALS ---
_cache = {}

def load_demand_data():
    if 'demand' in _cache:
        return _cache['demand']
    try:
        df = pd.read_csv('bcn_vehicles_2025.csv', sep=None, engine='python')
        df_electric = df[df['Tipus_Propulsio'].astype(str).str.contains('Elèctric|Electric', case=False, na=False)]
        df_demand = df_electric.groupby(['Codi_Barri', 'Nom_Barri'])['Nombre'].sum().reset_index()
        df_demand['Codi_Barri'] = df_demand['Codi_Barri'].astype(str).str.zfill(2)
        df_demand.columns = ['Neighborhood_ID', 'Neighborhood_Name', 'EV_Count']
        _cache['demand'] = df_demand
        return df_demand
    except Exception as e:
        return pd.DataFrame()

def fetch_charging_stations(retries=3, delay=3):
    if 'stations' in _cache:
        return _cache['stations']
    overpass_url = "https://overpass-api.de/api/interpreter"
    overpass_query = """
    [out:json][timeout:25];
    area[name="Barcelona"]["admin_level"="8"]->.searchArea;
    (node["amenity"="charging_station"](area.searchArea););
    out center;
    """
    headers = {
        'User-Agent': 'SmartCityOptimizationApp/1.0',
        'Accept': 'application/json',
        'Referer': 'https://ev-charging-analyzer.onrender.com'
    }
    for attempt in range(retries):
        try:
            response = requests.post(overpass_url, data={'data': overpass_query}, headers=headers, timeout=30)
            response.raise_for_status()
            stations = [(el['lat'], el['lon']) for el in response.json().get('elements', []) if 'lat' in el and 'lon' in el]
            _cache['stations'] = stations
            return stations
        except Exception:
            if attempt < retries - 1:
                time.sleep(delay)
    return []

def load_geojson():
    if 'geojson' in _cache:
        return _cache['geojson']
    url = "https://raw.githubusercontent.com/martgnz/bcn-geodata/master/barris/barris.geojson"
    data = requests.get(url).json()
    _cache['geojson'] = data
    return data

def parse_geometry(geom_dict):
    g_type = geom_dict['type']
    coords = geom_dict['coordinates']
    try:
        if g_type == 'Polygon':
            return Polygon(coords[0])
        elif g_type == 'MultiPolygon':
            return MultiPolygon([Polygon(poly[0]) for poly in coords])
    except Exception:
        pass
    return None

def compute_deficits(num_proposals=8, decay_scale_km=1.0):
    df_demand = load_demand_data()
    stations = fetch_charging_stations()
    bcn_geojson = load_geojson()

    if df_demand.empty:
        return [], [], {}, []

    props = bcn_geojson['features'][0]['properties']
    geo_id_key = next((k for k in props.keys() if k.upper() in ['BARRI', 'C_BARRI', 'CODI_BARRI', 'ID_BARRI']), None)
    geo_name_key = next((k for k in props.keys() if k.upper() in ['NOM', 'N_BARRI', 'LITERAL', 'NOM_BARRI']), None)

    demand_dict = dict(zip(df_demand['Neighborhood_ID'], df_demand['EV_Count']))
    name_dict = dict(zip(df_demand['Neighborhood_ID'], df_demand['Neighborhood_Name']))
    station_points = [Point(lon, lat) for lat, lon in stations]

    neighborhood_deficits = []
    geojson_copy = json.loads(json.dumps(bcn_geojson))

    for feature in geojson_copy['features']:
        raw_id = str(feature['properties'].get(geo_id_key, '')).zfill(2)
        ev_count = demand_dict.get(raw_id, 0)
        name = name_dict.get(raw_id, feature['properties'].get(geo_name_key, 'Unknown'))
        feature['properties']['EV_Count'] = int(ev_count)
        feature['properties']['Display_Name'] = name

        if 'geometry' in feature and feature['geometry']:
            poly = parse_geometry(feature['geometry'])
            if poly and poly.is_valid:
                centroid = poly.centroid
                centroid_pt = Point(centroid.x, centroid.y)
                decayed_supply = sum(
                    np.exp(-centroid_pt.distance(s_pt) * 111.0 / decay_scale_km)
                    for s_pt in station_points
                )
                deficit_score = ev_count / (decayed_supply + 1.0)
                neighborhood_deficits.append({
                    'id': raw_id,
                    'name': name,
                    'ev_count': int(ev_count),
                    'decayed_supply': round(float(decayed_supply), 2),
                    'deficit_score': round(float(deficit_score), 2),
                    'coord': [round(centroid.y, 6), round(centroid.x, 6)]
                })

    df_deficits = pd.DataFrame(neighborhood_deficits).sort_values('deficit_score', ascending=False)
    top_gaps = df_deficits.head(num_proposals).to_dict('records')

    metrics = {
        'total_evs': int(df_demand['EV_Count'].sum()),
        'total_stations': len(stations),
        'num_proposals': num_proposals,
        'total_neighborhoods': len(neighborhood_deficits)
    }

    return top_gaps, stations, metrics, geojson_copy

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/data')
def api_data():
    num_proposals = int(request.args.get('hubs', 8))
    decay_scale = float(request.args.get('decay', 1.0))
    top_gaps, stations, metrics, geojson = compute_deficits(num_proposals, decay_scale)
    return jsonify({
        'top_gaps': top_gaps,
        'stations': stations,
        'metrics': metrics,
        'geojson': geojson
    })

if __name__ == '__main__':
    app.run(debug=True)

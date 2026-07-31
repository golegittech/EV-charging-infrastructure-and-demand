from flask import Flask, render_template, jsonify, request
import pandas as pd
import requests
import numpy as np
import json
from shapely.geometry import Point, Polygon, MultiPolygon

app = Flask(__name__)
_cache = {}

def load_demand_data():
    if 'demand' in _cache:
        return _cache['demand']
    df = pd.read_csv('bcn_vehicles_2025.csv', sep=None, engine='python')
    df_electric = df[df['Tipus_Propulsio'].astype(str).str.contains('Elèctric|Electric', case=False, na=False)]
    df_demand = df_electric.groupby(['Codi_Barri', 'Nom_Barri'])['Nombre'].sum().reset_index()
    df_demand['Codi_Barri'] = df_demand['Codi_Barri'].astype(str).str.zfill(2)
    df_demand.columns = ['Neighborhood_ID', 'Neighborhood_Name', 'EV_Count']
    _cache['demand'] = df_demand
    return df_demand

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

def compute_deficits(stations, num_proposals=8):
    df_demand = load_demand_data()
    bcn_geojson = load_geojson()

    props = bcn_geojson['features'][0]['properties']
    geo_id_key = next((k for k in props.keys() if k.upper() in ['BARRI', 'C_BARRI', 'CODI_BARRI', 'ID_BARRI']), None)
    geo_name_key = next((k for k in props.keys() if k.upper() in ['NOM', 'N_BARRI', 'LITERAL', 'NOM_BARRI']), None)

    demand_dict = dict(zip(df_demand['Neighborhood_ID'], df_demand['EV_Count']))
    name_dict = dict(zip(df_demand['Neighborhood_ID'], df_demand['Neighborhood_Name']))

    # Use Point(lon, lat) exactly like your original
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
                # Exact same logic as your original: count stations inside polygon
                station_count = sum(1 for pt in station_points if poly.contains(pt))
                deficit_score = ev_count / (station_count + 1)
                centroid = poly.centroid
                neighborhood_deficits.append({
                    'id': raw_id,
                    'name': name,
                    'ev_count': int(ev_count),
                    'stations': station_count,
                    'deficit_score': round(float(deficit_score), 1),
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

    return top_gaps, metrics, geojson_copy

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/data', methods=['POST'])
def api_data():
    body = request.get_json()
    num_proposals = int(body.get('hubs', 8))
    stations = [(s['lat'], s['lon']) for s in body.get('stations', [])]
    top_gaps, metrics, geojson = compute_deficits(stations, num_proposals)
    return jsonify({'top_gaps': top_gaps, 'metrics': metrics, 'geojson': geojson})

if __name__ == '__main__':
    app.run(debug=True)

import streamlit as st
import pandas as pd
import folium
import requests
import branca.colormap as cm
import numpy as np
import time
from shapely.geometry import Point, Polygon, MultiPolygon
from streamlit_folium import st_folium

# --- PAGE CONFIGURATION ---
st.set_page_config(
    page_title="Barcelona EV Infrastructure Gap Analyzer",
    page_icon="⚡",
    layout="wide"
)

st.title("⚡ Barcelona Smart City: EV Infrastructure Gap & Distance-Decay Analyzer")
st.markdown("""
This application analyzes municipal electric vehicle registration data against live OpenStreetMap charging infrastructure. 
It applies **Distance-Decay Buffering** (where existing chargers lose coverage weight over distance) to isolate true infrastructure deserts 
and propose optimal high-priority expansion hubs.
""")

# --- CACHED DATA LOADERS ---

@st.cache_data
def load_robust_demand_data():
    """Loads CSV and groups by the exact Neighborhood ID."""
    try:
        df = pd.read_csv('bcn_vehicles_2025.csv', sep=None, engine='python')
        df_electric = df[df['Tipus_Propulsio'].astype(str).str.contains('Elèctric|Electric', case=False, na=False)]
        df_demand = df_electric.groupby(['Codi_Barri', 'Nom_Barri'])['Nombre'].sum().reset_index()
        df_demand['Codi_Barri'] = df_demand['Codi_Barri'].astype(str).str.zfill(2)
        df_demand.columns = ['Neighborhood_ID', 'Neighborhood_Name', 'EV_Count']
        return df_demand
    except Exception as e:
        st.error(f"Error loading 'bcn_vehicles_2025.csv': {e}")
        return pd.DataFrame()

@st.cache_data
def fetch_osm_charging_stations_with_retry(retries=3, delay=3):
    """Fetches live charging station coordinates securely with built-in retry logic."""
    overpass_url = "https://overpass-api.de/api/interpreter"
    overpass_query = """
    [out:json][timeout:25];
    area[name="Barcelona"]["admin_level"="8"]->.searchArea;
    (node["amenity"="charging_station"](area.searchArea););
    out center;
    """
    headers = {
        'User-Agent': 'SmartCityOptimizationApp/1.0 (ezikesomtosamuel@workspace.com)',
        'Accept': 'application/json',
        'Referer': 'http://localhost'
    }
    
    for attempt in range(retries):
        try:
            response = requests.post(overpass_url, data={'data': overpass_query}, headers=headers, timeout=30)
            response.raise_for_status()
            stations = [(el['lat'], el['lon']) for el in response.json().get('elements', []) if 'lat' in el and 'lon' in el]
            return stations
        except Exception:
            if attempt < retries - 1:
                time.sleep(delay)
            else:
                return []
    return []

@st.cache_data
def load_bcn_geojson():
    """Fetches Barcelona neighborhood boundaries GeoJSON."""
    geojson_url = "https://raw.githubusercontent.com/martgnz/bcn-geodata/master/barris/barris.geojson"
    return requests.get(geojson_url).json()

def parse_geometry(geom_dict):
    """Converts GeoJSON geometry dictionary into a Shapely geometry object."""
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

# --- SIDEBAR CONTROLS ---
st.sidebar.header("Configuration Panel")
num_proposals = st.sidebar.slider("Number of AI Proposed Hubs", min_value=3, max_value=15, value=8, step=1)
decay_scale_km = st.sidebar.slider("Distance-Decay Scale (km)", min_value=0.5, max_value=3.0, value=1.0, step=0.25,
                                   help="Higher values mean existing chargers provide influence over longer walking/driving distances.")

with st.spinner("Initializing pipeline, fetching live OSM feeds, and calculating distance-decay metrics..."):
    df_demand = load_robust_demand_data()
    stations = fetch_osm_charging_stations_with_retry()
    bcn_geojson = load_bcn_geojson()

if df_demand.empty:
    st.warning("Please ensure 'bcn_vehicles_2025.csv' is in the working directory.")
else:
    # --- PROCESSING: DISTANCE-DECAY DEFICIT SCORING ---
    props = bcn_geojson['features'][0]['properties']
    geo_id_key = next((k for k in props.keys() if k.upper() in ['BARRI', 'C_BARRI', 'CODI_BARRI', 'ID_BARRI']), None)
    geo_name_key = next((k for k in props.keys() if k.upper() in ['NOM', 'N_BARRI', 'LITERAL', 'NOM_BARRI']), None)
    
    demand_dict = dict(zip(df_demand['Neighborhood_ID'], df_demand['EV_Count']))
    name_dict = dict(zip(df_demand['Neighborhood_ID'], df_demand['Neighborhood_Name']))
    
    station_points = [Point(lon, lat) for lat, lon in stations]
    neighborhood_deficits = []
    
    for feature in bcn_geojson['features']:
        raw_id = str(feature['properties'].get(geo_id_key, '')).zfill(2)
        ev_count = demand_dict.get(raw_id, 0)
        name = name_dict.get(raw_id, feature['properties'].get(geo_name_key, 'Unknown'))
        feature['properties']['EV_Count'] = ev_count
        feature['properties']['Display_Name'] = name
        
        if 'geometry' in feature and feature['geometry']:
            poly = parse_geometry(feature['geometry'])
            if poly and poly.is_valid:
                centroid = poly.centroid
                centroid_pt = Point(centroid.x, centroid.y) # lon, lat
                
                # Distance-Decay Supply Calculation:
                # Instead of a hard binary count, each station contributes a decaying weight 
                # based on its geographic distance (in approximate degrees, scaled by decay factor)
                decayed_supply = 0.0
                for s_pt in station_points:
                    # Approximate conversion: 1 degree ~ 111 km
                    dist_km = centroid_pt.distance(s_pt) * 111.0
                    # Exponential decay formula: weight = exp(-dist / scale)
                    weight = np.exp(-dist_km / decay_scale_km)
                    decayed_supply += weight
                
                # Deficit Score = Demand / (Decayed Supply + 1 baseline offset)
                deficit_score = ev_count / (decayed_supply + 1.0)
                
                neighborhood_deficits.append({
                    'id': raw_id,
                    'name': name,
                    'ev_count': ev_count,
                    'decayed_supply': decayed_supply,
                    'deficit_score': deficit_score,
                    'coord': [centroid.y, centroid.x]
                })

    df_deficits = pd.DataFrame(neighborhood_deficits)
    df_deficits = df_deficits.sort_values(by='deficit_score', ascending=False)
    top_gap_areas = df_deficits.head(num_proposals)
    optimal_coords = top_gap_areas['coord'].tolist()

    # --- METRICS ROW ---
    col1, col2, col3 = st.columns(3)
    col1.metric("Total Registered EVs", f"{df_demand['EV_Count'].sum():,}")
    col2.metric("Live OSM Chargers Located", len(stations))
    col3.metric("Top Gap Hubs Proposed", num_proposals)

    # --- MAP GENERATION ---
    m = folium.Map(location=[41.3874, 2.1686], zoom_start=12, tiles='CartoDB Positron')
    
    # Layer 1: Demand Heatmap Colormap
    colormap = cm.LinearColormap(colors=['#ffeda0', '#feb24c', '#f03b20'], vmin=df_demand['EV_Count'].min(), vmax=df_demand['EV_Count'].max())
    colormap.caption = 'Registered Electric Vehicles'
    m.add_child(colormap)
    
    demand_group = folium.FeatureGroup(name='Actual EV Demand Heatmap', show=True)
    folium.GeoJson(
        bcn_geojson,
        style_function=lambda feature: {
            'fillColor': colormap(feature['properties'].get('EV_Count', 0)),
            'color': 'black',
            'weight': 1,
            'fillOpacity': 0.7,
            'opacity': 0.2
        },
        tooltip=folium.features.GeoJsonTooltip(
            fields=['Display_Name', 'EV_Count'],
            aliases=['Neighborhood:', 'Registered EVs:'],
            style="background-color: white; color: #333; font-family: arial; font-size: 13px; padding: 8px; border-radius: 4px;"
        )
    ).add_to(demand_group)
    demand_group.add_to(m)
    
    # Layer 2: Current Infrastructure
    station_group = folium.FeatureGroup(name="Current Charging Stations", show=True) 
    for lat, lon in stations:
        folium.CircleMarker(
            location=[lat, lon],
            radius=3,
            color="#006400",
            fill=True,
            fill_color="#32CD32",
            fill_opacity=0.9,
            tooltip="Existing EV Charger"
        ).add_to(station_group)
    station_group.add_to(m)

    # Layer 3: Distance-Decay Deficit Gap Hubs
    gap_group = folium.FeatureGroup(name="AI-Proposed Infrastructure Gaps", show=True)
    for idx, row in top_gap_areas.iterrows():
        lat, lon = row['coord']
        folium.CircleMarker(
            location=[lat, lon],
            radius=10,
            color="#00008B",
            weight=2.5,
            fill=True,
            fill_color="#0000FF",
            fill_opacity=1.0,
            tooltip=f"<b>{row['name']}</b><br>EVs: {row['ev_count']}<br>Decayed Supply: {row['decayed_supply']:.2f}<br>Deficit Score: {row['deficit_score']:.1f}"
        ).add_to(gap_group)
    gap_group.add_to(m)
    
    folium.LayerControl(collapsed=False).add_to(m)

    # Render Map in Streamlit
    st.subheader("Interactive Spatial Infrastructure Proposal Map")
    st_folium(m, width=1250, height=600)

    # --- DATAFRAME PREVIEW ---
    st.subheader("📋 Top Priority Neighborhood Expansion Targets")
    display_df = top_gap_areas[['name', 'ev_count', 'decayed_supply', 'deficit_score']].copy()
    display_df.columns = ['Neighborhood', 'Registered EVs', 'Decayed Supply Index', 'Infrastructure Deficit Score']
    display_df.reset_index(drop=True, inplace=True)
    st.dataframe(display_df, use_container_width=True)
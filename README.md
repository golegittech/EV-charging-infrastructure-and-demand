# Barcelona EV Infrastructure Gap Analyzer

A Flask web app analyzing EV charging infrastructure gaps using live OpenStreetMap data and distance-decay buffering.

## Project Structure

```
ev-app/
├── app.py                  # Flask backend
├── requirements.txt        # Python dependencies
├── render.yaml             # Render deployment config
├── bcn_vehicles_2025.csv   # ← ADD THIS FILE (your CSV data)
└── templates/
    └── index.html          # Frontend UI
```

## ⚠️ Important: Add Your CSV

Place your `bcn_vehicles_2025.csv` file in the root of this project before deploying.

## Local Development

```bash
pip install -r requirements.txt
python app.py
# Visit http://localhost:5000
```

## Deploy to Render

1. Push this entire folder to your GitHub repo
2. Go to render.com → New + → Web Service
3. Connect your GitHub repo
4. Render auto-detects render.yaml
5. Click Deploy

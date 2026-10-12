"""Build the Tanjong Pagar golden-case Silver inputs (buildings + weather_hourly) for the CEA transforms.

Buildings: OpenStreetMap footprints (ODbL, (c) OpenStreetMap contributors) around Tanjong Pagar Plaza,
reprojected to EPSG:3414, shaped like a Silver ``buildings`` table.
Weather: CEA's bundled Singapore-Changi_1990_2010_TMY.epw, shaped like Silver ``weather_hourly``.

    python rewire/tools/make_fixture.py rewire/cea.building_demand/golden/tanjong_pagar/inputs [--osm cached.json]
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

import geopandas as gpd
from shapely.geometry import Polygon

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cea_spine.weather import silver_from_epw

BBOX = (1.2755, 103.8420, 1.2780, 103.8450)  # south, west, north, east
QUERY = f'[out:json][timeout:60];way["building"]({",".join(map(str, BBOX))});out geom tags;'
# OSM tags say building=yes/public for these; give them the Silver use_type a planner would.
USE_OVERRIDES = {43755568: "RETAIL",     # 100AM mall podium
                 171975661: "RETAIL",    # Tanjong Pagar Plaza shops
                 171975666: "OTHER",     # Tanjong Pagar Market and Food Centre
                 172023450: "HOTEL"}     # Orchid Hotel
OSM_TO_SILVER_USE = {"residential": "RESIDENTIAL", "apartments": "RESIDENTIAL", "office": "OFFICE",
                     "commercial": "OFFICE", "retail": "RETAIL", "hotel": "HOTEL", "school": "SCHOOL",
                     "hospital": "HOSPITAL", "industrial": "INDUSTRIAL"}
REPO = Path(__file__).resolve().parents[2]
EPW = REPO / "cea" / "databases" / "weather" / "Singapore-Changi_1990_2010_TMY.epw"


def fetch_osm() -> dict:
    req = urllib.request.Request("https://overpass-api.de/api/interpreter",
                                 data=urllib.parse.urlencode({"data": QUERY}).encode(),
                                 headers={"User-Agent": "sg-urban-spine-cea-fixture/0.1"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def buildings_from_osm(osm: dict) -> gpd.GeoDataFrame:
    rows = []
    for e in osm["elements"]:
        if e.get("type") != "way" or len(e.get("geometry", [])) < 4:
            continue
        tags = e.get("tags", {})
        height, storeys = _num(tags.get("height")), _num(tags.get("building:levels"))
        rows.append({
            "building_id": f"osm_w{e['id']}",
            "height_m": height if height and height > 0 else None,
            "storeys": int(storeys) if storeys else None,
            "use_type": USE_OVERRIDES.get(e["id"], OSM_TO_SILVER_USE.get(tags.get("building"), "OTHER")),
            "year_built": None,
            "source_version": "osm-overpass-2026-10-12",
            "geometry": Polygon([(p["lon"], p["lat"]) for p in e["geometry"]]),
        })
    g = gpd.GeoDataFrame(rows, geometry="geometry", crs=4326).to_crs(3414)
    g = g[g.geometry.is_valid].sort_values("building_id").reset_index(drop=True)
    g["storeys"] = g["storeys"].astype("Int64")
    g["year_built"] = g["year_built"].astype("Int64")
    g["height_m"] = g["height_m"].astype("float64")
    return g


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument("--osm", type=Path, help="cached Overpass JSON (fetched and saved here if missing)")
    a = ap.parse_args()
    if a.osm and a.osm.exists():
        osm = json.loads(a.osm.read_text())
    else:
        osm = fetch_osm()
        if a.osm:
            a.osm.write_text(json.dumps(osm))
    a.out.mkdir(parents=True, exist_ok=True)
    b = buildings_from_osm(osm)
    b.to_parquet(a.out / "buildings.parquet", index=False)
    w = silver_from_epw(EPW.read_text(), "changi_tmy", "cea-bundled Singapore-Changi_1990_2010_TMY.epw")
    w.to_parquet(a.out / "weather.parquet", index=False)
    print(f"{len(b)} buildings, {len(w)} weather rows -> {a.out}")


if __name__ == "__main__":
    main()

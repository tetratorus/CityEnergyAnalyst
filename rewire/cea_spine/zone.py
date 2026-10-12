"""Silver ``buildings`` -> CEA ``zone.shp`` / ``surroundings.shp`` attribute tables (CEA 4 schema)."""
from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd

# Silver use_type vocabulary (spec/silver/tables.yaml) -> CEA SG archetype use types (cea/databases/SG/ARCHETYPES/USE)
SILVER_TO_CEA_USE = {
    "RESIDENTIAL": "MULTI_RES",
    "OFFICE": "OFFICE",
    "RETAIL": "RETAIL",
    "INDUSTRIAL": "INDUSTRIAL",
    "HOTEL": "HOTEL",
    "SCHOOL": "SCHOOL",
    "HOSPITAL": "HOSPITAL",
}
REFERENCE = "sg-urban-spine silver:buildings"


def _largest_polygon(geom):
    if geom is None or geom.is_empty:
        return None
    if not geom.is_valid:
        geom = geom.buffer(0)
    if geom.geom_type == "Polygon":
        return geom
    polys = [g for g in getattr(geom, "geoms", []) if g.geom_type == "Polygon"]
    return max(polys, key=lambda g: g.area) if polys else None


def _floors_and_height(b: pd.DataFrame, floor_height_m: float) -> tuple[np.ndarray, np.ndarray]:
    h = pd.to_numeric(b.get("height_m"), errors="coerce") if "height_m" in b else pd.Series(np.nan, index=b.index)
    s = pd.to_numeric(b.get("storeys"), errors="coerce") if "storeys" in b else pd.Series(np.nan, index=b.index)
    h = h.where(h > 0)
    s = s.where(s > 0)
    floors = s.fillna((h / floor_height_m).round()).fillna(1).clip(lower=1).astype(int)
    height = h.fillna(floors * floor_height_m).astype(float)
    return floors.to_numpy(), height.to_numpy()


def split_zone_surroundings(buildings: gpd.GeoDataFrame, zone_building_ids, surroundings_radius_m: float):
    """Zone = requested building_ids (all if empty). Surroundings = other buildings within the radius."""
    b = buildings
    if zone_building_ids:
        wanted = set(map(str, zone_building_ids))
        missing = wanted - set(b["building_id"].astype(str))
        if missing:
            raise ValueError(f"zone_building_ids not in silver:buildings: {sorted(missing)}")
        is_zone = b["building_id"].astype(str).isin(wanted)
    else:
        is_zone = pd.Series(True, index=b.index)
    zone = b[is_zone]
    others = b[~is_zone]
    if len(others) and surroundings_radius_m > 0:
        reach = zone.geometry.union_all().buffer(surroundings_radius_m)
        others = others[others.geometry.intersects(reach)]
    else:
        others = others.iloc[0:0]
    return zone, others


def zone_table(zone: gpd.GeoDataFrame, names: dict[str, str], params: dict) -> gpd.GeoDataFrame:
    floors, height = _floors_and_height(zone, params["floor_height_m"])
    use = zone["use_type"].astype("string").str.upper() if "use_type" in zone else pd.Series(pd.NA, index=zone.index)
    cea_use = use.map(SILVER_TO_CEA_USE).fillna(params["default_cea_use_type"])
    year = pd.to_numeric(zone["year_built"], errors="coerce") if "year_built" in zone else pd.Series(np.nan, index=zone.index)
    out = gpd.GeoDataFrame({
        "name": zone["building_id"].astype(str).map(names).to_numpy(),
        "floors_ag": floors,
        "floors_bg": 0,
        "void_deck": 0,
        "height_vd": 0.0,
        "height_ag": height,
        "height_bg": 0.0,
        "year": year.fillna(params["default_year"]).astype(int).to_numpy(),
        "const_type": params["const_type"],
        "use_type1": cea_use.to_numpy(),
        "use_type1r": 1.0,
        "use_type2": "NONE",
        "use_type2r": 0.0,
        "use_type3": "NONE",
        "use_type3r": 0.0,
        "reference": REFERENCE,
    }, geometry=[_largest_polygon(g) for g in zone.geometry], crs=zone.crs)
    return out[out.geometry.notna()].sort_values("name").reset_index(drop=True)


def surroundings_table(others: gpd.GeoDataFrame, params: dict) -> gpd.GeoDataFrame:
    floors, height = _floors_and_height(others, params["floor_height_m"])
    out = gpd.GeoDataFrame({
        "name": [f"S{i:05d}" for i in range(len(others))],
        "floors_ag": floors,
        "height_ag": height,
        "REFERENCE": REFERENCE,
    }, geometry=[_largest_polygon(g) for g in others.geometry], crs=others.crs)
    return out[out.geometry.notna()].reset_index(drop=True)

"""Container side: build a CEA scenario from Silver tables, run CEA's own scripts, read CEA's own results.

Imports CEA, so it runs inside the image built from this repo's Dockerfile (see rewire/Dockerfile).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import yaml

from .ids import bridge_rows
from .weather import epw_from_silver
from .zone import split_zone_surroundings, surroundings_table, zone_table

SCENARIO = "spine"
TEMPLATE_WEATHER = "Singapore-Changi_1990_2010_TMY"

# Same steps, order and parameters as CEA's own Singapore workflow (cea/tests/workflows/sg_cooling.yml).
PREPARE_STEPS = [
    {"script": "database-helper", "parameters": {"databases-path": "SG",
                                                 "databases": ["archetypes", "assemblies", "components"]}},
    {"script": "archetypes-mapper", "parameters": {"input-databases": [
        "comfort", "architecture", "air-conditioning", "internal-loads", "supply", "schedules"]}},
    {"script": "radiation", "parameters": {"neglect-adjacent-buildings": False}},
]
DEMAND_STEPS = PREPARE_STEPS + [{"script": "occupancy"}, {"script": "demand"}]


def solar_steps(params: dict) -> list[dict]:
    return PREPARE_STEPS + [{"script": "photovoltaic", "parameters": {
        "type-pvpanel": list(params["panel_types"]),
        "panel-on-roof": bool(params["panel_on_roof"]),
        "panel-on-wall": bool(params["panel_on_wall"]),
        "annual-radiation-threshold": float(params["annual_radiation_threshold_kwh_m2"]),
        "max-roof-coverage": float(params["max_roof_coverage"]),
    }}]


def run_in_scratch(inputs: dict, params: dict, steps, read):
    """Build a throwaway CEA project, run ``steps``, and return ``read(locator, bridge, hours)``."""
    import tempfile
    keep = os.environ.get("CEA_REWIRE_KEEP")
    with tempfile.TemporaryDirectory(prefix="cea-rewire-") as tmp:
        project = Path(keep) if keep else Path(tmp)
        project.mkdir(parents=True, exist_ok=True)
        locator, bridge, hours = build_scenario(project, inputs, params)
        run_workflow(project, steps(params) if callable(steps) else steps)
        return read(locator, bridge, hours)


def cea_version() -> str:
    import cea
    return cea.__version__


def source_version() -> str:
    return f"CityEnergyAnalyst {cea_version()}"


def bundled_weather(name: str = TEMPLATE_WEATHER) -> Path:
    import cea.databases
    return Path(cea.databases.__file__).parent / "weather" / f"{name}.epw"


def _write_flat_terrain(path: Path, bounds, crs, elevation_m: float, cell_m: float = 10.0) -> None:
    import rasterio
    from rasterio.transform import from_origin
    minx, miny, maxx, maxy = bounds
    width, height = int(np.ceil((maxx - minx) / cell_m)) + 1, int(np.ceil((maxy - miny) / cell_m)) + 1
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", driver="GTiff", width=width, height=height, count=1, dtype="float32",
                       crs=crs, transform=from_origin(minx, maxy, cell_m, cell_m)) as dst:
        dst.write(np.full((1, height, width), elevation_m, dtype="float32"))


def build_scenario(project: Path, inputs: dict, params: dict):
    """Write CEA input files for a scenario. Returns (InputLocator, id_bridge rows, UTC hours in EPW order)."""
    import cea.inputlocator
    from cea.utilities.standardize_coordinates import get_projected_coordinate_system

    buildings = inputs["buildings"]
    if not isinstance(buildings, gpd.GeoDataFrame):
        buildings = gpd.GeoDataFrame(buildings, geometry="geometry")
    if buildings.crs is None:
        buildings = buildings.set_crs("EPSG:3414")
    zone, others = split_zone_surroundings(buildings, params.get("zone_building_ids") or [],
                                           params["surroundings_radius_m"])
    bridge = bridge_rows(zone["building_id"], inputs.get("id_bridge"), source_version())
    names = dict(zip(bridge["hub_id"], bridge["source_id"]))

    centre = zone.geometry.union_all().centroid
    lon, lat = gpd.GeoSeries([centre], crs=zone.crs).to_crs(4326).iloc[0].coords[0]
    utm = get_projected_coordinate_system(lat, lon)
    zone_gdf = zone_table(zone, names, params).to_crs(utm)
    surr_gdf = surroundings_table(others, params).to_crs(utm)

    locator = cea.inputlocator.InputLocator(scenario=str(project / SCENARIO))
    zone_path = Path(locator.get_zone_geometry())
    zone_path.parent.mkdir(parents=True, exist_ok=True)
    zone_gdf.to_file(zone_path)
    surr_gdf.to_file(locator.get_surroundings_geometry())

    xmin, ymin, xmax, ymax = pd.concat([zone_gdf.geometry, surr_gdf.geometry]).total_bounds
    pad = 200.0
    _write_flat_terrain(Path(locator.get_terrain()), (xmin - pad, ymin - pad, xmax + pad, ymax + pad), utm,
                        float(params["terrain_elevation_m"]))

    weather_path = Path(locator.get_weather_file())
    weather_path.parent.mkdir(parents=True, exist_ok=True)
    epw, hours = epw_from_silver(inputs["weather"], bundled_weather().read_text(), params.get("station_id"))
    weather_path.write_text(epw)
    return locator, bridge, hours


def run_workflow(project: Path, steps: list[dict]) -> None:
    """Run CEA steps with CEA's own workflow runner (``cea workflow``), exactly as its tests do."""
    wf = [{"config": "default", "general:multiprocessing": False, "general:project": str(project)},
          {"config": ".", "general:scenario-name": SCENARIO}] + steps
    wf_path = project / "rewire-workflow.yml"
    wf_path.write_text(yaml.safe_dump(wf, sort_keys=False))
    proc = subprocess.run(["cea", "workflow", "--workflow", str(wf_path),
                           "--resume-file", str(project / "resume.yml")],
                          capture_output=True, text=True, cwd=project, check=False)
    log = proc.stdout + proc.stderr
    (project / "cea-workflow.log").write_text(log)
    if proc.returncode != 0:
        raise RuntimeError(f"cea workflow failed ({proc.returncode}):\n{log[-6000:]}")


def main_dir_contract(run, argv=None) -> None:
    """Directory contract (spine docker runtime): <inputs dir> <outputs dir> <params.json>."""
    argv = sys.argv[1:] if argv is None else argv
    idir, odir, pfile = Path(argv[0]), Path(argv[1]), Path(argv[2])
    params = json.loads(pfile.read_text()) if pfile.exists() else {}
    inputs = {}
    for p in idir.glob("*.parquet"):
        try:
            inputs[p.stem] = gpd.read_parquet(p)
        except ValueError:
            inputs[p.stem] = pd.read_parquet(p)
    outputs = run(inputs, params)
    odir.mkdir(parents=True, exist_ok=True)
    for name, df in outputs.items():
        df.to_parquet(odir / f"{name}.parquet", index=False)
    os.sync()

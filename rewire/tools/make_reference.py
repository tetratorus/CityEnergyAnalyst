"""Freeze golden expected outputs from an UNMODIFIED CEA run, independently of the adapters' weather path.

Runs inside the rewire image. The scenario gets the same zone/surroundings/terrain files the adapters write,
but its weather comes from CEA's own ``weather-helper`` copying the bundled Singapore-Changi_1990_2010_TMY.epw
(not from Silver weather_hourly), and hourly timestamps come from CEA's own ``date`` column. The adapters must
reproduce these numbers from Silver inputs for ``spine test`` to pass.

    docker run --rm -v $PWD/rewire:/rewire cea-rewire:local \
        python /rewire/tools/make_reference.py /rewire/cea.building_demand/golden/tanjong_pagar \
                                               /rewire/cea.solar_potential/golden/tanjong_pagar
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
from pathlib import Path

import geopandas as gpd
import pandas as pd

REWIRE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REWIRE))
from cea_spine import run as cea_run


def _adapter(transform_id: str):
    spec = importlib.util.spec_from_file_location(transform_id, REWIRE / transform_id / "adapter.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _defaults(transform_id: str) -> dict:
    import yaml
    d = yaml.safe_load((REWIRE / transform_id / "transform.yaml").read_text())
    return {k: v["default"] for k, v in d["parameters"]["properties"].items() if "default" in v}


def _case(case: Path, transform_id: str):
    params = {**_defaults(transform_id), **json.loads((case / "params.json").read_text())}
    inputs = {"buildings": gpd.read_parquet(case / "inputs" / "buildings.parquet")}
    weather = pd.read_parquet(case / "inputs" / "weather.parquet")
    return inputs, weather, params


def _scenario(project: Path, inputs, weather, params):
    locator, bridge, _ = cea_run.build_scenario(project, {**inputs, "weather": weather}, params)
    Path(locator.get_weather_file()).unlink()  # replaced by CEA's own weather-helper below
    return locator, bridge


def _weather_step():
    return [{"script": "weather-helper", "parameters": {"weather": cea_run.TEMPLATE_WEATHER}}]


def _utc_hours(date: pd.Series) -> pd.DatetimeIndex:
    """CEA's own hourly ``date`` column (local standard time, hour start) -> UTC."""
    d = pd.DatetimeIndex(pd.to_datetime(date))
    return (d.tz_localize("Asia/Singapore") if d.tz is None else d).tz_convert("UTC")


def demand(case: Path):
    tid = "cea.building_demand"
    mod = _adapter(tid)
    inputs, weather, params = _case(case, tid)
    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp)
        locator, bridge = _scenario(project, inputs, weather, params)
        cea_run.run_workflow(project, _weather_step() + cea_run.DEMAND_STEPS)
        total = pd.read_csv(locator.get_total_demand())
        hourly = {n: pd.read_csv(locator.get_demand_results_file(n)) for n in total["name"]}
        hours = _utc_hours(next(iter(hourly.values()))["date"])
        out = {"building_energy": mod.annual_table(total, bridge),
               "building_energy_hourly": mod.hourly_table(hourly, bridge, hours),
               "id_bridge": bridge}
        _write(case, out, project)


def solar(case: Path):
    tid = "cea.solar_potential"
    mod = _adapter(tid)
    inputs, weather, params = _case(case, tid)
    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp)
        locator, bridge = _scenario(project, inputs, weather, params)
        cea_run.run_workflow(project, _weather_step() + cea_run.solar_steps(params))
        totals = {p: pd.read_csv(locator.PV_total_buildings(p)) for p in params["panel_types"]}
        _write(case, {"solar_potential": mod.solar_table(totals, bridge), "id_bridge": bridge}, project)


def _write(case: Path, out: dict, project: Path):
    exp = case / "expected"
    if exp.exists():
        shutil.rmtree(exp)
    exp.mkdir(parents=True)
    for name, df in out.items():
        df.to_parquet(exp / f"{name}.parquet", index=False)
    (case / "reference-cea-workflow.log").write_text((project / "cea-workflow.log").read_text()[-20000:])
    print(f"froze {sorted(out)} -> {exp}")


if __name__ == "__main__":
    demand(Path(sys.argv[1]))
    if len(sys.argv) > 2:
        solar(Path(sys.argv[2]))

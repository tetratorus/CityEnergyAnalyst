"""cea.solar_potential: Silver buildings + weather_hourly -> CEA radiation + photovoltaic -> Gold solar_potential.

Runs inside the rewire image (docker runtime): ``python adapter.py <inputs> <outputs> <params.json>``.
CEA's own scripts do the physics (database-helper SG, archetypes-mapper, radiation/Daysim, photovoltaic);
this file only maps CEA's PV_<panel>_total_buildings.csv files back onto spine ids.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

WALLS = ["south", "north", "east", "west"]


def solar_table(totals: dict[str, pd.DataFrame], bridge: pd.DataFrame) -> pd.DataFrame:
    """totals: panel type (PV1..PV4) -> CEA PV_<panel>_total_buildings.csv."""
    hub = dict(zip(bridge["source_id"], bridge["hub_id"]))
    frames = []
    for panel, t in sorted(totals.items()):
        f = pd.DataFrame({"building_id": t["name"].map(hub), "panel_type": panel})
        if f["building_id"].isna().any():
            raise ValueError(f"CEA returned buildings not in the id bridge: {sorted(t.loc[f.building_id.isna(), 'name'])}")
        f["pv_area_m2"] = t["area_PV_m2"].astype(float)
        f["pv_roof_area_m2"] = t["PV_roofs_top_m2"].astype(float)
        f["pv_wall_area_m2"] = sum(t[f"PV_walls_{w}_m2"].astype(float) for w in WALLS)
        f["pv_annual_kwh"] = t["E_PV_gen_kWh"].astype(float)
        f["pv_roof_annual_kwh"] = t["PV_roofs_top_E_kWh"].astype(float)
        f["pv_wall_annual_kwh"] = sum(t[f"PV_walls_{w}_E_kWh"].astype(float) for w in WALLS)
        f["insolation_on_pv_kwh"] = t["radiation_kWh"].astype(float)
        frames.append(f)
    return pd.concat(frames, ignore_index=True).sort_values(["building_id", "panel_type"]).reset_index(drop=True)


def _reader(params):
    def read(locator, bridge, hours):
        totals = {p: pd.read_csv(locator.PV_total_buildings(p)) for p in params["panel_types"]}
        return {"solar_potential": solar_table(totals, bridge), "id_bridge": bridge}
    return read


def run(inputs, params):
    from cea_spine import run as cea_run
    return cea_run.run_in_scratch(inputs, params, cea_run.solar_steps, _reader(params))


if __name__ == "__main__":
    from cea_spine.run import main_dir_contract
    main_dir_contract(run)

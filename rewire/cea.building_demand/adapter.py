"""cea.building_demand: Silver buildings + weather_hourly -> CEA demand -> Gold building_energy per building_id.

Runs inside the rewire image (docker runtime): ``python adapter.py <inputs> <outputs> <params.json>``.
CEA's own scripts do all the physics (database-helper SG, archetypes-mapper, radiation, occupancy, demand);
this file only maps CEA's result files back onto spine ids and units.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MWH_TO_KWH = 1000.0
# Gold column <- CEA Total_demand.csv column (cea/schemas.yml: get_total_demand)
ANNUAL = {
    "gfa_m2": ("GFA_m2", 1.0),
    "conditioned_area_m2": ("Af_m2", 1.0),
    "annual_cooling_kwh": ("QC_sys_MWhyr", MWH_TO_KWH),
    "annual_space_cooling_kwh": ("Qcs_sys_MWhyr", MWH_TO_KWH),
    "annual_electricity_kwh": ("E_sys_MWhyr", MWH_TO_KWH),
    "annual_heating_kwh": ("QH_sys_MWhyr", MWH_TO_KWH),
    "peak_cooling_kw": ("QC_sys0_kW", 1.0),
    "peak_electricity_kw": ("E_sys0_kW", 1.0),
}
# Gold column <- CEA per-building hourly demand column (get_demand_results_file)
HOURLY = {"cooling_kwh": "QC_sys_kWh", "electricity_kwh": "E_sys_kWh", "heating_kwh": "QH_sys_kWh"}


def annual_table(total: pd.DataFrame, bridge: pd.DataFrame) -> pd.DataFrame:
    hub = dict(zip(bridge["source_id"], bridge["hub_id"]))
    out = pd.DataFrame({"building_id": total["name"].map(hub)})
    if out["building_id"].isna().any():
        raise ValueError(f"CEA returned buildings not in the id bridge: {sorted(total.loc[out.building_id.isna(), 'name'])}")
    for col, (cea_col, k) in ANNUAL.items():
        out[col] = total[cea_col].astype(float) * k
    out["annual_total_kwh"] = out[["annual_cooling_kwh", "annual_electricity_kwh", "annual_heating_kwh"]].sum(axis=1)
    return out.sort_values("building_id").reset_index(drop=True)


def hourly_table(per_building: dict[str, pd.DataFrame], bridge: pd.DataFrame, hours: pd.DatetimeIndex) -> pd.DataFrame:
    hub = dict(zip(bridge["source_id"], bridge["hub_id"]))
    frames = []
    for name, df in sorted(per_building.items()):
        if len(df) != len(hours):
            raise ValueError(f"{name}: CEA wrote {len(df)} hourly rows, weather has {len(hours)}")
        f = pd.DataFrame({"building_id": hub[name], "timestamp": hours})
        for col, cea_col in HOURLY.items():
            f[col] = df[cea_col].astype(float).to_numpy()
        frames.append(f)
    return pd.concat(frames, ignore_index=True).sort_values(["building_id", "timestamp"]).reset_index(drop=True)


def _read(locator, bridge, hours):
    total = pd.read_csv(locator.get_total_demand())
    hourly = {n: pd.read_csv(locator.get_demand_results_file(n)) for n in total["name"]}
    return {"building_energy": annual_table(total, bridge),
            "building_energy_hourly": hourly_table(hourly, bridge, hours),
            "id_bridge": bridge}


def run(inputs, params):
    from cea_spine import run as cea_run
    return cea_run.run_in_scratch(inputs, params, cea_run.DEMAND_STEPS, _read)


if __name__ == "__main__":
    from cea_spine.run import main_dir_contract
    main_dir_contract(run)

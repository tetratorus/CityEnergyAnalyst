"""Adapter unit tests: pure data mapping, no CEA needed (run on the host with `pytest rewire/tests`)."""
import importlib.util
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import box

REWIRE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REWIRE))
from cea_spine.ids import SOURCE, bridge_rows, generated_cea_name
from cea_spine.weather import epw_from_silver, read_epw, silver_from_epw
from cea_spine.zone import split_zone_surroundings, surroundings_table, zone_table

EPW = REWIRE.parent / "cea" / "databases" / "weather" / "Singapore-Changi_1990_2010_TMY.epw"
PARAMS = {"floor_height_m": 3.0, "default_cea_use_type": "MULTI_RES", "default_year": 2000, "const_type": "STANDARD3"}


def _adapter(tid):
    spec = importlib.util.spec_from_file_location(tid.replace(".", "_"), REWIRE / tid / "adapter.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _buildings():
    return gpd.GeoDataFrame({
        "building_id": ["b1", "b2", "b3"],
        "height_m": [30.0, None, 0.0],
        "storeys": pd.array([None, 4, None], dtype="Int64"),
        "use_type": ["RESIDENTIAL", "OFFICE", "OTHER"],
        "year_built": pd.array([1985, None, None], dtype="Int64"),
        "source_version": "t",
    }, geometry=[box(0, 0, 10, 10), box(30, 0, 40, 10), box(500, 500, 510, 510)], crs=3414)


def test_generated_names_are_stable_and_valid():
    assert generated_cea_name("osm_w1") == generated_cea_name("osm_w1")
    assert generated_cea_name("osm_w1") != generated_cea_name("osm_w2")
    assert generated_cea_name("a/b:c")[0] == "B"


def test_bridge_reuses_existing_cea_names():
    existing = pd.DataFrame([{"source": SOURCE, "source_id": "B1000", "hub": "building", "hub_id": "b1",
                              "match_method": "manual", "confidence": 0.9, "source_version": "old"},
                             {"source": "osm", "source_id": "w1", "hub": "building", "hub_id": "b2",
                              "match_method": "exact", "confidence": 1.0, "source_version": "old"}])
    b = bridge_rows(["b2", "b1"], existing, "cea test").set_index("hub_id")
    assert b.loc["b1", "source_id"] == "B1000" and b.loc["b1", "match_method"] == "manual"
    assert b.loc["b2", "source_id"] == generated_cea_name("b2")
    assert set(b["source"]) == {SOURCE} and set(b["source_version"]) == {"cea test"}


def test_bridge_rejects_bad_or_clashing_names():
    bad = pd.DataFrame([{"source": SOURCE, "source_id": "1abc", "hub": "building", "hub_id": "b1",
                         "match_method": "manual", "confidence": None, "source_version": "x"}])
    with pytest.raises(ValueError, match="start with a letter"):
        bridge_rows(["b1"], bad, "v")
    clash = pd.DataFrame([{"source": SOURCE, "source_id": "B1", "hub": "building", "hub_id": h,
                           "match_method": "manual", "confidence": None, "source_version": "x"} for h in ("b1", "b2")])
    with pytest.raises(ValueError, match="share"):
        bridge_rows(["b1", "b2"], clash, "v")


def test_zone_and_surroundings_mapping():
    b = _buildings()
    zone, others = split_zone_surroundings(b, ["b1"], 50.0)
    assert list(zone.building_id) == ["b1"] and list(others.building_id) == ["b2"]  # b3 is too far
    z = zone_table(b, {"b1": "B1", "b2": "B2", "b3": "B3"}, PARAMS).set_index("name")
    assert z.loc["B1", "floors_ag"] == 10 and z.loc["B1", "height_ag"] == 30.0
    assert z.loc["B2", "floors_ag"] == 4 and z.loc["B2", "height_ag"] == 12.0
    assert z.loc["B3", "floors_ag"] == 1  # height 0 treated as missing
    assert list(z.use_type1) == ["MULTI_RES", "OFFICE", "MULTI_RES"] and list(z.year) == [1985, 2000, 2000]
    s = surroundings_table(others, PARAMS)
    assert list(s.columns) == ["name", "floors_ag", "height_ag", "REFERENCE", "geometry"]
    with pytest.raises(ValueError, match="not in silver"):
        split_zone_surroundings(b, ["nope"], 50.0)


def test_weather_round_trip_is_lossless():
    text = EPW.read_text()
    silver = silver_from_epw(text, "changi", "t")
    assert len(silver) == 8760 and str(silver.timestamp.dt.tz) == "UTC"
    out, hours = epw_from_silver(silver.sample(frac=1, random_state=0), text)
    _, a, _ = read_epw(text)
    _, b, _ = read_epw(out)
    assert len(a) == len(b) == 8760
    for ra, rb in zip(a, b):
        assert ra[:6] == rb[:6]
        assert [float(x) for x in ra[6:]] == [float(x) for x in rb[6:]]
    assert hours.is_monotonic_increasing and hours[0] == silver.timestamp.min()


def test_weather_needs_every_hour_and_one_station():
    text = EPW.read_text()
    silver = silver_from_epw(text, "changi", "t")
    with pytest.raises(ValueError, match="missing hour"):
        epw_from_silver(silver.iloc[1:], text)
    two = pd.concat([silver, silver.assign(station_id="other")])
    with pytest.raises(ValueError, match="station_id"):
        epw_from_silver(two, text)
    assert epw_from_silver(two, text, "other")[0]


def test_demand_tables():
    mod = _adapter("cea.building_demand")
    bridge = bridge_rows(["b1"], None, "v")
    name = bridge.source_id[0]
    total = pd.DataFrame({"name": [name], "GFA_m2": [100.0], "Af_m2": [80.0], "QC_sys_MWhyr": [1.5],
                          "Qcs_sys_MWhyr": [1.2], "E_sys_MWhyr": [0.5], "QH_sys_MWhyr": [0.0],
                          "QC_sys0_kW": [3.0], "E_sys0_kW": [1.0]})
    a = mod.annual_table(total, bridge)
    assert a.loc[0, "building_id"] == "b1" and a.loc[0, "annual_cooling_kwh"] == 1500.0
    assert a.loc[0, "annual_total_kwh"] == 2000.0
    hours = pd.date_range("2005-01-01", periods=3, freq="h", tz="UTC")
    h = mod.hourly_table({name: pd.DataFrame({"QC_sys_kWh": [1, 2, 3], "E_sys_kWh": [0, 1, 0],
                                              "QH_sys_kWh": [0, 0, 0]})}, bridge, hours)
    assert list(h.cooling_kwh) == [1.0, 2.0, 3.0] and list(h.timestamp) == list(hours)
    with pytest.raises(ValueError, match="not in the id bridge"):
        mod.annual_table(total.assign(name="BUNKNOWN"), bridge)


def test_solar_table():
    mod = _adapter("cea.solar_potential")
    bridge = bridge_rows(["b1"], None, "v")
    walls = {f"PV_walls_{w}_{s}": [1.0] for w in mod.WALLS for s in ("m2", "E_kWh")}
    t = pd.DataFrame({"name": bridge.source_id, "area_PV_m2": [14.0], "PV_roofs_top_m2": [10.0],
                      "E_PV_gen_kWh": [300.0], "PV_roofs_top_E_kWh": [296.0], "radiation_kWh": [2000.0], **walls})
    s = mod.solar_table({"PV1": t}, bridge)
    assert s.loc[0, "building_id"] == "b1" and s.loc[0, "pv_wall_area_m2"] == 4.0 and s.loc[0, "panel_type"] == "PV1"

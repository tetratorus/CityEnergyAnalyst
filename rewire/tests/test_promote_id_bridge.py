"""Gold id_bridge -> Silver id_bridge promotion (union-preserving), on a throwaway lake. Needs sg-urban-spine."""
import sys
from pathlib import Path

import pandas as pd
import pytest

lake_mod = pytest.importorskip("spine.lake")
from spine.lake import write_table

REWIRE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REWIRE))
from cea_spine.ids import BRIDGE_COLUMNS, SOURCE, bridge_rows, promote_to_silver

TID = "cea.building_demand"


def _row(source, source_id, hub_id, method="exact", version="v1"):
    return {"source": source, "source_id": source_id, "hub": "building", "hub_id": hub_id,
            "match_method": method, "confidence": 1.0, "source_version": version}


def _publish(lake, rows, scenario="baseline", tid=TID):
    write_table(pd.DataFrame(rows, columns=BRIDGE_COLUMNS), lake.gold_dir(scenario, tid) / "id_bridge.parquet")


def test_baseline_promotion_keeps_existing_rows(tmp_path):
    lake = lake_mod.Lake(tmp_path)
    lake.silver_write("id_bridge", pd.DataFrame([_row("osm", "w1", "b1"), _row(SOURCE, "B1000", "b2", "manual", "old")]))
    _publish(lake, bridge_rows(["b1", "b2"], lake.silver_read("id_bridge"), "new").to_dict("records"))
    added = promote_to_silver(lake, [TID])
    silver = lake.silver_read("id_bridge").set_index(["source", "source_id"])
    assert list(added.hub_id) == ["b1"] and len(silver) == 3
    assert silver.loc[("osm", "w1"), "hub_id"] == "b1"
    assert silver.loc[(SOURCE, "B1000"), "source_version"] == "old"  # existing row untouched
    assert promote_to_silver(lake, [TID]).empty  # idempotent
    # the next run reuses the promoted names
    again = bridge_rows(["b1", "b2"], lake.silver_read("id_bridge"), "next").set_index("hub_id")
    assert again.loc["b1", "source_id"] == added.source_id[0] and again.loc["b2", "source_id"] == "B1000"


def test_promotion_creates_silver_table(tmp_path):
    lake = lake_mod.Lake(tmp_path)
    _publish(lake, [_row(SOURCE, "B1", "b1")])
    assert len(promote_to_silver(lake, [TID])) == 1
    assert list(lake.silver_read("id_bridge").source_id) == ["B1"]


def test_conflicting_rows_raise_and_write_nothing(tmp_path):
    lake = lake_mod.Lake(tmp_path)
    lake.silver_write("id_bridge", pd.DataFrame([_row(SOURCE, "B1", "b1")]))
    before = lake.silver_path("id_bridge").read_bytes()
    _publish(lake, [_row(SOURCE, "B1", "b9")])
    with pytest.raises(ValueError, match="more than one hub id"):
        promote_to_silver(lake, [TID])
    _publish(lake, [_row(SOURCE, "B2", "b1")])
    with pytest.raises(ValueError, match="more than one CEA zone name"):
        promote_to_silver(lake, [TID])
    assert lake.silver_path("id_bridge").read_bytes() == before


def test_scenario_promotion_goes_to_overlay(tmp_path):
    lake = lake_mod.Lake(tmp_path)
    lake.silver_write("id_bridge", pd.DataFrame([_row(SOURCE, "B1", "b1")]))
    lake.scenario_create("tall")
    _publish(lake, [_row(SOURCE, "B1", "b1"), _row(SOURCE, "B2", "b2")], scenario="tall")
    _publish(lake, [_row(SOURCE, "B3", "b3")], scenario="tall", tid="cea.solar_potential")
    added = promote_to_silver(lake, [TID, "cea.solar_potential"], scenario="tall")
    assert sorted(added.source_id) == ["B2", "B3"]
    assert sorted(lake.silver_read("id_bridge", "tall").source_id) == ["B1", "B2", "B3"]
    assert list(lake.silver_read("id_bridge").source_id) == ["B1"]  # baseline unchanged

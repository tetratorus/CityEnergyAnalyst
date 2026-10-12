"""CEA zone names <-> spine building_id as ``id_bridge`` rows (GERS-style bridge).

The transforms publish these rows to Gold (``gold/<scenario>/<transform_id>/id_bridge``), because the spine runner
writes transform outputs only to Gold. :func:`promote_to_silver` unions them into Silver ``id_bridge``.
"""
from __future__ import annotations

import hashlib
import re

import pandas as pd

SOURCE = "cea_zone"
HUB = "building"
BRIDGE_COLUMNS = ["source", "source_id", "hub", "hub_id", "match_method", "confidence", "source_version"]
_VALID_CEA_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")


def generated_cea_name(building_id: str) -> str:
    """Stable CEA zone name for a hub id: 'B' + 10 hex chars of sha1. Same id -> same name, on any machine."""
    return "B" + hashlib.sha1(building_id.encode("utf-8")).hexdigest()[:10].upper()


def bridge_rows(building_ids, id_bridge: pd.DataFrame | None, source_version: str) -> pd.DataFrame:
    """Return one id_bridge row per building_id.

    Existing ``cea_zone`` rows in Silver id_bridge are reused (e.g. B1000 from an existing CEA project), so a
    building keeps its CEA name across runs. Buildings without a row get :func:`generated_cea_name`.
    """
    ids = pd.Series(sorted(set(map(str, building_ids))), dtype="string")
    known = pd.DataFrame(columns=BRIDGE_COLUMNS)
    if id_bridge is not None and len(id_bridge):
        b = pd.DataFrame(id_bridge)
        known = b[(b["source"] == SOURCE) & (b["hub"] == HUB) & (b["hub_id"].isin(ids))]
        dup = known["hub_id"].duplicated(keep=False)
        if dup.any():
            raise ValueError(f"id_bridge maps these buildings to more than one CEA name: "
                             f"{sorted(known.loc[dup, 'hub_id'].unique())}")
    existing = dict(zip(known["hub_id"], known["source_id"]))
    rows = []
    for bid in ids:
        if bid in existing:
            r = known[known["hub_id"] == bid].iloc[0]
            rows.append({"source": SOURCE, "source_id": str(r["source_id"]), "hub": HUB, "hub_id": bid,
                         "match_method": str(r["match_method"]),
                         "confidence": None if pd.isna(r["confidence"]) else float(r["confidence"]),
                         "source_version": source_version})
        else:
            rows.append({"source": SOURCE, "source_id": generated_cea_name(bid), "hub": HUB, "hub_id": bid,
                         "match_method": "exact", "confidence": 1.0, "source_version": source_version})
    out = pd.DataFrame(rows, columns=BRIDGE_COLUMNS)
    bad = out.loc[~out["source_id"].str.match(_VALID_CEA_NAME), "source_id"]
    if len(bad):
        raise ValueError(f"CEA zone names must start with a letter and use only letters, digits, '_' or '-': "
                         f"{sorted(bad)}")
    clash = out["source_id"].duplicated(keep=False)
    if clash.any():
        raise ValueError(f"Two buildings would share a CEA zone name: {sorted(out.loc[clash, 'source_id'])}")
    out["confidence"] = out["confidence"].astype("float64")
    return out


def _key_conflicts(rows: pd.DataFrame, what: str) -> None:
    by_key = rows.drop_duplicates(["source", "source_id", "hub", "hub_id"])
    clash = by_key[["source", "source_id"]].duplicated(keep=False)
    if clash.any():
        raise ValueError(f"{what}: these (source, source_id) map to more than one hub id: "
                         f"{sorted(map(tuple, by_key.loc[clash, ['source', 'source_id']].values))}")
    cea = by_key[by_key["source"] == SOURCE]
    clash = cea[["hub", "hub_id"]].duplicated(keep=False)
    if clash.any():
        raise ValueError(f"{what}: these buildings have more than one CEA zone name: "
                         f"{sorted(cea.loc[clash, 'hub_id'].unique())}")


def new_bridge_rows(existing: pd.DataFrame, published: pd.DataFrame) -> pd.DataFrame:
    """Rows of ``published`` whose (source, source_id) is not in ``existing``. Raises on conflicting mappings."""
    _key_conflicts(pd.concat([existing[BRIDGE_COLUMNS], published[BRIDGE_COLUMNS]], ignore_index=True),
                   "id_bridge promotion")
    known = pd.MultiIndex.from_frame(existing[["source", "source_id"]].astype(str))
    pub = published.drop_duplicates(["source", "source_id"])
    keep = ~pd.MultiIndex.from_frame(pub[["source", "source_id"]].astype(str)).isin(known)
    return pub.loc[keep, BRIDGE_COLUMNS].reset_index(drop=True)


def promote_to_silver(lake, transform_ids, scenario: str = "baseline") -> pd.DataFrame:
    """Union the Gold ``id_bridge`` rows of ``transform_ids`` into Silver ``id_bridge``; return the rows added.

    Union-preserving: every existing Silver row is kept unchanged (also its match_method and source_version), and
    only rows with a new (source, source_id) are added. A row that maps an existing (source, source_id) to another
    hub id, or gives a building a second CEA name, raises ``ValueError`` and nothing is written.
    Baseline rows go to ``silver/id_bridge.parquet`` (``Lake.silver_read`` + ``Lake.silver_write``). Rows from a
    scenario go to that scenario's overlay (``Lake.scenario_set``), so the baseline is not changed by a scenario run.
    """
    published = pd.concat([lake.gold_read(tid, "id_bridge", scenario) for tid in transform_ids], ignore_index=True)
    try:
        existing = lake.silver_read("id_bridge", scenario)
    except FileNotFoundError:
        if scenario != "baseline" and lake.silver_path("id_bridge").exists():
            raise
        existing = pd.DataFrame({c: pd.Series(dtype="float64" if c == "confidence" else "object")
                                 for c in BRIDGE_COLUMNS})
        if scenario != "baseline":
            lake.silver_write("id_bridge", existing)
    add = new_bridge_rows(existing, published)
    if len(add):
        if scenario == "baseline":
            lake.silver_write("id_bridge", pd.concat([existing, add], ignore_index=True))
        else:
            lake.scenario_set(scenario, "id_bridge", add)
    return add

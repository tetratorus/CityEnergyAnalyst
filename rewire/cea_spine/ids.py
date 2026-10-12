"""CEA zone names <-> spine building_id, as Silver ``id_bridge`` rows (GERS-style bridge)."""
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

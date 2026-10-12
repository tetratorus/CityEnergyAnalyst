"""Promote the CEA transforms' Gold ``id_bridge`` rows into Silver ``id_bridge`` (union, never overwrite).

Run on the host after ``spine run`` (needs sg-urban-spine installed):

    python rewire/tools/promote_id_bridge.py --lake <lake> [--scenario <name>] [cea.building_demand cea.solar_potential]

See :func:`cea_spine.ids.promote_to_silver` for the rules.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from spine.lake import BASELINE, Lake

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cea_spine.ids import promote_to_silver


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("transform_ids", nargs="*", default=["cea.building_demand"])
    ap.add_argument("--lake", default=None, help="lake root (default: $SPINE_LAKE or ./lake)")
    ap.add_argument("--scenario", default=BASELINE)
    args = ap.parse_args(argv)
    added = promote_to_silver(Lake(args.lake), args.transform_ids, args.scenario)
    where = "silver/id_bridge.parquet" if args.scenario == BASELINE else f"scenarios/{args.scenario}/silver/id_bridge.parquet"
    print(f"added {len(added)} id_bridge rows to {where}")


if __name__ == "__main__":
    main()

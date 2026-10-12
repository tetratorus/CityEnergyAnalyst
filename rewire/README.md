# rewire/: City Energy Analyst on the sg-urban-spine

This wires CEA into [sg-urban-spine](https://github.com/tetratorus/saas-middleware/tree/sg-urban-spine) (contract: `spec/LAYERS.md`) without
changing any CEA code. It provides the **one canonical CEA transform**. duct-fom `bem-cea` and TWA `CEAAgent` should
point at `cea.building_demand` and not wrap CEA again.

| transform | reads | writes (Gold) | runtime | readiness |
|---|---|---|---|---|
| `cea.building_demand` | `silver:buildings`, `silver:weather_hourly`, `silver:id_bridge` (optional) | `building_energy` (annual per building_id), `building_energy_hourly`, `id_bridge` (promote to Silver, see below) | docker | RL6 |
| `cea.solar_potential` | same | `solar_potential` (per building_id x panel type), `id_bridge` (same) | docker | RL6 |

## What the adapters do (and do not do)
1. **Silver to CEA input files** (`cea_spine/`). These modules map data only; they do no physics.
   - `zone.py`: zone buildings go to `zone.shp`. Other Silver buildings within `surroundings_radius_m` go to `surroundings.shp`. Both are reprojected from EPSG:3414 to the UTM zone that CEA itself picks (`get_projected_coordinate_system`). `use_type` maps to CEA SG use types (RESIDENTIAL->MULTI_RES, ...; OTHER/null->`default_cea_use_type`). If `storeys` or `height_m` is missing, it is derived from the other one using `floor_height_m`. `const_type` is a parameter (default STANDARD3, the SG database default).
   - `weather.py`: `weather_hourly` goes to `weather.epw`. Silver `timestamp` is **UTC, start of hour**, and EPW is local standard time, end of hour. Silver columns overwrite their EPW fields. The EPW fields Silver does not carry (sky cover, IR sky, illuminance, ...) and the LOCATION header are taken from CEA's bundled `Singapore-Changi_1990_2010_TMY.epw` for the same hour of year. All 8760 hours are required.
   - `ids.py`: CEA zone names <-> `building_id` as `id_bridge` rows (`source=cea_zone`, `hub=building`). Existing `cea_zone` rows in **Silver** `id_bridge` are reused, for example to keep `B1000` from an existing CEA project. Other buildings get a stable name: `B` + 10 hex chars of sha1(building_id). See [id_bridge: Gold, then Silver](#id_bridge-gold-then-silver) for where the rows go.
   - Terrain: a flat GeoTIFF (`terrain_elevation_m`) is written, because the model step makes no network calls.
2. **CEA runs unmodified** through its own `cea workflow` runner. The steps and parameters are those of CEA's own `cea/tests/workflows/sg_cooling.yml`: `database-helper` (SG archetypes, assemblies, components), `archetypes-mapper`, `radiation` (Daysim), then `occupancy` + `demand`, or `photovoltaic`.
3. **CEA results to Gold**: CEA's own `Total_demand.csv`, per-building hourly demand and `PV_<panel>_total_buildings.csv` are renamed to `building_id` and kWh (MWh x 1000). Annual demand is CEA's *end-use* system demand (`QC_sys`, `E_sys`, `QH_sys`). It is **not** final energy after supply systems (CEA `final-energy` is not wired).

## Run it
All commands run from the repo root.
```bash
docker build -t cea-upstream:local .                          # the repo's own Dockerfile, unmodified
docker build -t cea-rewire:local -f rewire/Dockerfile rewire   # + rewire/ glue
pip install 'git+https://github.com/tetratorus/saas-middleware@sg-urban-spine' pytest
spine --path rewire validate                                  # both descriptors against spec/transform.schema.json
spine --path rewire test                                      # golden tests through the docker runtime (~2 min)
pytest rewire/tests                                           # adapter + id_bridge unit tests (host) + golden tests (if the image exists)
```
On a lake, after Silver `buildings` and `weather_hourly` are loaded:
```bash
spine --lake <lake> --path rewire run cea.building_demand --params '{"zone_building_ids": [...]}'
python rewire/tools/promote_id_bridge.py --lake <lake> cea.building_demand   # Gold id_bridge -> Silver id_bridge
```

## id_bridge: Gold, then Silver
LAYERS rule 9 says CEA zone names go to Silver `id_bridge`. The spine runner (`spine run`, `execute()`) publishes a
transform's outputs **only to Gold**, so each run writes its bridge rows to
`gold/<scenario>/cea.building_demand/id_bridge.parquet` (and `cea.solar_potential/id_bridge.parquet`). A transform
cannot write Silver itself (rule 3). The rows reach Silver in a separate, explicit step:
`rewire/tools/promote_id_bridge.py` (`cea_spine.ids.promote_to_silver`).
- **Union-preserving.** Every existing Silver row stays as it is (also its `match_method` and `source_version`). Only
  rows with a new `(source, source_id)` are added. Running it twice adds nothing.
- **Conflicts stop it.** If a Gold row maps an existing `(source, source_id)` to another hub id, or gives a building a
  second CEA name, it raises an error and writes nothing.
- **Baseline** rows are written with `Lake.silver_read` + `Lake.silver_write` to `silver/id_bridge.parquet` (the
  table is created if it does not exist). **Scenario** rows (`--scenario <name>`) go to that scenario's overlay with
  `Lake.scenario_set`, so a scenario run never changes the baseline. If baseline Silver has no `id_bridge` table yet,
  an empty one is created first, because scenario overlays need a baseline table.
- The next CEA run reads Silver `id_bridge` (optional input) and reuses the promoted names, so a building keeps its
  CEA name across runs. Without the promotion step the names are still stable, because they are derived from
  `building_id`, but names from an existing CEA project (e.g. `B1000`) are only kept if their rows are in Silver.

## Golden case: `golden/tanjong_pagar`
- Buildings: 126 OpenStreetMap footprints around Tanjong Pagar Plaza, as of 2026-10-12 (ODbL, (c) OpenStreetMap contributors), built by `tools/make_fixture.py`. The zone is 5 buildings: 3 HDB towers (`osm_w171979325/6/7`), the 100AM podium (`osm_w43755568`) and Tanjong Pagar Market (`osm_w171975666`). 10 neighbours within 50 m shade them.
- Weather: CEA's bundled `Singapore-Changi_1990_2010_TMY.epw` converted to `weather_hourly` rows.
- Expected values: `tools/make_reference.py` runs **unmodified CEA 4.0.0-beta.8** in the repo's own image. That run gets its weather from CEA's own `weather-helper` (the bundled EPW), not from the adapter, and its hourly timestamps from CEA's own `date` column. `spine test` then has to reproduce those values from the Silver inputs (tolerance rtol 1e-6). Two separate CEA runs gave byte-identical `Total_demand.csv` and PV totals.
- The bundled `reference-case-open` was not used as the golden case. Its geometry is a Swiss site in EPSG:32732, not Singapore.

## Upstream changes
None. All additions are under `rewire/`. CEA cannot be installed on the host with Pixi here: `cea_external_tools` ships a manylinux_2_39 wheel and the host has glibc 2.35. That is why the runtime is docker.

## Licences
CEA: MIT (`LICENSE`), so commercial use is allowed. The fixture's OSM data is ODbL and needs attribution if redistributed.

## Known gaps
- Archetypes are assigned only from Silver `use_type` + `year_built` with one `const_type`. There are no per-building envelope or HVAC overrides yet.
- Weather fields missing from Silver fall back to Changi TMY climatology. The EPW LOCATION header is always Changi, which is acceptable inside Singapore.
- Terrain is flat. Trees are not modelled.
- The image is built locally (`cea-rewire:local`) and not yet published to a registry. The container runs as root so it can use the spine runner's 0700 temp dirs.
- There is one 5-building golden case, and results have not been checked against measured consumption (e.g. BCA benchmarks).
- `final-energy`, emissions, costs and networks from the CEA SG workflow are not wired.

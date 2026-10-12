# rewire/: City Energy Analyst on the sg-urban-spine

This wires CEA into [sg-urban-spine](https://github.com/tetratorus/sg-urban-spine) (contract: `spec/LAYERS.md`) without
changing any CEA code. It provides the **one canonical CEA transform**. duct-fom `bem-cea` and TWA `CEAAgent` should
point at `cea.building_demand` and not wrap CEA again.

| transform | reads | writes (Gold) | runtime | readiness |
|---|---|---|---|---|
| `cea.building_demand` | `silver:buildings`, `silver:weather_hourly`, `silver:id_bridge` (optional) | `building_energy` (annual per building_id), `building_energy_hourly`, `id_bridge` | docker | RL6 |
| `cea.solar_potential` | same | `solar_potential` (per building_id x panel type), `id_bridge` | docker | RL6 |

## What the adapters do (and do not do)
1. **Silver to CEA input files** (`cea_spine/`). These modules map data only; they do no physics.
   - `zone.py`: zone buildings go to `zone.shp`. Other Silver buildings within `surroundings_radius_m` go to `surroundings.shp`. Both are reprojected from EPSG:3414 to the UTM zone that CEA itself picks (`get_projected_coordinate_system`). `use_type` maps to CEA SG use types (RESIDENTIAL->MULTI_RES, ...; OTHER/null->`default_cea_use_type`). If `storeys` or `height_m` is missing, it is derived from the other one using `floor_height_m`. `const_type` is a parameter (default STANDARD3, the SG database default).
   - `weather.py`: `weather_hourly` goes to `weather.epw`. Silver `timestamp` is **UTC, start of hour**, and EPW is local standard time, end of hour. Silver columns overwrite their EPW fields. The EPW fields Silver does not carry (sky cover, IR sky, illuminance, ...) and the LOCATION header are taken from CEA's bundled `Singapore-Changi_1990_2010_TMY.epw` for the same hour of year. All 8760 hours are required.
   - `ids.py`: CEA zone names <-> `building_id` as Silver `id_bridge` rows (`source=cea_zone`, `hub=building`). Existing `cea_zone` rows are reused, for example to keep `B1000` from an existing CEA project. Other buildings get a stable name: `B` + 10 hex chars of sha1(building_id).
   - Terrain: a flat GeoTIFF (`terrain_elevation_m`) is written, because the model step makes no network calls.
2. **CEA runs unmodified** through its own `cea workflow` runner. The steps and parameters are those of CEA's own `cea/tests/workflows/sg_cooling.yml`: `database-helper` (SG archetypes, assemblies, components), `archetypes-mapper`, `radiation` (Daysim), then `occupancy` + `demand`, or `photovoltaic`.
3. **CEA results to Gold**: CEA's own `Total_demand.csv`, per-building hourly demand and `PV_<panel>_total_buildings.csv` are renamed to `building_id` and kWh (MWh x 1000). Annual demand is CEA's *end-use* system demand (`QC_sys`, `E_sys`, `QH_sys`). It is **not** final energy after supply systems (CEA `final-energy` is not wired).

## Run it
```bash
docker build -t cea-upstream:local .                          # the repo's own Dockerfile, unmodified
docker build -t cea-rewire:local -f rewire/Dockerfile rewire   # + rewire/ glue
pip install 'git+https://github.com/tetratorus/sg-urban-spine@main' pytest
cd rewire && spine validate . && spine test .                 # golden tests through the docker runtime (~2 min)
pytest rewire/tests                                           # adapter unit tests (host) + golden tests (if the image exists)
```
On a lake: `spine run cea.building_demand --lake <lake>` after Silver `buildings` and `weather_hourly` are loaded.

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

"""Silver ``weather_hourly`` <-> EnergyPlus weather (EPW), the file CEA reads.

Convention (stated so other conformers can match it): ``timestamp`` is UTC and marks the START of the hour.
EPW rows are local standard time and mark the END of the hour, so EPW (month, day, hour=h) <-> UTC
``local(h-1:00) - tz``. Silver columns overwrite the matching EPW fields; EPW fields Silver does not carry
(sky cover, infrared sky radiation, illuminance, ...) come from the template EPW for the same hour of year.
"""
from __future__ import annotations

import csv
import io

import pandas as pd

EPW_FIELDS = ["year", "month", "day", "hour", "minute", "datasource", "drybulb_C", "dewpoint_C", "relhum_percent",
              "atmos_Pa", "exthorrad_Whm2", "extdirrad_Whm2", "horirsky_Whm2", "glohorrad_Whm2", "dirnorrad_Whm2",
              "difhorrad_Whm2", "glohorillum_lux", "dirnorillum_lux", "difhorillum_lux", "zenlum_lux",
              "winddir_deg", "windspd_ms", "totskycvr_tenths", "opaqskycvr_tenths", "visibility_km",
              "ceiling_hgt_m", "presweathobs", "presweathcodes", "precip_wtr_mm", "aerosol_opt_thousandths",
              "snowdepth_cm", "days_last_snow", "Albedo", "liq_precip_depth_mm", "liq_precip_rate_Hour"]
SILVER_TO_EPW = {
    "dry_bulb_c": "drybulb_C",
    "dew_point_c": "dewpoint_C",
    "rh_pct": "relhum_percent",
    "pressure_pa": "atmos_Pa",
    "ghi_wm2": "glohorrad_Whm2",
    "dni_wm2": "dirnorrad_Whm2",
    "dhi_wm2": "difhorrad_Whm2",
    "wind_dir_deg": "winddir_deg",
    "wind_speed_ms": "windspd_ms",
}
HOURS_IN_YEAR = 8760


def _fmt(v: float) -> str:
    s = f"{float(v):.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def read_epw(text: str) -> tuple[list[str], list[list[str]], float]:
    lines = text.splitlines()
    header, data = lines[:8], [r for r in csv.reader(lines[8:]) if r]
    tz = float(header[0].split(",")[8])
    return header, data, tz


def silver_from_epw(text: str, station_id: str, source_version: str) -> pd.DataFrame:
    """EPW -> silver weather_hourly rows (used to build golden fixtures from CEA's bundled Singapore EPW)."""
    _, data, tz = read_epw(text)
    df = pd.DataFrame([r[:len(EPW_FIELDS)] for r in data], columns=EPW_FIELDS)
    local_start = pd.to_datetime({"year": df.year.astype(int), "month": df.month.astype(int),
                                  "day": df.day.astype(int), "hour": df.hour.astype(int) - 1})
    out = pd.DataFrame({"station_id": station_id,
                        "timestamp": (local_start - pd.Timedelta(hours=tz)).dt.tz_localize("UTC")})
    for silver, epw in SILVER_TO_EPW.items():
        out[silver] = df[epw].astype(float)
    out["source_version"] = source_version
    return out


def epw_from_silver(weather: pd.DataFrame, template_text: str, station_id: str | None = None):
    """Write an 8760-hour EPW for CEA from silver weather_hourly, using the template for header and unmapped fields.

    Returns (epw text, UTC hour-start timestamps in EPW row order) so hourly CEA results map back to Silver time.
    """
    w = pd.DataFrame(weather)
    stations = sorted(w["station_id"].astype(str).unique())
    if station_id is None:
        if len(stations) != 1:
            raise ValueError(f"weather_hourly has stations {stations}; set params.station_id")
        station_id = stations[0]
    w = w[w["station_id"].astype(str) == station_id]
    if w.empty:
        raise ValueError(f"station_id {station_id!r} not in weather_hourly")
    header, data, tz = read_epw(template_text)
    ts = pd.to_datetime(w["timestamp"], utc=True).dt.tz_localize(None) + pd.Timedelta(hours=tz)
    w = w.assign(_y=ts.dt.year, _m=ts.dt.month, _d=ts.dt.day, _h=ts.dt.hour + 1)
    w = w[~((w._m == 2) & (w._d == 29))]
    if w.duplicated(["_m", "_d", "_h"]).any():
        raise ValueError("weather_hourly has more than one row for the same hour of year; pass a single year")
    by_hour = w.set_index(["_m", "_d", "_h"])
    rows, stamps = [], []
    for r in data:
        key = (int(r[1]), int(r[2]), int(r[3]))
        if key not in by_hour.index:
            raise ValueError(f"weather_hourly is missing hour (month, day, hour)={key}; CEA needs all 8760 hours")
        s = by_hour.loc[key]
        stamps.append(pd.Timestamp(s["timestamp"]))
        r = list(r)
        r[0] = str(int(s["_y"]))
        for silver, epw in SILVER_TO_EPW.items():
            if silver in s.index and not pd.isna(s[silver]):
                r[EPW_FIELDS.index(epw)] = _fmt(s[silver])
        rows.append(r)
    if len(rows) != HOURS_IN_YEAR:
        raise ValueError(f"template EPW has {len(rows)} rows, expected {HOURS_IN_YEAR}")
    buf = io.StringIO()
    buf.write("\n".join(header) + "\n")
    csv.writer(buf, lineterminator="\n").writerows(rows)
    utc = pd.DatetimeIndex(pd.to_datetime(pd.Series(stamps), utc=True))
    return buf.getvalue(), utc

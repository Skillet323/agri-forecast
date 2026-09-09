"""
Оркестратор конвейера. Запуск: python pipeline.py

Идемпотентность: все INSERT в raw/staging/curated выполнены как
INSERT OR REPLACE по стабильному ключу (field_id [+ obs_date]) — повторный
запуск с теми же входными параметрами перезаписывает те же строки, а не
плодит дубли.

Инкрементальность: диапазон дат берётся из config.SEASON_START/END; при
повторном запуске в течение сезона можно сузить диапазон до
[последняя_загруженная_дата+1, сегодня], чтобы догружать только новое
(в этой демонстрационной версии загружается весь сезон целиком для простоты
показа преподавателю; сама инкрементальная логика находится в
storage.db — ключ таблицы уже это поддерживает).
"""
import json
import uuid
import statistics
from datetime import datetime

import pandas as pd

from config import FIELDS, SEASON_START, SEASON_END
from storage.db import init_db, get_conn, log_ingestion, log_quality, save_raw, upsert_df
from ingestion import nasa_power, soilgrids, sentinel2, yield_bootstrap
from quality import checks


def run():
    run_id = str(uuid.uuid4())[:8]
    print(f"=== Запуск пайплайна run_id={run_id} ===")
    init_db()

    all_weather_rows, all_soil_rows, all_ndvi_rows = [], [], []

    # --- 1. INGEST + STAGING: погода и почва (реальные источники, с fallback) ---
    for f in FIELDS:
        fid, lat, lon = f["field_id"], f["lat"], f["lon"]

        # NASA POWER
        payload, status = nasa_power.fetch(fid, lat, lon, SEASON_START, SEASON_END)
        save_raw("raw_nasa_power", fid, datetime.utcnow().isoformat(), payload, status)
        try:
            params = payload["properties"]["parameter"]
            n_rows = 0
            for day_key, t2m_val in params["T2M"].items():
                obs_date = f"{day_key[0:4]}-{day_key[4:6]}-{day_key[6:8]}"
                row = {
                    "field_id": fid, "obs_date": obs_date,
                    "t2m": t2m_val,
                    "precip": params["PRECTOTCORR"].get(day_key),
                    "radiation": params["ALLSKY_SFC_SW_DWN"].get(day_key),
                    "humidity": params["RH2M"].get(day_key),
                    "source_status": status, "ingested_at": datetime.utcnow().isoformat(),
                }
                all_weather_rows.append(row)
                n_rows += 1
            log_ingestion(run_id, "nasa_power", fid, status, n_rows)
        except Exception as e:  # noqa: BLE001
            log_ingestion(run_id, "nasa_power", fid, "failed", 0, str(e))

        # SoilGrids
        payload, status = soilgrids.fetch(fid, lat, lon)
        save_raw("raw_soilgrids", fid, datetime.utcnow().isoformat(), payload, status)
        try:
            layers = {l["name"]: l["depths"][0]["values"]["mean"] for l in payload["properties"]["layers"]}
            row = {
                "field_id": fid,
                "ph": layers.get("phh2o"), "soc": layers.get("soc"),
                "clay": layers.get("clay"), "sand": layers.get("sand"),
                "nitrogen": layers.get("nitrogen"),
                "source_status": status, "ingested_at": datetime.utcnow().isoformat(),
            }
            all_soil_rows.append(row)
            log_ingestion(run_id, "soilgrids", fid, status, 1)
        except Exception as e:  # noqa: BLE001
            log_ingestion(run_id, "soilgrids", fid, "failed", 0, str(e))

        # Sentinel-2 NDVI
        payload, status = sentinel2.fetch(fid, lat, lon, SEASON_START, SEASON_END)
        save_raw("raw_sentinel2", fid, datetime.utcnow().isoformat(), payload, status)
        try:
            n_rows = 0
            for obs in payload["observations"]:
                if obs["ndvi"] is None:
                    continue  # облачность -> пропуск, не "нулевое" значение
                all_ndvi_rows.append({
                    "field_id": fid, "obs_date": obs["date"], "ndvi": obs["ndvi"],
                    "source_status": status, "ingested_at": datetime.utcnow().isoformat(),
                })
                n_rows += 1
            log_ingestion(run_id, "sentinel2", fid, status, n_rows)
        except Exception as e:  # noqa: BLE001
            log_ingestion(run_id, "sentinel2", fid, "failed", 0, str(e))

    weather_df = pd.DataFrame(all_weather_rows)
    soil_df = pd.DataFrame(all_soil_rows)
    ndvi_df = pd.DataFrame(all_ndvi_rows)

    # --- 2. КОНТРОЛЬ КАЧЕСТВА (staging) ---
    field_ids = [f["field_id"] for f in FIELDS]
    checks.check_completeness(run_id, weather_df, ["t2m", "precip", "radiation", "humidity"], "staging_weather")
    checks.check_uniqueness(run_id, weather_df, ["field_id", "obs_date"], "staging_weather")
    checks.check_validity_types(run_id, weather_df, ["t2m", "precip", "radiation", "humidity"], "staging_weather")
    checks.check_ranges(run_id, weather_df, {
        "t2m": (-40, 55), "precip": (0, 300), "radiation": (0, 40), "humidity": (0, 100)
    }, "staging_weather")
    checks.check_referential_integrity(run_id, weather_df["field_id"].unique(), field_ids, "staging_weather")
    if len(weather_df):
        checks.check_freshness(run_id, weather_df["obs_date"].max(), 365 * 3, "staging_weather")

    checks.check_completeness(run_id, soil_df, ["ph", "soc", "clay", "sand", "nitrogen"], "staging_soil")
    checks.check_uniqueness(run_id, soil_df, ["field_id"], "staging_soil")
    checks.check_ranges(run_id, soil_df, {"ph": (30, 90), "soc": (0, 200), "clay": (0, 700), "sand": (0, 950)}, "staging_soil")
    checks.check_referential_integrity(run_id, soil_df["field_id"].unique(), field_ids, "staging_soil")

    checks.check_completeness(run_id, ndvi_df, ["ndvi"], "staging_ndvi")
    checks.check_uniqueness(run_id, ndvi_df, ["field_id", "obs_date"], "staging_ndvi")
    checks.check_ranges(run_id, ndvi_df, {"ndvi": (-1.0, 1.0)}, "staging_ndvi")
    checks.check_referential_integrity(run_id, ndvi_df["field_id"].unique(), field_ids, "staging_ndvi")

    # --- 3. ЗАПИСЬ STAGING В БД (идемпотентно: INSERT OR REPLACE по ключу) ---
    weather_df["obs_date"] = weather_df["obs_date"].astype(str)
    upsert_df("staging_weather", weather_df)
    upsert_df("staging_soil", soil_df)
    if len(ndvi_df):
        ndvi_df["obs_date"] = ndvi_df["obs_date"].astype(str)
    upsert_df("staging_ndvi", ndvi_df)

    # --- 4. CURATED: широкая витрина field_id x date ---
    weather_df["obs_date"] = pd.to_datetime(weather_df["obs_date"])
    ndvi_df["obs_date"] = pd.to_datetime(ndvi_df["obs_date"]) if len(ndvi_df) else ndvi_df

    curated = weather_df.merge(soil_df.drop(columns=["source_status", "ingested_at"]), on="field_id", how="left")
    # NDVI снимается раз в ~5 дней -> добавляем к ежедневной сетке ffill по каждому полю
    ndvi_pivot = ndvi_df.set_index(["field_id", "obs_date"])["ndvi"] if len(ndvi_df) else pd.Series(dtype=float)
    curated = curated.set_index(["field_id", "obs_date"])
    if len(ndvi_pivot):
        curated["ndvi"] = ndvi_pivot
        curated["ndvi"] = curated.groupby(level="field_id")["ndvi"].ffill()
    else:
        curated["ndvi"] = None
    curated = curated.reset_index()

    # --- 5. Целевая переменная (урожайность) на конец сезона ---
    yield_rows = []
    for fid in field_ids:
        sub = curated[curated["field_id"] == fid]
        soil_row = soil_df[soil_df["field_id"] == fid].iloc[0]
        features = {
            "mean_precip": sub["precip"].mean(),
            "mean_radiation": sub["radiation"].mean(),
            "mean_ndvi_peak": sub["ndvi"].max() if sub["ndvi"].notna().any() else 0.3,
            "soc": soil_row["soc"],
            "ph": soil_row["ph"],
        }
        payload, status = yield_bootstrap.fetch(fid, features, season=SEASON_START.year)
        save_raw("raw_yield_history", fid, datetime.utcnow().isoformat(), payload, status)
        yield_rows.append({
            "field_id": fid, "season": SEASON_START.year, "yield_t_ha": payload["yield_t_ha"],
            "source_status": status, "ingested_at": datetime.utcnow().isoformat(),
        })
        log_ingestion(run_id, "yield_bootstrap", fid, status, 1)

    yield_df = pd.DataFrame(yield_rows)
    upsert_df("staging_yield", yield_df)

    curated = curated.merge(yield_df[["field_id", "yield_t_ha"]], on="field_id", how="left")
    curated["obs_date"] = curated["obs_date"].dt.strftime("%Y-%m-%d")
    curated["built_at"] = datetime.utcnow().isoformat()
    curated_to_save = curated[[
        "field_id", "obs_date", "t2m", "precip", "radiation", "humidity", "ndvi",
        "ph", "soc", "clay", "sand", "nitrogen", "yield_t_ha", "built_at"
    ]]
    upsert_df("curated_field_daily", curated_to_save)

    print(f"Загружено полей: {len(FIELDS)}")
    print(f"Строк погоды: {len(weather_df)}, строк NDVI: {len(ndvi_df)}, строк почвы: {len(soil_df)}")
    print(f"Итоговая витрина curated_field_daily: {len(curated_to_save)} строк")
    print(f"Статусы источников: "
          f"weather={weather_df['source_status'].unique().tolist() if len(weather_df) else []}, "
          f"soil={soil_df['source_status'].unique().tolist() if len(soil_df) else []}, "
          f"ndvi={ndvi_df['source_status'].unique().tolist() if len(ndvi_df) else []}")
    print(f"=== Пайплайн завершён, run_id={run_id} ===")
    return run_id


if __name__ == "__main__":
    run()

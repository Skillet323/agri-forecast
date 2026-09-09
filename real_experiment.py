"""
РЕАЛЬНЫЙ эксперимент для статьи (в отличие от demo-пайплайна pipeline.py,
где урожайность на уровне 5 полей — синтетика с нереалистичной калибровкой).

Идея: заменить единицу наблюдения "поле x один сезон" на "страна x год".
Урожайность пшеницы по РФ — официальная статистика FAOSTAT (реальная, ~20+ лет
истории). Погодные признаки — тоже реальные (NASA POWER), агрегированные по
вегетационному периоду каждого года в одной репрезентативной точке. Это даёт:
  - honestly реальные данные по всем осям (требование пользователя);
  - n ~ 20-25 лет вместо n=5 полей -> статистически более осмысленная LOYO-CV.

Запуск: python real_experiment.py
Результат: data/real_model_results.json + строки в таблице curated_national_annual.

ВАЖНО: FAOSTAT-часть не протестирована вживую (сеть песочницы блокирует
fenixservices.fao.org) — при падении на реальном запросе смотри сообщение
об ошибке в выводе, это будет расхождение в структуре API, а не тихий сбой.
NASA POWER часть уже подтверждена вживую на этом же проекте.
"""
import json
import uuid
from datetime import date, timezone, datetime
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, mean_absolute_percentage_error
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from config import (
    NATIONAL_REF_POINT, REAL_EXPERIMENT_YEAR_START, REAL_EXPERIMENT_YEAR_END,
)
from ingestion import nasa_power, soilgrids, faostat_yield, worldbank_yield
from storage.db import init_db, get_conn, log_ingestion, log_quality, save_raw, upsert_df

OUT_JSON = Path(__file__).parent / "data" / "real_model_results.json"

BASELINE_FEATURES = ["mean_t2m", "sum_precip"]
EXTENDED_FEATURES = BASELINE_FEATURES + ["mean_radiation", "mean_humidity", "soc", "ph"]


def fetch_national_yield(run_id):
    """
    Цепочка реальных источников с graceful fallback:
      1) FAOSTAT (урожайность именно пшеницы) — приоритетный, но на практике
         может лежать целиком как сторонний сервис (подтверждено 521 от их
         же Cloudflare, не связано с нашим кодом).
      2) World Bank (урожайность зерновых КАК ГРУППЫ — не то же самое, что
         пшеница; используется только если FAOSTAT недоступен, и это ЯВНО
         фиксируется в статусе/логах, чтобы не перепутать при анализе).
      3) Синтетика — крайний случай, чтобы демо не останавливалось; для
         статьи такой результат не годится.
    """
    series, status = {}, "failed"
    raw_payload, source_used = {}, None

    try:
        payload = faostat_yield.fetch_real(REAL_EXPERIMENT_YEAR_START, REAL_EXPERIMENT_YEAR_END)
        series = faostat_yield.parse_series(payload)
        status, raw_payload, source_used = "live", payload, "faostat_wheat"
    except Exception as e:  # noqa: BLE001
        print(f"FAOSTAT недоступен ({e}) -> пробуем World Bank (урожайность зерновых, не только пшеницы)")
        raw_payload = {"_faostat_error": str(e)}

    if not series:
        try:
            wb_series, wb_status, wb_payload = worldbank_yield.fetch(REAL_EXPERIMENT_YEAR_START, REAL_EXPERIMENT_YEAR_END)
            if wb_status == "live":
                series, status, source_used = wb_series, "live", "worldbank_cereals"
                raw_payload = {**raw_payload, "worldbank": wb_payload}
            else:
                raw_payload = {**raw_payload, "worldbank_error": wb_payload.get("_error")}
        except Exception as e:  # noqa: BLE001
            raw_payload = {**raw_payload, "worldbank_error": str(e)}

    if not series:
        series = faostat_yield._synthetic_series(REAL_EXPERIMENT_YEAR_START, REAL_EXPERIMENT_YEAR_END)
        status, source_used = "degraded_synthetic", "synthetic_fallback"
        raw_payload = {**raw_payload, "_synthetic": True}

    save_raw("raw_faostat_yield", "NATIONAL", datetime.now(timezone.utc).isoformat(),
             {**raw_payload, "_source_used": source_used}, status)
    log_ingestion(run_id, f"yield::{source_used}", "NATIONAL", status, len(series))
    print(f"Урожайность: источник={source_used}, статус={status}, лет получено={len(series)}")
    if status == "degraded_synthetic":
        print("  ПРЕДУПРЕЖДЕНИЕ: fallback-синтетика — для статьи нужен status=live.")
    elif source_used == "worldbank_cereals":
        print("  ВНИМАНИЕ: это урожайность ЗЕРНОВЫХ В ЦЕЛОМ (World Bank), не пшеницы конкретно —")
        print("  явно отразить эту замену предмета анализа в статье, если используешь этот результат.")
    return series, status, source_used


def fetch_annual_weather(run_id):
    """Один большой запрос NASA POWER на весь диапазон лет, затем нарезка по
    вегетационному окну (март-июль) каждого года — экономит запросы и укладывается
    в тот же реальный live-режим, что уже подтверждён в pipeline.py."""
    lat, lon = NATIONAL_REF_POINT["lat"], NATIONAL_REF_POINT["lon"]
    start = date(REAL_EXPERIMENT_YEAR_START, 3, 1)
    end = date(REAL_EXPERIMENT_YEAR_END, 7, 15)
    try:
        payload = nasa_power.fetch_real(lat, lon, start, end)
        status = "live"
    except Exception as e:  # noqa: BLE001
        # переиспользуем встроенный synthetic-генератор модуля через fetch() с фиктивным field_id
        payload, status = nasa_power.fetch("NATIONAL", lat, lon, start, end)
        payload["_error"] = str(e)

    params = payload["properties"]["parameter"]
    by_year = {}
    for day_key, t2m_val in params["T2M"].items():
        y = int(day_key[0:4])
        m = int(day_key[4:6])
        d = int(day_key[6:8])
        # берём только вегетационное окно 03-01..07-15 каждого года
        if not (date(y, 3, 1) <= date(y, m, d) <= date(y, 7, 15)):
            continue
        by_year.setdefault(y, {"t2m": [], "precip": [], "rad": [], "rh": []})
        by_year[y]["t2m"].append(t2m_val)
        by_year[y]["precip"].append(params["PRECTOTCORR"].get(day_key, 0.0))
        by_year[y]["rad"].append(params["ALLSKY_SFC_SW_DWN"].get(day_key))
        by_year[y]["rh"].append(params["RH2M"].get(day_key))

    rows = []
    for y, vals in sorted(by_year.items()):
        rows.append({
            "year": y,
            "mean_t2m": float(np.mean(vals["t2m"])),
            "sum_precip": float(np.sum(vals["precip"])),
            "mean_radiation": float(np.nanmean(vals["rad"])),
            "mean_humidity": float(np.nanmean(vals["rh"])),
        })
    log_ingestion(run_id, "nasa_power_annual", "NATIONAL", status, len(rows))
    print(f"NASA POWER (годовая агрегация): статус={status}, лет получено={len(rows)}")
    return pd.DataFrame(rows), status


def fetch_static_soil(run_id):
    payload, status = soilgrids.fetch("NATIONAL", NATIONAL_REF_POINT["lat"], NATIONAL_REF_POINT["lon"])
    log_ingestion(run_id, "soilgrids_national", "NATIONAL", status, 1)
    print(f"SoilGrids (репрезентативная точка): статус={status}")
    try:
        layers = {l["name"]: l["depths"][0]["values"]["mean"] for l in payload["properties"]["layers"]}
        return layers.get("phh2o"), layers.get("soc"), status
    except Exception:  # noqa: BLE001
        return None, None, "failed"


def build_dataset(run_id):
    yield_series, yield_status, yield_source = fetch_national_yield(run_id)
    weather_df, weather_status = fetch_annual_weather(run_id)
    ph, soc, soil_status = fetch_static_soil(run_id)

    weather_df["yield_t_ha"] = weather_df["year"].map(yield_series)
    weather_df["ph"] = ph
    weather_df["soc"] = soc
    weather_df["yield_source_status"] = f"{yield_status}::{yield_source}"
    weather_df["weather_source_status"] = weather_status
    weather_df["soil_source_status"] = soil_status
    weather_df["built_at"] = datetime.now(timezone.utc).isoformat()

    df = weather_df.dropna(subset=["yield_t_ha"]).reset_index(drop=True)

    # контроль качества: полнота целевой переменной и годового покрытия
    log_quality(run_id, "curated_national_annual", "completeness", "yield_present_share",
                passed=1, details=f"{len(df)}/{len(weather_df)} лет с известной урожайностью")
    log_quality(run_id, "curated_national_annual", "range", "yield_in_range[0.5,8.0]_t_ha",
                passed=int(df["yield_t_ha"].between(0.5, 8.0).all()),
                details=f"мин={df['yield_t_ha'].min() if len(df) else None}, макс={df['yield_t_ha'].max() if len(df) else None}")

    upsert_df("curated_national_annual", df[[
        "year", "yield_t_ha", "yield_source_status", "mean_t2m", "sum_precip",
        "mean_radiation", "mean_humidity", "weather_source_status", "ph", "soc",
        "soil_source_status", "built_at",
    ]])
    return df, {"yield": yield_status, "yield_source": yield_source, "weather": weather_status, "soil": soil_status}


def loyo_cv(df: pd.DataFrame, feature_cols: list[str]):
    """Leave-One-Year-Out CV. Возвращает MAE/MAPE + прогнозы по годам."""
    sub = df.copy()
    sub[feature_cols] = sub[feature_cols].fillna(sub[feature_cols].mean())
    years = sub["year"].tolist()
    y_true, y_pred = [], []
    for test_year in years:
        train = sub[sub["year"] != test_year]
        test = sub[sub["year"] == test_year]
        model = make_pipeline(StandardScaler(), Ridge(alpha=1.0))
        model.fit(train[feature_cols], train["yield_t_ha"])
        y_pred.append(float(model.predict(test[feature_cols])[0]))
        y_true.append(float(test["yield_t_ha"].iloc[0]))
    mae = mean_absolute_error(y_true, y_pred)
    mape = mean_absolute_percentage_error(y_true, y_pred)
    return {
        "years": years, "y_true": [round(v, 3) for v in y_true], "y_pred": [round(v, 3) for v in y_pred],
        "mae_t_ha": round(mae, 4), "mape_pct": round(mape * 100, 2), "n": len(years),
    }


def main():
    run_id = str(uuid.uuid4())[:8]
    print(f"=== Реальный эксперимент (национальный годовой ряд), run_id={run_id} ===")
    init_db()
    df, statuses = build_dataset(run_id)

    print(f"\nВсего лет с полным набором данных (реальная урожайность найдена): {len(df)}")
    if len(df) < 8:
        print("ПРЕДУПРЕЖДЕНИЕ: лет получилось мало для содержательной LOYO-CV — проверь,")
        print("что FAOSTAT реально вернул нужный диапазон годов (см. статус выше).")
        if len(df) < 3:
            print("Недостаточно данных для кросс-валидации — эксперимент прерван.")
            OUT_JSON.write_text(json.dumps({"error": "insufficient_data", "n_years": len(df), "statuses": statuses},
                                             ensure_ascii=False, indent=2), encoding="utf-8")
            return

    baseline = loyo_cv(df, BASELINE_FEATURES)
    extended = loyo_cv(df, EXTENDED_FEATURES)

    result = {
        "statuses": statuses,
        "yield_source": statuses.get("yield_source"),
        "n_years": len(df),
        "years_range": [int(df["year"].min()), int(df["year"].max())],
        "baseline_weather_only": baseline,
        "extended_weather_soil": extended,
    }
    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== LOYO-CV: базовая модель (T, осадки) vs расширенная (+радиация, влажность, почва) ===")
    print(f"n лет = {len(df)}, диапазон {result['years_range'][0]}-{result['years_range'][1]}")
    print(f"baseline_weather_only:  MAE={baseline['mae_t_ha']} т/га, MAPE={baseline['mape_pct']}%")
    print(f"extended_weather_soil:  MAE={extended['mae_t_ha']} т/га, MAPE={extended['mape_pct']}%")
    print(f"\nСтатусы источников: {statuses}")
    print(f"Полные результаты: {OUT_JSON}")


if __name__ == "__main__":
    main()

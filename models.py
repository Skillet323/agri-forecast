"""
Прогнозное ядро: две модели урожайности + абляция источников + ранний прогноз.

Идея (см. README и статью):
  - Строим признаки НАКОПИТЕЛЬНО по срезам сезона (cutoff), а не только по
    итогу — это даёт (а) больше обучающих примеров, чем 5 (число полей),
    и (б) прямой ответ на вопрос "как рано можно спрогнозировать урожай".
  - Модель A (baseline): только метео + почва.
  - Модель B (extended): метео + почва + NDVI.
  - Валидация: Leave-One-Field-Out (LOFO), а не k-fold по строкам — иначе
    строки одного поля утекут между train/test (data leakage по сущности).
  - Метрика: MAE и MAPE по т/га, отдельно по каждому cutoff'у сезона.

Честное ограничение: n=5 полей. Даже LOFO с 5 полями даёт очень шумную
оценку обобщающей способности — это explicitly обсуждается в отчёте/статье,
а не скрывается.
"""
import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, mean_absolute_percentage_error
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

DB_PATH = Path(__file__).parent / "data" / "warehouse.sqlite"
OUT_PATH = Path(__file__).parent / "data" / "model_results.json"

# Срезы сезона: доля прошедших дней от начала (03-01) до конца (07-15) наблюдений
CUTOFF_FRACTIONS = {"early_tillering": 0.30, "mid_heading": 0.60, "full_season": 1.00}

BASELINE_FEATURES = ["mean_t2m", "sum_precip", "mean_radiation", "mean_humidity", "ph", "soc", "clay", "sand", "nitrogen"]
EXTENDED_FEATURES = BASELINE_FEATURES + ["ndvi_mean_so_far", "ndvi_peak_so_far"]


def load_daily():
    con = sqlite3.connect(DB_PATH)
    df = pd.read_sql("select * from curated_field_daily", con, parse_dates=["obs_date"])
    con.close()
    return df


def build_cutoff_features(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for field_id, g in df.groupby("field_id"):
        g = g.sort_values("obs_date")
        start, end = g["obs_date"].min(), g["obs_date"].max()
        total_days = (end - start).days
        yield_val = g["yield_t_ha"].iloc[0]
        for cutoff_name, frac in CUTOFF_FRACTIONS.items():
            cutoff_date = start + pd.Timedelta(days=int(total_days * frac))
            sub = g[g["obs_date"] <= cutoff_date]
            if sub.empty:
                continue
            ndvi_obs = sub["ndvi"].dropna()
            rows.append({
                "field_id": field_id,
                "cutoff": cutoff_name,
                "cutoff_frac": frac,
                "mean_t2m": sub["t2m"].mean(),
                "sum_precip": sub["precip"].sum(),
                "mean_radiation": sub["radiation"].mean(),
                "mean_humidity": sub["humidity"].mean(),
                "ph": sub["ph"].iloc[0],
                "soc": sub["soc"].iloc[0],
                "clay": sub["clay"].iloc[0],
                "sand": sub["sand"].iloc[0],
                "nitrogen": sub["nitrogen"].iloc[0],
                "ndvi_mean_so_far": ndvi_obs.mean() if len(ndvi_obs) else np.nan,
                "ndvi_peak_so_far": ndvi_obs.max() if len(ndvi_obs) else np.nan,
                "yield_t_ha": yield_val,
            })
    return pd.DataFrame(rows)


def lofo_cv(features_df: pd.DataFrame, feature_cols: list[str], cutoff_name: str):
    """Leave-One-Field-Out CV для одного среза сезона. Возвращает y_true, y_pred, MAE, MAPE."""
    sub = features_df[features_df["cutoff"] == cutoff_name].copy()
    sub[feature_cols] = sub[feature_cols].fillna(sub[feature_cols].mean())
    fields = sub["field_id"].unique()
    y_true, y_pred, held_out = [], [], []
    for test_field in fields:
        train = sub[sub["field_id"] != test_field]
        test = sub[sub["field_id"] == test_field]
        model = make_pipeline(StandardScaler(), Ridge(alpha=1.0))
        model.fit(train[feature_cols], train["yield_t_ha"])
        pred = model.predict(test[feature_cols])[0]
        y_true.append(test["yield_t_ha"].iloc[0])
        y_pred.append(pred)
        held_out.append(test_field)
    mae = mean_absolute_error(y_true, y_pred)
    mape = mean_absolute_percentage_error(y_true, y_pred)
    return {
        "cutoff": cutoff_name,
        "held_out_fields": held_out,
        "y_true": [round(v, 3) for v in y_true],
        "y_pred": [round(v, 3) for v in y_pred],
        "mae_t_ha": round(mae, 4),
        "mape_pct": round(mape * 100, 2),
    }


def main():
    daily = load_daily()
    feats = build_cutoff_features(daily)

    results = {"baseline_meteo_soil": [], "extended_with_ndvi": []}
    for cutoff_name in CUTOFF_FRACTIONS:
        results["baseline_meteo_soil"].append(lofo_cv(feats, BASELINE_FEATURES, cutoff_name))
        results["extended_with_ndvi"].append(lofo_cv(feats, EXTENDED_FEATURES, cutoff_name))

    OUT_PATH.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=== Абляция источников данных x срез сезона (LOFO-CV, MAE в т/га) ===")
    print(f"{'cutoff':15s} {'baseline (метео+почва)':25s} {'+NDVI':15s} {'дельта':>8s}")
    for a, b in zip(results["baseline_meteo_soil"], results["extended_with_ndvi"]):
        delta = a["mae_t_ha"] - b["mae_t_ha"]
        print(f"{a['cutoff']:15s} {a['mae_t_ha']:<25} {b['mae_t_ha']:<15} {delta:>8.4f}")
    print("\nПРЕДУПРЕЖДЕНИЕ: n=5 полей -> LOFO-CV статистически ненадёжен (по 1 наблюдению")
    print("на фолд). Результат показывает НАПРАВЛЕНИЕ эффекта и пригоден для демонстрации")
    print("методики валидации, но не для сильных количественных выводов на защите/статье.")
    print(f"\nПолные результаты: {OUT_PATH}")


if __name__ == "__main__":
    main()

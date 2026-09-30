"""
Основной конвейер: пять полей, один вегетационный сезон. Запуск: python pipeline.py

Что происходит за один запуск:
  источники -> raw -> staging (+ проверки качества) -> curated -> урожайность -> lineage

Повторный запуск не ломает и не дублирует данные. Две вещи, которые это обеспечивают:

  Инкрементальность. В load_state лежит последняя загруженная дата по каждой паре
  (источник, поле). На втором запуске окно запроса сужается до ещё не загруженных дат,
  а если сезон догружен целиком — сетевого вызова не будет вообще, в журнале появится
  статус skipped_up_to_date.

  Идемпотентность. Запись идёт через INSERT OR REPLACE по первичному ключу. Здесь есть
  тонкость: REPLACE меняет строку целиком, поэтому урожайность нельзя писать вместе
  с дневными строками — иначе на инкрементальном запуске у старых дней затёрлись бы
  погода и NDVI. Урожайность проставляется отдельным UPDATE после записи витрины.
"""
import uuid
from datetime import datetime, timezone, timedelta, date

import pandas as pd

from config import FIELDS, SEASON_START, SEASON_END
from storage.db import (
    init_db, get_conn, log_ingestion, save_raw, upsert_df, save_quarantine,
    get_last_loaded_date, set_last_loaded_date, save_lineage, update_yield_for_field,
)
from ingestion import nasa_power, soilgrids, sentinel2, yield_bootstrap
from quality import checks

# Статусы, которые источник имеет право вернуть. Всё остальное в этой колонке —
# признак опечатки в коде загрузчика, проверка допустимости это поймает.
ALLOWED_STATUSES = {"live", "degraded_synthetic", "failed", "skipped_up_to_date"}
ALLOWED_CROPS = {f["crop"] for f in FIELDS}

WEATHER_RANGES = {"t2m": (-40, 55), "precip": (0, 300), "radiation": (0, 40), "humidity": (0, 100)}
SOIL_RANGES = {"ph": (30, 90), "soc": (0, 200), "clay": (0, 700), "sand": (0, 950)}


def incremental_window(source, field_id):
    """
    Какой отрезок дат ещё нужно догрузить. Возвращает (начало, конец, всё_уже_есть).

    Первый запуск отдаёт весь сезон. Последующие — только хвост после последней
    загруженной даты. Когда хвост пустой, вызывающий код пропускает источник.
    """
    last = get_last_loaded_date(source, field_id)
    if last is None:
        return SEASON_START, SEASON_END, False
    new_start = date.fromisoformat(last) + timedelta(days=1)
    if new_start > SEASON_END:
        return new_start, SEASON_END, True
    return new_start, SEASON_END, False


def _now():
    return datetime.now(timezone.utc).isoformat()


def ingest_weather(run_id, field, rows):
    fid, lat, lon = field["field_id"], field["lat"], field["lon"]
    start, end, up_to_date = incremental_window("nasa_power", fid)
    if up_to_date:
        log_ingestion(run_id, "nasa_power", fid, "skipped_up_to_date", 0)
        print(f"  {fid}: погода уже загружена по {SEASON_END}, запрос не делаем")
        return

    payload, status = nasa_power.fetch(fid, lat, lon, start, end)
    save_raw("raw_nasa_power", fid, _now(), payload, status)
    try:
        params = payload["properties"]["parameter"]
        n_rows = 0
        for day_key, t2m_val in params["T2M"].items():
            rows.append({
                "field_id": fid,
                "obs_date": f"{day_key[0:4]}-{day_key[4:6]}-{day_key[6:8]}",
                "t2m": t2m_val,
                "precip": params["PRECTOTCORR"].get(day_key),
                "radiation": params["ALLSKY_SFC_SW_DWN"].get(day_key),
                "humidity": params["RH2M"].get(day_key),
                "source_status": status,
                "ingested_at": _now(),
            })
            n_rows += 1
        log_ingestion(run_id, "nasa_power", fid, status, n_rows)
        if n_rows:
            set_last_loaded_date("nasa_power", fid, end.isoformat())
    except Exception as e:  # noqa: BLE001
        log_ingestion(run_id, "nasa_power", fid, "failed", 0, str(e))


def ingest_soil(run_id, field, rows):
    """Почву тянем каждый запуск целиком: она меняется раз в несколько лет, инкремент тут не нужен."""
    fid, lat, lon = field["field_id"], field["lat"], field["lon"]
    payload, status = soilgrids.fetch(fid, lat, lon)
    save_raw("raw_soilgrids", fid, _now(), payload, status)
    try:
        layers = {l["name"]: l["depths"][0]["values"]["mean"] for l in payload["properties"]["layers"]}
        rows.append({
            "field_id": fid,
            "ph": layers.get("phh2o"), "soc": layers.get("soc"),
            "clay": layers.get("clay"), "sand": layers.get("sand"),
            "nitrogen": layers.get("nitrogen"),
            "source_status": status, "ingested_at": _now(),
        })
        log_ingestion(run_id, "soilgrids", fid, status, 1)
    except Exception as e:  # noqa: BLE001
        log_ingestion(run_id, "soilgrids", fid, "failed", 0, str(e))


def ingest_ndvi(run_id, field, rows):
    fid, lat, lon = field["field_id"], field["lat"], field["lon"]
    start, end, up_to_date = incremental_window("sentinel2", fid)
    if up_to_date:
        log_ingestion(run_id, "sentinel2", fid, "skipped_up_to_date", 0)
        return

    payload, status = sentinel2.fetch(fid, lat, lon, start, end)
    save_raw("raw_sentinel2", fid, _now(), payload, status)
    try:
        n_rows = 0
        for obs in payload["observations"]:
            # Облачный день — это отсутствие наблюдения, а не ноль. Пропускаем строку целиком,
            # иначе провал в NDVI будет выглядеть как погибшие посевы.
            if obs["ndvi"] is None:
                continue
            rows.append({
                "field_id": fid, "obs_date": obs["date"], "ndvi": obs["ndvi"],
                "source_status": status, "ingested_at": _now(),
            })
            n_rows += 1
        log_ingestion(run_id, "sentinel2", fid, status, n_rows)
        set_last_loaded_date("sentinel2", fid, end.isoformat())
    except Exception as e:  # noqa: BLE001
        log_ingestion(run_id, "sentinel2", fid, "failed", 0, str(e))


def quarantine_bad_rows(run_id, df, ranges, table_name):
    """
    Что делаем с некорректными данными.

    Правило простое: строка, в которой хотя бы одно значение вылетело за физически
    возможные границы, в витрину не идёт. Но и не удаляется — уходит в таблицу quarantine
    вместе с причиной, чтобы потом можно было посмотреть, что именно забраковали.

    Пропуски (NaN) сюда не попадают: отсутствие значения это нормальная ситуация
    (облачность, дыра в источнике), с ней разбирается проверка полноты.

    Возвращает очищенный датафрейм.
    """
    if df.empty:
        return df

    bad_mask = pd.Series(False, index=df.index)
    reasons = {}
    for col, (lo, hi) in ranges.items():
        if col not in df.columns:
            continue
        col_bad = df[col].notna() & ((df[col] < lo) | (df[col] > hi))
        for idx in df.index[col_bad]:
            reasons.setdefault(idx, []).append(f"{col} вне [{lo}, {hi}]")
        bad_mask |= col_bad

    if not bad_mask.any():
        return df

    bad_rows = df[bad_mask]
    for idx, row in bad_rows.iterrows():
        save_quarantine(run_id, table_name, [row.to_dict()], "; ".join(reasons.get(idx, ["диапазон"])))
    print(f"  {table_name}: {len(bad_rows)} строк отправлено в карантин")
    return df[~bad_mask]


def run_quality_checks(run_id, weather_df, soil_df, ndvi_df, field_ids):
    if len(weather_df):
        cols = ["t2m", "precip", "radiation", "humidity"]
        checks.check_completeness(run_id, weather_df, cols, "staging_weather")
        checks.check_uniqueness(run_id, weather_df, ["field_id", "obs_date"], "staging_weather")
        checks.check_validity_types(run_id, weather_df, cols, "staging_weather")
        checks.check_admissibility(run_id, weather_df, "source_status", ALLOWED_STATUSES, "staging_weather")
        checks.check_ranges(run_id, weather_df, WEATHER_RANGES, "staging_weather")
        checks.check_referential_integrity(run_id, weather_df["field_id"].unique(), field_ids, "staging_weather")
        checks.check_freshness(run_id, weather_df["obs_date"].max(), 365 * 3, "staging_weather")
    else:
        print("  Новых строк погоды нет — проверки staging_weather пропущены")

    if len(soil_df):
        checks.check_completeness(run_id, soil_df, ["ph", "soc", "clay", "sand", "nitrogen"], "staging_soil")
        checks.check_uniqueness(run_id, soil_df, ["field_id"], "staging_soil")
        checks.check_admissibility(run_id, soil_df, "source_status", ALLOWED_STATUSES, "staging_soil")
        checks.check_ranges(run_id, soil_df, SOIL_RANGES, "staging_soil")
        checks.check_referential_integrity(run_id, soil_df["field_id"].unique(), field_ids, "staging_soil")

    if len(ndvi_df):
        checks.check_completeness(run_id, ndvi_df, ["ndvi"], "staging_ndvi")
        checks.check_uniqueness(run_id, ndvi_df, ["field_id", "obs_date"], "staging_ndvi")
        checks.check_admissibility(run_id, ndvi_df, "source_status", ALLOWED_STATUSES, "staging_ndvi")
        checks.check_ranges(run_id, ndvi_df, {"ndvi": (-1.0, 1.0)}, "staging_ndvi")
        checks.check_referential_integrity(run_id, ndvi_df["field_id"].unique(), field_ids, "staging_ndvi")

    # Справочник полей — тоже данные, и в нём тоже бывают опечатки.
    checks.check_admissibility(run_id, pd.DataFrame({"crop": [f["crop"] for f in FIELDS]}),
                               "crop", ALLOWED_CROPS, "reference_fields")


def build_curated(weather_df, soil_df, ndvi_df):
    """Собрать широкую витрину поле x день. Урожайность здесь ещё пустая, её ставим позже."""
    weather_df = weather_df.copy()
    weather_df["obs_date"] = pd.to_datetime(weather_df["obs_date"])

    curated = weather_df.merge(
        soil_df.drop(columns=["source_status", "ingested_at"]), on="field_id", how="left"
    ).set_index(["field_id", "obs_date"])

    if len(ndvi_df):
        ndvi = ndvi_df.copy()
        ndvi["obs_date"] = pd.to_datetime(ndvi["obs_date"])
        curated["ndvi"] = ndvi.set_index(["field_id", "obs_date"])["ndvi"]
        # Снимок раз в ~5 дней, а сетка дневная. Тянем последнее известное значение вперёд:
        # для медленно меняющейся вегетации это разумнее, чем оставлять дыры.
        curated["ndvi"] = curated.groupby(level="field_id")["ndvi"].ffill()
    else:
        curated["ndvi"] = None

    curated = curated.reset_index()
    curated["obs_date"] = curated["obs_date"].dt.strftime("%Y-%m-%d")
    curated["built_at"] = _now()
    curated["yield_t_ha"] = None
    return curated[[
        "field_id", "obs_date", "t2m", "precip", "radiation", "humidity", "ndvi",
        "ph", "soc", "clay", "sand", "nitrogen", "yield_t_ha", "built_at",
    ]]


def compute_yield(run_id, field_ids, soil_df):
    """
    Посчитать урожайность по накопленному сезону и записать происхождение показателя.

    Признаки берём из БД, а не из датафреймов этого запуска: на инкрементальном запуске
    новых строк может не быть совсем, и агрегаты по ним ушли бы в NaN.
    """
    conn = get_conn()
    full = pd.read_sql("SELECT * FROM curated_field_daily", conn)
    conn.close()

    rows = []
    for fid in field_ids:
        sub = full[full["field_id"] == fid]
        soil_row = soil_df[soil_df["field_id"] == fid]
        if sub.empty or soil_row.empty:
            print(f"  {fid}: данных пока нет, урожайность не считаем")
            continue
        soil_row = soil_row.iloc[0]

        features = {
            "mean_precip": sub["precip"].mean(),
            "mean_radiation": sub["radiation"].mean(),
            "mean_ndvi_peak": sub["ndvi"].max() if sub["ndvi"].notna().any() else 0.3,
            "soc": soil_row["soc"],
            "ph": soil_row["ph"],
        }
        payload, status = yield_bootstrap.fetch(fid, features, season=SEASON_START.year)
        fetched_at = _now()
        save_raw("raw_yield_history", fid, fetched_at, payload, status)
        rows.append({
            "field_id": fid, "season": SEASON_START.year, "yield_t_ha": payload["yield_t_ha"],
            "source_status": status, "ingested_at": fetched_at,
        })
        log_ingestion(run_id, "yield_bootstrap", fid, status, 1)
        update_yield_for_field("curated_field_daily", fid, payload["yield_t_ha"])

        save_lineage(
            metric_name="field_yield", metric_key=fid, run_id=run_id,
            raw_table="raw_yield_history", raw_key=fid, raw_fetched_at=fetched_at,
            curated_table="curated_field_daily",
            transformation_note=(
                f"агрегаты по curated_field_daily для {fid} "
                f"(осадки={features['mean_precip']:.2f}, радиация={features['mean_radiation']:.2f}, "
                f"пик NDVI={features['mean_ndvi_peak']:.2f}) плюс soc/ph из staging_soil "
                f"-> yield_bootstrap.fetch() -> UPDATE curated_field_daily.yield_t_ha"
            ),
            dashboard_panel="Предметный дашборд, панель «Прогноз vs факт по полям»",
        )
    return pd.DataFrame(rows)


def run():
    run_id = str(uuid.uuid4())[:8]
    print(f"=== Запуск пайплайна run_id={run_id} ===")
    init_db()

    weather_rows, soil_rows, ndvi_rows = [], [], []
    for field in FIELDS:
        ingest_weather(run_id, field, weather_rows)
        ingest_soil(run_id, field, soil_rows)
        ingest_ndvi(run_id, field, ndvi_rows)

    weather_df = pd.DataFrame(weather_rows, columns=[
        "field_id", "obs_date", "t2m", "precip", "radiation", "humidity", "source_status", "ingested_at"])
    soil_df = pd.DataFrame(soil_rows, columns=[
        "field_id", "ph", "soc", "clay", "sand", "nitrogen", "source_status", "ingested_at"])
    ndvi_df = pd.DataFrame(ndvi_rows, columns=[
        "field_id", "obs_date", "ndvi", "source_status", "ingested_at"])

    field_ids = [f["field_id"] for f in FIELDS]
    run_quality_checks(run_id, weather_df, soil_df, ndvi_df, field_ids)

    # Забракованное в витрину не пускаем, но и не теряем — см. quarantine_bad_rows
    weather_df = quarantine_bad_rows(run_id, weather_df, WEATHER_RANGES, "staging_weather")
    soil_df = quarantine_bad_rows(run_id, soil_df, SOIL_RANGES, "staging_soil")
    ndvi_df = quarantine_bad_rows(run_id, ndvi_df, {"ndvi": (-1.0, 1.0)}, "staging_ndvi")

    upsert_df("staging_weather", weather_df)
    upsert_df("staging_soil", soil_df)
    upsert_df("staging_ndvi", ndvi_df)

    new_curated_rows = 0
    if len(weather_df):
        curated = build_curated(weather_df, soil_df, ndvi_df)
        upsert_df("curated_field_daily", curated)
        new_curated_rows = len(curated)

    yield_df = compute_yield(run_id, field_ids, soil_df)
    upsert_df("staging_yield", yield_df)

    def statuses(df):
        return df["source_status"].unique().tolist() if len(df) else ["skipped_up_to_date"]

    print(f"Полей в справочнике: {len(FIELDS)}")
    print(f"Новых строк за этот запуск: погода {len(weather_df)}, NDVI {len(ndvi_df)}, почва {len(soil_df)}")
    print(f"Записано в curated_field_daily: {new_curated_rows}")
    print(f"Статусы: погода={statuses(weather_df)}, почва={statuses(soil_df)}, NDVI={statuses(ndvi_df)}")
    print(f"=== Готово, run_id={run_id} ===")
    return run_id


if __name__ == "__main__":
    run()

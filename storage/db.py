"""
Слой хранения. Три явно выделенные зоны ответственности (требование методички):

  RAW      — необработанные ответы источников как есть (json/сырые записи), неизменяемые.
  STAGING  — очищенные, типизированные, приведённые к единой сетке (field_id, date).
  CURATED  — витрина: одна широкая таблица field_id x date со всеми признаками
             + отдельная витрина по урожайности (целевая переменная).

Плюс сквозные технические таблицы:
  ingestion_log     — журнал каждого запуска загрузки (источник, статус, кол-во строк, ошибка).
  quality_check_log — результаты автоматических проверок качества данных.
"""
import sqlite3
import json
from datetime import datetime, timezone
from config import DB_PATH


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


DDL = """
-- RAW: сырые ответы источников, по одной строке на (источник, ключ_запроса, момент загрузки)
CREATE TABLE IF NOT EXISTS raw_nasa_power (
    field_id TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    source_status TEXT NOT NULL,      -- 'live' | 'degraded_synthetic'
    PRIMARY KEY (field_id, fetched_at)
);

CREATE TABLE IF NOT EXISTS raw_soilgrids (
    field_id TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    source_status TEXT NOT NULL,
    PRIMARY KEY (field_id, fetched_at)
);

CREATE TABLE IF NOT EXISTS raw_sentinel2 (
    field_id TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    source_status TEXT NOT NULL,
    PRIMARY KEY (field_id, fetched_at)
);

CREATE TABLE IF NOT EXISTS raw_yield_history (
    field_id TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    source_status TEXT NOT NULL,
    PRIMARY KEY (field_id, fetched_at)
);

CREATE TABLE IF NOT EXISTS raw_faostat_yield (
    field_id TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    source_status TEXT NOT NULL,
    PRIMARY KEY (field_id, fetched_at)
);

-- STAGING: очищенные типизированные временные ряды, ключ (field_id, obs_date)
CREATE TABLE IF NOT EXISTS staging_weather (
    field_id TEXT NOT NULL,
    obs_date TEXT NOT NULL,
    t2m REAL, precip REAL, radiation REAL, humidity REAL,
    source_status TEXT NOT NULL,
    ingested_at TEXT NOT NULL,
    PRIMARY KEY (field_id, obs_date)
);

CREATE TABLE IF NOT EXISTS staging_soil (
    field_id TEXT NOT NULL,
    ph REAL, soc REAL, clay REAL, sand REAL, nitrogen REAL,
    source_status TEXT NOT NULL,
    ingested_at TEXT NOT NULL,
    PRIMARY KEY (field_id)
);

CREATE TABLE IF NOT EXISTS staging_ndvi (
    field_id TEXT NOT NULL,
    obs_date TEXT NOT NULL,
    ndvi REAL,
    source_status TEXT NOT NULL,
    ingested_at TEXT NOT NULL,
    PRIMARY KEY (field_id, obs_date)
);

CREATE TABLE IF NOT EXISTS staging_yield (
    field_id TEXT NOT NULL,
    season INTEGER NOT NULL,
    yield_t_ha REAL,
    source_status TEXT NOT NULL,
    ingested_at TEXT NOT NULL,
    PRIMARY KEY (field_id, season)
);

-- CURATED: витрина для аналитики/моделей
CREATE TABLE IF NOT EXISTS curated_field_daily (
    field_id TEXT NOT NULL,
    obs_date TEXT NOT NULL,
    t2m REAL, precip REAL, radiation REAL, humidity REAL,
    ndvi REAL,
    ph REAL, soc REAL, clay REAL, sand REAL, nitrogen REAL,
    yield_t_ha REAL,           -- известна только на дату защиты сезона, иначе NULL
    built_at TEXT NOT NULL,
    PRIMARY KEY (field_id, obs_date)
);

-- Реальный эксперимент для статьи: годовая витрина (страна/репрезентативная точка)
CREATE TABLE IF NOT EXISTS curated_national_annual (
    year INTEGER NOT NULL,
    yield_t_ha REAL,              -- РЕАЛЬНЫЙ, из FAOSTAT
    yield_source_status TEXT NOT NULL,
    mean_t2m REAL, sum_precip REAL, mean_radiation REAL, mean_humidity REAL,
    weather_source_status TEXT NOT NULL,
    ph REAL, soc REAL,             -- статичны по годам, из SoilGrids
    soil_source_status TEXT NOT NULL,
    built_at TEXT NOT NULL,
    PRIMARY KEY (year)
);

-- Журнал загрузок
CREATE TABLE IF NOT EXISTS ingestion_log (
    run_id TEXT NOT NULL,
    source TEXT NOT NULL,
    field_id TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,       -- 'success' | 'degraded_synthetic' | 'failed'
    rows_written INTEGER,
    error_message TEXT
);

-- Журнал контроля качества
CREATE TABLE IF NOT EXISTS quality_check_log (
    run_id TEXT NOT NULL,
    checked_at TEXT NOT NULL,
    table_name TEXT NOT NULL,
    check_type TEXT NOT NULL,   -- completeness | uniqueness | validity | range | referential | freshness
    check_name TEXT NOT NULL,
    passed INTEGER NOT NULL,
    details TEXT
);
"""


def init_db():
    conn = get_conn()
    conn.executescript(DDL)
    conn.commit()
    conn.close()


def log_ingestion(run_id, source, field_id, status, rows_written, error_message=None, started_at=None):
    conn = get_conn()
    conn.execute(
        """INSERT INTO ingestion_log
           (run_id, source, field_id, started_at, finished_at, status, rows_written, error_message)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (run_id, source, field_id, started_at or datetime.now(timezone.utc).isoformat(),
         datetime.now(timezone.utc).isoformat(), status, rows_written, error_message),
    )
    conn.commit()
    conn.close()


def log_quality(run_id, table_name, check_type, check_name, passed, details=""):
    conn = get_conn()
    conn.execute(
        """INSERT INTO quality_check_log
           (run_id, checked_at, table_name, check_type, check_name, passed, details)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (run_id, datetime.now(timezone.utc).isoformat(), table_name, check_type, check_name, int(passed), details),
    )
    conn.commit()
    conn.close()


def upsert_df(table, df):
    """
    Идемпотентная запись DataFrame: INSERT OR REPLACE по PRIMARY KEY таблицы.
    Повторный запуск с теми же данными перезаписывает те же строки, а не
    плодит дубли и не падает на UNIQUE constraint (в отличие от df.to_sql(..., 'append')).
    """
    if len(df) == 0:
        return
    conn = get_conn()
    cols = list(df.columns)
    placeholders = ", ".join(["?"] * len(cols))
    sql = f"INSERT OR REPLACE INTO {table} ({', '.join(cols)}) VALUES ({placeholders})"
    conn.executemany(sql, df[cols].itertuples(index=False, name=None))
    conn.commit()
    conn.close()


def save_raw(table, field_id, fetched_at, payload: dict, source_status: str):
    conn = get_conn()
    conn.execute(
        f"INSERT OR REPLACE INTO {table} (field_id, fetched_at, payload_json, source_status) VALUES (?, ?, ?, ?)",
        (field_id, fetched_at, json.dumps(payload, ensure_ascii=False), source_status),
    )
    conn.commit()
    conn.close()

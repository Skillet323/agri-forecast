"""
Хранилище. Вся схема БД и все обращения к SQLite собраны здесь, чтобы остальной код
не писал SQL руками.

Слои и кто за что отвечает:
  RAW      — ответы источников как пришли, JSON-ом. Ничего не чистим, не переписываем.
             Нужен, чтобы можно было разобрать любой спорный показатель задним числом.
  STAGING  — то же самое, но разобранное по колонкам, с типами и общей сеткой
             (field_id, obs_date) либо (year).
  CURATED  — витрины, из которых читают модели и дашборды.

Сквозные таблицы:
  ingestion_log            — что загружали, когда, чем закончилось.
  quality_check_log        — результаты проверок качества.
  quarantine                — строки, не прошедшие проверки, вместе с причиной.
  load_state               — докуда догружен каждый источник (инкрементальная загрузка).
  national_yield_revisions — история пересмотров годовой урожайности.
  lineage_log              — происхождение показателей.
"""
import os
import sqlite3
import json
from datetime import datetime, timezone

from config import DB_PATH as DEFAULT_DB_PATH


def db_path():
    """
    Путь к базе. Читаем переменную окружения каждый раз, а не один раз при импорте:
    иначе тесты не смогли бы подсунуть временный файл, не перезагружая полпроекта.
    """
    return os.environ.get("AGRI_DB_PATH", DEFAULT_DB_PATH)


def get_conn():
    conn = sqlite3.connect(db_path())
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

-- Годовой контур: страна целиком, один год = одно наблюдение
CREATE TABLE IF NOT EXISTS curated_national_annual (
    year INTEGER NOT NULL,
    yield_t_ha REAL,
    yield_source_status TEXT NOT NULL,
    mean_t2m REAL, sum_precip REAL, mean_radiation REAL, mean_humidity REAL,
    weather_source_status TEXT NOT NULL,
    ph REAL, soc REAL,
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
    status TEXT NOT NULL,
    rows_written INTEGER,
    error_message TEXT
);

-- Журнал контроля качества
CREATE TABLE IF NOT EXISTS quality_check_log (
    run_id TEXT NOT NULL,
    checked_at TEXT NOT NULL,
    table_name TEXT NOT NULL,
    check_type TEXT NOT NULL,   -- completeness | uniqueness | admissibility | validity
                                -- | range | referential | freshness
    check_name TEXT NOT NULL,
    passed INTEGER NOT NULL,
    details TEXT
);

-- Карантин. Строки, не прошедшие жёсткие проверки, не попадают в витрину, но и не
-- пропадают: складываем их сюда вместе с причиной. Молча выбрасывать данные нельзя,
-- иначе потом не докажешь, почему в отчёте не хватает трёх дней мая.
CREATE TABLE IF NOT EXISTS quarantine (
    run_id TEXT NOT NULL,
    table_name TEXT NOT NULL,
    field_id TEXT,
    obs_date TEXT,
    reason TEXT NOT NULL,
    row_json TEXT NOT NULL,
    quarantined_at TEXT NOT NULL
);

-- Докуда уже догружен каждый источник. Без этой таблицы "инкрементальная загрузка"
-- остаётся словом в README: пайплайн каждый раз тянул бы весь сезон заново.
CREATE TABLE IF NOT EXISTS load_state (
    source TEXT NOT NULL,
    entity_key TEXT NOT NULL,     -- field_id, либо 'NATIONAL' для годового контура
    last_loaded_date TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (source, entity_key)
);

-- История значений урожайности по годам. Пишем только добавлением: World Bank и FAOSTAT
-- пересматривают уже опубликованные цифры задним числом, и хочется видеть, что именно
-- изменилось между двумя запусками, а не только последнее значение.
CREATE TABLE IF NOT EXISTS national_yield_revisions (
    year INTEGER NOT NULL,
    yield_t_ha REAL NOT NULL,
    yield_source TEXT NOT NULL,       -- faostat_wheat | worldbank_cereals | synthetic_fallback
    run_id TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    PRIMARY KEY (year, run_id)
);

-- Происхождение показателей. Одна строка = один посчитанный показатель в одном запуске,
-- со ссылкой на сырую запись, из которой он вырос. По ней lineage.py разворачивает
-- цепочку от дашборда обратно к исходному ответу API.
CREATE TABLE IF NOT EXISTS lineage_log (
    metric_name TEXT NOT NULL,        -- field_yield | national_annual_yield
    metric_key TEXT NOT NULL,         -- KRD-001 либо год
    run_id TEXT NOT NULL,
    raw_table TEXT NOT NULL,
    raw_key TEXT NOT NULL,
    raw_fetched_at TEXT NOT NULL,
    curated_table TEXT NOT NULL,
    transformation_note TEXT,
    dashboard_panel TEXT,
    recorded_at TEXT NOT NULL,
    PRIMARY KEY (metric_name, metric_key, run_id)
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
    плодит дубли и не падает на UNIQUE constraint.
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


def update_yield_for_field(table, field_id, yield_t_ha):
    """
    Проставить урожайность всем строкам поля.

    Отдельный UPDATE, а не upsert_df: INSERT OR REPLACE меняет строку целиком,
    поэтому запись урожайности через него затёрла бы погоду и NDVI в тех днях,
    которые в текущем запуске не догружались.
    """
    conn = get_conn()
    conn.execute(f"UPDATE {table} SET yield_t_ha = ? WHERE field_id = ?", (yield_t_ha, field_id))
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


def save_quarantine(run_id, table_name, rows, reason):
    """Отложить забракованные строки. rows — список словарей."""
    if not rows:
        return
    conn = get_conn()
    now = datetime.now(timezone.utc).isoformat()
    conn.executemany(
        """INSERT INTO quarantine
           (run_id, table_name, field_id, obs_date, reason, row_json, quarantined_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        [(run_id, table_name, r.get("field_id"), r.get("obs_date"), reason,
          json.dumps(r, ensure_ascii=False, default=str), now) for r in rows],
    )
    conn.commit()
    conn.close()


def get_last_loaded_date(source, entity_key):
    """Последняя загруженная дата в формате YYYY-MM-DD, либо None, если источник ещё не трогали."""
    conn = get_conn()
    row = conn.execute(
        "SELECT last_loaded_date FROM load_state WHERE source = ? AND entity_key = ?",
        (source, entity_key),
    ).fetchone()
    conn.close()
    return row[0] if row else None


def set_last_loaded_date(source, entity_key, last_loaded_date):
    conn = get_conn()
    conn.execute(
        """INSERT INTO load_state (source, entity_key, last_loaded_date, updated_at)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(source, entity_key) DO UPDATE SET
               last_loaded_date = excluded.last_loaded_date,
               updated_at = excluded.updated_at""",
        (source, entity_key, last_loaded_date, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()


def save_yield_revision(year, yield_t_ha, yield_source, run_id):
    conn = get_conn()
    conn.execute(
        """INSERT OR REPLACE INTO national_yield_revisions
           (year, yield_t_ha, yield_source, run_id, captured_at) VALUES (?, ?, ?, ?, ?)""",
        (year, yield_t_ha, yield_source, run_id, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()


def get_yield_revision_history(year=None):
    conn = get_conn()
    if year is None:
        rows = conn.execute(
            "SELECT year, yield_t_ha, yield_source, run_id, captured_at "
            "FROM national_yield_revisions ORDER BY year, captured_at"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT year, yield_t_ha, yield_source, run_id, captured_at "
            "FROM national_yield_revisions WHERE year = ? ORDER BY captured_at",
            (year,),
        ).fetchall()
    conn.close()
    return rows


def save_lineage(metric_name, metric_key, run_id, raw_table, raw_key, raw_fetched_at,
                 curated_table, transformation_note, dashboard_panel):
    conn = get_conn()
    conn.execute(
        """INSERT OR REPLACE INTO lineage_log
           (metric_name, metric_key, run_id, raw_table, raw_key, raw_fetched_at,
            curated_table, transformation_note, dashboard_panel, recorded_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (metric_name, metric_key, run_id, raw_table, raw_key, raw_fetched_at,
         curated_table, transformation_note, dashboard_panel,
         datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()


def get_lineage(metric_name, metric_key):
    conn = get_conn()
    rows = conn.execute(
        """SELECT metric_name, metric_key, run_id, raw_table, raw_key, raw_fetched_at,
                  curated_table, transformation_note, dashboard_panel, recorded_at
           FROM lineage_log WHERE metric_name = ? AND metric_key = ?
           ORDER BY recorded_at DESC""",
        (metric_name, metric_key),
    ).fetchall()
    conn.close()
    return rows


def get_raw_payload(table, key):
    """Последняя по времени сырая запись для ключа. Нужна lineage.py, чтобы показать исходный ответ API."""
    conn = get_conn()
    row = conn.execute(
        f"SELECT field_id, fetched_at, payload_json, source_status FROM {table} "
        f"WHERE field_id = ? ORDER BY fetched_at DESC LIMIT 1",
        (key,),
    ).fetchone()
    conn.close()
    return row

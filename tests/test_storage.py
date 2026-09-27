"""Хранилище: идемпотентность записи, состояние загрузки, история, происхождение."""
import pandas as pd


def test_upsert_is_idempotent(temp_db):
    """Повторная запись тех же строк не плодит дубли — на этом держится перезапуск пайплайна."""
    df = pd.DataFrame([
        {"field_id": "F-1", "obs_date": "2023-03-01", "t2m": 5.0, "precip": 0.0,
         "radiation": 10.0, "humidity": 70.0, "source_status": "live", "ingested_at": "t"},
        {"field_id": "F-1", "obs_date": "2023-03-02", "t2m": 6.0, "precip": 1.0,
         "radiation": 11.0, "humidity": 72.0, "source_status": "live", "ingested_at": "t"},
    ])
    temp_db.upsert_df("staging_weather", df)
    temp_db.upsert_df("staging_weather", df)

    conn = temp_db.get_conn()
    count = conn.execute("SELECT COUNT(*) FROM staging_weather").fetchone()[0]
    conn.close()
    assert count == 2


def test_upsert_overwrites_by_key(temp_db):
    """Изменившееся значение должно заменить старое, а не добавиться рядом."""
    row = {"field_id": "F-1", "obs_date": "2023-03-01", "t2m": 5.0, "precip": 0.0,
           "radiation": 10.0, "humidity": 70.0, "source_status": "live", "ingested_at": "t"}
    temp_db.upsert_df("staging_weather", pd.DataFrame([row]))
    temp_db.upsert_df("staging_weather", pd.DataFrame([{**row, "t2m": 9.9}]))

    conn = temp_db.get_conn()
    rows = conn.execute("SELECT t2m FROM staging_weather").fetchall()
    conn.close()
    assert rows == [(9.9,)]


def test_update_yield_keeps_other_columns(temp_db):
    """
    Урожайность ставится отдельным UPDATE именно чтобы не затереть погоду.
    Тест сторожит эту логику: через INSERT OR REPLACE тут получились бы NULL.
    """
    temp_db.upsert_df("curated_field_daily", pd.DataFrame([{
        "field_id": "F-1", "obs_date": "2023-03-01", "t2m": 5.0, "precip": 0.0,
        "radiation": 10.0, "humidity": 70.0, "ndvi": 0.4, "ph": 65.0, "soc": 30.0,
        "clay": 300.0, "sand": 400.0, "nitrogen": 2.0, "yield_t_ha": None, "built_at": "t",
    }]))
    temp_db.update_yield_for_field("curated_field_daily", "F-1", 5.5)

    conn = temp_db.get_conn()
    t2m, ndvi, yield_t_ha = conn.execute(
        "SELECT t2m, ndvi, yield_t_ha FROM curated_field_daily WHERE field_id = 'F-1'"
    ).fetchone()
    conn.close()
    assert (t2m, ndvi, yield_t_ha) == (5.0, 0.4, 5.5)


def test_load_state_roundtrip(temp_db):
    assert temp_db.get_last_loaded_date("nasa_power", "F-1") is None
    temp_db.set_last_loaded_date("nasa_power", "F-1", "2023-05-10")
    assert temp_db.get_last_loaded_date("nasa_power", "F-1") == "2023-05-10"
    temp_db.set_last_loaded_date("nasa_power", "F-1", "2023-06-01")
    assert temp_db.get_last_loaded_date("nasa_power", "F-1") == "2023-06-01"


def test_yield_revisions_accumulate(temp_db):
    """История пишется только добавлением: два запуска — две записи, старое значение живо."""
    temp_db.save_yield_revision(2020, 2.5, "worldbank_cereals", "run-1")
    temp_db.save_yield_revision(2020, 2.7, "worldbank_cereals", "run-2")

    history = temp_db.get_yield_revision_history(2020)
    assert [h[1] for h in history] == [2.5, 2.7]


def test_lineage_roundtrip(temp_db):
    temp_db.save_lineage(
        metric_name="field_yield", metric_key="F-1", run_id="run-1",
        raw_table="raw_yield_history", raw_key="F-1", raw_fetched_at="2026-01-01T00:00:00",
        curated_table="curated_field_daily", transformation_note="тест",
        dashboard_panel="панель",
    )
    rows = temp_db.get_lineage("field_yield", "F-1")
    assert len(rows) == 1
    assert rows[0][3] == "raw_yield_history"
    assert rows[0][7] == "тест"


def test_ingestion_log_records_failure(temp_db):
    temp_db.log_ingestion("run-1", "nasa_power", "F-1", "failed", 0, "таймаут")
    conn = temp_db.get_conn()
    status, err = conn.execute(
        "SELECT status, error_message FROM ingestion_log WHERE run_id = 'run-1'"
    ).fetchone()
    conn.close()
    assert status == "failed"
    assert err == "таймаут"

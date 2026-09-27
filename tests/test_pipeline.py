"""
Пайплайн целиком: повторный запуск, инкрементальность, заполнение журналов.

Тесты гоняют настоящий pipeline.run() на временной базе. Сеть не нужна: источники
в песочнице всё равно недоступны и уходят в заглушку, а для проверки инкрементальности
важно только то, что данные одинаковые между запусками.
"""
import pandas as pd
import pytest


@pytest.fixture()
def pipeline(temp_db, monkeypatch):
    monkeypatch.setattr("ingestion.nasa_power.fetch_real",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("тест без сети")))
    monkeypatch.setattr("ingestion.soilgrids.fetch_real",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("тест без сети")))
    monkeypatch.setattr("ingestion.sentinel2.fetch_real",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("тест без сети")))
    import pipeline as p
    return p


def _count(db, table):
    conn = db.get_conn()
    n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    conn.close()
    return n


def test_pipeline_populates_all_layers(pipeline, temp_db):
    pipeline.run()
    assert _count(temp_db, "raw_nasa_power") > 0
    assert _count(temp_db, "staging_weather") > 0
    assert _count(temp_db, "curated_field_daily") > 0


def test_second_run_does_not_duplicate_rows(pipeline, temp_db):
    """Главное свойство: пайплайн можно запускать сколько угодно раз."""
    pipeline.run()
    after_first = _count(temp_db, "curated_field_daily")
    pipeline.run()
    assert _count(temp_db, "curated_field_daily") == after_first


def test_second_run_skips_already_loaded_sources(pipeline, temp_db):
    """Инкрементальность: во второй раз источник должен быть помечен как пропущенный."""
    pipeline.run()
    pipeline.run()

    conn = temp_db.get_conn()
    statuses = conn.execute(
        "SELECT DISTINCT status FROM ingestion_log WHERE source = 'nasa_power'"
    ).fetchall()
    conn.close()
    assert ("skipped_up_to_date",) in statuses


def test_yield_survives_incremental_run(pipeline, temp_db):
    """
    Урожайность и погода должны пережить второй запуск, в котором новых строк нет.
    Ровно тут ломался INSERT OR REPLACE, пока урожайность не вынесли в отдельный UPDATE.
    """
    pipeline.run()
    pipeline.run()

    conn = temp_db.get_conn()
    total, with_yield, with_weather = conn.execute(
        "SELECT COUNT(*), COUNT(yield_t_ha), COUNT(t2m) FROM curated_field_daily"
    ).fetchone()
    conn.close()
    assert total == with_yield == with_weather


def test_quality_log_covers_all_seven_types(pipeline, temp_db):
    pipeline.run()
    conn = temp_db.get_conn()
    types = {row[0] for row in conn.execute("SELECT DISTINCT check_type FROM quality_check_log")}
    conn.close()
    assert types == {"completeness", "uniqueness", "admissibility", "validity",
                     "range", "referential", "freshness"}


def test_lineage_written_for_every_field(pipeline, temp_db):
    from config import FIELDS
    pipeline.run()
    conn = temp_db.get_conn()
    keys = {row[0] for row in conn.execute(
        "SELECT DISTINCT metric_key FROM lineage_log WHERE metric_name = 'field_yield'")}
    conn.close()
    assert keys == {f["field_id"] for f in FIELDS}


def test_ingestion_log_has_row_counts(pipeline, temp_db):
    pipeline.run()
    conn = temp_db.get_conn()
    rows = conn.execute(
        "SELECT SUM(rows_written) FROM ingestion_log WHERE source = 'nasa_power'"
    ).fetchone()[0]
    conn.close()
    assert rows > 0


def test_incremental_window_returns_full_season_when_empty(pipeline):
    from config import SEASON_START, SEASON_END
    start, end, up_to_date = pipeline.incremental_window("nasa_power", "F-новое")
    assert (start, end, up_to_date) == (SEASON_START, SEASON_END, False)


def test_incremental_window_reports_done(pipeline, temp_db):
    from config import SEASON_END
    temp_db.set_last_loaded_date("nasa_power", "F-1", SEASON_END.isoformat())
    _, _, up_to_date = pipeline.incremental_window("nasa_power", "F-1")
    assert up_to_date is True


def test_ndvi_forward_filled_in_curated(pipeline, temp_db):
    """
    NDVI приходит раз в несколько дней, а витрина дневная. Проверяем, что значения
    протянуты вперёд и в витрине не остаётся дыр после первого наблюдения.
    """
    pipeline.run()
    conn = temp_db.get_conn()
    df = pd.read_sql(
        "SELECT ndvi FROM curated_field_daily WHERE field_id = 'KRD-001' ORDER BY obs_date", conn)
    conn.close()
    after_first = df["ndvi"].loc[df["ndvi"].first_valid_index():]
    assert after_first.notna().all()


def test_quarantine_holds_impossible_values(pipeline, temp_db):
    """Строка с невозможной температурой не должна попасть в витрину, но должна сохраниться."""
    df = pd.DataFrame([
        {"field_id": "F-1", "obs_date": "2023-03-01", "t2m": 5.0, "precip": 1.0,
         "radiation": 10.0, "humidity": 70.0, "source_status": "live", "ingested_at": "t"},
        {"field_id": "F-1", "obs_date": "2023-03-02", "t2m": 999.0, "precip": 1.0,
         "radiation": 10.0, "humidity": 70.0, "source_status": "live", "ingested_at": "t"},
    ])
    clean = pipeline.quarantine_bad_rows("run-1", df, pipeline.WEATHER_RANGES, "staging_weather")

    assert len(clean) == 1
    assert clean.iloc[0]["t2m"] == 5.0

    conn = temp_db.get_conn()
    reason = conn.execute("SELECT reason FROM quarantine WHERE run_id = 'run-1'").fetchone()[0]
    conn.close()
    assert "t2m" in reason


def test_quarantine_keeps_missing_values(pipeline, temp_db):
    """Пропуск — это не ошибка диапазона, такие строки трогать нельзя."""
    df = pd.DataFrame([
        {"field_id": "F-1", "obs_date": "2023-03-01", "t2m": None, "precip": 1.0,
         "radiation": 10.0, "humidity": 70.0, "source_status": "live", "ingested_at": "t"},
    ])
    clean = pipeline.quarantine_bad_rows("run-2", df, pipeline.WEATHER_RANGES, "staging_weather")
    assert len(clean) == 1

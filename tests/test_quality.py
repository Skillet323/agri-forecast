"""Проверки качества: каждая должна ловить ровно то, ради чего написана."""
import pandas as pd
import pytest


@pytest.fixture()
def checks(temp_db):
    from quality import checks as c
    return c


def _results(db, check_type=None):
    conn = db.get_conn()
    if check_type:
        rows = conn.execute(
            "SELECT check_name, passed, details FROM quality_check_log WHERE check_type = ?",
            (check_type,),
        ).fetchall()
    else:
        rows = conn.execute("SELECT check_name, passed, details FROM quality_check_log").fetchall()
    conn.close()
    return rows


def test_completeness_flags_gaps(temp_db, checks):
    df = pd.DataFrame({"t2m": [1.0, None, None, None, None]})  # 20% заполнено
    checks.check_completeness("r", df, ["t2m"], "staging_weather")
    assert _results(temp_db, "completeness")[0][1] == 0


def test_completeness_passes_on_full_column(temp_db, checks):
    df = pd.DataFrame({"t2m": [1.0, 2.0, 3.0]})
    checks.check_completeness("r", df, ["t2m"], "staging_weather")
    assert _results(temp_db, "completeness")[0][1] == 1


def test_uniqueness_catches_duplicates(temp_db, checks):
    df = pd.DataFrame({"field_id": ["F-1", "F-1"], "obs_date": ["2023-03-01", "2023-03-01"]})
    checks.check_uniqueness("r", df, ["field_id", "obs_date"], "staging_weather")
    assert _results(temp_db, "uniqueness")[0][1] == 0


def test_validity_types_catches_text_in_numeric(temp_db, checks):
    df = pd.DataFrame({"t2m": [1.0, "не число", 3.0]})
    checks.check_validity_types("r", df, ["t2m"], "staging_weather")
    assert _results(temp_db, "validity")[0][1] == 0


def test_admissibility_catches_unknown_status(temp_db, checks):
    """Статус, которого нет в домене, обычно означает опечатку в загрузчике."""
    df = pd.DataFrame({"source_status": ["live", "неведомый_статус"]})
    checks.check_admissibility("r", df, "source_status", {"live", "degraded_synthetic"}, "staging_weather")
    name, passed, details = _results(temp_db, "admissibility")[0]
    assert passed == 0
    assert "неведомый_статус" in details


def test_admissibility_passes_on_known_values(temp_db, checks):
    df = pd.DataFrame({"source_status": ["live", "live", "degraded_synthetic"]})
    checks.check_admissibility("r", df, "source_status", {"live", "degraded_synthetic"}, "staging_weather")
    assert _results(temp_db, "admissibility")[0][1] == 1


def test_ranges_catch_impossible_ndvi(temp_db, checks):
    """NDVI по определению лежит в [-1, 1]; значение 5 означает ошибку расчёта."""
    df = pd.DataFrame({"ndvi": [0.5, 5.0]})
    checks.check_ranges("r", df, {"ndvi": (-1.0, 1.0)}, "staging_ndvi")
    assert _results(temp_db, "range")[0][1] == 0


def test_referential_integrity_catches_unknown_field(temp_db, checks):
    checks.check_referential_integrity("r", ["F-1", "F-99"], ["F-1", "F-2"], "staging_weather")
    name, passed, details = _results(temp_db, "referential")[0]
    assert passed == 0
    assert "F-99" in details


def test_freshness_fails_on_old_data(temp_db, checks):
    checks.check_freshness("r", "2000-01-01", max_lag_days=30, table_name="staging_weather")
    assert _results(temp_db, "freshness")[0][1] == 0


def test_all_seven_check_types_are_available(checks):
    """Методичка перечисляет семь типов проверок — следим, чтобы ни одна не потерялась."""
    expected = {
        "check_completeness", "check_uniqueness", "check_admissibility",
        "check_validity_types", "check_ranges", "check_referential_integrity",
        "check_freshness",
    }
    assert expected <= set(dir(checks))

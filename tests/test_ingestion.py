"""
Загрузчики: что происходит, когда источник недоступен.

Реальные запросы тут не делаются — сеть в тестах не нужна и вредна. Вместо этого
подменяем fetch_real исключением и смотрим, что модуль честно сообщает о деградации
и всё равно отдаёт данные нужной формы.
"""
from datetime import date

import pytest


def test_nasa_power_falls_back_on_network_error(monkeypatch):
    from ingestion import nasa_power

    def boom(*args, **kwargs):
        raise RuntimeError("соединение оборвалось")

    monkeypatch.setattr(nasa_power, "fetch_real", boom)
    payload, status = nasa_power.fetch("F-1", 45.0, 39.0, date(2023, 3, 1), date(2023, 3, 10))

    assert status == "degraded_synthetic"
    assert "соединение оборвалось" in payload["_error"]
    assert set(payload["properties"]["parameter"]) >= {"T2M", "PRECTOTCORR", "ALLSKY_SFC_SW_DWN", "RH2M"}


def test_nasa_power_synthetic_covers_requested_range(monkeypatch):
    from ingestion import nasa_power

    monkeypatch.setattr(nasa_power, "fetch_real", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("нет сети")))
    payload, _ = nasa_power.fetch("F-1", 45.0, 39.0, date(2023, 3, 1), date(2023, 3, 10))

    days = sorted(payload["properties"]["parameter"]["T2M"])
    assert days[0] == "20230301"
    assert days[-1] == "20230310"


def test_nasa_power_synthetic_values_are_physical(monkeypatch):
    """Заглушка должна выдавать правдоподобные числа, иначе проверки диапазонов бессмысленны."""
    from ingestion import nasa_power

    monkeypatch.setattr(nasa_power, "fetch_real", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("нет сети")))
    payload, _ = nasa_power.fetch("F-1", 45.0, 39.0, date(2023, 3, 1), date(2023, 7, 15))

    params = payload["properties"]["parameter"]
    assert all(-40 <= v <= 55 for v in params["T2M"].values())
    assert all(0 <= v <= 300 for v in params["PRECTOTCORR"].values())
    assert all(0 <= v <= 100 for v in params["RH2M"].values())


def test_synthetic_is_reproducible(monkeypatch):
    """Один и тот же field_id должен давать один и тот же ряд, иначе запуски не сравнить."""
    from ingestion import nasa_power

    monkeypatch.setattr(nasa_power, "fetch_real", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("нет сети")))
    first, _ = nasa_power.fetch("F-1", 45.0, 39.0, date(2023, 3, 1), date(2023, 3, 5))
    second, _ = nasa_power.fetch("F-1", 45.0, 39.0, date(2023, 3, 1), date(2023, 3, 5))
    assert first["properties"]["parameter"]["T2M"] == second["properties"]["parameter"]["T2M"]


def test_different_fields_give_different_series(monkeypatch):
    from ingestion import nasa_power

    monkeypatch.setattr(nasa_power, "fetch_real", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("нет сети")))
    first, _ = nasa_power.fetch("F-1", 45.0, 39.0, date(2023, 3, 1), date(2023, 3, 20))
    second, _ = nasa_power.fetch("F-2", 46.0, 39.5, date(2023, 3, 1), date(2023, 3, 20))
    assert first["properties"]["parameter"]["T2M"] != second["properties"]["parameter"]["T2M"]


def test_sentinel2_marks_cloudy_days_as_missing(monkeypatch):
    """
    Облачный день — это отсутствие наблюдения, а не ноль. Если бы заглушка ставила 0,
    в витрине появился бы провал NDVI, неотличимый от гибели посевов.
    """
    from ingestion import sentinel2

    monkeypatch.setattr(sentinel2, "fetch_real", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("нет OAuth")))
    payload, status = sentinel2.fetch("F-1", 45.0, 39.0, date(2023, 3, 1), date(2023, 7, 15))

    assert status == "degraded_synthetic"
    values = [o["ndvi"] for o in payload["observations"]]
    assert any(v is None for v in values), "должны быть пропуски из-за облачности"
    assert all(v is None or -1.0 <= v <= 1.0 for v in values)


def test_soilgrids_fallback_has_expected_layers(monkeypatch):
    from ingestion import soilgrids

    monkeypatch.setattr(soilgrids, "fetch_real", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("нет сети")))
    payload, status = soilgrids.fetch("F-1", 45.0, 39.0)

    assert status == "degraded_synthetic"
    names = {layer["name"] for layer in payload["properties"]["layers"]}
    assert {"phh2o", "soc", "clay", "sand", "nitrogen"} <= names


def test_worldbank_parse_converts_units():
    """World Bank отдаёт кг/га, в проекте всё считается в т/га."""
    from ingestion import worldbank_yield

    payload = [{"page": 1}, [
        {"date": "2020", "value": 2734.5},
        {"date": "2021", "value": 2900.0},
    ]]
    series = worldbank_yield.parse_series(payload)
    assert series == {2020: 2.735, 2021: 2.9}


def test_worldbank_parse_skips_missing_years():
    """За часть лет статистики может не быть — это null, а не ноль."""
    from ingestion import worldbank_yield

    payload = [{"page": 1}, [
        {"date": "2020", "value": 2500.0},
        {"date": "2021", "value": None},
    ]]
    assert worldbank_yield.parse_series(payload) == {2020: 2.5}


def test_worldbank_parse_rejects_garbage():
    from ingestion import worldbank_yield

    with pytest.raises(ValueError):
        worldbank_yield.parse_series({"unexpected": "structure"})

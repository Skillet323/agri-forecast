"""Помощники для ноутбуков: разбор ответов и свёртка погоды в годовые признаки."""
import numpy as np
import pandas as pd
import pytest

from ingestion import open_datasets as od


def _daily(year_start=2020, year_end=2021):
    idx = pd.date_range(f"{year_start}-01-01", f"{year_end}-12-31", freq="D")
    rng = np.random.default_rng(0)
    return pd.DataFrame({
        "T2M": rng.normal(15, 8, len(idx)),
        "T2M_MAX": rng.normal(21, 9, len(idx)),
        "PRECTOTCORR": rng.gamma(1.0, 2.0, len(idx)),
        "ALLSKY_SFC_SW_DWN": rng.normal(18, 4, len(idx)),
        "RH2M": rng.normal(70, 10, len(idx)),
    }, index=idx)


def test_season_features_one_row_per_year():
    feats = od.growing_season_features(_daily(), 2020, 2021)
    assert list(feats.index) == [2020, 2021]


def test_season_features_use_only_window():
    """Признаки должны считаться по вегетационному окну, а не по всему году."""
    daily = _daily(2020, 2020)
    narrow = od.growing_season_features(daily, 2020, 2020, season=((3, 1), (3, 31)))
    wide = od.growing_season_features(daily, 2020, 2020, season=((3, 1), (7, 15)))
    assert narrow.loc[2020, "sum_precip"] < wide.loc[2020, "sum_precip"]


def test_season_features_count_extremes():
    feats = od.growing_season_features(_daily(), 2020, 2021)
    assert "hot_days_over_30" in feats.columns
    assert "dry_days" in feats.columns
    assert (feats["dry_days"] >= 0).all()


def test_season_features_skip_years_without_data():
    """Год, которого нет в данных, просто не должен попасть в результат."""
    feats = od.growing_season_features(_daily(2020, 2020), 2019, 2021)
    assert list(feats.index) == [2020]


def test_kaggle_without_credentials_gives_clear_error(monkeypatch):
    """Без ключей должно быть понятное сообщение, а не 401 из глубины requests."""
    monkeypatch.delenv("KAGGLE_USERNAME", raising=False)
    monkeypatch.delenv("KAGGLE_KEY", raising=False)

    assert od.kaggle_credentials_available() is False
    with pytest.raises(RuntimeError, match="ключей Kaggle|Нет ключей"):
        od.kaggle_dataset("owner/dataset")


def test_kaggle_credentials_detected(monkeypatch):
    monkeypatch.setenv("KAGGLE_USERNAME", "user")
    monkeypatch.setenv("KAGGLE_KEY", "key")
    assert od.kaggle_credentials_available() is True

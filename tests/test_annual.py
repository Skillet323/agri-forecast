"""Годовой контур: агрегация по сезону, валидация по годам, история пересмотров."""
import numpy as np
import pandas as pd
import pytest


@pytest.fixture()
def annual_frame():
    rng = np.random.default_rng(0)
    years = list(range(2000, 2024))
    return pd.DataFrame({
        "year": years,
        "mean_t2m": rng.normal(17, 1, len(years)),
        "sum_precip": rng.normal(200, 40, len(years)),
        "mean_radiation": rng.normal(18, 1, len(years)),
        "mean_humidity": rng.normal(70, 5, len(years)),
        "ph": 65.0, "soc": 35.0,
        "yield_t_ha": np.linspace(1.6, 3.2, len(years)) + rng.normal(0, 0.2, len(years)),
    })


def test_loyo_holds_out_each_year(annual_frame):
    import real_experiment as re_
    result = re_.loyo_cv(annual_frame, ["mean_t2m", "sum_precip"])
    assert result["n"] == len(annual_frame)
    assert result["years"] == annual_frame["year"].tolist()


def test_loyo_predicts_every_year(annual_frame):
    import real_experiment as re_
    result = re_.loyo_cv(annual_frame, ["mean_t2m", "sum_precip"])
    assert len(result["y_pred"]) == len(annual_frame)
    assert all(np.isfinite(result["y_pred"]))


def test_loyo_training_set_excludes_test_year(annual_frame, monkeypatch):
    """Год из контроля не должен участвовать в обучении."""
    import real_experiment as re_
    from sklearn.pipeline import Pipeline

    train_sizes = []
    original_fit = Pipeline.fit

    def spy_fit(self, X, y, **kwargs):
        train_sizes.append(len(X))
        return original_fit(self, X, y, **kwargs)

    monkeypatch.setattr(Pipeline, "fit", spy_fit)
    re_.loyo_cv(annual_frame, ["mean_t2m", "sum_precip"])

    assert all(size == len(annual_frame) - 1 for size in train_sizes)


def test_constant_feature_does_not_break_scaling(annual_frame):
    """
    Почва по годам не меняется, дисперсия нулевая. Стандартизация такого признака
    могла бы дать деление на ноль — проверяем, что предсказания остаются числами.
    """
    import real_experiment as re_
    result = re_.loyo_cv(annual_frame, ["mean_t2m", "sum_precip", "ph", "soc"])
    assert all(np.isfinite(result["y_pred"]))


def test_extended_features_include_baseline():
    """Расширенный набор должен быть надмножеством базового, иначе это не абляция."""
    import real_experiment as re_
    assert set(re_.BASELINE_FEATURES) <= set(re_.EXTENDED_FEATURES)


def test_revision_history_shows_changed_value(temp_db):
    temp_db.save_yield_revision(2020, 2.5, "worldbank_cereals", "run-1")
    temp_db.save_yield_revision(2020, 2.8, "worldbank_cereals", "run-2")
    values = [row[1] for row in temp_db.get_yield_revision_history(2020)]
    assert values == [2.5, 2.8]


def test_revision_history_is_per_year(temp_db):
    temp_db.save_yield_revision(2020, 2.5, "worldbank_cereals", "run-1")
    temp_db.save_yield_revision(2021, 3.0, "worldbank_cereals", "run-1")
    assert len(temp_db.get_yield_revision_history(2020)) == 1
    assert len(temp_db.get_yield_revision_history()) == 2

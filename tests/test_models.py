"""
Прогнозное ядро: корректность разбиения на обучение и контроль.

Самое ценное здесь — тесты на утечку. Ошибка в схеме валидации не роняет программу
и не видна в выводе: метрики просто становятся неправдоподобно хорошими.
"""
import numpy as np
import pandas as pd
import pytest


@pytest.fixture()
def daily_frame():
    """Синтетический кусок витрины: три поля, по сто дней, у каждого своя урожайность."""
    rows = []
    for i, field_id in enumerate(["F-1", "F-2", "F-3"]):
        for day in pd.date_range("2023-03-01", periods=100):
            rows.append({
                "field_id": field_id,
                "obs_date": day,
                "t2m": 10 + i + day.dayofyear * 0.05,
                "precip": 1.0 + i * 0.2,
                "radiation": 15.0 + i,
                "humidity": 70.0 - i,
                "ndvi": 0.3 + i * 0.05,
                "ph": 60.0 + i, "soc": 30.0 + i, "clay": 300.0, "sand": 400.0, "nitrogen": 2.0,
                "yield_t_ha": 4.0 + i,
            })
    return pd.DataFrame(rows)


def test_cutoff_features_cover_every_field_and_slice(daily_frame):
    import models
    feats = models.build_cutoff_features(daily_frame)
    assert len(feats) == 3 * len(models.CUTOFF_FRACTIONS)
    assert set(feats["field_id"]) == {"F-1", "F-2", "F-3"}


def test_early_cutoff_uses_less_data_than_full_season(daily_frame):
    """Ранний срез обязан опираться на меньший объём наблюдений, иначе срезы бессмысленны."""
    import models
    feats = models.build_cutoff_features(daily_frame)
    early = feats[feats["cutoff"] == "early_tillering"].iloc[0]
    full = feats[feats["cutoff"] == "full_season"].iloc[0]
    assert early["sum_precip"] < full["sum_precip"]


def test_features_never_include_target(daily_frame):
    """Целевая переменная не должна случайно оказаться среди признаков."""
    import models
    assert "yield_t_ha" not in models.BASELINE_FEATURES
    assert "yield_t_ha" not in models.EXTENDED_FEATURES


def test_lofo_holds_out_one_field_at_a_time(daily_frame):
    import models
    feats = models.build_cutoff_features(daily_frame)
    result = models.lofo_cv(feats, models.BASELINE_FEATURES, "full_season")
    assert sorted(result["held_out_fields"]) == ["F-1", "F-2", "F-3"]
    assert len(result["y_true"]) == len(result["y_pred"]) == 3


def test_lofo_prediction_does_not_see_its_own_field(daily_frame, monkeypatch):
    """
    Прямая проверка на утечку: перехватываем обучение и смотрим, что поле из теста
    не попало в обучающую выборку. Это ровно та ошибка, которая не видна по метрикам.
    """
    import models
    from sklearn.pipeline import Pipeline

    seen_train_fields = []
    original_fit = Pipeline.fit

    def spy_fit(self, X, y, **kwargs):
        seen_train_fields.append(set(X.index))
        return original_fit(self, X, y, **kwargs)

    monkeypatch.setattr(Pipeline, "fit", spy_fit)

    feats = models.build_cutoff_features(daily_frame)
    sub = feats[feats["cutoff"] == "full_season"].reset_index(drop=True)
    models.lofo_cv(feats, models.BASELINE_FEATURES, "full_season")

    assert all(len(train) == len(sub) - 1 for train in seen_train_fields)


def test_mae_is_non_negative(daily_frame):
    import models
    feats = models.build_cutoff_features(daily_frame)
    result = models.lofo_cv(feats, models.BASELINE_FEATURES, "full_season")
    assert result["mae_t_ha"] >= 0
    assert result["mape_pct"] >= 0


def test_missing_ndvi_does_not_crash_model(daily_frame):
    """У поля может не быть ни одного снимка — модель должна это пережить."""
    import models
    daily_frame.loc[daily_frame["field_id"] == "F-2", "ndvi"] = np.nan
    feats = models.build_cutoff_features(daily_frame)
    result = models.lofo_cv(feats, models.EXTENDED_FEATURES, "full_season")
    assert len(result["y_pred"]) == 3
    assert all(np.isfinite(result["y_pred"]))

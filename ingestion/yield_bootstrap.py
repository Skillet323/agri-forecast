"""
Источник 4: историческая урожайность — bootstrap-источник.

В реальном проекте здесь: FAOSTAT API (региональный/страновой уровень) и/или
исходный CSV с Kaggle (AgriYield / открытые датасеты урожайности) на уровне
поля/района. Это принципиально другой тип источника по сравнению с первыми
тремя: не временной ряд с регулярным дозапросом, а разово загружаемый
исторический архив (bootstrap), обновляемый раз в сезон по факту уборки.

Из-за отсутствия доступа к внешним архивам в этом окружении используется
синтетическая целевая переменная, построенная НЕ произвольно, а как функция
от синтетических погодных/почвенных признаков — это специально сделано,
чтобы на синтетике тоже была видна содержательная зависимость для анализа
и последующего моделирования (иначе корреляции были бы просто шумом).
"""
import random


def compute_synthetic_yield(field_id, mean_precip, mean_radiation, mean_ndvi_peak, soc, ph_over_10, seed):
    """
    Простая агрономически мотивированная формула:
    выше пик NDVI, больше органики в почве, оптимальный pH и умеренные осадки -> выше урожайность.
    Добавлен шум, чтобы связь была реалистично зашумлённой, а не детерминированной.
    """
    rng = random.Random(seed)
    ph = ph_over_10 / 10.0
    ph_penalty = -0.4 * (ph - 6.5) ** 2  # оптимум pH ~6.5
    base = (
        1.5
        + 4.0 * mean_ndvi_peak
        + 0.02 * soc
        + 0.01 * mean_radiation
        - 0.005 * max(0, mean_precip - 4.0) ** 2  # переизбыток осадков вредит
        + ph_penalty
    )
    noise = rng.gauss(0, 0.35)
    return round(max(1.0, base + noise), 2)


def fetch(field_id, features: dict, season: int):
    """
    features: словарь с агрегатами по сезону (mean_precip, mean_radiation, mean_ndvi_peak, soc, ph).
    Возвращает (payload, status). Здесь всегда 'degraded_synthetic', т.к. в этом
    окружении нет доступа к FAOSTAT/Kaggle; реальная реализация — простой
    HTTP GET к FAOSTAT API либо чтение забутстрапленного CSV в staging.
    """
    seed = abs(hash(field_id + str(season))) % (2**32)
    y = compute_synthetic_yield(
        field_id,
        features["mean_precip"],
        features["mean_radiation"],
        features["mean_ndvi_peak"],
        features["soc"],
        features["ph"],
        seed,
    )
    payload = {"field_id": field_id, "season": season, "yield_t_ha": y, "_synthetic": True}
    return payload, "degraded_synthetic"

"""
Источник 3: Sentinel-2 (Copernicus Data Space) — вегетационный индекс NDVI.

ВАЖНО: в отличие от NASA POWER и SoilGrids, этот источник не отдаёт готовое
число одним HTTP GET-запросом. Реальная интеграция требует:
  1. OAuth2 client_id/client_secret, зарегистрированные на
     https://dataspace.copernicus.eu/ (бесплатно, но нужна регистрация);
  2. поиск сцен через STAC API (SENTINEL2_STAC_URL) по bbox поля и диапазону дат;
  3. скачивание нужных бэндов (B04/B08) и вычисление NDVI = (B08-B04)/(B08+B04)
     либо использование готового Sentinel Hub Statistical API, который считает
     это на стороне сервера и отдаёт агрегат по полигону.

Это отдельная, более тяжёлая часть ingestion-слоя, которую имеет смысл
делать вторым шагом (после того как согласована тема и поднят транспорт
для остальных источников). Здесь оставлена заготовка реальной функции
(fetch_real) с точками расширения и синтетический генератор, дающий
агрономически правдоподобную кривую NDVI (рост -> плато -> созревание/спад),
чтобы можно было прогнать весь конвейер и аналитику уже сейчас.
"""
import math
import random
from datetime import date, timedelta

from config import SENTINEL2_STAC_URL, REQUEST_TIMEOUT_SEC


def fetch_real(lat, lon, start: date, end: date, client_id=None, client_secret=None):
    """
    Точка расширения для реальной интеграции. Требует OAuth-креды, которых
    нет в этом окружении -> намеренно бросает NotImplementedError, чтобы
    fetch() ушёл в контролируемый degraded-режим, а не притворялся, что
    выполнил реальный запрос.
    """
    if not (client_id and client_secret):
        raise NotImplementedError(
            "Sentinel-2 требует OAuth client_id/secret от Copernicus Data Space "
            f"({SENTINEL2_STAC_URL}) — не сконфигурировано в этом окружении."
        )
    # Здесь был бы реальный STAC-поиск сцен + запрос Statistical API.
    raise NotImplementedError


def _synthetic_ndvi_series(field_id, start: date, end: date, seed: int):
    """
    Агрономически правдоподобная кривая NDVI для озимой пшеницы:
    низкий NDVI в начале весны -> рост в фазу кущения/выхода в трубку ->
    плато в колошение/цветение -> снижение к созреванию.
    Шаг наблюдений ~5 дней (типичная повторная съёмка Sentinel-2).
    """
    rng = random.Random(seed)
    total_days = (end - start).days
    points = []
    d = start
    while d <= end:
        progress = (d - start).days / max(total_days, 1)  # 0..1 по сезону
        # колоколообразная кривая с пиком около 55-65% сезона
        peak_pos = 0.6
        width = 0.28
        base_ndvi = 0.15 + 0.65 * math.exp(-((progress - peak_pos) ** 2) / (2 * width ** 2))
        cloud_noise = rng.gauss(0, 0.03)
        # облачность иногда "портит" наблюдение (реалистичный пропуск/выброс)
        cloud_flag = rng.random() < 0.12
        ndvi = None if cloud_flag else round(max(0.0, min(0.95, base_ndvi + cloud_noise)), 3)
        points.append({"date": d.strftime("%Y-%m-%d"), "ndvi": ndvi, "cloud_masked": cloud_flag})
        d += timedelta(days=5)
    return {"field_id": field_id, "observations": points, "_synthetic": True}


def fetch(field_id, lat, lon, start: date, end: date):
    try:
        payload = fetch_real(lat, lon, start, end)
        return payload, "live"
    except Exception as e:  # noqa: BLE001
        seed = abs(hash(field_id)) % (2**32)
        payload = _synthetic_ndvi_series(field_id, start, end, seed)
        payload["_error"] = str(e)
        return payload, "degraded_synthetic"

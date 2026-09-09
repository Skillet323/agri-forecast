"""
Конфигурация проекта.
Технически стек: Python + SQLite (слои raw/staging/curated в одной БД, разными таблицами).
Все параметры вынесены сюда — требование "конфигурируемость" из методички.
"""
import os
from datetime import date

# --- Пути ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
RAW_DIR = os.path.join(DATA_DIR, "raw")
DB_PATH = os.path.join(DATA_DIR, "warehouse.sqlite")

for d in (DATA_DIR, RAW_DIR, os.path.join(RAW_DIR, "nasa_power"), os.path.join(RAW_DIR, "soilgrids")):
    os.makedirs(d, exist_ok=True)

# --- Изучаемые поля (пример: Краснодарский край, пшеница) ---
# lat/lon реальные, привязаны к сельхозрайонам региона.
FIELDS = [
    {"field_id": "KRD-001", "name": "Тимашёвский р-н",  "lat": 45.61, "lon": 38.94, "crop": "wheat"},
    {"field_id": "KRD-002", "name": "Кущёвский р-н",    "lat": 46.20, "lon": 39.60, "crop": "wheat"},
    {"field_id": "KRD-003", "name": "Ленинградский р-н","lat": 46.30, "lon": 39.38, "crop": "wheat"},
    {"field_id": "KRD-004", "name": "Каневской р-н",    "lat": 46.10, "lon": 38.95, "crop": "wheat"},
    {"field_id": "KRD-005", "name": "Брюховецкий р-н",  "lat": 45.90, "lon": 38.98, "crop": "wheat"},
]

# --- Период наблюдений (вегетационный сезон озимой пшеницы) ---
SEASON_START = date(2023, 3, 1)
SEASON_END = date(2023, 7, 15)

# --- Источники данных ---
NASA_POWER_URL = "https://power.larc.nasa.gov/api/temporal/daily/point"
NASA_POWER_PARAMS = ["T2M", "PRECTOTCORR", "ALLSKY_SFC_SW_DWN", "RH2M"]  # темп., осадки, радиация, влажность

SOILGRIDS_URL = "https://rest.isric.org/soilgrids/v2.0/properties/query"
SOILGRIDS_PROPERTIES = ["phh2o", "soc", "clay", "sand", "nitrogen"]

# --- Реальный эксперимент для статьи: годовой ряд по стране (FAOSTAT) ---
# ВАЖНО: сеть в песочнице разработки не даёт достучаться до fenixservices.fao.org,
# поэтому эти константы/парсинг НЕ протестированы вживую и должны быть перепроверены
# при первом реальном запуске (см. real_experiment.py и README, раздел "Реальный
# эксперимент"). Если формат ответа FAOSTAT отличается — это будет видно сразу по
# исключению при парсинге, а не тихо даст неверные числа.
FAOSTAT_BASE_URL = "https://fenixservices.fao.org/faostat/api/v1/en"
FAOSTAT_DOMAIN = "QCL"              # Crops and livestock products
FAOSTAT_AREA_NAME = "Russian Federation"
FAOSTAT_ITEM_NAME = "Wheat"
FAOSTAT_ELEMENT_NAME = "Yield"

# Репрезентативная точка для агрегированной "национальной" погоды —
# упрощение: реальный национальный ряд урожайности сопоставляется с погодой
# в одном из ключевых зерновых регионов (Краснодарский край), а не с
# усреднением по всей стране. Это ограничение явно обсуждается в статье.
NATIONAL_REF_POINT = {"lat": 45.9, "lon": 39.2, "name": "Краснодарский край (репрезентативная точка)"}

REAL_EXPERIMENT_YEAR_START = 2000
REAL_EXPERIMENT_YEAR_END = 2023   # включительно; FAOSTAT публикует с задержкой ~1-2 года,
                                    # часть последних лет может отсутствовать в ответе — это нормально


# Sentinel-2 требует OAuth-регистрацию в Copernicus Data Space (client_id/secret).
# В демо-режиме синтезируем NDVI по агрономической логике (рост -> плато -> созревание).
SENTINEL2_STAC_URL = "https://catalogue.dataspace.copernicus.eu/stac"

# --- Сетевые настройки ---
REQUEST_TIMEOUT_SEC = 8
MAX_RETRIES = 2

"""
Настройки проекта. Всё, что имеет смысл менять, лежит здесь, а не по коду.

Стек: Python и SQLite. Слои raw/staging/curated разведены по таблицам одной базы —
для объёмов этого проекта отдельная СУБД была бы лишней сущностью.

Любую настройку можно переопределить переменной окружения, не трогая файл:

    AGRI_DB_PATH        путь к базе (тесты этим пользуются, чтобы не трогать рабочую)
    AGRI_SEASON_START   начало сезона, YYYY-MM-DD
    AGRI_SEASON_END     конец сезона
    AGRI_YEAR_START     первый год годового контура
    AGRI_YEAR_END       последний год
    AGRI_TIMEOUT_SEC    таймаут запроса к источнику
    AGRI_MAX_RETRIES    сколько раз повторять запрос
"""
import os
from datetime import date

# --- Пути ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
RAW_DIR = os.path.join(DATA_DIR, "raw")
DB_PATH = os.environ.get("AGRI_DB_PATH", os.path.join(DATA_DIR, "warehouse.sqlite"))

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
SEASON_START = date.fromisoformat(os.environ.get("AGRI_SEASON_START", "2023-03-01"))
SEASON_END = date.fromisoformat(os.environ.get("AGRI_SEASON_END", "2023-07-15"))

# --- Источники данных ---
NASA_POWER_URL = "https://power.larc.nasa.gov/api/temporal/daily/point"
NASA_POWER_PARAMS = ["T2M", "PRECTOTCORR", "ALLSKY_SFC_SW_DWN", "RH2M"]  # темп., осадки, радиация, влажность

SOILGRIDS_URL = "https://rest.isric.org/soilgrids/v2.0/properties/query"
SOILGRIDS_PROPERTIES = ["phh2o", "soc", "clay", "sand", "nitrogen"]

# --- Годовой контур: урожайность по стране ---
# FAOSTAT периодически лежит целиком (ловили 521 от их Cloudflare), поэтому парсинг
# написан по документации и подстрахован резервным источником в worldbank_yield.py.
# Если формат ответа изменится, это вылезет исключением при разборе, а не тихой подменой чисел.
FAOSTAT_BASE_URL = "https://fenixservices.fao.org/faostat/api/v1/en"
FAOSTAT_DOMAIN = "QCL"              # Crops and livestock products
FAOSTAT_AREA_NAME = "Russian Federation"
FAOSTAT_ITEM_NAME = "Wheat"
FAOSTAT_ELEMENT_NAME = "Yield"

# Репрезентативная точка для агрегированной "национальной" погоды —
# упрощение: реальный национальный ряд урожайности сопоставляется с погодой
# в одном из ключевых зерновых регионов (Краснодарский край), а не с
# усреднением по всей стране. Это ограничение явно обсуждается в отчёте.
NATIONAL_REF_POINT = {"lat": 45.9, "lon": 39.2, "name": "Краснодарский край (репрезентативная точка)"}

# Включительно. Статистику публикуют с задержкой год-два, поэтому последних лет
# в ответе может не оказаться — это нормально, они просто отсеются.
REAL_EXPERIMENT_YEAR_START = int(os.environ.get("AGRI_YEAR_START", 2000))
REAL_EXPERIMENT_YEAR_END = int(os.environ.get("AGRI_YEAR_END", 2023))

# Sentinel-2 требует OAuth-регистрацию в Copernicus Data Space (client_id/secret).
# Пока не пройдена — используется синтетический NDVI по агрономической логике
# (рост -> плато -> созревание).
SENTINEL2_STAC_URL = "https://catalogue.dataspace.copernicus.eu/stac"

# --- Сетевые настройки ---
REQUEST_TIMEOUT_SEC = int(os.environ.get("AGRI_TIMEOUT_SEC", 8))
MAX_RETRIES = int(os.environ.get("AGRI_MAX_RETRIES", 2))

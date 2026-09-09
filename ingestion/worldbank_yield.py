"""
Резервный реальный источник урожайности: World Bank Indicators API.

Причина появления модуля: FAOSTAT (fenixservices.fao.org) на момент работы
над проектом лежит целиком (подтверждено скриншотом пользователя — 521 от
Cloudflare, "Web server is down", а не блокировка сети на нашей стороне).
Ждать восстановления стороннего сервиса — плохая стратегия для курсового
проекта, поэтому вместо одного источника используется ЦЕПОЧКА реальных
источников с graceful fallback: FAOSTAT -> World Bank -> (в самом крайнем
случае) синтетика.

API: https://api.worldbank.org/v2/country/{iso3}/indicator/{indicator}
     ?date={y1}:{y2}&format=json&per_page=1000
Публичный, без авторизации, отдаёт [meta, data[]], где каждый элемент data:
    {"countryiso3code": "RUS", "date": "2020", "value": 2734.5, ...}

ВАЖНОЕ ОГРАНИЧЕНИЕ (честно, не для статьи как есть): показатель
AG.YLD.CREL.KG — это "Cereal yield" (урожайность ЗЕРНОВЫХ КУЛЬТУР В ЦЕЛОМ,
kg/ha), а не урожайность именно пшеницы. World Bank не публикует
культуроспецифичные показатели урожайности в своём основном API (это есть
только у FAOSTAT/USDA). Если этот источник используется — в статье это
нужно явно назвать сменой предмета анализа ("урожайность зерновых" вместо
"урожайность пшеницы"), а не подать как то же самое другими словами.

Про российский источник (Росстат/ЕМИСС, fedstat.ru): у него нет стабильного
публичного REST JSON API уровня FAOSTAT/World Bank — доступ обычно через
веб-интерфейс с выгрузкой в Excel/CSV вручную или через недокументированные
внутренние эндпоинты. Автоматизированная интеграция с гарантией
работоспособности здесь не реализована: слишком высокий риск повторить
историю с FAOSTAT, потратив время на нестабильный endpoint. Более надёжный
путь для точно российских данных — вручную выгрузить таблицу с fedstat.ru
или из статистических сборников Росстата и подключить как локальный CSV
(см. ingestion/local_csv_yield.py, если он понадобится).
"""
import requests

from config import REQUEST_TIMEOUT_SEC, MAX_RETRIES

WORLD_BANK_URL_TMPL = "https://api.worldbank.org/v2/country/{iso3}/indicator/{indicator}"
COUNTRY_ISO3 = "RUS"
INDICATOR = "AG.YLD.CREL.KG"  # Cereal yield (kg per hectare) — не культуроспецифично, см. docstring


def fetch_real(year_start: int, year_end: int, iso3: str = COUNTRY_ISO3, indicator: str = INDICATOR):
    url = WORLD_BANK_URL_TMPL.format(iso3=iso3, indicator=indicator)
    params = {"date": f"{year_start}:{year_end}", "format": "json", "per_page": 1000}
    last_err = None
    for _ in range(MAX_RETRIES + 1):
        try:
            resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT_SEC)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:  # noqa: BLE001
            last_err = e
    raise RuntimeError(f"World Bank API недоступен после {MAX_RETRIES + 1} попыток: {last_err}")


def parse_series(payload) -> dict:
    if not isinstance(payload, list) or len(payload) < 2 or payload[1] is None:
        raise ValueError("Неожиданный формат ответа World Bank API (ожидался [meta, data[]])")
    rows = payload[1]
    out = {}
    for row in rows:
        year, value = row.get("date"), row.get("value")
        if year is None or value is None:
            continue  # World Bank честно отдаёт null за годы без данных — это не ошибка
        out[int(year)] = round(float(value) / 1000.0, 3)  # kg/ha -> t/ha
    if not out:
        raise ValueError("World Bank вернул пустой ряд (все значения null) за запрошенные годы")
    return out


def fetch(year_start: int, year_end: int):
    """Возвращает (series_dict, status, raw_payload). status ∈ {'live', 'failed'} —
    здесь нет собственного synthetic-fallback: это уже сам fallback для FAOSTAT,
    решение "что делать, если и этот источник недоступен" принимает вызывающий код."""
    try:
        payload = fetch_real(year_start, year_end)
        series = parse_series(payload)
        return series, "live", payload
    except Exception as e:  # noqa: BLE001
        return {}, "failed", {"_error": str(e)}

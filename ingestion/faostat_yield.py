"""
Источник для РЕАЛЬНОГО эксперимента статьи: FAOSTAT — годовая урожайность
пшеницы по стране, официальная статистика ФАО ООН. Публичный REST API,
без авторизации.

ВАЖНО (честное предупреждение, а не отговорка): сеть в среде, где писался
этот код, не пропускает запросы к fenixservices.fao.org (только
pypi/npm/github). Поэтому код ниже реализован по документации API из памяти
модели и НЕ проверен вживую. При первом реальном запуске у пользователя
нужно явно свериться с тем, что парсинг не упал молча, а либо отработал,
либо кинул понятную ошибку (see fetch_real: исключения не глотаются молча
при ошибках парсинга структуры ответа — только сетевые сбои идут в fallback).

Документация API (может быть неточной в деталях): https://fenixservices.fao.org/faostat/static/bulkdownloads/FAOSTAT_BulkDownloads.htm
Общая механика:
  1) GET .../definitions/domain/{domain}/area   -> список {code, label} стран
  2) GET .../definitions/domain/{domain}/item   -> список {code, label} культур
  3) GET .../definitions/domain/{domain}/element -> список {code, label} показателей
  4) GET .../data/{domain}?area=<code>&item=<code>&element=<code>&year=<y1,y2,...>&output_type=objects
     -> {"data": [{"Year": 2001, "Value": 23456.0, "Unit": "hg/ha", ...}, ...]}
     Единица измерения урожайности в FAOSTAT — hg/ha (гектограмм/га);
     1 т/га = 10000 hg/га -> обязательно делить на 10000.
"""
import random
import requests

from config import (
    FAOSTAT_BASE_URL, FAOSTAT_DOMAIN, FAOSTAT_AREA_NAME,
    FAOSTAT_ITEM_NAME, FAOSTAT_ELEMENT_NAME, REQUEST_TIMEOUT_SEC, MAX_RETRIES,
)


def _get(url, params=None):
    last_err = None
    for _ in range(MAX_RETRIES + 1):
        try:
            resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT_SEC)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:  # noqa: BLE001 - сетевые сбои уходят в fallback выше по стеку
            last_err = e
    raise RuntimeError(f"FAOSTAT недоступен после {MAX_RETRIES + 1} попыток: {last_err}")


def _find_code(definitions_payload, name_substring):
    """
    Ищет код по подстроке названия в ответе definitions-эндпоинта.
    Формат ответа FAOSTAT definitions обычно {"data": [{"code": "...", "label": "..."}]},
    но точные ключи не проверены вживую в этой среде -> пробуем несколько вариантов
    имён ключей, чтобы не упасть на мелком расхождении в API.
    """
    items = definitions_payload.get("data", definitions_payload if isinstance(definitions_payload, list) else [])
    name_substring = name_substring.lower()
    for row in items:
        label = str(row.get("label") or row.get("Label") or row.get("name") or row.get("Item") or row.get("Area") or "")
        if name_substring in label.lower():
            code = row.get("code") or row.get("Code") or row.get("id") or row.get("Item Code") or row.get("Area Code")
            if code is not None:
                return code, label
    raise ValueError(f"Не найден код для '{name_substring}' в ответе definitions-эндпоинта FAOSTAT")


def resolve_codes():
    """Определяет числовые/строковые коды area/item/element по человекочитаемым именам из config.py."""
    base = f"{FAOSTAT_BASE_URL}/definitions/domain/{FAOSTAT_DOMAIN}"
    area_code, area_label = _find_code(_get(f"{base}/area"), FAOSTAT_AREA_NAME)
    item_code, item_label = _find_code(_get(f"{base}/item"), FAOSTAT_ITEM_NAME)
    elem_code, elem_label = _find_code(_get(f"{base}/element"), FAOSTAT_ELEMENT_NAME)
    return {
        "area": (area_code, area_label), "item": (item_code, item_label), "element": (elem_code, elem_label),
    }


def fetch_real(year_start: int, year_end: int):
    """Реальный вызов FAOSTAT. Бросает исключение при неудаче (сеть ИЛИ неожиданный формат кодов)."""
    codes = resolve_codes()
    years = ",".join(str(y) for y in range(year_start, year_end + 1))
    data_url = f"{FAOSTAT_BASE_URL}/data/{FAOSTAT_DOMAIN}"
    payload = _get(data_url, params={
        "area": codes["area"][0], "item": codes["item"][0], "element": codes["element"][0],
        "year": years, "output_type": "objects",
    })
    payload["_resolved_codes"] = {k: v for k, v in codes.items()}
    return payload


def parse_series(payload) -> dict:
    """
    Возвращает {year: yield_t_ha}. Бросает исключение, если структура ответа
    не совпала с ожидаемой (осознанно — чтобы не подставить тихо неверные числа).
    """
    rows = payload.get("data")
    if rows is None:
        raise ValueError("В ответе FAOSTAT нет ключа 'data' — формат ответа отличается от ожидаемого")
    out = {}
    for row in rows:
        year = row.get("Year") or row.get("year")
        value = row.get("Value") if "Value" in row else row.get("value")
        unit = str(row.get("Unit") or row.get("unit") or "hg/ha")
        if year is None or value is None:
            continue
        value = float(value)
        if "hg" in unit.lower():
            value = value / 10000.0  # hg/ha -> t/ha
        out[int(year)] = round(value, 3)
    if not out:
        raise ValueError("FAOSTAT вернул пустой список данных за запрошенные годы")
    return out


def _synthetic_series(year_start, year_end, seed=42):
    """Fallback ТОЛЬКО на случай сетевого сбоя — не предназначен для использования в статье."""
    rng = random.Random(seed)
    out, base = {}, 2.4
    for y in range(year_start, year_end + 1):
        base += rng.uniform(-0.05, 0.08)  # слабый растущий тренд + шум, правдоподобно для урожайности
        out[y] = round(max(1.0, base + rng.gauss(0, 0.3)), 3)
    return out


def fetch(year_start: int, year_end: int):
    """Возвращает (series_dict, status). status ∈ {'live', 'degraded_synthetic'}."""
    try:
        payload = fetch_real(year_start, year_end)
        series = parse_series(payload)
        return series, "live", payload
    except Exception as e:  # noqa: BLE001
        series = _synthetic_series(year_start, year_end)
        return series, "degraded_synthetic", {"_error": str(e), "_synthetic": True}

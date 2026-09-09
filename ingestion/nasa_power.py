"""
Источник 1: NASA POWER — суточные метеорологические временные ряды по координатам.
Реальный REST API, JSON. Документация: https://power.larc.nasa.gov/docs/

Формат реального запроса (проверено по документации API):
GET https://power.larc.nasa.gov/api/temporal/daily/point
    ?parameters=T2M,PRECTOTCORR,ALLSKY_SFC_SW_DWN,RH2M
    &community=AG
    &longitude=<lon>&latitude=<lat>
    &start=YYYYMMDD&end=YYYYMMDD
    &format=JSON

Этот модуль пытается выполнить реальный запрос. Если источник недоступен
(таймаут, сетевая политика окружения, rate limit, 5xx) — событие фиксируется
в журнале загрузок, и модуль переключается на синтетический генератор с тем
же контрактом данных, чтобы конвейер не останавливался (требование
"устойчивость к сбоям" из методички).
"""
import math
import random
import requests
from datetime import date, timedelta, datetime

from config import NASA_POWER_URL, NASA_POWER_PARAMS, REQUEST_TIMEOUT_SEC, MAX_RETRIES


def fetch_real(lat, lon, start: date, end: date):
    """Реальный вызов NASA POWER API. Бросает исключение при неудаче."""
    params = {
        "parameters": ",".join(NASA_POWER_PARAMS),
        "community": "AG",
        "longitude": lon,
        "latitude": lat,
        "start": start.strftime("%Y%m%d"),
        "end": end.strftime("%Y%m%d"),
        "format": "JSON",
    }
    last_err = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = requests.get(NASA_POWER_URL, params=params, timeout=REQUEST_TIMEOUT_SEC)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:  # noqa: BLE001 - сознательно широкий catch для сетевых сбоев
            last_err = e
    raise RuntimeError(f"NASA POWER недоступен после {MAX_RETRIES + 1} попыток: {last_err}")


def _synthetic_daily(lat, lon, start: date, end: date, seed: int):
    """
    Синтетический генератор с той же структурой ответа, что и реальный API,
    построенный на упрощённой сезонной модели (годовая синусоида + шум),
    чтобы данные оставались правдоподобными для средней полосы/юга РФ.
    """
    rng = random.Random(seed)
    days = (end - start).days + 1
    dates, t2m, precip, rad, rh = [], {}, {}, {}, {}
    for i in range(days):
        d = start + timedelta(days=i)
        doy = d.timetuple().tm_yday
        # сезонная составляющая температуры (пик к середине июля)
        seasonal = 15 * math.sin(2 * math.pi * (doy - 80) / 365)
        t = 8 + seasonal + rng.gauss(0, 2.0)
        p = max(0.0, rng.gauss(2.5, 4.0)) if rng.random() < 0.35 else 0.0
        r = max(0.5, 12 + 10 * math.sin(2 * math.pi * (doy - 80) / 365) + rng.gauss(0, 1.5))
        h = min(100, max(20, 65 + rng.gauss(0, 10)))
        key = d.strftime("%Y%m%d")
        t2m[key] = round(t, 2)
        precip[key] = round(p, 2)
        rad[key] = round(r, 2)
        rh[key] = round(h, 2)
    return {
        "properties": {
            "parameter": {
                "T2M": t2m,
                "PRECTOTCORR": precip,
                "ALLSKY_SFC_SW_DWN": rad,
                "RH2M": rh,
            }
        },
        "_synthetic": True,
    }


def fetch(field_id, lat, lon, start: date, end: date):
    """
    Возвращает (payload, status) где status ∈ {'live', 'degraded_synthetic'}.
    Никогда не бросает исключение наружу — сбой обрабатывается внутри и логируется вызывающей стороной.
    """
    try:
        payload = fetch_real(lat, lon, start, end)
        return payload, "live"
    except Exception as e:  # noqa: BLE001
        seed = abs(hash(field_id)) % (2**32)
        payload = _synthetic_daily(lat, lon, start, end, seed)
        payload["_error"] = str(e)
        return payload, "degraded_synthetic"

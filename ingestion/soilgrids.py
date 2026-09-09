"""
Источник 2: ISRIC SoilGrids — характеристики почвы по координатам (REST API).
Документация: https://www.isric.org/explore/soilgrids/faq-soilgrids#What_do_the_filenames_mean

Реальный запрос:
GET https://rest.isric.org/soilgrids/v2.0/properties/query
    ?lon=<lon>&lat=<lat>
    &property=phh2o&property=soc&property=clay&property=sand&property=nitrogen
    &depth=0-5cm&value=mean

Разнородность формата относительно NASA POWER: другая структура JSON
(layers/depths/values), другая частота обновления (почва меняется медленно —
это quasi-static источник, а не временной ряд), что отдельно интересно
для демонстрации разных стратегий инкрементальной загрузки в одном проекте.
"""
import random
import requests

from config import SOILGRIDS_URL, SOILGRIDS_PROPERTIES, REQUEST_TIMEOUT_SEC, MAX_RETRIES


def fetch_real(lat, lon):
    params = [("lon", lon), ("lat", lat), ("depth", "0-5cm"), ("value", "mean")]
    for prop in SOILGRIDS_PROPERTIES:
        params.append(("property", prop))
    last_err = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = requests.get(SOILGRIDS_URL, params=params, timeout=REQUEST_TIMEOUT_SEC)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:  # noqa: BLE001
            last_err = e
    raise RuntimeError(f"SoilGrids недоступен после {MAX_RETRIES + 1} попыток: {last_err}")


def _synthetic_soil(field_id, seed):
    rng = random.Random(seed)
    # правдоподобные диапазоны для чернозёмов юга РФ
    return {
        "properties": {
            "layers": [
                {"name": "phh2o", "depths": [{"values": {"mean": round(rng.uniform(60, 75), 1)}}]},   # pH*10
                {"name": "soc",   "depths": [{"values": {"mean": round(rng.uniform(20, 45), 1)}}]},    # g/kg *10
                {"name": "clay",  "depths": [{"values": {"mean": round(rng.uniform(250, 400), 1)}}]},  # g/kg
                {"name": "sand",  "depths": [{"values": {"mean": round(rng.uniform(200, 350), 1)}}]},
                {"name": "nitrogen", "depths": [{"values": {"mean": round(rng.uniform(1.5, 3.5), 2)}}]},
            ]
        },
        "_synthetic": True,
    }


def fetch(field_id, lat, lon):
    try:
        payload = fetch_real(lat, lon)
        return payload, "live"
    except Exception as e:  # noqa: BLE001
        seed = abs(hash(field_id)) % (2**32)
        payload = _synthetic_soil(field_id, seed)
        payload["_error"] = str(e)
        return payload, "degraded_synthetic"

"""
Общая обвязка для тестов.

Единственное, что тут по-настоящему важно: тесты не должны писать в рабочую базу.
storage.db читает AGRI_DB_PATH при каждом обращении, поэтому достаточно подменить
переменную окружения — перезагружать модули не нужно.
"""
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    """Пустая база на один тест. Отдаёт модуль storage.db, уже настроенный на неё."""
    monkeypatch.setenv("AGRI_DB_PATH", str(tmp_path / f"test_{uuid.uuid4().hex[:8]}.sqlite"))
    import storage.db as db
    db.init_db()
    return db


@pytest.fixture()
def run_id():
    return "test" + uuid.uuid4().hex[:4]

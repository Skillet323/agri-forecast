"""
Автоматизированные проверки качества данных (обязательный компонент по выбору,
методичка требует минимум несколько типов проверок). Реализованы все семь
типов, перечисленных в методичке. Результат каждой проверки пишется в
quality_check_log через storage.db.log_quality.
"""
from datetime import datetime, timedelta, timezone
from storage.db import log_quality


def check_completeness(run_id, df, columns, table_name):
    """Полнота: доля непустых значений по ключевым колонкам."""
    for col in columns:
        if col not in df.columns:
            continue
        share_present = df[col].notna().mean() if len(df) else 0
        passed = share_present >= 0.8  # порог для демо; в проде — по SLA источника
        log_quality(run_id, table_name, "completeness", f"{col}_present_share",
                    passed, f"доля непустых значений = {share_present:.2%}")


def check_uniqueness(run_id, df, key_columns, table_name):
    """Уникальность: отсутствие дублей по ключу (field_id, дата/сезон)."""
    dup_count = df.duplicated(subset=key_columns).sum() if len(df) else 0
    passed = dup_count == 0
    log_quality(run_id, table_name, "uniqueness", "duplicate_keys",
                passed, f"дублей по ключу {key_columns}: {dup_count}")


def check_validity_types(run_id, df, numeric_columns, table_name):
    """Соответствие типов: значения должны приводиться к числу."""
    import pandas as pd
    for col in numeric_columns:
        if col not in df.columns:
            continue
        coerced = pd.to_numeric(df[col], errors="coerce")
        bad = (coerced.isna() & df[col].notna()).sum()
        passed = bad == 0
        log_quality(run_id, table_name, "validity", f"{col}_numeric_type",
                    passed, f"нечисловых значений: {bad}")


def check_ranges(run_id, df, ranges: dict, table_name):
    """Соответствие диапазонам: физически допустимые границы величин."""
    for col, (lo, hi) in ranges.items():
        if col not in df.columns:
            continue
        series = df[col].dropna()
        out_of_range = ((series < lo) | (series > hi)).sum()
        passed = out_of_range == 0
        log_quality(run_id, table_name, "range", f"{col}_in_range[{lo},{hi}]",
                    passed, f"значений вне диапазона: {out_of_range}")


def check_admissibility(run_id, df, column, allowed_values, table_name):
    """
    Допустимость значений: колонка должна принимать значения ТОЛЬКО из
    заранее известного конечного множества (в отличие от check_ranges —
    там числовой диапазон, здесь — категориальный домен). Отдельный от
    'соответствия типам' тип проверки, как того явно требует методичка.
    """
    if column not in df.columns or len(df) == 0:
        log_quality(run_id, table_name, "admissibility", f"{column}_in_allowed_set",
                    passed=1, details="колонка отсутствует или пусто — проверка пропущена")
        return
    bad_values = set(df[column].dropna().unique()) - set(allowed_values)
    passed = len(bad_values) == 0
    log_quality(run_id, table_name, "admissibility", f"{column}_in_allowed_set",
                passed, f"недопустимые значения: {sorted(bad_values) if bad_values else 'нет'} "
                        f"(разрешено: {sorted(allowed_values)})")


def check_referential_integrity(run_id, child_field_ids, parent_field_ids, table_name):
    """Ссылочная целостность: все field_id в дочерней таблице есть в справочнике полей."""
    missing = set(child_field_ids) - set(parent_field_ids)
    passed = len(missing) == 0
    log_quality(run_id, table_name, "referential", "field_id_in_reference",
                passed, f"неизвестных field_id: {sorted(missing)}")


def check_freshness(run_id, last_obs_date_str, max_lag_days, table_name):
    """Актуальность: последняя дата наблюдения не должна отставать от 'сегодня' сильнее порога."""
    try:
        last_date = datetime.strptime(last_obs_date_str, "%Y-%m-%d")
    except ValueError:
        last_date = datetime.strptime(last_obs_date_str, "%Y%m%d")
    lag = (datetime.now(timezone.utc).replace(tzinfo=None) - last_date).days
    passed = lag <= max_lag_days
    log_quality(run_id, table_name, "freshness", "max_lag_days",
                passed, f"отставание данных: {lag} дн. (порог {max_lag_days})")

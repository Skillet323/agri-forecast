"""
Прогнать проект целиком: python run_all.py

Порядок шагов важен — каждый следующий читает то, что записал предыдущий:

    pipeline.py          загрузка и витрина по полям
    models.py            две модели на полях, сравнение наборов признаков
    real_experiment.py   годовой контур по стране
    dashboard_export.py  оба дашборда в один html
    make_diagrams.py     базовые диаграммы
    report_figures.py    дополнительные рисунки для отчёта

Шаг, который упал, не останавливает остальные: если, скажем, недоступен источник
для годового контура, дашборды по полям всё равно соберутся. В конце печатается сводка,
где видно, что прошло, а что нет.

Полезные флаги:
    --skip-annual    не трогать годовой контур (он ходит в сеть дольше всех)
    --only pipeline  выполнить один конкретный шаг
"""
import argparse
import subprocess
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).parent

STEPS = [
    ("pipeline", "pipeline.py", "загрузка данных и витрина по полям"),
    ("models", "models.py", "модели на полевых данных"),
    ("annual", "real_experiment.py", "годовой контур по стране"),
    ("dashboard", "dashboard_export.py", "дашборды"),
    ("diagrams", "make_diagrams.py", "базовые диаграммы"),
    ("report-figures", "report_figures.py", "дополнительные рисунки для отчёта"),
]


def run_step(script):
    started = time.monotonic()
    result = subprocess.run([sys.executable, str(BASE_DIR / script)], cwd=BASE_DIR)
    return result.returncode == 0, time.monotonic() - started


def main():
    parser = argparse.ArgumentParser(description="Запуск всех шагов проекта")
    parser.add_argument("--skip-annual", action="store_true", help="пропустить годовой контур")
    parser.add_argument("--only", help="выполнить только этот шаг: " + ", ".join(s[0] for s in STEPS))
    args = parser.parse_args()

    steps = STEPS
    if args.only:
        steps = [s for s in STEPS if s[0] == args.only]
        if not steps:
            print(f"Нет такого шага: {args.only}")
            print("Доступны:", ", ".join(s[0] for s in STEPS))
            return 1
    elif args.skip_annual:
        steps = [s for s in STEPS if s[0] != "annual"]

    report = []
    for name, script, description in steps:
        print(f"\n{'=' * 70}\n{name}: {description}\n{'=' * 70}")
        ok, elapsed = run_step(script)
        report.append((name, ok, elapsed))
        if not ok:
            print(f"[{name}] завершился с ошибкой, продолжаем со следующего шага")

    print(f"\n{'=' * 70}\nИтог\n{'=' * 70}")
    for name, ok, elapsed in report:
        print(f"  {'успешно' if ok else 'ошибка ':<9} {name:<12} {elapsed:6.1f} с")

    failed = [name for name, ok, _ in report if not ok]
    if failed:
        print(f"\nНе прошли: {', '.join(failed)}")
        print("Чаще всего причина — недоступный источник данных. Подробности выше по выводу.")
        return 1

    print("\nГотово. Дашборды: data/dashboard.html, диаграммы и рисунки отчёта: data/diagrams/")
    return 0


if __name__ == "__main__":
    sys.exit(main())

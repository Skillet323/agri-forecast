"""
Генерация диаграмм для статьи/отчёта:
  data/diagrams/architecture.png        — архитектура системы (источники -> слои -> витрина -> модели -> дашборды)
  data/diagrams/system_analysis.png     — системный анализ (акторы, поток управления, обратная связь по качеству)
  data/diagrams/results_ablation.png    — MAE базовой vs расширенной модели по срокам/годам
  data/diagrams/results_yoy.png         — факт vs прогноз по годам (LOYO-CV, реальный эксперимент)
  data/diagrams/yield_weather_trend.png — динамика урожайности и ключевых погодных признаков по годам

Запуск: python make_diagrams.py (после pipeline.py/models.py и real_experiment.py —
использует их результаты; если файлов результатов ещё нет, соответствующий график пропускается).
"""
import json
from pathlib import Path

import graphviz
import matplotlib.pyplot as plt
import pandas as pd

DATA_DIR = Path(__file__).parent / "data"
OUT_DIR = DATA_DIR / "diagrams"
OUT_DIR.mkdir(exist_ok=True)

PALETTE = {
    "bg": "#f6f5f2", "ink": "#23241f", "accent": "#3f6b3f",
    "warn": "#b3541e", "muted": "#8a8a80", "panel": "#ffffff", "border": "#c9c7bd",
}


# ---------------------------------------------------------------------------
# 1. Архитектура системы
# ---------------------------------------------------------------------------
def make_architecture_diagram():
    g = graphviz.Digraph("architecture", format="png")
    g.attr(rankdir="LR", bgcolor=PALETTE["bg"], fontname="Helvetica", splines="ortho", nodesep="0.4", ranksep="0.7")
    g.attr("node", fontname="Helvetica", fontsize="11", style="filled", color=PALETTE["border"],
           fillcolor=PALETTE["panel"], fontcolor=PALETTE["ink"], shape="box", margin="0.18,0.12")
    g.attr("edge", color=PALETTE["muted"], fontname="Helvetica", fontsize="9", fontcolor=PALETTE["muted"])

    with g.subgraph(name="cluster_sources") as c:
        c.attr(label="Источники (разнородные)", style="dashed", color=PALETTE["border"], fontcolor=PALETTE["muted"], fontsize="11")
        c.node("nasa", "NASA POWER\nметео, REST JSON\nсуточный ряд")
        c.node("soil", "ISRIC SoilGrids\nпочва, REST JSON\nquasi-static")
        c.node("s2", "Sentinel-2\nNDVI, STAC+OAuth\nрастры")
        c.node("yield_src", "FAOSTAT / World Bank\nурожайность, REST JSON\nгодовой bootstrap")

    with g.subgraph(name="cluster_layers") as c:
        c.attr(label="Слои хранения (SQLite)", style="dashed", color=PALETTE["border"], fontcolor=PALETTE["muted"], fontsize="11")
        c.node("raw", "RAW\nсырые ответы, неизменяемые")
        c.node("staging", "STAGING\nочищено, типизировано,\nобщая сетка field×date / год")
        c.node("curated", "CURATED\nвитрина(ы):\ncurated_field_daily\ncurated_national_annual")

    g.node("quality", "Контроль качества\n7 типов проверок\n+ журнал", fillcolor="#f6e7d8")
    g.node("logs", "Журнал загрузок\n(ingestion_log)", fillcolor="#f6e7d8")

    with g.subgraph(name="cluster_models") as c:
        c.attr(label="Прогнозное ядро", style="dashed", color=PALETTE["border"], fontcolor=PALETTE["muted"], fontsize="11")
        c.node("model_base", "Baseline\nметео(+почва)")
        c.node("model_ext", "Extended\n+NDVI / +радиация,влажность")
        c.node("cv", "LOFO / LOYO\nвалидация\n(без утечек по сущности)")

    with g.subgraph(name="cluster_dash") as c:
        c.attr(label="Визуализация", style="dashed", color=PALETTE["border"], fontcolor=PALETTE["muted"], fontsize="11")
        c.node("dash_subj", "Дашборд:\nпредметный\n(агроном)", fillcolor="#e4efe4")
        c.node("dash_ops", "Дашборд:\nоперационный\n(инж. данных)", fillcolor="#e4efe4")

    for src in ["nasa", "soil", "s2", "yield_src"]:
        g.edge(src, "raw")
    g.edge("raw", "staging")
    g.edge("staging", "quality", style="dashed", label="проверки")
    g.edge("quality", "logs", style="dashed")
    g.edge("staging", "curated")
    g.edge("curated", "model_base")
    g.edge("curated", "model_ext")
    g.edge("model_base", "cv")
    g.edge("model_ext", "cv")
    g.edge("cv", "dash_subj")
    g.edge("curated", "dash_subj")
    g.edge("logs", "dash_ops")
    g.edge("quality", "dash_ops")

    g.render(OUT_DIR / "architecture", cleanup=True)
    print("architecture.png готов")


# ---------------------------------------------------------------------------
# 2. Системный анализ: акторы, потоки, обратная связь
# ---------------------------------------------------------------------------
def make_system_analysis_diagram():
    g = graphviz.Digraph("system_analysis", format="png")
    g.attr(rankdir="TB", bgcolor=PALETTE["bg"], fontname="Helvetica", nodesep="0.5", ranksep="0.6")
    g.attr("node", fontname="Helvetica", fontsize="11", style="filled", color=PALETTE["border"],
           fillcolor=PALETTE["panel"], fontcolor=PALETTE["ink"])
    g.attr("edge", color=PALETTE["muted"], fontname="Helvetica", fontsize="9", fontcolor=PALETTE["muted"])

    g.node("agronomist", "Агроном\n(конечный пользователь 1)", shape="ellipse", fillcolor="#e4efe4")
    g.node("engineer", "Инженер данных\n(конечный пользователь 2)", shape="ellipse", fillcolor="#e4efe4")
    g.node("scheduler", "Оркестратор\n(запуск по требованию /\nпо расписанию)", shape="box3d")

    g.node("orchestrate", "pipeline.py /\nreal_experiment.py", shape="box")
    g.node("checks", "Контроль качества\n(pass/fail по 7 типам)", shape="box", fillcolor="#f6e7d8")
    g.node("resilience", "Fallback-цепочка\nlive -> альт.источник ->\nsynthetic", shape="box", fillcolor="#f6e7d8")
    g.node("model", "Обучение и валидация\nмоделей (LOFO/LOYO)", shape="box")
    g.node("marts", "Витрины (curated)", shape="cylinder")
    g.node("dash1", "Дашборд: предметный", shape="note", fillcolor="#e4efe4")
    g.node("dash2", "Дашборд: операционный", shape="note", fillcolor="#e4efe4")

    g.edge("scheduler", "orchestrate", label="запуск")
    g.edge("orchestrate", "resilience", label="на каждый источник")
    g.edge("resilience", "checks", label="staging")
    g.edge("checks", "marts", label="если прошли\n(или помечено)")
    g.edge("marts", "model")
    g.edge("model", "dash1")
    g.edge("marts", "dash1")
    g.edge("checks", "dash2", label="статус проверок")
    g.edge("resilience", "dash2", label="статус источников")
    g.edge("dash1", "agronomist", style="bold")
    g.edge("dash2", "engineer", style="bold")
    g.edge("engineer", "orchestrate", style="dashed", label="решение о повторном\nзапуске / смене источника")

    g.render(OUT_DIR / "system_analysis", cleanup=True)
    print("system_analysis.png готов")


# ---------------------------------------------------------------------------
# 3. Результаты: абляция (baseline vs extended), по срезам/годам
# ---------------------------------------------------------------------------
def make_ablation_chart():
    src = DATA_DIR / "real_model_results.json"
    if not src.exists():
        print("real_model_results.json не найден — пропускаю results_ablation.png (сначала запусти real_experiment.py)")
        return
    res = json.loads(src.read_text(encoding="utf-8"))
    if "baseline_weather_only" not in res:
        print("В real_model_results.json нет данных модели — пропускаю results_ablation.png")
        return

    base, ext = res["baseline_weather_only"], res["extended_weather_soil"]
    labels = ["MAE, т/га", "MAPE, %"]
    base_vals = [base["mae_t_ha"], base["mape_pct"]]
    ext_vals = [ext["mae_t_ha"], ext["mape_pct"]]

    fig, axes = plt.subplots(1, 2, figsize=(8, 4))
    fig.patch.set_facecolor(PALETTE["bg"])
    for ax, label, bv, ev in zip(axes, labels, base_vals, ext_vals):
        ax.set_facecolor(PALETTE["bg"])
        bars = ax.bar(["Baseline\n(погода)", "Extended\n(+почва)"], [bv, ev],
                       color=[PALETTE["accent"], PALETTE["warn"]])
        ax.set_title(label, fontsize=11)
        for b in bars:
            ax.annotate(f"{b.get_height():.3g}", (b.get_x() + b.get_width() / 2, b.get_height()),
                        ha="center", va="bottom", fontsize=10)
        ax.spines[["top", "right"]].set_visible(False)

    status_note = f"источник урожайности: {res.get('yield_source', '?')}, статус: {res['statuses'].get('yield')}"
    fig.suptitle(f"LOYO-CV, n={res['n_years']} лет ({res['years_range'][0]}–{res['years_range'][1]})\n{status_note}", fontsize=10)
    fig.tight_layout()
    fig.savefig(OUT_DIR / "results_ablation.png", dpi=150, facecolor=fig.get_facecolor())
    plt.close(fig)
    print("results_ablation.png готов")


def make_yoy_chart():
    src = DATA_DIR / "real_model_results.json"
    if not src.exists():
        print("real_model_results.json не найден — пропускаю results_yoy.png")
        return
    res = json.loads(src.read_text(encoding="utf-8"))
    if "extended_weather_soil" not in res:
        return
    ext = res["extended_weather_soil"]
    yield_label = "Факт (реальная урожайность)" if res["statuses"].get("yield") == "live" else "Факт (СИНТЕТИКА — заглушка)"
    fig, ax = plt.subplots(figsize=(9, 4))
    fig.patch.set_facecolor(PALETTE["bg"])
    ax.set_facecolor(PALETTE["bg"])
    ax.plot(ext["years"], ext["y_true"], marker="o", color=PALETTE["accent"], label=yield_label)
    ax.plot(ext["years"], ext["y_pred"], marker="s", color=PALETTE["warn"], linestyle="--", label="Прогноз (LOYO-CV)")
    ax.set_ylabel("т/га")
    ax.set_title(f"Факт vs прогноз по годам — источник: {res.get('yield_source', '?')} (статус: {res['statuses'].get('yield')})")
    ax.legend(frameon=False)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(OUT_DIR / "results_yoy.png", dpi=150, facecolor=fig.get_facecolor())
    plt.close(fig)
    print("results_yoy.png готов")


def make_yield_weather_trend():
    import sqlite3
    db_path = DATA_DIR / "warehouse.sqlite"
    if not db_path.exists():
        print("warehouse.sqlite не найден — пропускаю yield_weather_trend.png")
        return
    con = sqlite3.connect(db_path)
    try:
        df = pd.read_sql("select * from curated_national_annual order by year", con)
    except Exception:
        print("Таблица curated_national_annual не найдена — пропускаю yield_weather_trend.png")
        return
    finally:
        con.close()
    if df.empty:
        return

    fig, ax1 = plt.subplots(figsize=(9, 4))
    fig.patch.set_facecolor(PALETTE["bg"])
    ax1.set_facecolor(PALETTE["bg"])
    ax1.plot(df["year"], df["yield_t_ha"], color=PALETTE["accent"], marker="o", label="Урожайность, т/га")
    ax1.set_ylabel("Урожайность, т/га", color=PALETTE["accent"])
    ax1.spines[["top"]].set_visible(False)

    ax2 = ax1.twinx()
    ax2.plot(df["year"], df["sum_precip"], color=PALETTE["warn"], marker="x", linestyle=":", label="Осадки за сезон, мм")
    ax2.set_ylabel("Осадки за сезон, мм", color=PALETTE["warn"])
    ax2.spines[["top"]].set_visible(False)

    fig.suptitle("Урожайность и суммарные осадки вегетационного периода по годам", fontsize=11)
    yield_status_note = str(df["yield_source_status"].iloc[0]) if "yield_source_status" in df.columns else "?"
    ax1.set_title(f"источник урожайности: {yield_status_note}", fontsize=9, color=PALETTE["muted"])
    fig.tight_layout()
    fig.savefig(OUT_DIR / "yield_weather_trend.png", dpi=150, facecolor=fig.get_facecolor())
    plt.close(fig)
    print("yield_weather_trend.png готов")


if __name__ == "__main__":
    make_architecture_diagram()
    make_system_analysis_diagram()
    make_ablation_chart()
    make_yoy_chart()
    make_yield_weather_trend()
    print(f"\nВсе доступные диаграммы сохранены в {OUT_DIR}")

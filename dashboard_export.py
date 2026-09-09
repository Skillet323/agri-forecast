"""
Генерация двух дашбордов проекта в один HTML-файл (data/dashboard.html):

  1) Предметный дашборд (пользователь — агроном/агроном-аналитик):
     - прогноз урожайности по полям (факт vs предсказание модели, LOFO)
     - динамика NDVI по полям в течение сезона
     - поля-риски (низкий прогноз / деградированный источник NDVI)

  2) Операционный дашборд (пользователь — инженер данных):
     - статус последнего запуска по каждому источнику (live/degraded/failed)
     - результаты проверок качества данных по последнему запуску
     - история запусков (журнал загрузок)

Дашборд регенерируется этим скриптом при каждом запуске пайплайна —
это и есть "автоматическое обновление от витрин" из критериев методички.
Данные внедряются в HTML как JSON на момент генерации (без внешнего
бэкенда/сервера — уместно для прототипа курсового проекта).
"""
import json
import sqlite3
from pathlib import Path

import pandas as pd

DB_PATH = Path(__file__).parent / "data" / "warehouse.sqlite"
MODEL_RESULTS_PATH = Path(__file__).parent / "data" / "model_results.json"
OUT_HTML = Path(__file__).parent / "data" / "dashboard.html"


def load_data():
    con = sqlite3.connect(DB_PATH)
    curated = pd.read_sql("select * from curated_field_daily", con, parse_dates=["obs_date"])
    ingestion = pd.read_sql("select * from ingestion_log order by started_at", con)
    quality = pd.read_sql("select * from quality_check_log order by checked_at", con)
    con.close()

    model_results = {}
    if MODEL_RESULTS_PATH.exists():
        model_results = json.loads(MODEL_RESULTS_PATH.read_text(encoding="utf-8"))

    last_run = ingestion["run_id"].iloc[-1] if not ingestion.empty else None

    ndvi_series = {}
    for field_id, g in curated.groupby("field_id"):
        g = g.sort_values("obs_date")
        obs = g.dropna(subset=["ndvi"])
        ndvi_series[field_id] = {
            "dates": obs["obs_date"].dt.strftime("%Y-%m-%d").tolist(),
            "ndvi": obs["ndvi"].round(3).tolist(),
        }

    fields = sorted(curated["field_id"].unique().tolist())
    yield_by_field = curated.drop_duplicates("field_id").set_index("field_id")["yield_t_ha"].round(2).to_dict()

    last_run_ingestion = ingestion[ingestion["run_id"] == last_run] if last_run else ingestion
    source_status = (
        last_run_ingestion.groupby("source")["status"]
        .agg(lambda s: s.value_counts().idxmax())
        .to_dict()
    )

    last_run_quality = quality[quality["run_id"] == last_run] if last_run else quality
    quality_summary = (
        last_run_quality.groupby(["table_name", "check_type"])["passed"].min().reset_index()
    )
    quality_rows = quality_summary.to_dict(orient="records")

    runs_history = (
        ingestion.groupby("run_id")
        .agg(started_at=("started_at", "min"), sources=("source", "nunique"), rows=("rows_written", "sum"))
        .reset_index()
        .sort_values("started_at")
        .to_dict(orient="records")
    )

    # риск: последний известный field_id, у которого источник ndvi последнего запуска
    # деградирован ИЛИ прогноз (full_season, extended) заметно ниже среднего
    risk_fields = []
    ndvi_status_by_field = (
        last_run_ingestion[last_run_ingestion["source"] == "sentinel2"]
        .set_index("field_id")["status"].to_dict()
    )
    mean_yield = sum(yield_by_field.values()) / max(len(yield_by_field), 1)
    for f in fields:
        reasons = []
        if ndvi_status_by_field.get(f) == "degraded_synthetic":
            reasons.append("NDVI: синтетика (нет OAuth Copernicus)")
        if yield_by_field.get(f, mean_yield) < mean_yield * 0.9:
            reasons.append("прогноз урожайности ниже среднего на >10%")
        if reasons:
            risk_fields.append({"field_id": f, "reasons": reasons})

    return {
        "fields": fields,
        "yield_by_field": yield_by_field,
        "ndvi_series": ndvi_series,
        "model_results": model_results,
        "source_status": source_status,
        "quality_rows": quality_rows,
        "runs_history": runs_history,
        "risk_fields": risk_fields,
        "last_run": last_run,
    }


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<title>Прогноз урожайности — дашборды проекта</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.0/chart.umd.min.js"></script>
<style>
  :root {
    --bg: #f6f5f2; --panel: #ffffff; --ink: #23241f; --muted: #6b6b63;
    --accent: #3f6b3f; --warn: #b3541e; --bad: #a4302a; --good: #3f6b3f;
    --border: #e3e1da;
  }
  * { box-sizing: border-box; }
  body { margin:0; font-family: -apple-system, Segoe UI, Roboto, sans-serif; background:var(--bg); color:var(--ink); }
  header { padding: 20px 28px; border-bottom: 1px solid var(--border); background:var(--panel); }
  header h1 { margin:0; font-size: 20px; }
  header p { margin: 4px 0 0; color:var(--muted); font-size: 13px; }
  .tabs { display:flex; gap:8px; padding: 14px 28px 0; background:var(--panel); }
  .tab { padding:10px 18px; border-radius: 10px 10px 0 0; cursor:pointer; font-size:14px; color:var(--muted); border:1px solid transparent; }
  .tab.active { background:var(--bg); color:var(--ink); font-weight:600; border:1px solid var(--border); border-bottom:none; }
  main { padding: 24px 28px 60px; max-width: 1180px; margin: 0 auto; }
  .grid { display:grid; grid-template-columns: repeat(2, 1fr); gap: 18px; }
  .card { background:var(--panel); border:1px solid var(--border); border-radius:14px; padding:18px; }
  .card h2 { margin:0 0 4px; font-size:15px; }
  .card .q { margin:0 0 14px; font-size:12px; color:var(--muted); }
  .full { grid-column: 1 / -1; }
  table { width:100%; border-collapse: collapse; font-size:13px; }
  th, td { text-align:left; padding:6px 8px; border-bottom:1px solid var(--border); }
  th { color:var(--muted); font-weight:600; }
  .pill { display:inline-block; padding:2px 9px; border-radius:999px; font-size:12px; font-weight:600; }
  .pill.live { background:#e4efe4; color:var(--good); }
  .pill.degraded_synthetic { background:#f6e7d8; color:var(--warn); }
  .pill.failed { background:#f5dede; color:var(--bad); }
  .pill.ok { background:#e4efe4; color:var(--good); }
  .pill.fail { background:#f5dede; color:var(--bad); }
  .risk-item { padding:8px 0; border-bottom:1px solid var(--border); font-size:13px; }
  .risk-item:last-child { border-bottom:none; }
  .risk-item b { color:var(--bad); }
  canvas { max-height: 260px; }
  .hidden { display:none; }
  .foot-note { font-size:11px; color:var(--muted); margin-top:6px; }
</style>
</head>
<body>
<header>
  <h1>Прогноз урожайности озимой пшеницы — дашборды</h1>
  <p>Последний запуск пайплайна: <span id="lastRun"></span> · генерируется автоматически из витрины curated_field_daily</p>
</header>
<div class="tabs">
  <div class="tab active" data-tab="subject">Предметный (агроном)</div>
  <div class="tab" data-tab="ops">Операционный (инженер данных)</div>
</div>
<main>
  <section id="tab-subject" class="grid">
    <div class="card full">
      <h2>Прогноз vs факт по полям (LOFO-CV, полный сезон, модель +NDVI)</h2>
      <p class="q">Вопрос: где модель занижает/завышает урожайность и насколько сильно?</p>
      <canvas id="chartYield"></canvas>
    </div>
    <div class="card full">
      <h2>Динамика NDVI по полям</h2>
      <p class="q">Вопрос: у каких полей вегетация отстаёт от нормы в течение сезона?</p>
      <canvas id="chartNdvi"></canvas>
    </div>
    <div class="card full">
      <h2>Поля риска</h2>
      <p class="q">Вопрос: на какие поля агроному стоит обратить внимание в первую очередь?</p>
      <div id="riskList"></div>
    </div>
  </section>

  <section id="tab-ops" class="grid hidden">
    <div class="card full">
      <h2>Статус источников (последний запуск)</h2>
      <p class="q">Вопрос: какие источники сейчас работают в live-режиме, а какие деградировали?</p>
      <table id="sourceStatusTable"><thead><tr><th>Источник</th><th>Статус</th></tr></thead><tbody></tbody></table>
    </div>
    <div class="card full">
      <h2>Контроль качества данных (последний запуск)</h2>
      <p class="q">Вопрос: какие проверки не прошли и в какой таблице?</p>
      <table id="qualityTable"><thead><tr><th>Таблица</th><th>Тип проверки</th><th>Результат</th></tr></thead><tbody></tbody></table>
    </div>
    <div class="card full">
      <h2>История запусков пайплайна</h2>
      <p class="q">Вопрос: пайплайн действительно запускается регулярно и стабильно пишет данные?</p>
      <canvas id="chartRuns"></canvas>
    </div>
  </section>
</main>

<script>
const DATA = __DATA_JSON__;

document.getElementById('lastRun').textContent = DATA.last_run || 'н/д';

document.querySelectorAll('.tab').forEach(t => t.addEventListener('click', () => {
  document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
  t.classList.add('active');
  document.getElementById('tab-subject').classList.toggle('hidden', t.dataset.tab !== 'subject');
  document.getElementById('tab-ops').classList.toggle('hidden', t.dataset.tab !== 'ops');
}));

// --- Предметный: прогноз vs факт ---
(function() {
  const ext = (DATA.model_results.extended_with_ndvi || []).find(r => r.cutoff === 'full_season');
  if (!ext) return;
  new Chart(document.getElementById('chartYield'), {
    type: 'bar',
    data: {
      labels: ext.held_out_fields,
      datasets: [
        { label: 'Факт, т/га', data: ext.y_true, backgroundColor: '#3f6b3f' },
        { label: 'Прогноз (LOFO), т/га', data: ext.y_pred, backgroundColor: '#b3541e' },
      ]
    },
    options: { responsive:true, plugins:{legend:{position:'bottom'}} }
  });
})();

// --- Предметный: NDVI по полям ---
(function() {
  const colors = ['#3f6b3f','#b3541e','#4a6fa5','#8a5fa0','#a4302a'];
  const datasets = Object.keys(DATA.ndvi_series).map((f, i) => ({
    label: f,
    data: DATA.ndvi_series[f].ndvi,
    borderColor: colors[i % colors.length],
    fill: false, tension: 0.25, pointRadius: 1,
  }));
  const labels = Object.values(DATA.ndvi_series)[0]?.dates || [];
  new Chart(document.getElementById('chartNdvi'), {
    type: 'line',
    data: { labels, datasets },
    options: { responsive:true, plugins:{legend:{position:'bottom'}}, scales:{x:{ticks:{maxTicksLimit:8}}} }
  });
})();

// --- Предметный: риски ---
(function() {
  const el = document.getElementById('riskList');
  if (!DATA.risk_fields.length) { el.innerHTML = '<p class="foot-note">Полей риска не выявлено.</p>'; return; }
  el.innerHTML = DATA.risk_fields.map(r =>
    `<div class="risk-item"><b>${r.field_id}</b>: ${r.reasons.join('; ')}</div>`
  ).join('');
})();

// --- Операционный: статус источников ---
(function() {
  const tbody = document.querySelector('#sourceStatusTable tbody');
  tbody.innerHTML = Object.entries(DATA.source_status).map(([src, status]) =>
    `<tr><td>${src}</td><td><span class="pill ${status}">${status}</span></td></tr>`
  ).join('');
})();

// --- Операционный: качество данных ---
(function() {
  const tbody = document.querySelector('#qualityTable tbody');
  tbody.innerHTML = DATA.quality_rows.map(r =>
    `<tr><td>${r.table_name}</td><td>${r.check_type}</td><td><span class="pill ${r.passed ? 'ok' : 'fail'}">${r.passed ? 'пройдено' : 'ошибка'}</span></td></tr>`
  ).join('');
})();

// --- Операционный: история запусков ---
(function() {
  new Chart(document.getElementById('chartRuns'), {
    type: 'bar',
    data: {
      labels: DATA.runs_history.map(r => r.started_at.slice(0,19)),
      datasets: [{ label: 'Строк записано', data: DATA.runs_history.map(r => r.rows), backgroundColor:'#4a6fa5' }]
    },
    options: { responsive:true, plugins:{legend:{display:false}} }
  });
})();
</script>
</body>
</html>
"""


def main():
    data = load_data()
    html = HTML_TEMPLATE.replace("__DATA_JSON__", json.dumps(data, ensure_ascii=False))
    OUT_HTML.write_text(html, encoding="utf-8")
    print(f"Дашборд сгенерирован: {OUT_HTML}")


if __name__ == "__main__":
    main()

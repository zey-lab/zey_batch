#!/usr/bin/env python3
"""Live business dashboard for Zey Brow & Wax.

Serves one HTML page, computed fresh from Supabase on every request:
today's report (SMS/email, revenue so far, appointments, opt-outs) plus the
monthly growth charts. Gated two ways: a shared-secret token in the URL
(?key=...) and an on/off switch controlled by touching/removing a state
file -- so the same URL can be safely left configured in Cloudflare
permanently, and access is toggled without redeploying anything.
"""

from __future__ import annotations

import html
import json
import os
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sms_campaign.db import get_connection  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / "data" / "dashboard_state.txt"
HOST = os.getenv("DASHBOARD_HOST", "127.0.0.1")
PORT = int(os.getenv("DASHBOARD_PORT", "8788"))
TOKEN = os.getenv("DASHBOARD_TOKEN", "")

MONTH_LABELS_TR = {
    "01": "Oca", "02": "Şub", "03": "Mar", "04": "Nis", "05": "May", "06": "Haz",
    "07": "Tem", "08": "Ağu", "09": "Eyl", "10": "Eki", "11": "Kas", "12": "Ara",
}


def is_enabled() -> bool:
    return STATE_PATH.exists() and STATE_PATH.read_text().strip() == "enabled"


def month_label(month: str) -> str:
    y, m = month.split("-")
    return f"{MONTH_LABELS_TR[m]} {y[2:]}"


def fetch_dashboard_data() -> dict:
    today_ct = datetime.now(ZoneInfo("America/Chicago")).date().isoformat()
    conn = get_connection()
    try:
        sms_rows = conn.execute(
            "SELECT campaign_type, status, COUNT(*) c FROM sms_history "
            "WHERE (sent_at::timestamptz AT TIME ZONE 'America/Chicago')::date=%s "
            "GROUP BY campaign_type, status ORDER BY campaign_type",
            (today_ct,),
        ).fetchall()
        email_today = conn.execute(
            "SELECT COUNT(*) c FROM email_history "
            "WHERE (sent_at::timestamptz AT TIME ZONE 'America/Chicago')::date=%s",
            (today_ct,),
        ).fetchone()["c"]
        txn_today = conn.execute(
            "SELECT customer_name, total_amount, "
            "transaction_date::timestamptz AT TIME ZONE 'America/Chicago' AS local_time "
            "FROM transactions WHERE (transaction_date::timestamptz AT TIME ZONE 'America/Chicago')::date=%s "
            "ORDER BY local_time",
            (today_ct,),
        ).fetchall()
        appts_today = conn.execute(
            "SELECT COUNT(*) c FROM services WHERE service_date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T' "
            "AND (service_date::timestamptz AT TIME ZONE 'America/Chicago')::date=%s",
            (today_ct,),
        ).fetchone()["c"]
        optouts_today = conn.execute(
            "SELECT COUNT(*) c FROM customers WHERE opt_out_date::date=%s", (today_ct,)
        ).fetchone()["c"]

        monthly = conn.execute(
            "SELECT to_char((transaction_date::timestamptz AT TIME ZONE 'America/Chicago'), 'YYYY-MM') AS month, "
            "COUNT(*) txn, COUNT(DISTINCT customer_id) cust, SUM(total_amount) revenue "
            "FROM transactions WHERE transaction_date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}' "
            "GROUP BY month ORDER BY month"
        ).fetchall()

        newrepeat = conn.execute(
            """
            WITH txn AS (
              SELECT customer_id, total_amount,
                     to_char((transaction_date::timestamptz AT TIME ZONE 'America/Chicago'), 'YYYY-MM') AS month
              FROM transactions
              WHERE customer_id IS NOT NULL AND transaction_date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'
            ),
            first_month AS (SELECT customer_id, MIN(month) AS first_month FROM txn GROUP BY customer_id)
            SELECT t.month,
              SUM(CASE WHEN t.month = f.first_month THEN t.total_amount ELSE 0 END) AS new_revenue,
              SUM(CASE WHEN t.month != f.first_month THEN t.total_amount ELSE 0 END) AS repeat_revenue
            FROM txn t JOIN first_month f ON f.customer_id = t.customer_id
            GROUP BY t.month ORDER BY t.month
            """
        ).fetchall()

        cumulative = conn.execute(
            """
            WITH txn AS (
              SELECT customer_id,
                     to_char((transaction_date::timestamptz AT TIME ZONE 'America/Chicago'), 'YYYY-MM') AS month
              FROM transactions
              WHERE customer_id IS NOT NULL AND transaction_date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'
            ),
            first_month AS (SELECT customer_id, MIN(month) AS first_month FROM txn GROUP BY customer_id),
            per_month AS (SELECT first_month AS month, COUNT(*) AS new_customers FROM first_month GROUP BY first_month)
            SELECT month, SUM(new_customers) OVER (ORDER BY month) AS cumulative
            FROM per_month ORDER BY month
            """
        ).fetchall()

        top_days = conn.execute(
            "SELECT (transaction_date::timestamptz AT TIME ZONE 'America/Chicago')::date AS day, "
            "SUM(total_amount) revenue, COUNT(*) txn_count, COUNT(DISTINCT customer_id) distinct_customers "
            "FROM transactions WHERE transaction_date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}' "
            "GROUP BY day ORDER BY revenue DESC LIMIT 3"
        ).fetchall()

        # Valuable customers who haven't come back yet -- stays on the list
        # until a new transaction updates their last_visit forward, at which
        # point they naturally drop off (or move down) on the next refresh.
        lapsed = conn.execute(
            """
            SELECT t.customer_id, COALESCE(c.first_name || ' ' || c.last_name, t.customer_name) AS name,
                   c.mobile, c.last_visit, c.sms_opt_out, c.email_opt_out,
                   (CURRENT_DATE - c.last_visit::date) AS days_since_visit,
                   SUM(t.total_amount) AS total_spent
            FROM transactions t
            LEFT JOIN customers c ON c.customer_id = t.customer_id
            WHERE t.customer_id IS NOT NULL AND c.active = 1 AND c.last_visit IS NOT NULL
            GROUP BY t.customer_id, c.first_name, c.last_name, t.customer_name, c.mobile,
                     c.last_visit, c.sms_opt_out, c.email_opt_out
            HAVING SUM(t.total_amount) > 200 AND (CURRENT_DATE - c.last_visit::date) > 30
            ORDER BY days_since_visit DESC, total_spent DESC
            """
        ).fetchall()
    finally:
        conn.close()

    nr_by_month = {r["month"]: r for r in newrepeat}
    cum_by_month = {r["month"]: r["cumulative"] for r in cumulative}
    current_month = today_ct[:7]

    series = []
    prev_revenue = None
    for r in monthly:
        m = r["month"]
        nr = nr_by_month.get(m, {"new_revenue": 0, "repeat_revenue": 0})
        revenue = float(r["revenue"] or 0)
        txn = r["txn"]
        mom_growth = ((revenue - prev_revenue) / prev_revenue * 100) if prev_revenue else None
        series.append({
            "month": m,
            "revenue": revenue,
            "txn": txn,
            "cust": r["cust"],
            "newRev": float(nr["new_revenue"] or 0),
            "repeatRev": float(nr["repeat_revenue"] or 0),
            "cumCust": int(cum_by_month.get(m, 0)),
            "avgTicket": (revenue / txn) if txn else 0.0,
            "momGrowth": mom_growth,
            "partial": m == current_month,
        })
        prev_revenue = revenue

    revenue_today = sum(float(r["total_amount"] or 0) for r in txn_today)
    avg_ticket_today = (revenue_today / len(txn_today)) if txn_today else 0.0
    current_mom = series[-1]["momGrowth"] if series else None

    return {
        "today_ct": today_ct,
        "sms_rows": [dict(r) for r in sms_rows],
        "email_today": email_today,
        "txn_today": [dict(r) for r in txn_today],
        "revenue_today": revenue_today,
        "avg_ticket_today": avg_ticket_today,
        "current_mom": current_mom,
        "appts_today": appts_today,
        "optouts_today": optouts_today,
        "series": series,
        "top_days": [dict(r) for r in top_days],
        "lapsed": [dict(r) for r in lapsed],
    }


def render_html(data: dict) -> str:
    sms_ok = sum(r["c"] for r in data["sms_rows"] if r["status"] in ("sent", "delivered"))
    sms_fail = sum(r["c"] for r in data["sms_rows"] if r["status"] not in ("sent", "delivered"))
    sms_breakdown = "".join(
        f"<tr><td>{html.escape(str(r['campaign_type']))}</td><td>{html.escape(str(r['status']))}</td>"
        f"<td>{r['c']}</td></tr>"
        for r in data["sms_rows"]
    )
    txn_rows_html = "".join(
        f"<tr><td>{html.escape(str(r['customer_name'] or '—'))}</td>"
        f"<td>${float(r['total_amount'] or 0):,.2f}</td>"
        f"<td>{r['local_time'].strftime('%H:%M')}</td></tr>"
        for r in data["txn_today"]
    )
    top_days_html = "".join(
        f"""<div class="top-day-tile">
              <div class="top-day-rank">{medal} {i+1}.</div>
              <div class="top-day-date">{d['day'].strftime('%d %B %Y')}</div>
              <div class="top-day-rev">${float(d['revenue']):,.2f}</div>
              <div class="top-day-meta">{d['txn_count']} işlem · {d['distinct_customers']} farklı müşteri</div>
            </div>"""
        for i, (medal, d) in enumerate(zip(["🥇", "🥈", "🥉"], data["top_days"]))
    )
    def fmt_mom(v):
        if v is None:
            return "—"
        cls = "stat-good" if v >= 0 else "stat-bad"
        sign = "+" if v >= 0 else ""
        return f'<span class="{cls}">{sign}{v:.1f}%</span>'

    def opt_badge(row):
        tags = []
        if row["sms_opt_out"]:
            tags.append('<span class="badge badge-bad">SMS opt-out</span>')
        if row["email_opt_out"]:
            tags.append('<span class="badge badge-bad">Email opt-out</span>')
        return " ".join(tags) or "—"

    lapsed_html = "".join(
        f"<tr><td>{html.escape(str(r['name'] or '—'))}</td>"
        f"<td>{html.escape(str(r['mobile'] or '—'))}</td>"
        f"<td>${float(r['total_spent'] or 0):,.2f}</td>"
        f"<td>{r['days_since_visit']}</td>"
        f"<td>{opt_badge(r)}</td></tr>"
        for r in data["lapsed"]
    )

    table_rows_html = "".join(
        f"<tr><td>{month_label(s['month'])}{' *' if s['partial'] else ''}</td>"
        f"<td>${s['revenue']:,.0f}</td><td>{s['txn']}</td><td>{s['cust']}</td>"
        f"<td>${s['newRev']:,.0f}</td><td>${s['repeatRev']:,.0f}</td><td>{s['cumCust']}</td>"
        f"<td>${s['avgTicket']:,.2f}</td><td>{fmt_mom(s['momGrowth'])}</td></tr>"
        for s in data["series"]
    )
    labels_json = json.dumps([month_label(s["month"]) for s in data["series"]])
    revenue_json = json.dumps([s["revenue"] for s in data["series"]])
    txn_json = json.dumps([s["txn"] for s in data["series"]])
    cust_json = json.dumps([s["cust"] for s in data["series"]])
    cum_json = json.dumps([s["cumCust"] for s in data["series"]])
    new_rev_json = json.dumps([s["newRev"] for s in data["series"]])
    repeat_rev_json = json.dumps([s["repeatRev"] for s in data["series"]])
    avg_ticket_json = json.dumps([s["avgTicket"] for s in data["series"]])
    mom_json = json.dumps([s["momGrowth"] for s in data["series"]])

    return f"""<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="120">
<title>Canlı Panel</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
  :root {{
    --surface-1: #fcfcfb; --surface-2: #f3f2ef; --text-primary: #0b0b0b;
    --text-secondary: #52514e; --text-muted: #86847c; --grid: #e4e2dc;
    --series-1: #2a78d6; --series-2: #eb6834; --series-3: #1baf7a; --series-4: #4a3aa7;
    --good: #1baf7a; --bad: #e34948;
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    background: var(--surface-1); color: var(--text-primary); }}
  .viz-root {{ padding: 24px 20px 48px; max-width: 1000px; margin: 0 auto; }}
  h1 {{ font-size: 20px; margin: 0 0 4px; font-weight: 700; }}
  .subtitle {{ font-size: 12.5px; color: var(--text-secondary); margin: 0 0 22px; }}
  .stat-row {{ display: flex; gap: 12px; margin-bottom: 22px; flex-wrap: wrap; }}
  .stat-tile {{ flex: 1; min-width: 140px; background: var(--surface-2); border-radius: 12px; padding: 14px 16px;
    box-shadow: 0 1px 2px rgba(0,0,0,.04); }}
  .stat-label {{ font-size: 11px; color: var(--text-secondary); margin-bottom: 4px; }}
  .stat-value {{ font-size: 22px; font-weight: 700; font-variant-numeric: tabular-nums; }}
  .stat-good {{ color: var(--good); }} .stat-bad {{ color: var(--bad); }}
  .section-title {{ font-size: 15px; font-weight: 700; margin: 30px 0 10px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 12.5px; }}
  th, td {{ text-align: right; padding: 6px 8px; border-bottom: 1px solid var(--grid); font-variant-numeric: tabular-nums; }}
  th:first-child, td:first-child {{ text-align: left; }}
  th {{ color: var(--text-secondary); font-weight: 600; }}
  .top-days {{ display: flex; gap: 12px; margin-bottom: 10px; flex-wrap: wrap; }}
  .top-day-tile {{ flex: 1; min-width: 180px; background: var(--surface-2); border-radius: 12px; padding: 16px 18px;
    box-shadow: 0 1px 2px rgba(0,0,0,.04); }}
  .top-day-rank {{ font-size: 12px; color: var(--text-secondary); margin-bottom: 4px; }}
  .top-day-date {{ font-size: 12.5px; color: var(--text-secondary); margin-bottom: 2px; }}
  .top-day-rev {{ font-size: 22px; font-weight: 700; font-variant-numeric: tabular-nums; }}
  .top-day-meta {{ font-size: 11px; color: var(--text-muted); margin-top: 4px; }}
  .panel {{ margin-bottom: 30px; background: var(--surface-2); border-radius: 14px; padding: 16px 18px 8px;
    box-shadow: 0 1px 2px rgba(0,0,0,.04); }}
  .panel-title {{ font-size: 13.5px; font-weight: 700; margin: 0 0 2px; }}
  .panel-sub {{ font-size: 11.5px; color: var(--text-muted); margin: 0 0 10px; }}
  .panel canvas {{ max-height: 220px; }}
  .footer-note {{ font-size: 10.5px; color: var(--text-muted); margin-top: 20px; }}
  .table-wrap {{ overflow-x: auto; }}
  .badge {{ font-size: 10px; padding: 2px 6px; border-radius: 5px; white-space: nowrap; }}
  .badge-bad {{ background: #fbe4e4; color: var(--bad); }}
</style>
</head>
<body>
<div class="viz-root">
  <h1>Canlı Panel</h1>
  <p class="subtitle">Bugün: {data['today_ct']} (CDT) · her 2 dakikada otomatik güncellenir · üretildi: {datetime.now(ZoneInfo('America/Chicago')).strftime('%H:%M:%S')}</p>

  <div class="stat-row">
    <div class="stat-tile"><div class="stat-label">Bugünkü Ciro</div><div class="stat-value">${data['revenue_today']:,.2f}</div></div>
    <div class="stat-tile"><div class="stat-label">Bugünkü İşlem</div><div class="stat-value">{len(data['txn_today'])}</div></div>
    <div class="stat-tile"><div class="stat-label">SMS (başarılı/başarısız)</div>
      <div class="stat-value"><span class="stat-good">{sms_ok}</span> / <span class="stat-bad">{sms_fail}</span></div></div>
    <div class="stat-tile"><div class="stat-label">Email Bugün</div><div class="stat-value">{data['email_today']}</div></div>
    <div class="stat-tile"><div class="stat-label">Randevu Bugün</div><div class="stat-value">{data['appts_today']}</div></div>
    <div class="stat-tile"><div class="stat-label">Yeni Opt-out</div><div class="stat-value">{data['optouts_today']}</div></div>
    <div class="stat-tile"><div class="stat-label">Ortalama İşlem (Bugün)</div><div class="stat-value">${data['avg_ticket_today']:,.2f}</div></div>
    <div class="stat-tile"><div class="stat-label">Ay-üstü-Ay Büyüme</div><div class="stat-value">{fmt_mom(data['current_mom'])}</div></div>
  </div>

  <p class="section-title">Bugünkü İşlemler</p>
  <div class="table-wrap"><table><thead><tr><th>Müşteri</th><th>Tutar</th><th>Saat</th></tr></thead>
  <tbody>{txn_rows_html or '<tr><td colspan="3">Henüz işlem yok</td></tr>'}</tbody></table></div>

  <p class="section-title">SMS Kırılımı (Bugün)</p>
  <div class="table-wrap"><table><thead><tr><th>Tip</th><th>Durum</th><th>Adet</th></tr></thead>
  <tbody>{sms_breakdown or '<tr><td colspan="3">Henüz gönderim yok</td></tr>'}</tbody></table></div>

  <p class="section-title">En Yüksek Ciro Yapan 3 Gün (Tüm Zamanlar)</p>
  <div class="top-days">{top_days_html}</div>

  <p class="section-title">Değerli Ama Uzun Süredir Gelmeyen Müşteriler</p>
  <p class="panel-sub">$200+ harcamış, 30+ gündür gelmemiş müşteriler — geri gelene kadar burada kalır (en uzun süredir gelmeyen en üstte)</p>
  <div class="table-wrap">
  <table><thead><tr><th>Müşteri</th><th>Telefon</th><th>Toplam Harcama</th><th>Gün Önce</th><th>Not</th></tr></thead>
  <tbody>{lapsed_html or '<tr><td colspan="5">Şu an listede kimse yok</td></tr>'}</tbody></table>
  </div>

  <p class="section-title">Aylık Büyüme</p>

  <div class="panel">
    <p class="panel-title">Aylık Ciro</p>
    <p class="panel-sub">Toplam tahsilat ($)</p>
    <canvas id="chart-revenue"></canvas>
  </div>

  <div class="panel">
    <p class="panel-title">Yeni Müşteri vs Sadık Müşteri Geliri</p>
    <p class="panel-sub">O ay ilk kez gelenlerden gelen gelir (yeni) vs daha önce gelmiş müşterilerden gelen gelir (sadık)</p>
    <canvas id="chart-newrepeat"></canvas>
  </div>

  <div class="panel">
    <p class="panel-title">Aylık İşlem Sayısı</p>
    <p class="panel-sub">Toplam satış/ziyaret adedi</p>
    <canvas id="chart-txn"></canvas>
  </div>

  <div class="panel">
    <p class="panel-title">Aylık Farklı Müşteri Sayısı</p>
    <p class="panel-sub">O ay en az bir kez gelen benzersiz müşteri sayısı</p>
    <canvas id="chart-cust"></canvas>
  </div>

  <div class="panel">
    <p class="panel-title">Kümülatif Toplam Müşteri Sayısı</p>
    <p class="panel-sub">Şimdiye kadar en az bir kez gelmiş, benzersiz müşteri toplamı</p>
    <canvas id="chart-cumcust"></canvas>
  </div>

  <div class="panel">
    <p class="panel-title">Ay-üstü-Ay Büyüme %</p>
    <p class="panel-sub">Önceki aya göre ciro değişimi</p>
    <canvas id="chart-mom"></canvas>
  </div>

  <div class="panel">
    <p class="panel-title">Ortalama İşlem Büyüklüğü</p>
    <p class="panel-sub">Aylık ciro / aylık işlem sayısı</p>
    <canvas id="chart-avgticket"></canvas>
  </div>

  <p class="section-title">Tablo Görünümü</p>
  <div class="table-wrap">
  <table><thead><tr><th>Ay</th><th>Ciro</th><th>İşlem</th><th>Farklı Müşteri</th>
    <th>Yeni Müşteri Geliri</th><th>Sadık Müşteri Geliri</th><th>Kümülatif Müşteri</th>
    <th>Ort. İşlem</th><th>AÜA Büyüme</th></tr></thead>
  <tbody>{table_rows_html}</tbody></table>
  </div>

  <p class="footer-note">* = ay henüz tamamlanmadı, diğer aylarla doğrudan kıyaslanmamalı.</p>
</div>
<script>
const LABELS = {labels_json};
const REVENUE = {revenue_json};
const TXN = {txn_json};
const CUST = {cust_json};
const CUM = {cum_json};
const NEW_REV = {new_rev_json};
const REPEAT_REV = {repeat_rev_json};
const AVG_TICKET = {avg_ticket_json};
const MOM = {mom_json};

const FONT = "-apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif";
Chart.defaults.font.family = FONT;
Chart.defaults.color = "#52514e";

function baseOptions(moneyAxis) {{
  return {{
    responsive: true,
    plugins: {{
      legend: {{ display: false }},
      tooltip: {{
        backgroundColor: "#0b0b0b", padding: 10, cornerRadius: 8,
        titleFont: {{ family: FONT, weight: 600 }}, bodyFont: {{ family: FONT }},
        callbacks: moneyAxis ? {{
          label: (ctx) => ctx.dataset.label ? ctx.dataset.label + ": $" + ctx.parsed.y.toLocaleString("en-US") :
                          "$" + ctx.parsed.y.toLocaleString("en-US")
        }} : {{}}
      }}
    }},
    scales: {{
      x: {{ grid: {{ display: false }}, ticks: {{ font: {{ size: 10.5 }} }} }},
      y: {{ beginAtZero: true, grid: {{ color: "#e4e2dc" }},
            ticks: {{ font: {{ size: 10.5 }}, callback: v => moneyAxis ? "$" + v.toLocaleString("en-US") : v }} }}
    }}
  }};
}}

function bar(id, label, data, color) {{
  new Chart(document.getElementById(id), {{
    type: "bar",
    data: {{ labels: LABELS, datasets: [{{ label, data, backgroundColor: color, borderRadius: 5, maxBarThickness: 28 }}] }},
    options: baseOptions(label.includes("$") || label.includes("Ciro") || label.includes("Geliri"))
  }});
}}

bar("chart-revenue", "Ciro ($)", REVENUE, "#2a78d6");
bar("chart-txn", "İşlem", TXN, "#eb6834");
bar("chart-cust", "Farklı Müşteri", CUST, "#1baf7a");
bar("chart-cumcust", "Kümülatif Müşteri", CUM, "#4a3aa7");

new Chart(document.getElementById("chart-newrepeat"), {{
  type: "bar",
  data: {{
    labels: LABELS,
    datasets: [
      {{ label: "Yeni müşteri", data: NEW_REV, backgroundColor: "#2a78d6", borderRadius: 5, maxBarThickness: 20 }},
      {{ label: "Sadık müşteri", data: REPEAT_REV, backgroundColor: "#eb6834", borderRadius: 5, maxBarThickness: 20 }}
    ]
  }},
  options: {{
    ...baseOptions(true),
    plugins: {{
      ...baseOptions(true).plugins,
      legend: {{ display: true, position: "top", align: "start",
        labels: {{ boxWidth: 10, boxHeight: 10, font: {{ size: 11.5 }} }} }}
    }}
  }}
}});

new Chart(document.getElementById("chart-mom"), {{
  type: "bar",
  data: {{
    labels: LABELS,
    datasets: [{{
      label: "AÜA Büyüme %", data: MOM, borderRadius: 5, maxBarThickness: 28,
      backgroundColor: MOM.map(v => v === null ? "#c3c2b7" : (v >= 0 ? "#1baf7a" : "#e34948"))
    }}]
  }},
  options: {{
    responsive: true,
    plugins: {{
      legend: {{ display: false }},
      tooltip: {{
        backgroundColor: "#0b0b0b", padding: 10, cornerRadius: 8,
        titleFont: {{ family: FONT, weight: 600 }}, bodyFont: {{ family: FONT }},
        callbacks: {{ label: (ctx) => ctx.parsed.y === null ? "veri yok" : (ctx.parsed.y >= 0 ? "+" : "") + ctx.parsed.y.toFixed(1) + "%" }}
      }}
    }},
    scales: {{
      x: {{ grid: {{ display: false }}, ticks: {{ font: {{ size: 10.5 }} }} }},
      y: {{ grid: {{ color: "#e4e2dc" }}, ticks: {{ font: {{ size: 10.5 }}, callback: v => v + "%" }} }}
    }}
  }}
}});

bar("chart-avgticket", "Ortalama İşlem ($)", AVG_TICKET, "#eb6834");
</script>
</body>
</html>"""


def render_disabled_html() -> str:
    return """<!DOCTYPE html><html lang="tr"><head><meta charset="UTF-8">
<title>Panel Kapalı</title>
<style>body{font-family:-apple-system,sans-serif;background:#1a1a19;color:#fff;
display:flex;align-items:center;justify-content:center;height:100vh;margin:0}
.box{text-align:center}</style></head>
<body><div class="box"><h2>🔒 Panel şu anda kapalı</h2>
<p style="color:#9a9890">Erişim açıldığında bu sayfa otomatik güncellenecek.</p></div></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        query_key = ""
        if "?" in self.path:
            qs = self.path.split("?", 1)[1]
            for part in qs.split("&"):
                if part.startswith("key="):
                    query_key = part[4:]

        if not TOKEN or query_key != TOKEN:
            self._send(403, "<h3>403 — geçersiz anahtar</h3>")
            return

        if not is_enabled():
            self._send(200, render_disabled_html())
            return

        try:
            data = fetch_dashboard_data()
            self._send(200, render_html(data))
        except Exception as exc:  # noqa: BLE001
            self._send(500, f"<h3>Hata</h3><pre>{html.escape(str(exc))}</pre>")

    def log_message(self, format, *args):  # noqa: A002, N802
        pass

    def _send(self, status: int, body: str) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Set DASHBOARD_TOKEN before starting the dashboard server")
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not STATE_PATH.exists():
        STATE_PATH.write_text("disabled")
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Dashboard listening on http://{HOST}:{PORT} (state: {'enabled' if is_enabled() else 'disabled'})")
    server.serve_forever()

#!/usr/bin/env python3
"""Daily "Durum Değerlendirmesi ve Aksiyon Planı" for the live panel, written
by an LLM from database facts and checked before it is shown.

Runs once a day (Hermes cron, 06:00 America/Chicago):

1. collect_facts() computes every number the report may use from Supabase.
   The model never sees raw rows and is told not to do arithmetic.
2. The model (Codex CLI on the ChatGPT subscription) writes the report as
   JSON; every item lists the ids of the facts it relies on.
3. verify_report() is deterministic: cited facts must exist and every number
   in the text must appear in a cited fact's display value or label.
4. A second model call (the judge) checks directions, comparisons and claims
   against the cited facts.
5. Failed checks are fed back for one more attempt. If that also fails the
   day is stored as rejected and the panel keeps showing the last published
   report, or the rule-based insights when there is none.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sms_campaign.db import get_connection  # noqa: E402

TZ = ZoneInfo("America/Chicago")
CODEX_BIN = os.getenv("CODEX_BIN", "/opt/data/.local/bin/codex")
MAX_ATTEMPTS = 3
MAX_ITEMS = 4

TX_DAY = "(transaction_date::timestamptz AT TIME ZONE 'America/Chicago')::date"
SV_DAY = "(service_date::timestamptz AT TIME ZONE 'America/Chicago')::date"
SV_ISO = "service_date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T'"
TX_ISO = "transaction_date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'"
# Paying customer even before they're in our customers table.
CUST_KEY = ("COALESCE(customer_id::text, substring(raw_json from '\"CustomerID\": \"?([^\",}]+)'), "
            "'txn-' || transaction_id)")

TR_MONTHS = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz",
             "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]


@dataclass
class Fact:
    id: str
    label: str
    value: float
    display: str


def money(v: float, cents: bool = False) -> str:
    return f"${v:,.2f}" if cents else f"${v:,.0f}"


def change(new: float, old: float) -> tuple[float, str]:
    if not old:
        return 0.0, "karşılaştırılamaz (önceki dönem 0)"
    pct = round((new - old) / old * 100)
    if pct == 0:
        return 0.0, "değişim yok (%0)"
    return float(pct), f"%{abs(pct)} {'artış' if pct > 0 else 'düşüş'}"


def month_name(ym: str) -> str:
    y, m = ym.split("-")
    return f"{TR_MONTHS[int(m) - 1]} {y}"


def span(start: date, end: date) -> str:
    return f"{start.isoformat()} – {end.isoformat()}"


# ── Facts ──────────────────────────────────────────────────────────────


def collect_facts(today: date) -> list[Fact]:
    import live_dashboard

    data = live_dashboard.fetch_dashboard_data()
    facts: list[Fact] = []

    def add(fid: str, label: str, value: float, display: str) -> None:
        facts.append(Fact(fid, label, float(value), display))

    # Monthly picture -- same series the panel charts use; full months only.
    full = [s for s in data["series"] if not s["partial"]][-6:]
    prev_cum = None
    for s in full:
        m, name = s["month"], month_name(s["month"])
        add(f"m.{m}.revenue", f"{name} cirosu (bahşiş dahil)", round(s["revenue"]), money(s["revenue"]))
        add(f"m.{m}.customers", f"{name} ödeme yapan müşteri sayısı", s["cust"], str(s["cust"]))
        add(f"m.{m}.services", f"{name} ödenen hizmet sayısı", s["txn"], str(s["txn"]))
        per_cust = s["revenue"] / s["cust"] if s["cust"] else 0
        add(f"m.{m}.per_customer", f"{name} müşteri başı ortalama ciro", round(per_cust, 2), money(per_cust, True))
        share = s["repeatRev"] / s["revenue"] * 100 if s["revenue"] else 0
        add(f"m.{m}.repeat_share", f"{name} cironun daha önce gelmiş müşterilerden gelen payı",
            round(share), f"%{share:.0f}")
        if prev_cum is not None:
            add(f"m.{m}.new_customers", f"{name} ilk kez gelen müşteri sayısı",
                s["cumCust"] - prev_cum, str(s["cumCust"] - prev_cum))
        prev_cum = s["cumCust"]
    if full:
        add("customers.total", f"{month_name(full[-1]['month'])} sonu itibarıyla toplam müşteri sayısı",
            full[-1]["cumCust"], str(full[-1]["cumCust"]))
    if len(full) >= 2:
        last, prev = full[-1], full[-2]
        label = f"{month_name(last['month'])}, {month_name(prev['month'])} ayına göre"
        for key, what in (("revenue", "ciro"), ("cust", "müşteri sayısı")):
            v, d = change(last[key], prev[key])
            add(f"chg.month.{key}", f"{label} {what} değişimi", v, d)
    if len(full) >= 6:
        recent, earlier = full[-3:], full[-6:-3]
        r_label = f"{month_name(recent[0]['month'])}–{month_name(recent[-1]['month'])}"
        e_label = f"{month_name(earlier[0]['month'])}–{month_name(earlier[-1]['month'])}"
        for key, what in (("revenue", "aylık ortalama ciro"), ("cust", "aylık ortalama müşteri sayısı")):
            r_avg = sum(s[key] for s in recent) / 3
            e_avg = sum(s[key] for s in earlier) / 3
            fmt = money if key == "revenue" else (lambda v: f"{v:.0f}")
            add(f"avg3.recent.{key}", f"Son 3 tam ay ({r_label}) {what}", round(r_avg), fmt(r_avg))
            add(f"avg3.earlier.{key}", f"Önceki 3 ay ({e_label}) {what}", round(e_avg), fmt(e_avg))
            v, d = change(r_avg, e_avg)
            add(f"chg.3m.{key}", f"Son 3 tam ayın {what}, önceki 3 aya göre değişimi", v, d)

    conn = get_connection()
    try:
        def revenue_customers(start: date, end: date) -> tuple[float, int, int]:
            r = conn.execute(
                f"SELECT COALESCE(SUM(total_amount), 0) rev, COUNT(DISTINCT {CUST_KEY}) cust, "
                f"COUNT(DISTINCT {TX_DAY}) open_days FROM transactions "
                f"WHERE {TX_ISO} AND {TX_DAY} BETWEEN %s AND %s", (start, end)).fetchone()
            return float(r["rev"]), r["cust"], r["open_days"]

        def visits(start: date, end: date) -> dict:
            return conn.execute(
                f"""WITH v AS (
                      SELECT {SV_DAY} AS day, COALESCE(customer_id::text, 'svc-' || service_id) AS who,
                             bool_or(booking_status = 'Service Completed') AS completed,
                             bool_and(COALESCE(booking_status, '') IN ('Cancel', 'Deleted')) AS cancelled,
                             COUNT(*) FILTER (WHERE COALESCE(booking_status, '') NOT IN ('Cancel', 'Deleted')) AS lines
                      FROM services WHERE {SV_ISO} AND {SV_DAY} BETWEEN %s AND %s
                      GROUP BY 1, 2)
                    SELECT COUNT(*) FILTER (WHERE completed) completed,
                           COUNT(*) FILTER (WHERE cancelled) cancelled,
                           COUNT(*) FILTER (WHERE NOT completed AND NOT cancelled) open,
                           COALESCE(SUM(lines) FILTER (WHERE NOT cancelled), 0) services
                    FROM v""", (start, end)).fetchone()

        def new_customers(start: date, end: date) -> int:
            return conn.execute(
                f"SELECT COUNT(*) n FROM (SELECT customer_id, MIN({TX_DAY}) first_day FROM transactions "
                f"WHERE customer_id IS NOT NULL AND {TX_ISO} GROUP BY 1) f "
                f"WHERE first_day BETWEEN %s AND %s", (start, end)).fetchone()["n"]

        # This month so far vs the same days of last month.
        yesterday = today - timedelta(days=1)
        if today.day > 1:
            month_start = today.replace(day=1)
            prev_start = (month_start - timedelta(days=1)).replace(day=1)
            prev_end = min(prev_start.replace(day=yesterday.day), month_start - timedelta(days=1))
            rev, cust, _ = revenue_customers(month_start, yesterday)
            p_rev, p_cust, _ = revenue_customers(prev_start, prev_end)
            add("mtd.revenue", f"Bu ay şimdiye kadar ({span(month_start, yesterday)}) ciro", round(rev), money(rev))
            add("mtd.customers", f"Bu ay şimdiye kadar ({span(month_start, yesterday)}) ödeme yapan müşteri",
                cust, str(cust))
            add("mtd.prev.revenue", f"Geçen ayın aynı günleri ({span(prev_start, prev_end)}) ciro",
                round(p_rev), money(p_rev))
            v, d = change(rev, p_rev)
            add("chg.mtd.revenue", "Bu ay şimdiye kadarki ciro, geçen ayın aynı günlerine göre", v, d)

        # Last 7 days vs the 7 before.
        w1 = (today - timedelta(days=7), yesterday)
        w0 = (today - timedelta(days=14), today - timedelta(days=8))
        for tag, (start, end), name in (("w1", w1, "Son 7 gün"), ("w0", w0, "Ondan önceki 7 gün")):
            label = f"{name} ({span(start, end)})"
            rev, cust, open_days = revenue_customers(start, end)
            v = visits(start, end)
            add(f"{tag}.revenue", f"{label} ciro", round(rev), money(rev))
            add(f"{tag}.customers", f"{label} ödeme yapan müşteri", cust, str(cust))
            add(f"{tag}.open_days", f"{label} ödeme alınan gün sayısı", open_days, str(open_days))
            add(f"{tag}.completed", f"{label} tamamlanan randevu", v["completed"], str(v["completed"]))
            add(f"{tag}.cancelled", f"{label} iptal edilen/silinen randevu", v["cancelled"], str(v["cancelled"]))
            add(f"{tag}.open", f"{label} tarihi geçtiği halde Vagaro'da tamamlanmamış randevu "
                f"(gelmedi ya da kapatılmadı)", v["open"], str(v["open"]))
            nc = new_customers(start, end)
            add(f"{tag}.new_customers", f"{label} ilk kez gelen müşteri", nc, str(nc))
        rev1, rev0 = next(f.value for f in facts if f.id == "w1.revenue"), next(f.value for f in facts if f.id == "w0.revenue")
        v, d = change(rev1, rev0)
        add("chg.week.revenue", "Son 7 günün cirosu, ondan önceki 7 güne göre", v, d)

        # The week ahead.
        ahead = (today, today + timedelta(days=6))
        v = visits(*ahead)
        add("next7.visits", f"Önümüzdeki 7 gün ({span(*ahead)}) alınmış randevu", v["open"] + v["completed"],
            str(v["open"] + v["completed"]))
        add("next7.services", f"Önümüzdeki 7 gün ({span(*ahead)}) alınmış hizmet", v["services"], str(v["services"]))

        # SMS, last 30 days. Email is left out: no email campaign is set up, so
        # "0 emails" would be reported as a problem every day.
        m30 = (today - timedelta(days=30), yesterday)
        label = f"Son 30 gün ({span(*m30)})"
        sms = conn.execute(
            "SELECT COUNT(*) FILTER (WHERE status IN ('sent', 'delivered')) ok, "
            "COUNT(*) FILTER (WHERE status IN ('failed', 'undelivered')) failed FROM sms_history "
            "WHERE (sent_at::timestamptz AT TIME ZONE 'America/Chicago')::date BETWEEN %s AND %s", m30).fetchone()
        # Bulk imports stamp many customers with one identical opt_out_date
        # (2026-09-16: 132 at once); only individual opt-outs say anything
        # about how customers react to the messages.
        optouts = conn.execute(
            "SELECT COUNT(*) n FROM customers WHERE opt_out_date IS NOT NULL "
            "AND opt_out_date::date BETWEEN %s AND %s AND opt_out_date IN ("
            "SELECT opt_out_date FROM customers GROUP BY 1 HAVING COUNT(*) <= 5)", m30).fetchone()["n"]
        add("m30.sms_ok", f"{label} gönderilen SMS", sms["ok"], str(sms["ok"]))
        add("m30.sms_failed", f"{label} teslim edilemeyen SMS (failed/undelivered)", sms["failed"], str(sms["failed"]))
        add("m30.optouts", f"{label} SMS listesinden kendisi çıkan müşteri (toplu içe aktarımlar hariç)",
            optouts, str(optouts))
    finally:
        conn.close()

    lapsed = data["lapsed"]
    spent = sum(float(r["total_spent"] or 0) for r in lapsed)
    reachable = sum(1 for r in lapsed if r["mobile"] and r["sms_opt_out"] != 1)
    add("lapsed.count", "Toplam $200'dan fazla harcamış, 30 günden uzun süredir gelmeyen müşteri", len(lapsed), str(len(lapsed)))
    add("lapsed.spent", "Bu müşterilerin bugüne kadarki toplam harcaması", round(spent), money(spent))
    add("lapsed.sms_reachable", "Bu müşterilerden SMS ile ulaşılabilen (telefonu olan, listeden çıkmamış)",
        reachable, str(reachable))
    return facts


# ── Checks ─────────────────────────────────────────────────────────────

NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


def numbers_in(text: str) -> set[float]:
    found = set()
    for token in NUMBER.findall(text):
        try:
            found.add(round(float(token.replace(",", "")), 2))
        except ValueError:  # e.g. "1.234.5"
            found.add(-1.0)  # never matches a fact
    return found


def report_items(report: dict) -> list[tuple[str, str, list[str]]]:
    items = [("summary", report.get("summary", ""), report.get("summary_facts", []))]
    for i, g in enumerate(report.get("good", [])):
        items.append((f"good[{i}]", g.get("text", ""), g.get("facts_used", [])))
    for i, w in enumerate(report.get("watch", [])):
        text = " ".join(w.get(k, "") for k in ("issue", "why", "action"))
        items.append((f"watch[{i}]", text, w.get("facts_used", [])))
    return items


def verify_report(report: dict, facts: list[Fact]) -> list[str]:
    """Deterministic checks; returns a list of problems (empty = passed)."""
    by_id = {f.id: f for f in facts}
    problems = []
    if not report.get("summary", "").strip():
        problems.append("summary is empty")
    for key in ("good", "watch"):
        if len(report.get(key, [])) > MAX_ITEMS:
            problems.append(f"{key} has more than {MAX_ITEMS} items")
    for where, text, cited in report_items(report):
        unknown = [c for c in cited if c not in by_id]
        if unknown:
            problems.append(f"{where}: cites unknown fact ids {unknown}")
        known = [by_id[c] for c in cited if c in by_id]
        if text.strip() and not known:
            problems.append(f"{where}: cites no facts")
        allowed = set()
        for f in known:
            allowed |= numbers_in(f.display) | numbers_in(f.label)
        stray = sorted(n for n in numbers_in(text) - allowed)
        if stray:
            shown = ", ".join("?" if n < 0 else f"{n:g}" for n in stray)
            problems.append(f"{where}: number(s) {shown} not found in its cited facts {cited}")
    return problems


# ── Model calls ────────────────────────────────────────────────────────

REPORT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["summary", "summary_facts", "good", "watch"],
    "properties": {
        "summary": {"type": "string"},
        "summary_facts": {"type": "array", "items": {"type": "string"}},
        "good": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["text", "facts_used"],
            "properties": {"text": {"type": "string"},
                           "facts_used": {"type": "array", "items": {"type": "string"}}}}},
        "watch": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["issue", "why", "action", "facts_used"],
            "properties": {"issue": {"type": "string"}, "why": {"type": "string"},
                           "action": {"type": "string"},
                           "facts_used": {"type": "array", "items": {"type": "string"}}}}},
    },
}

JUDGE_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["ok", "problems"],
    "properties": {"ok": {"type": "boolean"}, "problems": {"type": "array", "items": {"type": "string"}}},
}

WRITER_PROMPT = """You are the business analyst for ZeyBrow, a one-person eyebrow and lash salon in Dallas, TX. \
Write today's "Durum Değerlendirmesi ve Aksiyon Planı" for the owner's live dashboard, in Turkish.

The report is checked automatically and rejected if it breaks any of these rules:
1. Use only the facts below. Don't use anything else you might know or assume about this business, and don't \
do arithmetic: if a number isn't in a fact, don't write it.
2. Write numbers exactly as they appear in a fact's "display" (for example "$12,346", "%12"), or as written in \
its "label" (dates, "7 gün").
3. In facts_used (summary_facts for the summary) list the id of every fact the text relies on.
4. Directions and comparisons (arttı, azaldı, daha yüksek) must match the facts. Present causes as \
possibilities ("olabilir"), never as facts.
5. Each action says what to do, for whom and by when, and follows from the item's facts. Say "by when" in \
words (bugün, bu hafta, bu ay), never as a date. "Review", "monitor" or "keep an eye on" is not an action.
6. watch is only for problems or risks the facts actually show. A neutral number, such as how many \
appointments are booked, belongs in neither list unless the facts show it is good or bad.
7. summary: 2-3 sentences on the overall picture. good: at most {max_items} items. watch: at most {max_items} \
items, most important first. Leave a list short or empty rather than padding it.
8. Answer with the JSON object only. Don't run commands or read files.

Notes on the data: the salon is closed on some days (see the open_days facts), "Son 7 gün" and "Bu ay" can \
cover the same days, and a customer counts as new in the period of their first payment.

Today is {today} (America/Chicago).

Facts:
{facts}"""

JUDGE_PROMPT = """You are checking a Turkish business report for a salon owner against the facts it cites. \
For every item (summary, good[i], watch[i]) check that:
- each claim, number, direction (arttı/azaldı) and comparison is supported by the facts listed in that item's \
facts_used / summary_facts;
- nothing is stated that the cited facts don't show (causes must be phrased as possibilities, e.g. "olabilir");
- each watch item describes a problem or risk the cited facts actually show, not a neutral number;
- the action follows from the item, is specific (what, for whom, by when) and is something the owner can \
actually do -- "review" or "monitor" alone is not an action.
Ignore style and wording preferences. Set ok to false only for real problems, and name the item in each problem.
Answer with the JSON object only. Don't run commands or read files.

Facts:
{facts}

Report:
{report}"""


class LLMError(RuntimeError):
    pass


def run_codex(prompt: str, schema: dict, timeout: int = 600) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        schema_path = Path(tmp, "schema.json")
        schema_path.write_text(json.dumps(schema))
        out_path = Path(tmp, "answer.json")
        proc = subprocess.run(
            [CODEX_BIN, "exec", "--skip-git-repo-check", "--ephemeral", "--sandbox", "read-only",
             "--color", "never", "--cd", tmp, "--output-schema", str(schema_path),
             "--output-last-message", str(out_path), "-"],
            input=prompt, text=True, capture_output=True, timeout=timeout,
        )
        if proc.returncode != 0 or not out_path.exists():
            raise LLMError(f"codex exited {proc.returncode}: {proc.stderr.strip()[-500:]}")
        try:
            return json.loads(out_path.read_text())
        except json.JSONDecodeError as exc:
            raise LLMError(f"codex returned non-JSON output: {exc}") from exc


def facts_json(facts: list[Fact]) -> str:
    return json.dumps([{"id": f.id, "label": f.label, "display": f.display} for f in facts],
                      ensure_ascii=False, indent=1)


def write_report(facts: list[Fact], today: date, llm=run_codex) -> tuple[dict | None, list[dict]]:
    """Returns (published report or None, one record per attempt)."""
    prompt = WRITER_PROMPT.format(max_items=MAX_ITEMS, today=today.isoformat(), facts=facts_json(facts))
    attempts: list[dict] = []
    feedback: list[str] = []
    for _ in range(MAX_ATTEMPTS):
        full_prompt = prompt
        if feedback:
            full_prompt += ("\n\nYour previous answer was rejected for these reasons. Fix them:\n- "
                            + "\n- ".join(feedback))
        record: dict = {}
        attempts.append(record)
        try:
            report = llm(full_prompt, REPORT_SCHEMA)
            record["report"] = report
            feedback = verify_report(report, facts)
            record["verify_problems"] = feedback
            if not feedback:
                cited = {c for _, _, ids in report_items(report) for c in ids}
                judge = llm(JUDGE_PROMPT.format(
                    facts=facts_json([f for f in facts if f.id in cited]),
                    report=json.dumps(report, ensure_ascii=False, indent=1)), JUDGE_SCHEMA)
                record["judge"] = judge
                if judge.get("ok") and not judge.get("problems"):
                    return report, attempts
                feedback = judge.get("problems") or ["judge rejected the report without details"]
        except (LLMError, subprocess.TimeoutExpired) as exc:
            record["error"] = str(exc)
            feedback = []
    return None, attempts


# ── Storage ────────────────────────────────────────────────────────────


def store(report_date: date, status: str, facts: list[Fact], report: dict | None, attempts: list[dict]) -> None:
    conn = get_connection()
    try:
        # A failed re-run never replaces a report already published for the day.
        conn.execute(
            """INSERT INTO daily_reports
                 (report_date, generated_at, status, model, attempts, facts_json, report_json, checks_json)
               VALUES (%s, now(), %s, %s, %s, %s, %s, %s)
               ON CONFLICT (report_date) DO UPDATE SET
                 generated_at = now(), status = EXCLUDED.status, model = EXCLUDED.model,
                 attempts = EXCLUDED.attempts, facts_json = EXCLUDED.facts_json,
                 report_json = EXCLUDED.report_json, checks_json = EXCLUDED.checks_json
               WHERE daily_reports.status <> 'published' OR EXCLUDED.status = 'published'""",
            (report_date, status, "codex-cli", len(attempts),
             json.dumps([asdict(f) for f in facts], ensure_ascii=False),
             json.dumps(report, ensure_ascii=False) if report else None,
             json.dumps(attempts, ensure_ascii=False)),
        )
        conn.commit()
    finally:
        conn.close()


def already_published(report_date: date) -> bool:
    conn = get_connection()
    try:
        return conn.execute(
            "SELECT 1 FROM daily_reports WHERE report_date=%s AND status='published'", (report_date,)
        ).fetchone() is not None
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Write and verify the daily panel report.")
    parser.add_argument("--force", action="store_true", help="regenerate even if today's report is published")
    parser.add_argument("--dry-run", action="store_true", help="print facts and report, store nothing")
    args = parser.parse_args()

    today = datetime.now(TZ).date()
    if not args.force and not args.dry_run and already_published(today):
        return 0
    facts = collect_facts(today)
    report, attempts = write_report(facts, today)
    status = "published" if report else "rejected"
    if args.dry_run:
        print(json.dumps({"facts": [asdict(f) for f in facts], "status": status, "report": report,
                          "attempts": attempts}, ensure_ascii=False, indent=1))
    else:
        store(today, status, facts, report, attempts)
        print(json.dumps({"report_date": today.isoformat(), "status": status, "attempts": len(attempts),
                          "good": len(report["good"]) if report else 0,
                          "watch": len(report["watch"]) if report else 0}))
    return 0 if report else 1


if __name__ == "__main__":
    sys.exit(main())

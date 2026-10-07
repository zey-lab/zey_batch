from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import daily_report  # noqa: E402
from daily_report import Fact, verify_report, write_report  # noqa: E402

FACTS = [
    Fact("m.2026-09.revenue", "Eylül 2026 cirosu (bahşiş dahil)", 12346, "$12,346"),
    Fact("chg.month.revenue", "Eylül 2026, Ağustos 2026 ayına göre ciro değişimi", -4, "%4 düşüş"),
    Fact("w1.cancelled", "Son 7 gün (2026-09-30 – 2026-10-06) iptal edilen/silinen randevu", 3, "3"),
]
TODAY = date(2026, 10, 7)


def report(summary: str = "Eylül 2026 cirosu $12,346 oldu ve Ağustos'a göre %4 düşüş var.",
           watch_action: str = "İptal eden 3 müşteriyi arayın.") -> dict:
    return {
        "summary": summary,
        "summary_facts": ["m.2026-09.revenue", "chg.month.revenue"],
        "good": [],
        "watch": [{"issue": "Son 7 günde 3 randevu iptal edildi.", "why": "Takvimde boşluk olabilir.",
                   "action": watch_action, "facts_used": ["w1.cancelled"]}],
    }


class TestVerifyReport(unittest.TestCase):
    def test_report_using_only_cited_numbers_passes(self) -> None:
        self.assertEqual(verify_report(report(), FACTS), [])

    def test_number_not_in_cited_facts_is_rejected(self) -> None:
        problems = verify_report(report(summary="Eylül 2026 cirosu $13,000 oldu."), FACTS)
        self.assertEqual(len(problems), 1)
        self.assertIn("13000", problems[0])

    def test_number_from_an_uncited_fact_is_rejected(self) -> None:
        problems = verify_report(report(watch_action="Eylül'deki $12,346 ciroyu koruyun."), FACTS)
        self.assertTrue(any(p.startswith("watch[0]") and "12346" in p for p in problems))

    def test_unknown_fact_id_and_missing_citation_are_rejected(self) -> None:
        r = report()
        r["summary_facts"] = ["m.2026-13.revenue"]
        problems = verify_report(r, FACTS)
        self.assertTrue(any("unknown fact ids" in p for p in problems))
        self.assertTrue(any("cites no facts" in p for p in problems))


class TestWriteReport(unittest.TestCase):
    def fake_llm(self, answers: list[dict]):
        calls: list[str] = []

        def llm(prompt: str, schema: dict) -> dict:
            calls.append(prompt)
            return answers.pop(0)
        return llm, calls

    def test_rejected_draft_is_retried_with_the_problems_as_feedback(self) -> None:
        llm, calls = self.fake_llm([report(summary="Ciro $99 oldu."), report(), {"ok": True, "problems": []}])
        published, attempts = write_report(FACTS, TODAY, llm=llm)
        self.assertEqual(published, report())
        self.assertEqual(len(attempts), 2)
        self.assertIn("rejected for these reasons", calls[1])
        self.assertIn("99", calls[1])

    def test_judge_objection_blocks_publishing_after_last_attempt(self) -> None:
        objection = {"ok": False, "problems": ["summary: says revenue rose but the fact shows a drop"]}
        llm, calls = self.fake_llm([report(), objection] * daily_report.MAX_ATTEMPTS)
        published, attempts = write_report(FACTS, TODAY, llm=llm)
        self.assertIsNone(published)
        self.assertEqual(len(attempts), daily_report.MAX_ATTEMPTS)
        self.assertIn("revenue rose", calls[2])


class TestAgentAnswerHandling(unittest.TestCase):
    def test_json_is_taken_from_fenced_or_chatty_answers(self) -> None:
        self.assertEqual(daily_report.parse_json_answer('```json\n{"ok": true}\n```'), {"ok": True})
        self.assertEqual(daily_report.parse_json_answer('Here it is: {"ok": true} Done.'), {"ok": True})
        with self.assertRaises(daily_report.LLMError):
            daily_report.parse_json_answer("I could not do that.")

    def test_answer_missing_a_required_field_is_rejected(self) -> None:
        daily_report.check_shape(report(), daily_report.REPORT_SCHEMA)
        broken = report()
        del broken["watch"][0]["action"]
        with self.assertRaisesRegex(daily_report.LLMError, r"watch\[0\] is missing \['action'\]"):
            daily_report.check_shape(broken, daily_report.REPORT_SCHEMA)

    def test_codex_is_used_only_when_the_agent_fails(self) -> None:
        def agent_down(prompt: str, schema: dict) -> dict:
            raise daily_report.LLMError("connection refused")

        with patch.object(daily_report, "run_hermes", agent_down), \
                patch.object(daily_report, "run_codex", lambda prompt, schema: {"ok": True, "problems": []}), \
                patch.object(daily_report, "BACKENDS_USED", []):
            self.assertEqual(daily_report.ask_llm("p", daily_report.JUDGE_SCHEMA), {"ok": True, "problems": []})
            self.assertEqual(daily_report.BACKENDS_USED, ["codex-cli"])


if __name__ == "__main__":
    unittest.main()

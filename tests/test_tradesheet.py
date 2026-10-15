import json
import unittest
from copy import deepcopy
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import weasyprint

import tradesheet


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "tradesheet.example.json"
REPORT_PATH = ROOT / "examples" / "bni_report.json"


class TradesheetContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        cls.report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
        cls.meeting = date(2026, 9, 24)

    def test_context_exposes_only_source_documents_and_meeting_values(self) -> None:
        context = tradesheet.assemble_context(self.report, self.config, self.meeting)

        self.assertEqual(
            set(context),
            {"tradesheet", "bni_report", "meeting_date", "meeting_date_iso", "meeting_day_name"},
        )
        self.assertIs(context["tradesheet"], self.config)
        self.assertIs(context["bni_report"], self.report)
        self.assertEqual(context["meeting_date"], "24/09/2026")
        self.assertEqual(context["meeting_date_iso"], "2026-09-24")
        self.assertEqual(context["meeting_day_name"], "Thursday")

    def test_next_meeting_date_is_the_next_wednesday(self) -> None:
        self.assertEqual(tradesheet.next_meeting_date(date(2026, 9, 21)), date(2026, 9, 23))
        self.assertEqual(tradesheet.next_meeting_date(date(2026, 9, 22)), date(2026, 9, 23))
        self.assertEqual(tradesheet.next_meeting_date(date(2026, 9, 23)), date(2026, 9, 30))
        self.assertEqual(tradesheet.next_meeting_date(date(2026, 9, 24)), date(2026, 9, 30))

    def test_pdf_uses_report_date_separately_from_meeting_date(self) -> None:
        meeting_date = date(2026, 9, 30)
        report_date = date(2026, 9, 24)
        with TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "tradesheet.pdf"
            with (
                patch("tradesheet.load_or_generate_report", return_value=self.report) as load_report,
                patch("tradesheet.render_html", return_value="<html></html>"),
                patch("tradesheet.weasyprint.HTML") as html,
            ):
                html.return_value.render.return_value.pages = [object(), object()]
                tradesheet.create_pdf(
                    CONFIG_PATH,
                    REPORT_PATH,
                    output_path,
                    meeting_date,
                    report_date=report_date,
                )

        load_report.assert_called_once_with(report_date, offline=False, report_path=REPORT_PATH, force=False)

    def test_member_rows_sort_by_surname_without_mutating_roster_records(self) -> None:
        roster = [
            {"name": "Zoe Smith"},
            {"name": "Ava Adams"},
            {"name": "Ben Jones"},
            {"name": "Cara Brown"},
            {"name": "Dan Young"},
        ]

        rows = tradesheet._member_rows_by_last_name(None, roster)

        self.assertEqual([row["left"]["name"] for row in rows], ["Ava Adams", "Cara Brown", "Ben Jones"])
        self.assertEqual([row["right"]["name"] for row in rows if row["right"]], ["Zoe Smith", "Dan Young"])
        self.assertEqual(roster[0]["name"], "Zoe Smith")

    def test_leadership_rows_order_core_roles_and_collate_teams(self) -> None:
        leaders = [
            {"role": "Visitor Host", "name": "Ava Visitor"},
            {"role": "Growth and Events Coordinator", "name": "Cameron Growth"},
            {"role": "Membership Committee - Quality Assurance", "name": "Wayne Member"},
            {"role": "Go for Green Co-ordinator", "name": "Sean Green"},
            {"role": "Education Co-ordinator", "name": "Dana Education"},
            {"role": "President", "name": "Priya President"},
            {"role": "Membership Committee", "name": "Wayne Member"},
            {"role": "Vice President", "name": "Victor Vice"},
            {"role": "Secretary/Treasurer", "name": "Sonia Secretary"},
            {"role": "Mentor Coordinator", "name": "Mina Mentor"},
            {"role": "Growth and Events Coordinator", "name": "Troy Growth"},
            {"role": "Membership Committee", "name": "Maria Member"},
            {"role": "Visitor Host", "name": "Ben Visitor"},
        ]

        rows = tradesheet._leadership_rows(None, leaders)

        self.assertEqual(
            rows,
            [
                {"role": "President", "names": "Priya President"},
                {"role": "Vice President", "names": "Victor Vice"},
                {"role": "Secretary/Treasurer", "names": "Sonia Secretary"},
                {"role": "Mentor Coordinator", "names": "Mina Mentor"},
                {"role": "Go4Green Coordinator", "names": "Sean Green"},
                {"role": "Education Co-ordinator", "names": "Dana Education"},
                {"role": "Growth and Events", "names": "Cameron Growth, Troy Growth"},
                {"role": "Membership Committee", "names": "Wayne Member, Maria Member"},
                {"role": "Visitor Hosts", "names": "Ava Visitor, Ben Visitor"},
            ],
        )

    def test_template_renders_raw_documents_as_a_two_page_tradesheet(self) -> None:
        context = tradesheet.assemble_context(self.report, self.config, self.meeting)
        html = tradesheet.render_html(context)

        self.assertIn("BNI Example", html)
        self.assertIn("1,000", html)
        self.assertIn("Example Member Orientation, Zoom", html)
        self.assertIn("Chapter Ambassador", html)
        self.assertIn("Regional Example", html)
        self.assertIn("Education Co-ordinator", html)
        for member in ["Avery Example", "Blair Example", "Casey Example", "Devon Example"]:
            self.assertIn(member, html)
        self.assertIn("Example Business 1", html)
        self.assertNotIn("Slideshow Master", html)

        document = weasyprint.HTML(string=html, base_url=str(ROOT)).render()
        self.assertEqual(len(document.pages), 2)

    def test_template_uses_downloaded_featured_speakers_not_static_config(self) -> None:
        report = deepcopy(self.report)
        report["upcoming_featured_speakers"] = {
            "start_date": "2026-09-24",
            "end_date": "2026-10-21",
            "speakers": [
                {"meeting_date": "2026-09-30", "name": "Downloaded Speaker"},
                {"meeting_date": "2026-10-07", "name": "Another Downloaded Speaker"},
            ],
        }
        config = deepcopy(self.config)
        config["speaker_roster"] = [["2026-01-01", "Static Config Speaker"]]

        html = tradesheet.render_html(tradesheet.assemble_context(report, config, self.meeting))

        self.assertIn("30-Sep-26", html)
        self.assertIn("Downloaded Speaker", html)
        self.assertIn("Another Downloaded Speaker", html)
        self.assertNotIn("Static Config Speaker", html)

    def test_template_uses_generated_target_professionals_not_static_config(self) -> None:
        report = deepcopy(self.report)
        report["target_professionals"] = ["Generated Profession One", "Generated Profession Two"]
        config = deepcopy(self.config)
        config["target_professionals"] = ["Static Config Profession"]

        html = tradesheet.render_html(tradesheet.assemble_context(report, config, self.meeting))

        self.assertIn("Generated Profession One", html)
        self.assertIn("Generated Profession Two", html)
        self.assertNotIn("Static Config Profession", html)


if __name__ == "__main__":
    unittest.main()

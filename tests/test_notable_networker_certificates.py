import json
import tempfile
import unittest
from copy import deepcopy
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import notable_networker_certificates as certificates


ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = ROOT / "examples" / "bni_report.json"


class NotableNetworkerCertificateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))

    def test_builds_rank_one_contexts_in_award_order(self) -> None:
        contexts, missing = certificates.assemble_certificate_contexts(self.report)

        self.assertEqual(missing, [])
        self.assertEqual([context["winner_name"] for context in contexts], [
            "Avery Example", "Blair Example", "Casey Example", "Devon Example",
        ])
        self.assertEqual(contexts[0]["award_line"], "Most 1-2-1s - 22")
        self.assertEqual(contexts[1]["award_line"], "Most Referrals - 22")
        self.assertEqual(contexts[2]["award_line"], "Most TYFCB - $25,870")
        self.assertEqual(contexts[3]["award_line"], "Most Invited Visitors - 1")
        self.assertEqual(contexts[0]["award_month_slug"], "2026-08")

    def test_reports_missing_categories_without_blocking_available_ones(self) -> None:
        report = deepcopy(self.report)
        del report["notable_networkers"]["categories"]["most_invited_visitors"]

        contexts, missing = certificates.assemble_certificate_contexts(report)

        self.assertEqual(len(contexts), 3)
        self.assertEqual(missing, ["Most Invited Visitors"])

    def test_extracts_the_chapter_president(self) -> None:
        self.assertEqual(certificates.extract_president(self.report), "Blair Example")
        self.assertEqual(certificates.extract_president({"chapter_roster": {"leaders": []}}), "")

    def test_template_renders_as_one_landscape_letter_page(self) -> None:
        context, _ = certificates.assemble_certificate_contexts(self.report)
        html = certificates.render_html({**context[2], "president_name": "Blair Example"})

        self.assertIn("Casey Example", html)
        self.assertIn("Most TYFCB - $25,870", html)
        self.assertIn("Growing Forward Together", html)
        document = certificates.weasyprint.HTML(string=html, base_url=str(ROOT)).render()
        self.assertEqual(len(document.pages), 1)

    def test_batch_writes_only_available_certificate_paths(self) -> None:
        report = deepcopy(self.report)
        del report["notable_networkers"]["categories"]["most_invited_visitors"]

        class FakeRendered:
            pages = [object()]

            def write_pdf(self, target: str) -> None:
                self.target = target

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch.object(certificates, "load_or_generate_report", return_value=report),
            patch.object(certificates.weasyprint, "HTML", return_value=SimpleNamespace(render=lambda: FakeRendered())),
        ):
            paths = certificates.create_certificates(
                report_path=REPORT_PATH,
                output_dir=Path(temp_dir),
                target_date=date(2026, 9, 24),
            )

        self.assertEqual(len(paths), 3)
        self.assertTrue(all("notable-networker" in path.name.casefold() for path in paths))


if __name__ == "__main__":
    unittest.main()

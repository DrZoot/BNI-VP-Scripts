from pathlib import Path
import unittest
from datetime import datetime
from unittest.mock import patch

import bni_report
from parsers.speaker_report import parse_speaker_report


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "speaker_report.html"


class SpeakerReportParserTests(unittest.TestCase):
    def test_extracts_and_sorts_featured_speakers(self) -> None:
        speakers = parse_speaker_report(FIXTURE.read_text(encoding="utf-8"))

        self.assertEqual(
            speakers,
            [
                {
                    "name": "Ava Presenter",
                    "chapter_name": "BNI Example",
                    "meeting_date": "2026-10-01",
                },
                {
                    "name": "Ben Presenter",
                    "chapter_name": "BNI Example",
                    "meeting_date": "2026-10-01",
                },
                {
                    "name": "Zoe Speaker",
                    "chapter_name": "BNI Example",
                    "meeting_date": "2026-10-08",
                },
            ],
        )

    def test_returns_empty_list_for_missing_or_unrecognised_report(self) -> None:
        self.assertEqual(parse_speaker_report(None), [])
        self.assertEqual(parse_speaker_report("<table><tr><td>No data</td></tr></table>"), [])


class SpeakerReportGenerationTests(unittest.TestCase):
    def test_requests_four_weeks_and_exports_speakers(self) -> None:
        speaker_html = FIXTURE.read_text(encoding="utf-8")

        class FakeBNISession:
            instance = None

            def __init__(self, *_args) -> None:
                self.requests = []
                FakeBNISession.instance = self

            def get_report(self, url, params):
                self.requests.append((url, params.copy()))
                return speaker_html if url == bni_report.REPORT_SPEAKER_URL else None

        with (
            patch.object(bni_report, "BNISession", FakeBNISession),
            patch.object(bni_report, "BNI_USERNAME", "test-user"),
            patch.object(bni_report, "BNI_PASSWORD", "test-password"),
            patch.object(bni_report, "BNI_REGION_ID", "2"),
            patch.object(
                bni_report,
                "select_target_professionals",
                return_value=["Target One", "Target Two", "Target Three", "Target Four", "Target Five"],
            ),
        ):
            report = bni_report.generate_report(
                output_file=None,
                reference_date=datetime(2026, 9, 24),
            )

        speaker_requests = [
            params
            for url, params in FakeBNISession.instance.requests
            if url == bni_report.REPORT_SPEAKER_URL
        ]
        self.assertEqual(
            speaker_requests,
            [
                {
                    "IdRegion": "2",
                    "IdOrg": bni_report.BNI_CHAPTER_ID,
                    "startDate": "09/24/2026",
                    "endDate": "10/21/2026",
                }
            ],
        )
        self.assertEqual(report["upcoming_featured_speakers"]["start_date"], "2026-09-24")
        self.assertEqual(report["upcoming_featured_speakers"]["end_date"], "2026-10-21")
        self.assertEqual(report["upcoming_featured_speakers"]["speakers"][0]["name"], "Ava Presenter")
        self.assertEqual(
            report["target_professionals"],
            ["Target One", "Target Two", "Target Three", "Target Four", "Target Five"],
        )


if __name__ == "__main__":
    unittest.main()
